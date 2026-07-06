"""Jupiter V6 Swap API Client with Transaction Broadcast."""
import aiohttp
import base64
import logging
import time
import base58
from typing import Optional

from solders.transaction import VersionedTransaction
from solbot.config import JupiterConfig, SolanaConfig
from solbot.wallet import Wallet
from solbot.models import TradeResult

logger = logging.getLogger("bot.jupiter")


class JupiterClient:
    """Async client for Jupiter V6 Swap API."""

    def __init__(self, config: JupiterConfig, wallet: Wallet, solana: Optional[SolanaConfig] = None):
        self._config = config
        self._wallet = wallet
        self._solana = solana
        self._base_url = config.api_url.rstrip("/") or "https://quote-api.jup.ag/v6"
        self._rpc_pool = None
        self._observability = None
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_rpc_url(self) -> str:
        if self._rpc_pool:
            return await self._rpc_pool.get_best_node()
        if self._solana:
            return self._solana.rpc_url
        return "https://api.mainnet-beta.solana.com"

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
            "onlyDirectRoutes": "false",
        }
        async with self._session.get(f"{self._base_url}/quote", params=params) as resp:
            if resp.status != 200:
                text = await resp.text()
                logger.error("Jupiter quote error: %s", text)
                return None
            return await resp.json()

    async def execute_trade(
        self,
        mint: str,
        action: str = "buy",
        amount: Optional[float] = None,
        slippage: Optional[int] = None,
        priority_fee: Optional[float] = None,
    ) -> TradeResult:
        start_time = time.perf_counter()
        try:
            sol_mint = "So11111111111111111111111111111111111111112"
            input_mint = sol_mint if action == "buy" else mint
            output_mint = mint if action == "buy" else sol_mint

            trade_amount = int((amount or self._config.buy_amount_sol) * 1e9) if action == "buy" else int(amount or 0)

            quote = await self.get_quote(input_mint, output_mint, trade_amount, slippage or self._config.slippage_bps)
            if not quote:
                return TradeResult(success=False, token_mint=mint, error="Failed to get quote")

            swap_payload = {
                "quoteResponse": quote,
                "userPublicKey": self._wallet.pubkey_str,
                "wrapAndUnwrapSol": True,
                "prioritizationFeeLamports": int((priority_fee or 0.001) * 1e9),
            }

            async with self._session.post(f"{self._base_url}/swap", json=swap_payload) as resp:
                if resp.status != 200:
                    return TradeResult(success=False, token_mint=mint, error=f"Swap API Error: {await resp.text()}")
                swap_data = await resp.json()
                tx_b64 = swap_data["swapTransaction"]

            raw_tx = base64.b64decode(tx_b64)
            tx = VersionedTransaction.from_bytes(raw_tx)
            signed_tx = VersionedTransaction(tx.message, [self._wallet.keypair])

            rpc_url = await self._get_rpc_url()
            rpc_payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendTransaction",
                "params": [
                    base58.b58encode(bytes(signed_tx)).decode("utf-8"),
                    {"skipPreflight": True, "encoding": "base58"},
                ],
            }

            broadcast_start = time.perf_counter()
            async with self._session.post(rpc_url, json=rpc_payload) as r_resp:
                r_data = await r_resp.json()
                latency_b = (time.perf_counter() - broadcast_start) * 1000
                ok = r_resp.status == 200 and "error" not in r_data
                if getattr(self, "_observability", None):
                    self._observability.record_rpc(rpc_url, "sendTransaction", latency_b, ok, r_resp.status)
                if self._rpc_pool:
                    await self._rpc_pool.report_metrics(rpc_url, ok, latency_b, status_code=r_resp.status)
                latency = (time.perf_counter() - start_time) * 1000
                sig = r_data.get("result")
                if ok and sig:
                    return TradeResult(success=True, token_mint=mint, tx_signature=sig, latency_ms=latency)
                return TradeResult(
                    success=False,
                    token_mint=mint,
                    error=f"RPC Error: {r_data.get('error')}",
                    latency_ms=latency,
                )

        except Exception as e:
            return TradeResult(success=False, token_mint=mint, error=str(e))