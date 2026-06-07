"""PumpPortal Local Transaction Signing Client.

Provides asynchronous methods to fetch transactions from PumpPortal
and sign them locally before sending to the Solana network.
"""

import asyncio
import time
import base58
from typing import Optional

import aiohttp
from solders.transaction import VersionedTransaction
from solders.keypair import Keypair

from solbot.config import JupiterConfig, SolanaConfig, BotConfig
from solbot.logger import get_logger
from solbot.models import TradeResult
from solbot.wallet import Wallet

logger = get_logger("pumpfun_client")


class PumpFunClient:
    """Async client for PumpPortal local transaction signing."""

    def __init__(self, config: BotConfig, wallet: Wallet):
        self._bot_config = config
        self._jupiter_config = config.jupiter
        self._solana_config = config.solana
        self._wallet = wallet
        self._session: Optional[aiohttp.ClientSession] = None
        self._base_url = "https://pumpportal.fun/api/trade-local"

    async def start(self):
        """Initialize the aiohttp session."""
        if not self._session:
            self._session = aiohttp.ClientSession()

    async def stop(self):
        """Close the aiohttp session."""
        if self._session:
            await self._session.close()
            self._session = None

    async def execute_trade(
        self, 
        mint: str, 
        action: str = "buy", 
        amount: Optional[float] = None,
        slippage: Optional[int] = None,
        priority_fee: Optional[float] = None
    ) -> TradeResult:
        """Fetch, sign, and broadcast a trade transaction.
        
        Args:
            mint: The token mint address.
            action: "buy" or "sell".
            amount: Amount in SOL (buy) or token units (sell). 
                    Defaults to config.buy_amount_sol for buys.
            slippage: Slippage in basis points. Defaults to config.slippage_bps.
            priority_fee: Transaction priority fee in SOL.
        """
        start_time = time.perf_counter()
        
        if amount is None and action == "buy":
            amount = self._jupiter_config.buy_amount_sol
        
        if slippage is None:
            slippage = self._jupiter_config.slippage_bps
            
        if priority_fee is None:
            priority_fee = 0.00001 # Default baseline

        payload = {
            "publicKey": self._wallet.pubkey_str,
            "action": action,
            "mint": mint,
            "denominatedInSol": "true" if action == "buy" else "false",
            "amount": amount,
            "slippage": slippage,
            "priorityFee": priority_fee,
            "pool": "pump"
        }

        try:
            async with self._session.post(self._base_url, json=payload) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    return TradeResult(
                        success=False,
                        token_mint=mint,
                        error=f"PumpPortal API error: {resp.status} - {error_text}",
                        latency_ms=(time.perf_counter() - start_time) * 1000
                    )
                
                # The local API returns the raw transaction bytes
                tx_data = await resp.read()
                
            # 1. Deserialize the transaction
            tx = VersionedTransaction.from_bytes(tx_data)
            
            # 2. Sign locally
            signed_tx = VersionedTransaction(tx.message, [self._wallet.keypair])
            raw_tx = bytes(signed_tx)

            # 3. Broadcast to the Solana RPC
            rpc_payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendTransaction",
                "params": [
                    base58.b58encode(raw_tx).decode("utf-8"),
                    {
                        "skipPreflight": True,
                        "preflightCommitment": "processed",
                        "encoding": "base58",
                        "maxRetries": 2,
                    },
                ],
            }

            async with self._session.post(self._solana_config.rpc_url, json=rpc_payload) as r_resp:
                r_data = await r_resp.json()
                latency = (time.perf_counter() - start_time) * 1000
                
                if "result" in r_data:
                    return TradeResult(
                        success=True,
                        token_mint=mint,
                        tx_signature=r_data["result"],
                        latency_ms=latency
                    )
                else:
                    error_msg = r_data.get("error", "Unknown RPC error")
                    return TradeResult(
                        success=False,
                        token_mint=mint,
                        error=f"RPC Broadcast failed: {error_msg}",
                        latency_ms=latency
                    )

        except Exception as e:
            logger.error(f"PumpFun trade execution failed: {e}")
            return TradeResult(
                success=False,
                token_mint=mint,
                error=str(e),
                latency_ms=(time.perf_counter() - start_time) * 1000
            )
