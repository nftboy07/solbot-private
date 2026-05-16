"""Main bot orchestrator - production-grade auto-buy/sell with full lifecycle.

Architecture:
    1. PumpFunMonitor (thread) -> asyncio.Queue -> token events
    2. Blacklist check (O(1) in-memory) -> reject blacklisted creators
    3. TokenFilter (basic filters) -> qualified tokens
    4. ScoringEngine (async) -> confidence classification
    5. TelegramAlert (async) -> notifications for qualified tokens
    6. PositionManager -> enforce max positions, cooldown, open position
    7. JupiterClient (async/aiohttp) -> swap execution (live or paper)
    8. PositionManager monitor loop -> stop loss, take profit, trailing stop
    9. Kill switch -> emergency halt on catastrophic loss
   10. CommandHandler -> Telegram command polling for remote management
"""

import asyncio
import logging
import signal
from time import time
from typing import Optional

from solbot.birdeye import BirdeyeClient
from solbot.blacklist import BlacklistReason, CreatorBlacklist
from solbot.commands import CommandHandler, log_capture
from solbot.config import BotConfig
from solbot.database import Database
from solbot.dexscreener import DexScreenerClient
from solbot.ipc_server import IPCServer
from solbot.filters import TokenFilter
from solbot.jupiter import JupiterClient
from solbot.logger import get_logger, setup_logger
from solbot.market_intel import MarketIntelConfig, MarketIntelEngine, MarketSignal
from solbot.models import PositionSnapshot, TokenEvent, TradeResult
from solbot.positions import PositionManager, SellReason, TradingConfig
from solbot.pumpfun import PumpFunMonitor
from solbot.scoring import Confidence, ScoringEngine, TokenScore
from solbot.smart_money import SmartMoneyEngine
from solbot.telegram import TelegramAlert
from solbot.wallet import Wallet

logger = get_logger("bot")


class _LogCaptureHandler(logging.Handler):
    """Logging handler that feeds into the command system's log buffer."""

    def emit(self, record):
        try:
            msg = self.format(record)
            log_capture.add(msg)
        except Exception:
            pass


