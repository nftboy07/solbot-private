"""Main bot orchestrator - Clean DEGEN Sniper."""

import asyncio
import signal
from typing import Optional, Dict, List

from solbot.config import BotConfig
from solbot.filters import TokenFilter
from solbot.jupiter import JupiterClient
from solbot.logger import get_logger, setup_logger
from solbot.models import TokenEvent, TradeResult
from solbot.pumpfun import PumpFunMonitor
from solbot.pumpfun_client import PumpFunClient
from solbot.telegram import TelegramManager
from solbot.wallet import Wallet

logger = get_logger("bot")

class Solbot:
    """Streamlined DEGEN sniper bot."""

    def __init__(self, config: BotConfig):
        self._config = config
        self._wallet: Optional[Wallet] = None
        self._monitor: Optional[PumpFunMonitor] = None
        self._pump_client: Optional[PumpFunClient] = None
        self._jupiter: Optional[JupiterClient] = None
        self._telegram: Optional[TelegramManager] = None
        self._filter: Optional[TokenFilter] = None
        self._running = False
        self._trades: List[TradeResult] = []
        self._positions: Dict[str, dict] = {}
        self._paused = False

    async def start(self):
        """Initialize and start the sniper."""
        setup_logger(self._config.logging)
        logger.info("========================================")
        logger.info("   SOLBOT DEGEN SNIPER STARTING")
        logger.info("========================================")

        errors = self._config.validate()
        if errors:
            raise RuntimeError(f"Configuration invalid: {errors}")

        self._wallet = Wallet(self._config.solana)
        self._filter = TokenFilter(self._config)

        # Start low-latency signing client
        self._pump_client = PumpFunClient(self._config, self._wallet)
        await self._pump_client.start()

        self._jupiter = JupiterClient(self._config.jupiter, self._wallet)
        await self._jupiter.start()

        # Command interface
        self._telegram = TelegramManager(self._config.telegram)
        await self._telegram.start(self)
        await self._telegram.send_message("🚀 <b>Degen Sniper Started!</b>")

        # Live stream monitor
        loop = asyncio.get_running_loop()
        self._monitor = PumpFunMonitor(self._config.pumpfun, loop)
        self._monitor.start()

        self._running = True
        
        # Start core event processing
        asyncio.create_task(self._process_events())
        logger.info("Bot is active and monitoring stream...")

    async def stop(self):
        """Graceful shutdown."""
        self._running = False
        if self._monitor: self._monitor.stop()
        if self._pump_client: await self._pump_client.stop()
        if self._jupiter: await self._jupiter.stop()
        if self._telegram: 
            await self._telegram.send_message("🛑 <b>Bot Stopped.</b>")
            await self._telegram.stop()
        logger.info("Solbot stopped.")

    async def _process_events(self):
        """Main loop: Detect -> Filter -> Buy."""
        while self._running:
            if self._paused:
                await asyncio.sleep(0.5)
                continue
            try:
                token = await asyncio.wait_for(self._monitor.queue.get(), timeout=1.0)
                qualified, size = self._filter.is_qualified(token)
                if qualified:
                    # Non-blocking buy execution
                    asyncio.create_task(self._execute_trade(token, size))
            except asyncio.TimeoutError:
                continue

    async def _execute_trade(self, token: TokenEvent, size: float):
        """Build, sign, and broadcast buy transaction."""
        fee = self._filter.get_dynamic_fee(token.mint)
        
        # Immediate execution via bonding curve client
        result = await self._pump_client.execute_trade(token.mint, amount=size, fee=fee)
        self._trades.append(result)

        if result.success:
            self._positions[token.mint] = {
                "symbol": token.symbol,
                "size": size,
                "mint": token.mint
            }
            await self._telegram.send_message(
                f"✅ <b>SNIPED: {token.symbol}</b>\n"
                f"Size: {size} SOL\n"
                f"<a href='https://solscan.io/tx/{result.tx_signature}'>View Transaction</a>"
            )
        else:
            logger.error(f"Buy failed for {token.symbol}: {result.error}")

    async def _exit_position(self, pos: dict, reason: str, pct: float):
        """Emergency or manual liquidation."""
        result = await self._pump_client.execute_trade(pos["mint"], action="sell", amount=pct)
        if result.success:
            if pct >= 1.0:
                del self._positions[pos["mint"]]
            await self._telegram.send_message(f"🚨 <b>EXIT: {pos['symbol']}</b> ({reason})")

async def run_bot():
    """Bot entry point."""
    config = BotConfig()
    bot = Solbot(config)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.stop()))

    await bot.start()
    while bot._running:
        await asyncio.sleep(1)
