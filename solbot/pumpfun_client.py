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
from solders.message import Message
from solders.hash import Hash

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
        self._rpc_pool = None

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

    async def _get_rpc_url(self) -> str:
        if hasattr(self, '_rpc_pool') and self._rpc_pool:
            return await self._rpc_pool.get_best_node()
        return self._solana_config.rpc_url

    async def _report_rpc_metric(self, url: str, success: bool, latency: float = 0.0, slot: int = 0, status_code: Optional[int] = None):
        if hasattr(self, '_rpc_pool') and self._rpc_pool:
            await self._rpc_pool.report_metrics(url, success, latency, slot, status_code)

    async def get_sol_balance(self) -> float:
        """Fetch the current SOL balance for the wallet."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [self._wallet.pubkey_str]
        }
        url = await self._get_rpc_url()
        start = time.perf_counter()
        try:
            async with self._session.post(url, json=payload) as resp:
                data = await resp.json()
                latency = (time.perf_counter() - start) * 1000
                await self._report_rpc_metric(url, True, latency, status_code=resp.status)
                lamports = data.get("result", {}).get("value", 0)
                return lamports / 1_000_000_000
        except Exception as e:
            logger.error(f"Error fetching SOL balance: {e}")
            await self._report_rpc_metric(url, False, status_code=500)
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
            url = await self._get_rpc_url()
            start = time.perf_counter()
            try:
                async with self._session.post(url, json=payload) as resp:
                    data = await resp.json()
                    latency = (time.perf_counter() - start) * 1000
                    await self._report_rpc_metric(url, True, latency, status_code=resp.status)
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
                await self._report_rpc_metric(url, False, status_code=500)
        
        return balances

    async def get_token_metadata_onchain(self, mint: str) -> Dict:
        """Fetch token metadata on-chain from Solana RPC via Metaplex Metadata PDA."""
        try:
            from solders.pubkey import Pubkey
            import base64
            import struct
            import time

            mint_pubkey = Pubkey.from_string(mint)
            metadata_program_id = Pubkey.from_string("metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s")
            seeds = [b"metadata", bytes(metadata_program_id), bytes(mint_pubkey)]
            metadata_pda, _ = Pubkey.find_program_address(seeds, metadata_program_id)

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [
                    str(metadata_pda),
                    {"encoding": "base64"}
                ]
            }

            url = await self._get_rpc_url()
            start = time.perf_counter()
            async with self._session.post(url, json=payload) as resp:
                latency = (time.perf_counter() - start) * 1000
                await self._report_rpc_metric(url, True, latency, status_code=resp.status)
                if resp.status == 200:
                    res_data = await resp.json()
                    value = res_data.get("result", {}).get("value")
                    if value and value.get("data"):
                        b64_data = value["data"][0]
                        data = base64.b64decode(b64_data)
                        if len(data) >= 101:
                            offset = 65
                            name_len = struct.unpack("<I", data[offset:offset+4])[0]
                            offset += 4
                            name = data[offset:offset+name_len].decode("utf-8", errors="ignore").strip("\x00 \t\n\r")
                            offset += 32
                            
                            symbol_len = struct.unpack("<I", data[offset:offset+4])[0]
                            offset += 4
                            symbol = data[offset:offset+symbol_len].decode("utf-8", errors="ignore").strip("\x00 \t\n\r")
                            
                            return {
                                "symbol": symbol,
                                "name": name,
                                "creator": "unknown",
                                "market_cap_sol": 0,
                                "liquidity_sol": 0
                            }
        except Exception as e:
            logger.error(f"Error fetching on-chain metadata for {mint}: {e}")
        return {"symbol": "???", "name": "Unknown", "creator": "unknown", "market_cap_sol": 0, "liquidity_sol": 0}

    async def get_token_metadata(self, mint: str) -> Dict:
        """Fetch basic token metadata (symbol)."""
        url = f"https://frontend-api.pump.fun/coins/{mint}"
        
        proxy = getattr(self, "_network_manager", None)
        proxy_url = proxy.get_proxy() if proxy else None
        
        import time
        start = time.time()
        try:
            async with self._session.get(url, proxy=proxy_url) as resp:
                if proxy and proxy_url:
                    proxy.report_result(proxy_url, resp.status == 200, resp.status, time.time() - start)
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            if proxy and proxy_url:
                proxy.report_result(proxy_url, False, 500, time.time() - start)
        
        # Fallback to on-chain metadata
        logger.info(f"Frontend API blocked/failed for {mint}. Falling back to on-chain metadata.")
        return await self.get_token_metadata_onchain(mint)

    async def get_bonding_curve_mcap(self, mint: str, sol_price: float) -> float:
        """Fetch the token's market cap in USD directly from the Solana RPC by querying the bonding curve account."""
        try:
            import base64
            import struct
            mint_pubkey = Pubkey.from_string(mint)
            bonding_curve, _ = Pubkey.find_program_address(
                [b"bonding-curve", bytes(mint_pubkey)],
                Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")
            )
            
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [
                    str(bonding_curve),
                    {"encoding": "base64"}
                ]
            }
            url = await self._get_rpc_url()
            start = time.perf_counter()
            async with self._session.post(url, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    latency = (time.perf_counter() - start) * 1000
                    await self._report_rpc_metric(url, True, latency, status_code=resp.status)
                    
                    value = data.get("result", {}).get("value")
                    if value and value.get("data"):
                        data_b64 = value["data"][0]
                        data_bytes = base64.b64decode(data_b64)
                        
                        # Read virtualTokenReserves and virtualSolReserves
                        # Offset 8: virtualTokenReserves (u64, 8 bytes)
                        # Offset 16: virtualSolReserves (u64, 8 bytes)
                        virtual_token_reserves = struct.unpack("<Q", data_bytes[8:16])[0]
                        virtual_sol_reserves = struct.unpack("<Q", data_bytes[16:24])[0]
                        
                        if virtual_token_reserves > 0:
                            market_cap_sol = (virtual_sol_reserves * 1_000_000) / virtual_token_reserves
                            return market_cap_sol * sol_price
        except Exception as e:
            logger.error(f"Error fetching bonding curve mcap for {mint}: {e}")
        return 0.0


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
            url = await self._get_rpc_url()
            start = time.perf_counter()
            try:
                async with self._session.post(url, json=payload) as resp:
                    data = await resp.json()
                    latency = (time.perf_counter() - start) * 1000
                    await self._report_rpc_metric(url, True, latency, status_code=resp.status)
                    accounts = data.get("result", {}).get("value", [])
                    if accounts:
                        amount_info = accounts[0]["account"]["data"]["parsed"]["info"]["tokenAmount"]
                        return float(amount_info["uiAmount"] or 0)
            except:
                await self._report_rpc_metric(url, False, status_code=500)
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
        denominated_in_sol: bool = True,
        jito_tip: Optional[float] = None
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

            rpc_url = await self._get_rpc_url()

            if use_jito:
                recent_blockhash = None
                try:
                    payload_hash = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getLatestBlockhash",
                        "params": [{"commitment": "confirmed"}]
                    }
                    async with self._session.post(rpc_url, json=payload_hash) as hb_resp:
                        if hb_resp.status == 200:
                            hb_data = await hb_resp.json()
                            recent_blockhash = hb_data.get("result", {}).get("value", {}).get("blockhash")
                except Exception as e:
                    logger.error(f"Failed to fetch blockhash for Jito tip: {e}")

                if not recent_blockhash:
                    return TradeResult(success=False, token_mint=mint, error="Failed to fetch recent blockhash for Jito tip")

                # Dynamically set tip size based on buy size or congestion override
                if jito_tip is not None:
                    tip_sol = jito_tip
                else:
                    tip_sol = 0.001
                    if amount >= 0.02:
                        tip_sol = 0.002
                    elif amount <= 0.001:
                        tip_sol = 0.0005

                tip_account = Pubkey.from_string("ADaUMid9yfUytqMBB6f7JSt39zG9u4L9J6vCjW2H96Mh")
                tip_lamports = int(tip_sol * 1e9)

                tip_inst = transfer(TransferParams(
                    from_pubkey=self._wallet.keypair.pubkey(),
                    to_pubkey=tip_account,
                    lamports=tip_lamports
                ))
                tip_msg = Message.new_with_blockhash(
                    [tip_inst],
                    self._wallet.keypair.pubkey(),
                    Hash.from_string(recent_blockhash)
                )
                signed_tip_tx = VersionedTransaction(tip_msg, [self._wallet.keypair])

                bundle_id = await self._jito.send_bundle([signed_tx, signed_tip_tx])
                latency = (time.perf_counter() - start_time) * 1000
                if bundle_id:
                    return TradeResult(success=True, token_mint=mint, tx_signature=bundle_id, latency_ms=latency)
                else:
                    logger.warning("Jito bundle submission failed. Falling back to direct RPC transaction broadcast.")
            
            # Direct sendTransaction path (either as main path or fallback)
            rpc_payload = {
                "jsonrpc": "2.0", "id": 1, "method": "sendTransaction",
                "params": [base58.b58encode(bytes(signed_tx)).decode("utf-8"), {"skipPreflight": True}]
            }
            start_broadcast = time.perf_counter()
            async with self._session.post(rpc_url, json=rpc_payload) as r_resp:
                r_data = await r_resp.json()
                latency_b = (time.perf_counter() - start_broadcast) * 1000
                await self._report_rpc_metric(rpc_url, True, latency_b, status_code=r_resp.status)
                latency = (time.perf_counter() - start_time) * 1000
                tx_sig = r_data.get("result")
                if tx_sig:
                    return TradeResult(success=True, token_mint=mint, tx_signature=tx_sig, latency_ms=latency)
                else:
                    error_msg = r_data.get("error", {}).get("message", "Unknown RPC error")
                    return TradeResult(success=False, token_mint=mint, error=f"RPC Send Failed: {error_msg}", latency_ms=latency)

        except Exception as e:
            logger.error(f"Execution failed for {mint}: {e}")
            return TradeResult(success=False, token_mint=mint, error=str(e))

