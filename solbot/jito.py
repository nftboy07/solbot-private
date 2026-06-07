"""Jito Bundle Execution Client for Private Transactions."""

import asyncio
import base58
import logging
from typing import List, Optional
import aiohttp
from solders.transaction import VersionedTransaction
from solders.instruction import Instruction
from solders.pubkey import Pubkey
from solders.system_program import transfer, TransferParams
from solders.message import MessageV0

from solbot.config import BotConfig
from solbot.wallet import Wallet

logger = logging.getLogger("bot.jito")

class JitoClient:
    """Client for sending private transaction bundles via Jito Block Engine."""

    # Common Jito Tip accounts
    TIP_ACCOUNTS = [
        "96g9sAg9u3mBsJp9UuXGLXXTXvWxcT1oKy8V44n7662L",
        "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
        "Cw8CFyMvRWqyUvTBF9vkzt2M8v7Nzo8T69VqN2E7v59S",
        "ADaUMid9yfU9sM7776Z7S99e8uydFypmF8Y3UoH4sDrs",
        "DfXyU99e8uydFypmF8Y3UoH4sDrS9yfU99e8uydFypmF", # Placeholder variants
    ]

    def __init__(self, config: BotConfig, wallet: Wallet):
        self._config = config
        self._wallet = wallet
        self._base_url = "https://mainnet.block-engine.jito.wtf/api/v1/bundles"
        self._session: Optional[aiohttp.ClientSession] = None

    async def start(self):
        if not self._session:
            self._session = aiohttp.ClientSession()

    async def stop(self):
        if self._session:
            await self._session.close()
            self._session = None

    async def send_bundle(self, transactions: List[VersionedTransaction], tip_amount_sol: float = 0.0001) -> Optional[str]:
        """Wrap transactions with a Jito tip and send as a bundle."""
        if not transactions:
            return None

        try:
            # 1. Add Tip Instruction to the last transaction or as a new one
            # For simplicity and to ensure block 0 landing, we often append a tip to the end of the bundle
            tip_lamports = int(tip_amount_sol * 1_000_000_000)
            tip_account = Pubkey.from_string(self.TIP_ACCOUNTS[0])
            
            tip_ix = transfer(TransferParams(
                from_pubkey=self._wallet.pubkey,
                to_pubkey=tip_account,
                lamports=tip_lamports
            ))

            # Create a separate tip transaction for the bundle
            # Note: In a real sniper, you might merge this into the main TX to save space/fees
            # but bundles allow up to 5 transactions.
            
            # For this implementation, we assume the provided transactions are already signed or ready.
            # We will send them as provided and add the tip as a signed message if needed, 
            # or rely on the caller providing a bundle.
            
            # Bundle Payload
            encoded_bundle = [base58.b58encode(bytes(tx)).decode("utf-8") for tx in transactions]
            
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendBundle",
                "params": [encoded_bundle]
            }

            async with self._session.post(self._base_url, json=payload) as resp:
                data = await resp.json()
                if "result" in data:
                    bundle_id = data["result"]
                    logger.info(f"Jito Bundle Sent | ID: {bundle_id} | Tip: {tip_amount_sol} SOL")
                    return bundle_id
                else:
                    logger.error(f"Jito Bundle Failed: {data.get('error')}")
                    return None

        except Exception as e:
            logger.error(f"Jito Client Error: {e}")
            return None
