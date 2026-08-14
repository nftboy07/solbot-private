"""Cross-DEX arbitrage scanner and guarded Jito bundle executor."""

import asyncio
import base64
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import permutations
from typing import Optional

import aiohttp

from solbot.config import ArbitrageConfig
from solbot.jito import JitoClient

logger = logging.getLogger("bot.arbitrage")

SOL_MINT = "So11111111111111111111111111111111111111112"


@dataclass(frozen=True)
class RouteQuote:
    dex: str
    input_mint: str
    output_mint: str
    in_amount: int
    out_amount: int
    quote: dict


@dataclass(frozen=True)
class ArbitrageOpportunity:
    mint: str
    buy_dex: str
    sell_dex: str
    input_sol: float
    output_sol: float
    estimated_fees_sol: float
    jito_tip_sol: float
    net_profit_sol: float
    buy_quote: dict
    sell_quote: dict

    @property
    def profitable(self) -> bool:
        return self.net_profit_sol > 0


@dataclass(frozen=True)
class BundleExecutionResult:
    sent: bool
    bundle_id: Optional[str]
    reason: str


class DEXArbitrageEngine:
    """Scans Jupiter route-restricted quotes for two-leg SOL arbitrage."""

    def __init__(self, bot, config: ArbitrageConfig | None = None):
        self._bot = bot
        self._config = config or bot._config.arbitrage
        self._session: Optional[aiohttp.ClientSession] = None
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._jito = JitoClient(bot._config)

    async def start(self):
        if not self._config.enabled:
            logger.info("Cross-DEX arbitrage engine disabled.")
            return
        if self._running:
            return
        timeout = aiohttp.ClientTimeout(total=self._config.quote_timeout_seconds)
        self._session = aiohttp.ClientSession(timeout=timeout)
        self._running = True
        self._task = asyncio.create_task(self._scan_loop(), name="dex-arbitrage-scan")
        mode = "DRY-RUN" if self._config.dry_run else "LIVE"
        logger.info("Cross-DEX arbitrage engine started in %s mode.", mode)

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._session:
            await self._session.close()
        logger.info("Cross-DEX arbitrage engine stopped.")

    async def _scan_loop(self):
        while self._running:
            try:
                opportunities = await self.scan_once()
                for opportunity in opportunities:
                    await self._handle_opportunity(opportunity)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Arbitrage scan failed: %s", exc)
            await asyncio.sleep(self._config.scan_interval_seconds)

    async def scan_once(self, mints: Optional[list[str]] = None) -> list[ArbitrageOpportunity]:
        mints = mints or self._candidate_mints()
        if not mints:
            logger.debug("Arbitrage scan skipped: no candidate mints configured.")
            return []

        input_lamports = int(self._config.input_sol * 1_000_000_000)
        opportunities: list[ArbitrageOpportunity] = []
        for mint in mints:
            for buy_dex, sell_dex in permutations(self._config.route_dexes, 2):
                opportunity = await self._get_two_leg_opportunity(
                    mint=mint,
                    buy_dex=buy_dex,
                    sell_dex=sell_dex,
                    input_lamports=input_lamports,
                )
                if opportunity and opportunity.net_profit_sol >= self._config.min_profit_sol:
                    opportunities.append(opportunity)

        opportunities.sort(key=lambda item: item.net_profit_sol, reverse=True)
        return opportunities

    async def _get_two_leg_opportunity(
        self,
        mint: str,
        buy_dex: str,
        sell_dex: str,
        input_lamports: int,
    ) -> Optional[ArbitrageOpportunity]:
        buy_quote = await self._get_route_quote(SOL_MINT, mint, input_lamports, buy_dex)
        if not buy_quote:
            return None

        token_amount = int(buy_quote.quote.get("outAmount", 0))
        if token_amount <= 0:
            return None

        sell_quote = await self._get_route_quote(mint, SOL_MINT, token_amount, sell_dex)
        if not sell_quote:
            return None

        output_lamports = int(sell_quote.quote.get("outAmount", 0))
        output_sol = output_lamports / 1_000_000_000
        net_profit = calculate_net_profit_sol(
            input_sol=self._config.input_sol,
            output_sol=output_sol,
            estimated_fees_sol=self._config.estimated_fees_sol,
            jito_tip_sol=self._config.jito_tip_sol,
        )
        return ArbitrageOpportunity(
            mint=mint,
            buy_dex=buy_dex,
            sell_dex=sell_dex,
            input_sol=self._config.input_sol,
            output_sol=output_sol,
            estimated_fees_sol=self._config.estimated_fees_sol,
            jito_tip_sol=self._config.jito_tip_sol,
            net_profit_sol=net_profit,
            buy_quote=buy_quote.quote,
            sell_quote=sell_quote.quote,
        )

    async def _get_route_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        dex: str,
    ) -> Optional[RouteQuote]:
        if not self._session:
            return None
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount),
            "slippageBps": str(self._config.slippage_bps),
            "onlyDirectRoutes": "true",
            "dexes": dex,
        }
        try:
            base_url = self._bot._config.jupiter.api_url.rstrip("/")
            async with self._session.get(f"{base_url}/quote", params=params) as resp:
                if resp.status != 200:
                    logger.debug("Quote miss for %s route %s -> %s: %s", dex, input_mint, output_mint, resp.status)
                    # Attempt Hummingbot Gateway quote fallback if enabled
                    if (
                        hasattr(self._bot, "_hummingbot_gateway")
                        and self._bot._hummingbot_gateway
                        and getattr(self._bot._config, "hummingbot", None)
                        and self._bot._config.hummingbot.enabled
                    ):
                        gw_connector = dex.lower().replace(".", "").replace(" ", "")
                        gw_quote = await self._bot._hummingbot_gateway.get_quote(
                            connector=gw_connector,
                            base_token=output_mint if input_mint == SOL_MINT else input_mint,
                            quote_token="SOL",
                            amount=amount / 1e9 if input_mint == SOL_MINT else float(amount),
                            side="BUY" if input_mint == SOL_MINT else "SELL",
                        )
                        if gw_quote and "expectedOutput" in gw_quote:
                            out_amt = int(float(gw_quote["expectedOutput"]) * (1e9 if output_mint == SOL_MINT else 1))
                            if out_amt > 0:
                                return RouteQuote(
                                    dex=dex,
                                    input_mint=input_mint,
                                    output_mint=output_mint,
                                    in_amount=amount,
                                    out_amount=out_amt,
                                    quote=gw_quote,
                                )
                    return None
                quote = await resp.json()
        except Exception as exc:
            logger.debug("Quote failed for %s route %s -> %s: %s", dex, input_mint, output_mint, exc)
            return None

        out_amount = int(quote.get("outAmount", 0) or 0)
        if out_amount <= 0:
            return None
        return RouteQuote(
            dex=dex,
            input_mint=input_mint,
            output_mint=output_mint,
            in_amount=amount,
            out_amount=out_amount,
            quote=quote,
        )

    async def _handle_opportunity(self, opportunity: ArbitrageOpportunity):
        line = (
            f"{opportunity.mint} {opportunity.buy_dex}->{opportunity.sell_dex} "
            f"in={opportunity.input_sol:.4f} SOL out={opportunity.output_sol:.4f} SOL "
            f"net={opportunity.net_profit_sol:.4f} SOL"
        )
        logger.warning("ARBITRAGE OPPORTUNITY %s", line)
        await self._append_arbitrage_log(line)

        if self._config.dry_run:
            return

        result = await self.execute_opportunity(opportunity)
        if result.sent:
            logger.warning("Arbitrage bundle sent: %s", result.bundle_id)
        else:
            logger.warning("Arbitrage bundle not sent: %s", result.reason)

    async def execute_opportunity(self, opportunity: ArbitrageOpportunity) -> BundleExecutionResult:
        if opportunity.net_profit_sol < self._config.min_profit_sol:
            return BundleExecutionResult(False, None, "Net profit below configured threshold.")
        if self._config.dry_run:
            return BundleExecutionResult(False, None, "Dry-run mode is enabled.")

        buy_tx = await self._build_signed_swap_transaction(opportunity.buy_quote)
        sell_tx = await self._build_signed_swap_transaction(opportunity.sell_quote)
        tip_tx = await self._build_tip_transaction()
        if not buy_tx or not sell_tx or not tip_tx:
            return BundleExecutionResult(False, None, "Failed to build all bundle transactions.")

        bundle_id = await self._jito.send_bundle(self._bundle_transactions(buy_tx, sell_tx, tip_tx))
        if not bundle_id:
            return BundleExecutionResult(False, None, "Jito did not return a bundle id.")
        return BundleExecutionResult(True, bundle_id, "Bundle submitted.")

    async def _build_signed_swap_transaction(self, quote: dict):
        if not self._session or not getattr(self._bot, "_wallet", None):
            return None
        payload = {
            "quoteResponse": quote,
            "userPublicKey": self._bot._wallet.pubkey_str,
            "wrapAndUnwrapSol": True,
            "prioritizationFeeLamports": int(self._config.estimated_fees_sol * 1_000_000_000),
        }
        base_url = self._bot._config.jupiter.api_url.rstrip("/")
        try:
            async with self._session.post(f"{base_url}/swap", json=payload) as resp:
                if resp.status != 200:
                    logger.warning("Jupiter swap build failed: %s", await resp.text())
                    return None
                swap_data = await resp.json()
        except Exception as exc:
            logger.warning("Jupiter swap build request failed: %s", exc)
            return None

        from solders.transaction import VersionedTransaction

        raw_tx = base64.b64decode(swap_data["swapTransaction"])
        tx = VersionedTransaction.from_bytes(raw_tx)
        return VersionedTransaction(tx.message, [self._bot._wallet.keypair])

    async def _build_tip_transaction(self):
        if not getattr(self._bot, "_pump_client", None) or not getattr(self._bot, "_wallet", None):
            return None
        try:
            from solders.hash import Hash
            from solders.message import Message
            from solders.pubkey import Pubkey
            from solders.system_program import TransferParams, transfer
            from solders.transaction import VersionedTransaction

            rpc_url = await self._bot._pump_client._get_rpc_url()
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getLatestBlockhash",
                "params": [{"commitment": "confirmed"}],
            }
            async with self._session.post(rpc_url, json=payload) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
            blockhash = data.get("result", {}).get("value", {}).get("blockhash")
            if not blockhash:
                return None

            tip_account = Pubkey.from_string("ADaUMid9yfUytqMBB6f7JSt39zG9u4L9J6vCjW2H96Mh")
            tip_inst = transfer(
                TransferParams(
                    from_pubkey=self._bot._wallet.keypair.pubkey(),
                    to_pubkey=tip_account,
                    lamports=int(self._config.jito_tip_sol * 1_000_000_000),
                )
            )
            message = Message.new_with_blockhash(
                [tip_inst],
                self._bot._wallet.keypair.pubkey(),
                Hash.from_string(blockhash),
            )
            return VersionedTransaction(message, [self._bot._wallet.keypair])
        except Exception as exc:
            logger.warning("Failed to build Jito tip transaction: %s", exc)
            return None

    @staticmethod
    def _bundle_transactions(buy_tx, sell_tx, tip_tx) -> list:
        return [buy_tx, sell_tx, tip_tx]

    def _candidate_mints(self) -> list[str]:
        mints = set(self._config.watch_mints)
        positions = getattr(self._bot, "_positions", {}) or {}
        mints.update(positions.keys())
        daily_runners = getattr(self._bot, "_daily_runners", {}) or {}
        mints.update(daily_runners.keys())
        return sorted(mints)

    async def _append_arbitrage_log(self, line: str):
        timestamp = datetime.now(timezone.utc).isoformat()
        message = f"{timestamp} {line}\n"
        await asyncio.to_thread(_append_line, self._config.log_file, message)


def calculate_net_profit_sol(
    input_sol: float,
    output_sol: float,
    estimated_fees_sol: float,
    jito_tip_sol: float,
) -> float:
    return output_sol - input_sol - estimated_fees_sol - jito_tip_sol


def _append_line(path: str, message: str):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(message)
