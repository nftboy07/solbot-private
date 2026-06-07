"""PumpPortal Local Transaction Signing Client.

Provides asynchronous methods to fetch transactions from PumpPortal
and sign them locally before sending to the Solana network.
"""

import asyncio
import time
from typing import Optional

import aiohttp
from solders.transaction import VersionedTransaction
from solders.message import MessageV0
from solders.keypair import Keypair

from solbot.config import JupiterConfig  # Using JupiterConfig for buy_amount/slippage defaults
from solbot.logger import get_logger
from solbot.models import TradeResult
from solbot.wallet import Wallet

logger = get_logger("pumpfun_client")


class PumpFunClient:
    """Async client for PumpPortal local transaction signing."""

    def __init__(self, config: JupiterConfig, wallet: Wallet):
        self._config = config
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
        slippage: Optional[int] = None
    ) -> TradeResult:
        """Fetch, sign, and broadcast a trade transaction via PumpPortal.
        
        Args:
            mint: The token mint address.
            action: "buy" or "sell".
            amount: Amount in SOL (buy) or token units (sell). 
                    Defaults to config.buy_amount_sol for buys.
            slippage: Slippage in basis points. Defaults to config.slippage_bps.
        """
        start_time = time.perf_counter()
        
        if amount is None and action == "buy":
            amount = self._config.buy_amount_sol
        
        if slippage is None:
            slippage = self._config.slippage_bps

        payload = {
            "publicKey": self._wallet.pubkey_str,
            "action": action,
            "mint": mint,
            "denominatedInSol": "true" if action == "buy" else "false",
            "amount": amount,
            "slippage": slippage,
            "priorityFee": 0.00001,  # Default priority fee
            "pool": "pump"
        }

        try:
            async with self._session.post(self._base_url, json=payload) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    return TradeResult(
                        success=False,
                        error=f"PumpPortal API error: {resp.status} - {error_text}",
                        latency_ms=(time.perf_counter() - start_time) * 1000
                    )
                
                # The local API returns the raw transaction bytes
                tx_data = await resp.read()
                
            # 1. Deserialize the transaction
            tx = VersionedTransaction.from_bytes(tx_data)
            
            # 2. Sign locally
            # Note: VersionedTransaction.sign takes a list of keypairs
            tx.sign([self._wallet.keypair])
            
            # 3. Broadcast to the network via PumpPortal's lightning endpoint 
            # (or use custom RPC if available). PumpPortal docs suggest sending 
            # the signed tx back to their broadcast endpoint or using your own.
            # Here we follow the local signing logic: fetch -> sign -> broadcast.
            
            broadcast_url = "https://pumpportal.fun/api/broadcast"
            broadcast_payload = {
                "signedTransaction": bytes(tx).hex()
            }
            
            async with self._session.post(broadcast_url, json=broadcast_payload) as b_resp:
                b_data = await b_resp.json()
                latency = (time.perf_counter() - start_time) * 1000
                
                if b_resp.status == 200 and "signature" in b_data:
                    return TradeResult(
                        success=True,
                        tx_signature=b_data["signature"],
                        latency_ms=latency
                    )
                else:
                    return TradeResult(
                        success=False,
                        error=f"Broadcast failed: {b_data.get('errors', 'Unknown error')}",
                        latency_ms=latency
                    )

        except Exception as e:
            logger.error(f"PumpFun trade execution failed: {e}")
            return TradeResult(
                success=False,
                error=str(e),
                latency_ms=(time.perf_counter() - start_time) * 1000
            )
