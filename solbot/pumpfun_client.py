"""PumpPortal Local Transaction Signing Client with Jito Support."""

import os
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
from solbot.jito_tip_estimator import JitoTipEstimator

logger = logging.getLogger("bot.pumpfun_client")

from solbot.mayhem import TOKEN_2022_PROGRAM, metadata_indicates_mayhem, ws_payload_indicates_mayhem

SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_PROGRAMS = (SPL_TOKEN_PROGRAM, TOKEN_2022_PROGRAM)

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
        self._observability = None
        self._tip_estimator = JitoTipEstimator()

        # --- paper trading ledger -------------------------------------------
        # When DRY_RUN is set the client answers balance queries and trade
        # submissions from this ledger instead of the chain, so the strategy,
        # position manager, TP ladder and moonbag logic all run untouched
        # against live prices without signing anything.
        self._paper_enabled = bool(getattr(config.strategy, "dry_run", False) or os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes", "on"))
        self._paper_sol = float(getattr(config.strategy, "dry_run_start_sol", 5.0) or os.getenv("DRY_RUN_START_SOL", "5.0"))
        self._paper_tokens: Dict[str, float] = {}
        self._paper_basis: Dict[str, float] = {}
        self._paper_marks: Dict[str, float] = {}
        self._paper_fills = 0
        if self._paper_enabled:
            logger.warning(
                "DRY_RUN active — no transaction will be signed or broadcast. "
                "Paper wallet starts at %.4f SOL.", self._paper_sol,
            )

    # Token quantities are notional in paper mode: profit and loss is driven by
    # the marked ROI, not by a simulated token count.
    PAPER_TOKENS_PER_SOL = 1_000_000.0

    @property
    def paper_enabled(self) -> bool:
        return self._paper_enabled

    def set_paper_mark(self, mint: str, roi: float) -> None:
        """Record the live ROI so a paper sell credits realistic proceeds."""
        if self._paper_enabled and roi > 0:
            self._paper_marks[mint] = float(roi)

    def paper_summary(self) -> Dict[str, float]:
        held = sum(self._paper_basis.values())
        return {
            "sol": self._paper_sol,
            "open_basis_sol": held,
            "equity_sol": self._paper_sol + held,
            "fills": self._paper_fills,
        }

    def _paper_trade(self, mint, action, amount, denominated_in_sol, start_time) -> TradeResult:
        latency = (time.perf_counter() - start_time) * 1000
        self._paper_fills += 1
        signature = f"DRYRUN-{action}-{mint[:8]}-{self._paper_fills}"

        if action == "buy":
            cost = float(amount)
            if cost > self._paper_sol:
                return TradeResult(
                    success=False, token_mint=mint,
                    error=f"Paper wallet short: need {cost:.4f} SOL, have {self._paper_sol:.4f}",
                    latency_ms=latency,
                )
            qty = cost * self.PAPER_TOKENS_PER_SOL
            self._paper_sol -= cost
            self._paper_tokens[mint] = self._paper_tokens.get(mint, 0.0) + qty
            self._paper_basis[mint] = self._paper_basis.get(mint, 0.0) + cost
            logger.info(
                "[PAPER_SIM] BUY %s for %.4f SOL | paper wallet %.4f SOL",
                mint[:8], cost, self._paper_sol,
            )
            return TradeResult(
                success=True, token_mint=mint, tx_signature=signature,
                amount_in=cost, amount_out=qty, latency_ms=latency,
            )

        held = self._paper_tokens.get(mint, 0.0)
        if held <= 0:
            return TradeResult(
                success=False, token_mint=mint, error="Paper position already closed",
                latency_ms=latency,
            )
        qty = min(float(amount), held) if not denominated_in_sol else held
        fraction = qty / held if held else 0.0
        basis_portion = self._paper_basis.get(mint, 0.0) * fraction
        roi = self._paper_marks.get(mint, 1.0)
        proceeds = basis_portion * roi

        self._paper_sol += proceeds
        self._paper_tokens[mint] = held - qty
        self._paper_basis[mint] = self._paper_basis.get(mint, 0.0) - basis_portion
        if self._paper_tokens[mint] <= 0:
            self._paper_tokens.pop(mint, None)
            self._paper_basis.pop(mint, None)
            self._paper_marks.pop(mint, None)
        logger.info(
            "[PAPER_SIM] SELL %s %.1f%% at %.2fx | +%.4f SOL | paper wallet %.4f SOL",
            mint[:8], fraction * 100, roi, proceeds, self._paper_sol,
        )
        return TradeResult(
            success=True, token_mint=mint, tx_signature=signature,
            amount_in=qty, amount_out=proceeds, latency_ms=latency,
        )

    def _rpc_success(self, data: dict, http_status: int) -> bool:
        return http_status == 200 and "error" not in data

    async def start(self):
        if not self._session:
            timeout = aiohttp.ClientTimeout(total=10)
            self._session = aiohttp.ClientSession(timeout=timeout)
        # JitoClient only takes config
        self._jito = JitoClient(self._bot_config)
        await self._tip_estimator.start()

    async def stop(self):
        if self._session:
            await self._session.close()
        await self._tip_estimator.stop()

    async def _get_rpc_url(self) -> str:
        if hasattr(self, '_rpc_pool') and self._rpc_pool:
            return await self._rpc_pool.get_best_node()
        return self._solana_config.rpc_url

    async def _rpc_urls_for_retry(self) -> List[str]:
        if hasattr(self, "_rpc_pool") and self._rpc_pool and hasattr(self._rpc_pool, "get_retry_urls"):
            return await self._rpc_pool.get_retry_urls()
        return [await self._get_rpc_url()]

    def _is_rate_limited(self, data: dict, http_status: int) -> bool:
        if http_status == 429:
            return True
        err = data.get("error") or {}
        msg = str(err.get("message", "")).lower()
        return "rate limit" in msg or "too many" in msg

    async def _rpc_post(
        self,
        payload: dict,
        method: str = "rpc",
        max_attempts: int = 3,
    ) -> tuple[Optional[dict], int, Optional[str]]:
        last_error = None
        urls = await self._rpc_urls_for_retry()
        for attempt in range(max_attempts):
            url = urls[attempt % len(urls)]
            start = time.perf_counter()
            try:
                async with self._session.post(url, json=payload) as resp:
                    data = await resp.json()
                    latency = (time.perf_counter() - start) * 1000
                    ok = self._rpc_success(data, resp.status)
                    await self._report_rpc_metric(
                        url, ok, latency, status_code=resp.status, method=method,
                    )
                    if ok:
                        return data, resp.status, url
                    if self._is_rate_limited(data, resp.status):
                        last_error = data.get("error", {}).get("message", "rate limited")
                        if hasattr(self, "_rpc_pool") and self._rpc_pool:
                            await self._rpc_pool.report_metrics(
                                url, False, latency, status_code=429,
                            )
                        await asyncio.sleep(0.3 * (attempt + 1))
                        continue
                    return data, resp.status, url
            except Exception as exc:
                last_error = str(exc)
                await self._report_rpc_metric(url, False, status_code=500, method=method)
                await asyncio.sleep(0.2 * (attempt + 1))
        logger.warning("RPC %s failed after %s attempts: %s", method, max_attempts, last_error)
        return None, 500, None

    async def _report_rpc_metric(self, url: str, success: bool, latency: float = 0.0, slot: int = 0, status_code: Optional[int] = None, method: str = "rpc"):
        if hasattr(self, "_rpc_pool") and self._rpc_pool:
            await self._rpc_pool.report_metrics(url, success, latency, slot, status_code)
        if getattr(self, "_observability", None):
            self._observability.record_rpc(url, method, latency, success, status_code)

    async def get_sol_balance(self) -> float:
        """Fetch the current SOL balance for the wallet."""
        if self._paper_enabled:
            return self._paper_sol
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [self._wallet.pubkey_str]
        }
        data, _, _ = await self._rpc_post(payload, method="getBalance")
        if not data:
            return 0.0
        lamports = data.get("result", {}).get("value", 0)
        return lamports / 1_000_000_000

    async def get_all_token_balances(self) -> Dict[str, Dict]:
        """Fetch all SPL and Token-2022 balances with metadata."""
        if self._paper_enabled:
            # Without this the startup reconcile would treat every paper bag as a
            # ghost with no on-chain balance and purge it immediately.
            return {
                mint: {"balance": qty, "program": TOKEN_PROGRAMS[0]}
                for mint, qty in self._paper_tokens.items() if qty > 0
            }
        programs = list(TOKEN_PROGRAMS)
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
            data, _, _ = await self._rpc_post(payload, method="getTokenAccountsByOwner")
            if not data:
                continue
            accounts = data.get("result", {}).get("value", [])
            for acc in accounts:
                info = acc["account"]["data"]["parsed"]["info"]
                mint = info["mint"]
                amount = float(info["tokenAmount"]["uiAmount"] or 0)
                if amount > 0:
                    balances[mint] = {
                        "balance": amount,
                        "program": "Token-2022" if program_id == TOKEN_2022_PROGRAM else "SPL",
                    }
        
        return balances

    async def token_2022_block_reason(self, mint: str) -> Optional[str]:
        """Why a Token-2022 mint cannot be sold, or None if it is fine.

        pump.fun issues ordinary new mints under Token-2022 (they carry only
        metadataPointer/tokenMetadata), so the program ID alone says nothing about
        sellability. Only specific extensions actually block a transfer. When the
        lookup fails we return None: we already hold the bag, and attempting a sell
        is strictly better than abandoning it on an unverified suspicion.
        """
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAccountInfo",
            "params": [mint, {"encoding": "jsonParsed"}],
        }
        data, _, _ = await self._rpc_post(payload, method="getAccountInfo")
        if not data:
            return None
        value = (data.get("result", {}) or {}).get("value") or {}
        if value.get("owner") != TOKEN_2022_PROGRAM:
            return None
        info = ((value.get("data") or {}).get("parsed") or {}).get("info") or {}

        if info.get("freezeAuthority"):
            return "freeze authority is set"

        for ext in info.get("extensions") or []:
            name = ext.get("extension")
            state = ext.get("state") or {}
            if name == "nonTransferable":
                return "mint is non-transferable"
            if name == "transferHook" and state.get("programId"):
                return f"transfer hook {str(state.get('programId'))[:8]}"
            if name == "permanentDelegate" and state.get("delegate"):
                return "permanent delegate can seize the balance"
            if name == "defaultAccountState" and str(state.get("accountState", "")).lower() == "frozen":
                return "token accounts default to frozen"
            if name == "transferFeeConfig":
                bps = 0
                for key in ("newerTransferFee", "olderTransferFee"):
                    fee = state.get(key) or {}
                    try:
                        bps = max(bps, int(fee.get("transferFeeBasisPoints", 0) or 0))
                    except (TypeError, ValueError):
                        continue
                if bps >= 5000:
                    return f"confiscatory transfer fee ({bps} bps)"
        return None

    async def _mint_uses_token_2022(self, mint: str) -> bool:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAccountInfo",
            "params": [mint, {"encoding": "jsonParsed"}],
        }
        data, _, _ = await self._rpc_post(payload, method="getAccountInfo")
        if not data:
            return False
        owner = (data.get("result", {}).get("value") or {}).get("owner")
        return owner == TOKEN_2022_PROGRAM

    async def is_mayhem_token(self, mint: str, hint: Optional[Dict] = None) -> bool:
        """Return True if token is Mayhem Mode (unsellable on pump.fun)."""
        if hint and ws_payload_indicates_mayhem(hint):
            return True
        meta = await self.get_token_metadata(mint)
        if metadata_indicates_mayhem(meta):
            return True
        reason = await self.token_2022_block_reason(mint)
        if reason:
            logger.warning("Token %s is unsellable: %s", mint[:8], reason)
            return True
        return False

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
                "method": "getMultipleAccounts",
                "params": [
                    [mint, str(metadata_pda)],
                    {"encoding": "base64"}
                ]
            }

            url = await self._get_rpc_url()
            start = time.perf_counter()
            
            symbol = "???"
            name = "Unknown"
            # Not "is this Token-2022" — pump.fun's ordinary mints are. Only a
            # blocking extension makes a token genuinely unsellable.
            mayhem_mode = bool(await self.token_2022_block_reason(mint))

            async with self._session.post(url, json=payload) as resp:
                latency = (time.perf_counter() - start) * 1000
                await self._report_rpc_metric(url, True, latency, status_code=resp.status)
                if resp.status == 200:
                    res_data = await resp.json()
                    value = res_data.get("result", {}).get("value", [])

                    # Check metadata PDA for symbol/name
                    if len(value) > 1 and value[1] and value[1].get("data"):
                        b64_data = value[1]["data"][0]
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
                "liquidity_sol": 0,
                "mayhem_mode": mayhem_mode,
            }
        except Exception as e:
            logger.error(f"Error fetching on-chain metadata for {mint}: {e}")
        return {"symbol": "???", "name": "Unknown", "creator": "unknown", "market_cap_sol": 0, "liquidity_sol": 0, "mayhem_mode": False}

    async def get_token_metadata(self, mint: str) -> Dict:
        """Fetch basic token metadata (symbol)."""
        url = f"https://frontend-api-v3.pump.fun/coins/{mint}"
        
        proxy = getattr(self, "_network_manager", None)
        proxy_url = proxy.get_proxy() if proxy else None
        
        import time
        start = time.time()
        for attempt, use_proxy in enumerate((True, False)):
            current_proxy = proxy_url if use_proxy and attempt == 0 else None
            try:
                async with self._session.get(url, proxy=current_proxy, timeout=8) as resp:
                    if proxy and current_proxy:
                        proxy.report_result(
                            current_proxy, resp.status == 200, resp.status, time.time() - start,
                        )
                    if resp.status == 200:
                        data = await resp.json()
                        if metadata_indicates_mayhem(data):
                            data["mayhem_mode"] = True
                        return data
                    if resp.status in (402, 407) and attempt == 0:
                        logger.debug("Pump.fun API returned %s via proxy; retrying direct.", resp.status)
                        continue
            except Exception:
                if proxy and current_proxy:
                    proxy.report_result(current_proxy, False, 500, time.time() - start)
                if attempt == 0:
                    continue
        
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
        if self._paper_enabled:
            return self._paper_tokens.get(mint, 0.0)
        for program_id in TOKEN_PROGRAMS:
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
            data, _, _ = await self._rpc_post(payload, method="getTokenAccountsByOwner")
            if not data:
                continue
            accounts = data.get("result", {}).get("value", [])
            if accounts:
                amount_info = accounts[0]["account"]["data"]["parsed"]["info"]["tokenAmount"]
                return float(amount_info["uiAmount"] or 0)
        return 0.0

    async def get_recent_prioritization_fee(self, mint: str) -> float:
        """Queries getRecentPrioritizationFees and returns the 75th percentile fee in SOL."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getRecentPrioritizationFees",
            "params": [[mint]]
        }
        url = await self._get_rpc_url()
        try:
            async with self._session.post(url, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    fees = data.get("result", [])
                    if fees:
                        fee_vals = sorted([float(f.get("prioritizationFee", 0)) for f in fees])
                        idx = int(len(fee_vals) * 0.75)
                        micro_lamports = fee_vals[idx] if idx < len(fee_vals) else fee_vals[-1]
                        
                        # Convert to SOL (assume 200k compute units limit standard)
                        lamports = (micro_lamports * 200000) / 1000000.0
                        fee_sol = lamports / 1e9
                        # Safety limits: min 0.00001 SOL, max 0.001 SOL
                        return max(0.00001, min(0.001, fee_sol))
        except Exception as e:
            logger.error(f"Error fetching prioritization fees: {e}")
        return 0.00001

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
        if slippage is None:
            slippage = 1000 if action == "sell" else getattr(self._jupiter_config, "slippage_bps", 300)
        # Config carries basis points; PumpPortal's `slippage` field is a percent.
        # Sending 1000 bps becomes 10.0% slippage.
        slippage_pct = slippage / 100.0

        if self._paper_enabled:
            return self._paper_trade(mint, action, amount, denominated_in_sol, start_time)

        # 1. Dynamic prioritization fee estimation
        if priority_fee is None:
            priority_fee = await self.get_recent_prioritization_fee(mint)

        payload = {
            "publicKey": self._wallet.pubkey_str,
            "action": action,
            "mint": mint,
            "denominatedInSol": "true" if denominated_in_sol else "false",
            "amount": amount,
            "slippage": slippage_pct,
            "priorityFee": priority_fee,
            "pool": "auto"
        }

        try:
            async with self._session.post(self._base_url, json=payload) as resp:
                if resp.status != 200:
                    return TradeResult(success=False, token_mint=mint, error=f"API Error: {resp.status}")
                tx_data = await resp.read()
                
            tx = VersionedTransaction.from_bytes(tx_data)
            signed_tx = VersionedTransaction(tx.message, [self._wallet.keypair])

            # Pre-flight simulation check to prevent fee waste on reverting transactions
            if not self._paper_enabled:
                try:
                    sim_payload = {
                        "jsonrpc": "2.0", "id": 1, "method": "simulateTransaction",
                        "params": [base58.b58encode(bytes(signed_tx)).decode("utf-8"), {"sigVerify": False, "commitment": "processed"}]
                    }
                    sim_data, _, _ = await self._rpc_post(sim_payload, method="simulateTransaction", max_attempts=2)
                    if sim_data and "result" in sim_data:
                        sim_err = sim_data["result"].get("value", {}).get("err")
                        if sim_err:
                            logger.warning("Pre-flight transaction simulation failed for %s: %s (Trade Aborted - Gas Preserved)", mint, sim_err)
                            return TradeResult(success=False, token_mint=mint, error=f"Pre-flight Sim Failed: {sim_err}", latency_ms=(time.perf_counter() - start_time) * 1000)
                except Exception as e:
                    logger.debug(f"Pre-flight simulation check skipped: {e}")

            if use_jito:
                recent_blockhash = None
                try:
                    payload_hash = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getLatestBlockhash",
                        "params": [{"commitment": "confirmed"}]
                    }
                    rpc_url = await self._get_rpc_url()
                    async with self._session.post(rpc_url, json=payload_hash) as hb_resp:
                        if hb_resp.status == 200:
                            hb_data = await hb_resp.json()
                            recent_blockhash = hb_data.get("result", {}).get("value", {}).get("blockhash")
                except Exception as e:
                    logger.error(f"Failed to fetch blockhash for Jito tip: {e}")

                if not recent_blockhash:
                    return TradeResult(success=False, token_mint=mint, error="Failed to fetch recent blockhash for Jito tip")

                # 2. Dynamic Jito tip estimation
                if jito_tip is not None:
                    tip_sol = jito_tip
                else:
                    priority_level = "medium"
                    if action == "sell" or amount >= 0.1:
                        priority_level = "high"
                    tip_sol = self._tip_estimator.get_tip(priority_level)

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

                bundle_id = await self._jito.send_bundle([signed_tx, signed_tip_tx], session=self._session)
                latency = (time.perf_counter() - start_time) * 1000
                if bundle_id:
                    tx_sig = await self._jito.confirm_bundle(bundle_id, self._session)
                    if tx_sig:
                        return TradeResult(success=True, token_mint=mint, tx_signature=tx_sig, latency_ms=latency)
                    logger.warning("Jito bundle %s submitted but not confirmed in time; falling back to RPC.", bundle_id)
                else:
                    logger.warning("Jito bundle submission failed. Falling back to direct RPC transaction broadcast.")
            
            # Direct sendTransaction path (either as main path or fallback)
            rpc_payload = {
                "jsonrpc": "2.0", "id": 1, "method": "sendTransaction",
                "params": [base58.b58encode(bytes(signed_tx)).decode("utf-8"), {"skipPreflight": True}]
            }
            start_broadcast = time.perf_counter()
            r_data, r_status, rpc_url = await self._rpc_post(
                rpc_payload, method="sendTransaction", max_attempts=4,
            )
            latency_b = (time.perf_counter() - start_broadcast) * 1000
            latency = (time.perf_counter() - start_time) * 1000
            if not r_data:
                return TradeResult(
                    success=False, token_mint=mint,
                    error="RPC Send Failed: rate limits exceeded on all endpoints",
                    latency_ms=latency,
                )
            ok = self._rpc_success(r_data, r_status or 500)
            tx_sig = r_data.get("result")
            if ok and tx_sig:
                return TradeResult(success=True, token_mint=mint, tx_signature=tx_sig, latency_ms=latency)
            error_msg = r_data.get("error", {}).get("message", "Unknown RPC error")
            return TradeResult(success=False, token_mint=mint, error=f"RPC Send Failed: {error_msg}", latency_ms=latency)

        except Exception as e:
            logger.error(f"Execution failed for {mint}: {e}")
            return TradeResult(success=False, token_mint=mint, error=str(e))

    async def reclaim_empty_token_accounts(self) -> float:
        """Finds all 0-balance token accounts and closes them to refund rent (0.00204 SOL each) to wallet."""
        if self._paper_enabled:
            return 0.0
        try:
            from spl.token.instructions import close_account, CloseAccountParams
            from spl.token.constants import TOKEN_PROGRAM_ID
            from solders.instruction import Instruction
            from solders.message import Message
            from solders.hash import Hash
            from solders.transaction import VersionedTransaction

            programs = list(TOKEN_PROGRAMS)
            reclaimed_sol = 0.0
            instructions = []
            
            for program_id in programs:
                prog_pk = Pubkey.from_string(program_id)
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
                data, _, _ = await self._rpc_post(payload, method="getTokenAccountsByOwner")
                if not data:
                    continue
                accounts = data.get("result", {}).get("value", [])
                for acc in accounts:
                    info = acc["account"]["data"]["parsed"]["info"]
                    amount = float(info["tokenAmount"]["uiAmount"] or 0)
                    acc_pubkey = Pubkey.from_string(acc["pubkey"])
                    if amount == 0:
                        ix = close_account(CloseAccountParams(
                            program_id=prog_pk,
                            account=acc_pubkey,
                            dest=self._wallet.keypair.pubkey(),
                            owner=self._wallet.keypair.pubkey()
                        ))
                        instructions.append(ix)
                        reclaimed_sol += 0.002039

            if not instructions:
                return 0.0

            rpc_url = await self._get_rpc_url()
            for chunk in [instructions[i:i+10] for i in range(0, len(instructions), 10)]:
                payload_hash = {
                    "jsonrpc": "2.0", "id": 1, "method": "getLatestBlockhash",
                    "params": [{"commitment": "confirmed"}]
                }
                async with self._session.post(rpc_url, json=payload_hash) as hb_resp:
                    if hb_resp.status == 200:
                        hb_data = await hb_resp.json()
                        recent_blockhash = hb_data.get("result", {}).get("value", {}).get("blockhash")
                        if recent_blockhash:
                            msg = Message.new_with_blockhash(chunk, self._wallet.keypair.pubkey(), Hash.from_string(recent_blockhash))
                            tx = VersionedTransaction(msg, [self._wallet.keypair])
                            rpc_payload = {
                                "jsonrpc": "2.0", "id": 1, "method": "sendTransaction",
                                "params": [base58.b58encode(bytes(tx)).decode("utf-8"), {"skipPreflight": True}]
                            }
                            await self._rpc_post(rpc_payload, method="sendTransaction")
            logger.info(f"Reclaimed ~{reclaimed_sol:.4f} SOL from {len(instructions)} empty token accounts!")
            return reclaimed_sol
        except Exception as e:
            logger.error(f"Error reclaiming empty token accounts: {e}")
            return 0.0

