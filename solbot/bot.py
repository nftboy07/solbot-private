"""Main bot orchestrator - ties all async components together."""

import asyncio
import signal
from typing import Optional

from solbot.config import BotConfig
from solbot.filters import TokenFilter
from solbot.jupiter import JupiterClient
from solbot.logger import get_logger, setup_logger
from solbot.models import TokenEvent, TradeResult
from solbot.pumpfun import PumpFunMonitor
from solbot.scoring import Confidence, ScoringEngine, TokenScore
from solbot.telegram import TelegramAlert
from solbot.wallet import Wallet

logger = get_logger("bot")


class Solbot:
    """Main bot class orchestrating Pump.fun monitoring and Jupiter execution.

    Architecture:
        1. PumpFunMonitor (thread) -> asyncio.Queue -> token events
        2. Event processor (async) -> TokenFilter -> qualified tokens
        3. ScoringEngine (async) -> confidence classification
        4. TelegramAlert (async) -> notifications for qualified tokens
        5. JupiterClient (async/aiohttp) -> swap execution (live or paper)
    """

    def __init__(self, config: BotConfig):
        self._config = config
        self._wallet: Optional[Wallet] = None
        self._monitor: Optional[PumpFunMonitor] = None
        self._jupiter: Optional[JupiterClient] = None
        self._filter: Optional[TokenFilter] = None
        self._scorer: Optional[ScoringEngine] = None
        self._telegram: Optional[TelegramAlert] = None
        self._running = False
        self._trades: list[TradeResult] = []

    async def start(self):
        """Initialize all components and start the bot."""
        # Setup logging
        setup_logger(self._config.logging)
        logger.info("=" * 60)
        logger.info("SOLBOT STARTING")
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
            # Create a dummy wallet for paper trading
            self._wallet = None
        else:
            raise RuntimeError("WALLET_PRIVATE_KEY required for live trading")

        # Initialize components
        self._filter = TokenFilter(self._config.pumpfun)
        self._scorer = ScoringEngine(self._config.scoring)

        # Start Telegram alerts
        self._telegram = TelegramAlert(self._config.telegram)
        await self._telegram.start()

        # Start Jupiter client
        self._jupiter = JupiterClient(self._config.jupiter, self._wallet)
        await self._jupiter.start()

        # Start Pump.fun monitor
        loop = asyncio.get_running_loop()
        self._monitor = PumpFunMonitor(self._config.pumpfun, loop)
        self._monitor.start()

        self._running = True
        mode = "PAPER" if self._config.jupiter.paper_trade else "LIVE"
        logger.info(f"All components initialized | mode={mode} - entering main loop")

        # Send startup notification
        await self._telegram.send_startup_message()

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

        if self._monitor:
            self._monitor.stop()

        if self._jupiter:
            await self._jupiter.stop()

        if self._telegram:
            await self._telegram.stop()

        # Print trade summary
        self._print_summary()
        logger.info("Solbot stopped")

    async def _process_events(self):
        """Main event loop: consume tokens from queue, filter, score, and execute."""
        while self._running:
            try:
                # Wait for token events with timeout (allows graceful shutdown)
                token = await asyncio.wait_for(
                    self._monitor.queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue

            # Step 1: Apply basic filters (dedup, age, liquidity, mcap)
            if not self._filter.is_qualified(token):
                continue

            # Step 2: Score and decide in background (non-blocking)
            asyncio.create_task(self._score_and_trade(token))

    async def _score_and_trade(self, token: TokenEvent):
        """Score a qualified token, alert, and optionally execute trade."""
        # Score the token
        score = await self._scorer.score_token(token)

        # Send Telegram alert for all qualified tokens (regardless of confidence)
        if self._config.telegram.alert_on_qualified:
            asyncio.create_task(self._telegram.send_token_alert(score))

        # Check if token meets minimum confidence for trading
        min_confidence = self._config.scoring.min_trade_confidence.upper()
        if not self._meets_confidence(score, min_confidence):
            logger.info(
                f"SKIP TRADE: {token.symbol} | conf={score.confidence.value} "
                f"(need >={min_confidence})"
            )
            return

        # Execute trade
        await self._execute_trade(token, score)

    def _meets_confidence(self, score: TokenScore, min_level: str) -> bool:
        """Check if score meets minimum confidence threshold."""
        levels = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
        token_level = levels.get(score.confidence.value, 0)
        required_level = levels.get(min_level, 1)
        return token_level >= required_level

    async def _execute_trade(self, token: TokenEvent, score: TokenScore):
        """Execute a trade for a scored token."""
        mode = "[PAPER]" if self._jupiter.is_paper_mode else "[LIVE]"
        logger.info(
            f"{mode} BUYING: {token.symbol} ({token.mint[:12]}...) | "
            f"conf={score.confidence.value} | score={score.composite_score:.1f}"
        )

        result = await self._jupiter.execute_swap(token.mint)
        self._trades.append(result)

        if result.success:
            logger.info(
                f"{mode} BUY OK: {token.symbol} | tx={result.tx_signature[:20]}... | "
                f"{result.latency_ms:.0f}ms"
            )
        else:
            logger.error(
                f"{mode} BUY FAIL: {token.symbol} | err={result.error} | "
                f"{result.latency_ms:.0f}ms"
            )

        # Send trade alert via Telegram
        if self._config.telegram.alert_on_trade:
            asyncio.create_task(
                self._telegram.send_trade_alert(score, result.tx_signature, result.success)
            )

    def _print_summary(self):
        """Print trading session summary."""
        if not self._trades:
            logger.info("No trades executed this session")
            return

        successful = [t for t in self._trades if t.success]
        failed = [t for t in self._trades if not t.success]
        avg_latency = (
            sum(t.latency_ms for t in self._trades) / len(self._trades)
        )

        mode = "[PAPER]" if self._config.jupiter.paper_trade else "[LIVE]"
        logger.info("=" * 60)
        logger.info(f"{mode} SESSION SUMMARY")
        logger.info(f"  Total trades: {len(self._trades)}")
        logger.info(f"  Successful:   {len(successful)}")
        logger.info(f"  Failed:       {len(failed)}")
        logger.info(f"  Avg latency:  {avg_latency:.0f}ms")
        logger.info(f"  Tokens seen:  {self._filter.seen_count}")
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
