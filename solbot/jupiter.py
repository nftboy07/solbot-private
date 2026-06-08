"""Jupiter V6 Swap API Client with Transaction Broadcast."""
import aiohttp
import base64
import logging
import time
import base58
from typing import Optional, Dict, List
from solders.transaction import VersionedTransaction
from solbot.config import JupiterConfig
from solbot.wallet import Wallet
from solbot.models import TradeResult

logger = logging.getLogger("bot.jupiter")

class JupiterClient:
    """Async client for Jupiter V6 Swap API."""

    def __init__(self, config: JupiterConfig, wallet: Wallet):
        self._config = config
        self._wallet = wallet
        self._base_url = "https://quote-api.jup.ag/v6"
        self._rpc_url = "https://api.mainnet-beta.solana.com" # Should come from config
        self._session: Optional[aiohttp.ClientSession] = None

    async def start(self):
        if not self._session:
            self._session = aiohttp.ClientSession()

    async def stop(self):
        if self._session:
            await self._session.close()

    async def get_quote(self, input_mint: str, output_mint: str, amount: int, slippage_bps: int):
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount),
            "slippageBps": slippage_bps,
            "onlyDirectRoutes": "false"
        }
        async with self._session.get(f"{self._base_url}/quote", params=params) as resp:
            if resp.status != 200:
                text = await resp.text()
                logger.error(f"Jupiter quote error: {text}")
                return None
            return await resp.json()

    async def execute_trade(
        self,
        mint: str,
        action: str = "buy",
        amount: Optional[float] = None,
        slippage: Optional[int] = None,
        priority_fee: Optional[float] = None
    ) -> TradeResult:
        start_time = time.perf_counter()
        try:
            sol_mint = "So11111111111111111111111111111111111111112"
            input_mint = sol_mint if action == "buy" else mint
            output_mint = mint if action == "buy" else sol_mint
            
            # Convert SOL amount to lamports if buying
            trade_amount = int((amount or self._config.buy_amount_sol) * 1e9) if action == "buy" else int(amount)
            
            quote = await self.get_quote(input_mint, output_mint, trade_amount, slippage or self._config.slippage_bps)
            if not quote:
                return TradeResult(success=False, token_mint=mint, error="Failed to get quote")

            swap_payload = {
                "quoteResponse": quote,
                "userPublicKey": self._wallet.pubkey_str,
                "wrapAndUnwrapSol": True,
                "prioritizationFeeLamports": int((priority_fee or 0.001) * 1e9)
            }

            async with self._session.post(f"{self._base_url}/swap", json=swap_payload) as resp:
                if resp.status != 200:
                    return TradeResult(success=False, token_mint=mint, error=f"Swap API Error: {await resp.text()}")
                swap_data = await resp.json()
                tx_b64 = swap_data["swapTransaction"]

            raw_tx = base64.b64decode(tx_b64)
            tx = VersionedTransaction.from_bytes(raw_tx)
            signed_tx = VersionedTransaction(tx.message, [self._wallet.keypair])
            
            # Broadcast the transaction
            rpc_payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendTransaction",
                "params": [
                    base58.b58encode(bytes(signed_tx)).decode("utf-8"),
                    {"skipPreflight": True, "encoding": "base58"}
                ]
            }
            
            async with self._session.post(self._rpc_url, json=rpc_payload) as r_resp:
                r_data = await r_resp.json()
                sig = r_data.get("result")
                latency = (time.perf_counter() - start_time) * 1000
                if sig:
                    return TradeResult(success=True, token_mint=mint, tx_signature=sig, latency_ms=latency)
                else:
                    return TradeResult(success=False, token_mint=mint, error=f"RPC Error: {r_data.get('error')}", latency_ms=latency)

        except Exception as e:
            return TradeResult(success=False, token_mint=mint, error=str(e))
