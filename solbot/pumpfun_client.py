"""PumpPortal Local Transaction Signing Client with Jito Support."""

import asyncio
import time
import base58
import logging
from typing import Optional, List, Dict

import aiohttp
from solders.transaction import VersionedTransaction
from solders.system_program import transfer, TransferParams
from solders.pubkey import Pubkey

from solbot.config import BotConfig
from solbot.models import TradeResult
from solbot.wallet import Wallet
from solbot.jito import JitoClient

logger = logging.getLogger("bot.pumpfun_client")

class PumpFunClient:
    """Async client for PumpPortal local transaction signing with Jito bundling."""

    def __init__(self, config: BotConfig, wallet: Wallet):
        self._bot_config = config
        self._jupiter_config = config.jupiter
        self._solana_config = config.solana
        self._wallet = wallet
        self._session: Optional[aiohttp.ClientSession] = None
        self._jito: Optional[JitoClient] = None
        self._base_url = "https://pumpportal.fun/api/trade-local"

    async def start(self):
        if not self._session:
            timeout = aiohttp.ClientTimeout(total=10)
            self._session = aiohttp.ClientSession(timeout=timeout)
        # JitoClient only takes config
        self._jito = JitoClient(self._bot_config)
        # JitoClient has no start method

    async def stop(self):
        if self._session:
            await self._session.close()
        # JitoClient has no stop method

    async def get_sol_balance(self) -> float:
        """Fetch the current SOL balance for the wallet."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [self._wallet.pubkey_str]
        }
        rpc_url = self._solana_config.rpc_url
        logger.debug(f"Fetching SOL balance for {self._wallet.pubkey_str} via {rpc_url}")
        try:
            async with self._session.post(rpc_url, json=payload) as resp:
                raw_response = await resp.text()
                logger.debug(f"RPC raw response: {raw_response}")
                data = await resp.json()
                lamports = data.get("result", {}).get("value", 0)
                balance = lamports / 1_000_000_000
                logger.info(f"Calculated SOL balance for {self._wallet.pubkey_str}: {balance:.4f}")
                return balance
        except Exception as e:
            logger.error(f"Error fetching SOL balance: {e}")
            return 0.0

    async def get_all_token_balances(self) -> Dict[str, Dict]:
        """Fetch all SPL and Token-2022 balances with metadata."""
        programs = [
            "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", # SPL Token
            "TokenzQdBNbLqP5VEhdkAS6EP2H6V3MG69L7AHXTo"  # Token-2022
        ]
        balances = {}
        
        for program_id in programs:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTokenAccountsByOwner",
                "params": [
                    self._wallet.pubkey_str,
                    {"programId": program_id},
                    {"encoding": "jsonParsed"}
                ]
            }
            try:
                async with self._session.post(self._solana_config.rpc_url, json=payload) as resp:
                    data = await resp.json()
                    accounts = data.get("result", {}).get("value", [])
                    for acc in accounts:
                        info = acc["account"]["data"]["parsed"]["info"]
                        mint = info["mint"]
                        amount = float(info["tokenAmount"]["uiAmount"] or 0)
                        if amount > 0:
                            balances[mint] = {
                                "balance": amount,
                                "program": "Token-2022" if program_id.endswith("To") else "SPL"
                            }
            except Exception as e:
                logger.error(f"Error fetching balances for {program_id}: {e}")
        
        return balances

    async def get_token_metadata(self, mint: str) -> Dict:
        """Fetch basic token metadata (symbol)."""
        url = f"https://frontend-api.pump.fun/coins/{mint}"
        try:
            async with self._session.get(url) as resp:
                if resp.status == 200:
                    return await resp.json()
        except:
            pass
        return {"symbol": "???", "name": "Unknown", "creator": "unknown", "market_cap_sol": 0, "liquidity_sol": 0}

    async def get_token_balance(self, mint: str) -> float:
        """Fetch the current token balance for the wallet."""
        for program_id in ["TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "TokenzQdBNbLqP5VEhdkAS6EP2H6V3MG69L7AHXTo"]:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTokenAccountsByOwner",
                "params": [
                    self._wallet.pubkey_str,
                    {"mint": mint, "programId": program_id},
                    {"encoding": "jsonParsed"}
                ]
            }
            try:
                async with self._session.post(self._solana_config.rpc_url, json=payload) as resp:
                    data = await resp.json()
                    accounts = data.get("result", {}).get("value", [])
                    if accounts:
                        amount_info = accounts[0]["account"]["data"]["parsed"]["info"]["tokenAmount"]
                        return float(amount_info["uiAmount"] or 0)
            except:
                continue
        return 0.0

    async def execute_trade(
        self, 
        mint: str, 
        action: str = "buy", 
        amount: Optional[float] = None,
        slippage: Optional[int] = None,
        priority_fee: Optional[float] = None,
        use_jito: bool = True,
        denominated_in_sol: bool = True
    ) -> TradeResult:
        start_time = time.perf_counter()
        
        amount = amount or self._jupiter_config.buy_amount_sol
        slippage = slippage or self._jupiter_config.slippage_bps
        priority_fee = priority_fee or 0.00001

        payload = {
            "publicKey": self._wallet.pubkey_str,
            "action": action,
            "mint": mint,
            "denominatedInSol": "true" if denominated_in_sol else "false",
            "amount": amount,
            "slippage": slippage,
            "priorityFee": priority_fee,
            "pool": "pump"
        }

        try:
            async with self._session.post(self._base_url, json=payload) as resp:
                if resp.status != 200:
                    return TradeResult(success=False, token_mint=mint, error=f"API Error: {resp.status}")
                tx_data = await resp.read()
                
            tx = VersionedTransaction.from_bytes(tx_data)
            signed_tx = VersionedTransaction(tx.message, [self._wallet.keypair])

            if use_jito:
                # Execute via Jito Bundle
                bundle_id = await self._jito.send_bundle([signed_tx], tip_amount_sol=0.001)
                latency = (time.perf_counter() - start_time) * 1000
                if bundle_id:
                    return TradeResult(success=True, token_mint=mint, tx_signature=bundle_id, latency_ms=latency)
                else:
                    return TradeResult(success=False, token_mint=mint, error="Jito Bundle Failed", latency_ms=latency)
            else:
                # Direct RPC Broadcast
                rpc_payload = {
                    "jsonrpc": "2.0", "id": 1, "method": "sendTransaction",
                    "params": [base58.b58encode(bytes(signed_tx)).decode("utf-8"), {"skipPreflight": True}]
                }
                async with self._session.post(self._solana_config.rpc_url, json=rpc_payload) as r_resp:
                    r_data = await r_resp.json()
                    latency = (time.perf_counter() - start_time) * 1000
                    return TradeResult(success=True, token_mint=mint, tx_signature=r_data.get("result"), latency_ms=latency)

        except Exception as e:
            return TradeResult(success=False, token_mint=mint, error=str(e))