class Solbot:
    """Main bot class: full production pipeline with auto-buy/sell.

    Pipeline:
        Token Event → Blacklist Check → Filter → Score → Buy Decision
        → Position Open → Monitor Loop → Auto-Sell → Position Close

    Kill Switch:
        Monitors cumulative losses and consecutive failures.
        Triggers emergency close-all and halts trading.
    """

    def __init__(self, config: BotConfig):
        self._config = config
        self._wallet: Optional[Wallet] = None
        self._db: Optional[Database] = None
        self._monitor: Optional[PumpFunMonitor] = None
        self._jupiter: Optional[JupiterClient] = None
        self._filter: Optional[TokenFilter] = None
        self._scorer: Optional[ScoringEngine] = None
        self._telegram: Optional[TelegramAlert] = None
        self._blacklist: Optional[CreatorBlacklist] = None
        self._positions: Optional[PositionManager] = None
        self._commands: Optional[CommandHandler] = None
        self._market_intel: Optional[MarketIntelEngine] = None
        self._dex_client: Optional[DexScreenerClient] = None
        self._birdeye_client: Optional[BirdeyeClient] = None
        self._ipc_server: Optional[IPCServer] = None
        self._smart_money: Optional[SmartMoneyEngine] = None
        self._running = False
        self._paused = False   # Pause state (stops new buys, monitoring continues)
        self._killed = False   # Kill switch state

        # Kill switch tracking
        self._total_realized_pnl_sol: float = 0.0
        self._consecutive_losses: int = 0

    async def start(self):
        """Initialize all components and start the bot."""
        # Setup logging
        setup_logger(self._config.logging)
        logger.info("=" * 60)
        logger.info("SOLBOT STARTING - Production Mode")
        logger.info("=" * 60)

        # Validate config
        errors = self._config.validate()
        if errors:
            for err in errors:
                logger.error(f"Config error: {err}")
            raise RuntimeError(f"Configuration invalid: {errors}")

        # Initialize wallet (optional in paper mode)
        if self._config.solana.private_key:
            self._wallet = Wallet(self._config.solana)
        elif self._config.jupiter.paper_trade:
            logger.info("Paper trading mode - wallet not required")
            self._wallet = None
        else:
            raise RuntimeError("WALLET_PRIVATE_KEY required for live trading")

        # Initialize database
        self._db = Database(self._config.trading.db_path)
        await self._db.initialize()

        # Initialize blacklist
        self._blacklist = CreatorBlacklist(
            db=self._db,
            auto_blacklist_enabled=self._config.trading.auto_blacklist_enabled,
        )
        await self._blacklist.initialize()

        # Initialize position manager
        trading_cfg = TradingConfig(
            stop_loss_pct=self._config.trading.stop_loss_pct,
            tp1_multiplier=self._config.trading.tp1_multiplier,
            tp1_sell_pct=self._config.trading.tp1_sell_pct,
            tp2_multiplier=self._config.trading.tp2_multiplier,
            tp2_sell_pct=self._config.trading.tp2_sell_pct,
            tp3_multiplier=self._config.trading.tp3_multiplier,
            tp3_sell_pct=self._config.trading.tp3_sell_pct,
            trailing_stop_pct=self._config.trading.trailing_stop_pct,
            trailing_stop_activation_pct=self._config.trading.trailing_stop_activation_pct,
            max_concurrent_positions=self._config.trading.max_concurrent_positions,
            buy_cooldown_seconds=self._config.trading.buy_cooldown_seconds,
            price_check_interval_seconds=self._config.trading.price_check_interval_seconds,
        )
        self._positions = PositionManager(config=trading_cfg, db=self._db)
        await self._positions.initialize()
        self._positions.set_sell_callback(self._execute_sell)

        # Initialize other components
        self._filter = TokenFilter(self._config.pumpfun)
        self._scorer = ScoringEngine(self._config.scoring)

        # Start Telegram alerts
        self._telegram = TelegramAlert(self._config.telegram)
        await self._telegram.start()

        # Start Jupiter client
        self._jupiter = JupiterClient(self._config.jupiter, self._wallet)
        await self._jupiter.start()

        # Initialize market intelligence engine
        self._dex_client = DexScreenerClient()
        await self._dex_client.start()
        self._birdeye_client = BirdeyeClient(
            api_key=self._config.market_intel.birdeye_api_key
        )
        await self._birdeye_client.start()

        mi_cfg = MarketIntelConfig(
            poll_interval_seconds=self._config.market_intel.dex_poll_interval_seconds,
            birdeye_poll_interval_seconds=self._config.market_intel.birdeye_poll_interval_seconds,
            liquidity_drain_warning_pct=self._config.market_intel.liquidity_drain_warning_pct,
            liquidity_drain_critical_pct=self._config.market_intel.liquidity_drain_critical_pct,
            volume_collapse_threshold=self._config.market_intel.volume_collapse_threshold_pct,
            sell_imbalance_warning=self._config.market_intel.sell_imbalance_warning,
            sell_imbalance_critical=self._config.market_intel.sell_imbalance_critical,
            mcap_spike_threshold_pct=self._config.market_intel.mcap_spike_threshold_pct,
            volume_surge_threshold_pct=self._config.market_intel.volume_surge_threshold_pct,
            holder_growth_surge_pct=self._config.market_intel.holder_growth_surge_pct,
            dynamic_trailing_enabled=self._config.market_intel.dynamic_trailing_enabled,
            volatility_trailing_multiplier=self._config.market_intel.volatility_trailing_multiplier,
            min_trailing_stop_pct=self._config.market_intel.min_trailing_stop_pct,
            max_trailing_stop_pct=self._config.market_intel.max_trailing_stop_pct,
        )
        self._market_intel = MarketIntelEngine(
            config=mi_cfg,
            dex_client=self._dex_client,
            birdeye_client=self._birdeye_client,
        )
        self._market_intel.add_signal_callback(self._handle_market_signal)
        await self._market_intel.start()

        # Start Pump.fun monitor
        loop = asyncio.get_running_loop()
        self._monitor = PumpFunMonitor(self._config.pumpfun, loop)
        self._monitor.start()

        # Start position monitoring (auto-sell loop)
        await self._positions.start_monitoring()

        # Attach log capture handler for /logs command
        root_logger = logging.getLogger("solbot")
        capture_handler = _LogCaptureHandler()
        capture_handler.setFormatter(logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
            datefmt="%H:%M:%S",
        ))
        root_logger.addHandler(capture_handler)

        # Start Telegram command handler
        self._commands = CommandHandler(self._config.telegram, self)
        await self._commands.start()

        # Start IPC server for OpenClaw integration
        self._ipc_server = IPCServer(
            bot=self,
            socket_path=self._config.ipc.socket_path,
            auth_token=self._config.ipc.auth_token,
            service_id=self._config.ipc.service_id,
        )
        await self._ipc_server.start()

        # Start Smart Money Intelligence engine
        self._smart_money = SmartMoneyEngine(db=self._db)
        await self._smart_money.start()

        self._running = True
        mode = "PAPER" if self._config.jupiter.paper_trade else "LIVE"
        logger.info(
            f"All components initialized | mode={mode} | "
            f"positions={self._positions.open_count}/{self._config.trading.max_concurrent_positions} | "
            f"blacklisted={self._blacklist.count}"
        )

        # Send startup notification
        await self._telegram.send_startup_message(
            mode=mode,
            positions=self._positions.open_count,
            blacklisted=self._blacklist.count,
        )

        # Run event processing loop
        try:
            await self._process_events()
        except asyncio.CancelledError:
            logger.info("Bot cancelled")
        finally:
            await self.stop()

    async def stop(self):
        """Gracefully shut down all components."""
        self._running = False

        if self._commands:
            await self._commands.stop()

        if self._ipc_server:
            await self._ipc_server.stop()

        if self._smart_money:
            await self._smart_money.stop()

        if self._market_intel:
            await self._market_intel.stop()

        if self._positions:
            await self._positions.stop_monitoring()

        if self._monitor:
            self._monitor.stop()

        if self._jupiter:
            await self._jupiter.stop()

        if self._dex_client:
            await self._dex_client.stop()

        if self._birdeye_client:
            await self._birdeye_client.stop()

        if self._telegram:
            await self._telegram.stop()

        if self._db:
            await self._db.close()

        self._print_summary()
        logger.info("Solbot stopped")

    # ── Main Event Loop ─────────────────────────────────────────────────

    async def _process_events(self):
        """Main event loop: consume tokens, filter, score, buy."""
        while self._running and not self._killed:
            try:
                token = await asyncio.wait_for(
                    self._monitor.queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue

            # Process in background to not block the queue consumer
            asyncio.create_task(self._handle_token(token))

    async def _handle_token(self, token: TokenEvent):
        """Full pipeline for a single token event with lifecycle tracing."""
        from solbot.filters import rejection_counters, DEBUG_MODE
        import time as _time
        t_start = _time.time()

        # ── TOKEN DETECTED ──────────────────────────────────────────────
        logger.info(
            f"TOKEN DETECTED: {token.symbol} | mint={token.mint[:16]}... | "
            f"creator={token.creator[:12] if token.creator else '?'}... | "
            f"liq={token.liquidity_sol:.2f} SOL | mcap=${token.market_cap_usd:.0f}"
        )

        # Step 1: Blacklist check
        if await self._blacklist.check_and_reject(token):
            rejection_counters.rejected_blacklist += 1
            logger.info(
                f"REJECTED reason=BLACKLISTED token={token.symbol} "
                f"creator={token.creator[:16] if token.creator else '?'}"
            )
            return

        # Step 2: Filters (returns tuple now)
        passed, reject_reason = self._filter.is_qualified(token)
        if not passed:
            # Already logged inside filter with specific reason
            return

        t_after_filter = _time.time()

        # ── TOKEN SCORED ────────────────────────────────────────────────
        score = await self._scorer.score_token(token)

        if DEBUG_MODE:
            logger.info(
                f"TOKEN SCORED: {token.symbol} | composite={score.composite_score:.1f} | "
                f"liq_score={score.liquidity_score:.1f} | creator={score.creator_score:.1f} | "
                f"pressure={score.buy_pressure_score:.1f} | rug={score.anti_rug_score:.1f} | "
                f"confidence={score.confidence.value} | flags={score.flags}"
            )
        else:
            logger.info(f"TOKEN SCORED: {token.symbol} | confidence={score.confidence.value} | score={score.composite_score:.1f}")

        # Step 3b: Smart Money evaluation
        if self._smart_money:
            sm_signal = await self._smart_money.evaluate_token(
                mint=token.mint,
                creator=token.creator or "",
            )
            if sm_signal.confidence_modifier != 0:
                score.composite_score += sm_signal.confidence_modifier
                score.composite_score = max(0.0, min(100.0, score.composite_score))
                if sm_signal.confidence_modifier > 0:
                    score.flags.append("SMART_MONEY_BOOST")
                elif sm_signal.confidence_modifier < 0:
                    score.flags.append("TOXIC_WALLET_OVERLAP")
                logger.info(
                    f"SMART_MONEY: {token.symbol} | modifier={sm_signal.confidence_modifier:+.1f} | "
                    f"{sm_signal.message}"
                )

            asyncio.create_task(
                self._smart_money.record_token_launch(
                    mint=token.mint,
                    creator=token.creator or "",
                    liquidity_sol=token.liquidity_sol,
                )
            )

        t_after_score = _time.time()

        # Step 4: Telegram alert
        asyncio.create_task(self._telegram.send_token_alert(score))

        # Step 5: Pause check
        if self._paused:
            rejection_counters.rejected_paused += 1
            logger.info(f"REJECTED reason=PAUSED token={token.symbol}")
            return

        # Step 6: Confidence check
        if score.confidence != Confidence.HIGH:
            rejection_counters.rejected_low_confidence += 1
            logger.info(
                f"REJECTED reason=LOW_CONFIDENCE token={token.symbol} "
                f"confidence={score.confidence.value} required=HIGH "
                f"score={score.composite_score:.1f}"
            )
            return

        # Step 7: Position limits and cooldown
        if not self._positions.can_buy():
            # Determine specific reason
            if self._positions.open_count >= self._positions._config.max_concurrent_positions:
                rejection_counters.rejected_max_positions += 1
                logger.info(
                    f"REJECTED reason=MAX_POSITIONS token={token.symbol} "
                    f"current={self._positions.open_count} limit={self._positions._config.max_concurrent_positions}"
                )
            else:
                rejection_counters.rejected_cooldown += 1
                logger.info(
                    f"REJECTED reason=COOLDOWN token={token.symbol} "
                    f"cooldown={self._positions._config.buy_cooldown_seconds}s"
                )
            return

        # Step 8: Double-buy check
        if self._positions.has_position(token.mint):
            logger.info(f"REJECTED reason=ALREADY_HOLDING token={token.symbol}")
            return

        # ── TOKEN ELIGIBLE FOR BUY ──────────────────────────────────────
        logger.info(
            f"TOKEN ELIGIBLE_FOR_BUY: {token.symbol} | "
            f"confidence={score.confidence.value} | score={score.composite_score:.1f} | "
            f"pipeline_ms={(t_after_score - t_start)*1000:.0f}"
        )

        # Step 9: Execute buy
        await self._execute_buy(token, score)

    # ── Buy Execution ───────────────────────────────────────────────────

    async def _execute_buy(self, token: TokenEvent, score: TokenScore):
        """Execute a buy and open a position."""
        if self._killed:
            return

        mode = "[PAPER]" if self._jupiter.is_paper_mode else "[LIVE]"
        logger.info(
            f"{mode} BUYING: {token.symbol} ({token.mint[:12]}...) | "
            f"conf={score.confidence.value} | score={score.composite_score:.1f}"
        )

        result = await self._jupiter.execute_swap(token.mint)

        if result.success:
            # Open position
            await self._positions.open_position(
                mint=token.mint,
                symbol=token.symbol,
                name=token.name,
                creator=token.creator or "",
                entry_price_sol=result.amount_in,
                entry_amount_tokens=result.amount_out,
                entry_tx=result.tx_signature or "",
                confidence=score.confidence.value,
                composite_score=score.composite_score,
            )

            # Record trade
            await self._db.record_trade(
                mint=token.mint,
                symbol=token.symbol,
                side="buy",
                amount_sol=result.amount_in,
                amount_tokens=result.amount_out,
                tx_signature=result.tx_signature or "",
                is_paper=self._jupiter.is_paper_mode,
                confidence=score.confidence.value,
                composite_score=score.composite_score,
                latency_ms=result.latency_ms,
            )

            # Start market intelligence tracking for this token
            if self._market_intel:
                await self._market_intel.track_token(token.mint, token.symbol)

            # Record buy in Smart Money engine (tracks our wallet activity)
            if self._smart_money and self._wallet:
                asyncio.create_task(
                    self._smart_money.record_wallet_buy(
                        wallet=self._wallet.pubkey_str,
                        mint=token.mint,
                        amount_sol=result.amount_in,
                    )
                )

            logger.info(
                f"{mode} BUY OK: {token.symbol} | tx={result.tx_signature[:20]}... | "
                f"{result.latency_ms:.0f}ms | positions={self._positions.open_count}"
            )

            # Telegram alert
            asyncio.create_task(
                self._telegram.send_buy_alert(
                    score=score,
                    tx_signature=result.tx_signature,
                    success=True,
                    amount_sol=result.amount_in,
                    amount_tokens=result.amount_out,
                    is_paper=self._jupiter.is_paper_mode,
                )
            )
        else:
            logger.error(
                f"{mode} BUY FAIL: {token.symbol} | err={result.error} | "
                f"{result.latency_ms:.0f}ms"
            )
            asyncio.create_task(
                self._telegram.send_buy_alert(
                    score=score, tx_signature=None, success=False
                )
            )

    # ── Sell Execution (callback for PositionManager) ───────────────────

    async def _execute_sell(self, mint: str, sell_pct: float, reason: str):
        """Execute a sell order triggered by the position manager.

        This is the SellCallback registered with PositionManager.

        Args:
            mint: Token mint to sell.
            sell_pct: Fraction of position to sell (0.0-1.0).
            reason: SellReason value string.
        """
        if self._killed and reason != SellReason.EMERGENCY.value:
            return

        pos = self._positions.positions.get(mint)
        if not pos:
            logger.warning(f"Sell requested for unknown position: {mint[:12]}...")
            return

        mode = "[PAPER]" if self._jupiter.is_paper_mode else "[LIVE]"
        logger.info(
            f"{mode} SELLING {sell_pct*100:.0f}%: {pos.symbol} | reason={reason}"
        )

        # For sells, we swap token back to SOL
        # In paper mode, simulate the sell based on current price
        if self._jupiter.is_paper_mode:
            result = await self._jupiter.execute_paper_sell(
                mint=mint,
                token_amount=pos.remaining_tokens * sell_pct,
                estimated_sol_value=pos.current_price_sol * sell_pct,
            )
        else:
            # Live sell: swap tokens back to SOL via Jupiter
            result = await self._jupiter.execute_sell(
                input_mint=mint,
                token_amount=int(pos.remaining_tokens * sell_pct),
            )

        if result.success:
            # Build snapshot for alert
            pnl_sol = result.amount_out - (pos.entry_price_sol * sell_pct)
            pnl_pct = ((result.amount_out / (pos.entry_price_sol * sell_pct)) - 1.0) * 100 if pos.entry_price_sol > 0 else 0.0

            snapshot = PositionSnapshot(
                mint=pos.mint,
                symbol=pos.symbol,
                name=pos.name,
                creator=pos.creator,
                entry_price_sol=pos.entry_price_sol,
                current_price_sol=pos.current_price_sol,
                highest_price_sol=pos.highest_price_sol,
                pnl_pct=pnl_pct,
                pnl_sol=pnl_sol,
                confidence=pos.confidence,
                composite_score=pos.composite_score,
                age_seconds=pos.age_seconds,
                sell_reason=reason,
                exit_tx=result.tx_signature,
                exit_amount_sol=result.amount_out,
            )

            # Close position (full sell) or update remaining
            if sell_pct >= 0.99:
                # Full close
                await self._positions.close_position(
                    mint=mint,
                    exit_price_sol=pos.current_price_sol,
                    exit_amount_sol=result.amount_out,
                    exit_tx=result.tx_signature or "",
                    reason=SellReason(reason),
                )
                # Stop market intel tracking
                if self._market_intel:
                    await self._market_intel.untrack_token(mint)
            else:
                # Partial sell - reduce remaining tokens
                pos.remaining_tokens *= (1.0 - sell_pct)

            # Record trade
            await self._db.record_trade(
                mint=mint,
                symbol=pos.symbol,
                side="sell",
                amount_sol=result.amount_out,
                amount_tokens=pos.remaining_tokens * sell_pct,
                tx_signature=result.tx_signature or "",
                is_paper=self._jupiter.is_paper_mode,
                latency_ms=result.latency_ms,
            )

            # Record sell in Smart Money engine
            if self._smart_money and self._wallet:
                asyncio.create_task(
                    self._smart_money.record_wallet_sell(
                        wallet=self._wallet.pubkey_str,
                        mint=mint,
                        amount_sol=result.amount_out,
                        pnl_sol=pnl_sol,
                        hold_seconds=pos.age_seconds,
                    )
                )

            # Update kill switch tracking
            self._total_realized_pnl_sol += pnl_sol
            if pnl_sol < 0:
                self._consecutive_losses += 1
            else:
                self._consecutive_losses = 0

            # Check kill switch
            await self._check_kill_switch(reason)

            # Auto-blacklist on stop loss
            if reason == SellReason.STOP_LOSS.value and self._config.trading.blacklist_on_stop_loss:
                if pos.creator:
                    newly_added = await self._blacklist.auto_blacklist_on_rug(
                        creator_address=pos.creator,
                        reason=BlacklistReason.RUG_STOP_LOSS_HIT,
                        mint=pos.mint,
                        symbol=pos.symbol,
                    )
                    if newly_added:
                        asyncio.create_task(
                            self._telegram.send_blacklist_alert(
                                creator_address=pos.creator,
                                reason=BlacklistReason.RUG_STOP_LOSS_HIT,
                                related_symbol=pos.symbol,
                                related_mint=pos.mint,
                            )
                        )

            # Auto-blacklist on rug detection
            if reason == SellReason.RUG_DETECTED.value:
                if pos.creator:
                    newly_added = await self._blacklist.auto_blacklist_on_rug(
                        creator_address=pos.creator,
                        reason=BlacklistReason.RUG_LIQUIDITY_PULL,
                        mint=pos.mint,
                        symbol=pos.symbol,
                    )
                    if newly_added:
                        asyncio.create_task(
                            self._telegram.send_blacklist_alert(
                                creator_address=pos.creator,
                                reason=BlacklistReason.RUG_LIQUIDITY_PULL,
                                related_symbol=pos.symbol,
                                related_mint=pos.mint,
                            )
                        )

                # Record rug in Smart Money engine
                if self._smart_money:
                    asyncio.create_task(
                        self._smart_money.record_rug(
                            mint=pos.mint,
                            creator=pos.creator,
                        )
                    )

            # Telegram sell alert
            asyncio.create_task(
                self._telegram.send_sell_alert(
                    snapshot=snapshot,
                    is_paper=self._jupiter.is_paper_mode,
                )
            )

            logger.info(
                f"{mode} SELL OK: {pos.symbol} | reason={reason} | "
                f"pnl={pnl_pct:+.1f}% | tx={result.tx_signature[:20]}..."
            )
        else:
            logger.error(
                f"{mode} SELL FAIL: {pos.symbol} | reason={reason} | "
                f"err={result.error}"
            )

    # ── Market Intelligence Signal Handler ──────────────────────────────

    async def _handle_market_signal(self, signal: MarketSignal):
        """Handle signals from the market intelligence engine.

        Actions based on signal type and severity:
        - critical rug signals -> immediate position exit + blacklist
        - warning signals -> Telegram alert
        - info/momentum signals -> Telegram alert (if enabled)
        - dynamic trailing stop adjustments
        """
        mint = signal.mint
        pos = self._positions.positions.get(mint) if self._positions else None

        # Log all signals
        logger.info(f"MARKET SIGNAL [{signal.severity.upper()}]: {signal.message}")

        # ── Critical: Exit position immediately ─────────────────────────
        if signal.severity == "critical" and pos:
            should_exit = False

            if signal.signal_type == "rug_liquidity_critical" and self._config.market_intel.exit_on_liq_critical:
                should_exit = True
            elif signal.signal_type == "rug_sell_dump" and self._config.market_intel.exit_on_sell_dump:
                should_exit = True

            if should_exit:
                logger.critical(
                    f"MARKET INTEL EXIT: {pos.symbol} | {signal.signal_type} | "
                    f"Selling 100% immediately"
                )
                # Execute emergency sell
                await self._execute_sell(mint, 1.0, SellReason.RUG_DETECTED.value)

                # Send rug alert via Telegram
                if self._config.market_intel.alert_on_rug_warning:
                    await self._send_market_signal_alert(signal)
                return

        # ── Warning: Alert + dynamic trailing tighten ───────────────────
        if signal.severity == "warning":
            if self._config.market_intel.alert_on_rug_warning:
                await self._send_market_signal_alert(signal)

            # Tighten trailing stop on warnings
            if pos and self._market_intel and self._config.market_intel.dynamic_trailing_enabled:
                dynamic_pct = self._market_intel.get_dynamic_trailing_stop(mint)
                if dynamic_pct is not None:
                    logger.info(
                        f"Dynamic trailing stop adjusted: {pos.symbol} -> {dynamic_pct:.1f}%"
                    )

        # ── Info: Momentum alerts ───────────────────────────────────────
        if signal.severity == "info":
            if self._config.market_intel.alert_on_momentum:
                await self._send_market_signal_alert(signal)

    async def _send_market_signal_alert(self, signal: MarketSignal):
        """Send a market signal alert via Telegram."""
        if not self._telegram:
            return

        severity_emoji = {
            "critical": "🚨",
            "warning": "⚠️",
            "info": "📊",
        }
        emoji = severity_emoji.get(signal.severity, "📊")

        type_labels = {
            "rug_liquidity_critical": "LP DRAIN (CRITICAL)",
            "rug_liquidity_warning": "LP Drain Warning",
            "rug_sell_dump": "DUMP DETECTED",
            "sell_pressure_warning": "Sell Pressure",
            "volume_collapse": "Volume Collapse",
            "momentum_mcap_spike": "MCAP Surge",
            "momentum_volume_surge": "Volume Surge",
            "holder_growth_surge": "Holder Growth",
        }
        type_label = type_labels.get(signal.signal_type, signal.signal_type)

        message = (
            f"{emoji} <b>MARKET INTEL: {type_label}</b>\n\n"
            f"{signal.message}\n\n"
            f"<b>Severity:</b> {signal.severity.upper()}"
        )

        await self._telegram._send_message(message)

    # ── Kill Switch ─────────────────────────────────────────────────────

    async def _check_kill_switch(self, last_reason: str):
        """Check if kill switch should be triggered."""
        if not self._config.trading.kill_switch_enabled:
            return
        if self._killed:
            return

        triggered = False
        trigger_reason = ""

        # Check cumulative loss
        if self._total_realized_pnl_sol <= -self._config.trading.kill_switch_max_loss_sol:
            triggered = True
            trigger_reason = (
                f"Cumulative loss ({self._total_realized_pnl_sol:.4f} SOL) "
                f"exceeded max ({-self._config.trading.kill_switch_max_loss_sol:.4f} SOL)"
            )

        # Check consecutive losses
        if self._consecutive_losses >= self._config.trading.kill_switch_max_consecutive_losses:
            triggered = True
            trigger_reason = (
                f"Consecutive losses ({self._consecutive_losses}) "
                f"exceeded max ({self._config.trading.kill_switch_max_consecutive_losses})"
            )

        if triggered:
            self._killed = True
            logger.critical(f"KILL SWITCH TRIGGERED: {trigger_reason}")

            # Emergency close all positions
            closed_mints = await self._positions.emergency_close_all()

            # Alert
            await self._telegram.send_kill_switch_alert(
                reason=trigger_reason,
                positions_closed=len(closed_mints),
            )

            # Stop accepting new trades
            self._running = False

    # ── Summary ─────────────────────────────────────────────────────────

    def _print_summary(self):
        """Print trading session summary."""
        mode = "[PAPER]" if self._config.jupiter.paper_trade else "[LIVE]"
        logger.info("=" * 60)
        logger.info(f"{mode} SESSION SUMMARY")
        logger.info(f"  Open positions:      {self._positions.open_count if self._positions else 0}")
        logger.info(f"  Blacklisted creators: {self._blacklist.count if self._blacklist else 0}")
        logger.info(f"  Realized P&L:        {self._total_realized_pnl_sol:+.4f} SOL")
        logger.info(f"  Consecutive losses:  {self._consecutive_losses}")
        logger.info(f"  Kill switch:         {'TRIGGERED' if self._killed else 'OK'}")
        logger.info(f"  Tokens seen:         {self._filter.seen_count if self._filter else 0}")
        logger.info("=" * 60)


async def run_bot():
    """Entry point: load config, wire up signal handling, and run."""
    config = BotConfig()
    bot = Solbot(config)

    # Graceful shutdown on SIGINT/SIGTERM
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.stop()))

    await bot.start()
