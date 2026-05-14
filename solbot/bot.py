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
from solbot.wallet import Wallet

logger = get_logger("bot")


class Solbot:
    """Main bot class orchestrating Pump.fun monitoring and Jupiter execution.

    Architecture:
        1. PumpFunMonitor (thread) -> asyncio.Queue -> token events
        2. Event processor (async) -> TokenFilter -> qualified tokens
        3. JupiterClient (async/aiohttp) -> swap execution
    """

    def __init__(self, config: BotConfig):
        self._config = config
        self._wallet: Optional[Wallet] = None
        self._monitor: Optional[PumpFunMonitor] = None
        self._jupiter: Optional[JupiterClient] = None
        self._filter: Optional[TokenFilter] = None
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

        # Initialize components
        self._wallet = Wallet(self._config.solana)
        self._filter = TokenFilter(self._config.pumpfun)

        # Start Jupiter client
        self._jupiter = JupiterClient(self._config.jupiter, self._wallet)
        await self._jupiter.start()

        # Start Pump.fun monitor
        loop = asyncio.get_running_loop()
        self._monitor = PumpFunMonitor(self._config.pumpfun, loop)
        self._monitor.start()

        self._running = True
        logger.info("All components initialized - entering main loop")

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

        # Print trade summary
        self._print_summary()
        logger.info("Solbot stopped")

    async def _process_events(self):
        """Main event loop: consume tokens from queue, filter, and execute."""
        while self._running:
            try:
                # Wait for token events with timeout (allows graceful shutdown)
                token = await asyncio.wait_for(
                    self._monitor.queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue

            # Apply filters
            if not self._filter.is_qualified(token):
                continue

            # Execute swap in background task (non-blocking)
            asyncio.create_task(self._execute_trade(token))

    async def _execute_trade(self, token: TokenEvent):
        """Execute a trade for a qualified token."""
        logger.info(f"BUYING: {token.symbol} ({token.mint[:12]}...)")

        result = await self._jupiter.execute_swap(token.mint)
        self._trades.append(result)

        if result.success:
            logger.info(
                f"BUY OK: {token.symbol} | tx={result.tx_signature[:16]}... | "
                f"{result.latency_ms:.0f}ms"
            )
        else:
            logger.error(
                f"BUY FAIL: {token.symbol} | err={result.error} | "
                f"{result.latency_ms:.0f}ms"
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

        logger.info("=" * 60)
        logger.info("SESSION SUMMARY")
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
