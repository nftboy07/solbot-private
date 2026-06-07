"""Main bot orchestrator and Sniper for Solbot with Automatic Moonbag Strategy."""

import asyncio
import signal
from time import time
from dataclasses import dataclass, field
from typing import Optional, Dict, List

from solbot.config import BotConfig, BotMode
from solbot.filters import TokenFilter
from solbot.jupiter import JupiterClient
from solbot.logger import get_logger, setup_logger
from solbot.models import TokenEvent, TradeResult
from solbot.pumpfun import PumpFunMonitor
from solbot.pumpfun_client import PumpFunClient
from solbot.telegram import TelegramManager
from solbot.wallet import Wallet

logger = get_logger("bot")

@dataclass
class Position:
    mint: str
    symbol: str
    entry_price: float  # Market Cap USD as price proxy
    size: float        # Amount in SOL initially, then tokens on sell
    active: bool = True
    tp_sold: bool = False
    start_time: float = field(default_factory=time)

class Solbot:
    """High-speed DEGEN Sniper with Automatic Moonbag Management."""

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
        self._positions: Dict[str, Position] = {}
        self._paused = False

    async def start(self):
        setup_logger(self._config.logging)
        logger.info("SOLBOT DEGEN SNIPER + MOONBAG STARTING")

        errors = self._config.validate()
        if errors:
            raise RuntimeError(f"Configuration invalid: {errors}")

        self._wallet = Wallet(self._config.solana)
        self._filter = TokenFilter(self._config)

        self._pump_client = PumpFunClient(self._config, self._wallet)
        await self._pump_client.start()

        self._jupiter = JupiterClient(self._config.jupiter, self._wallet)
        await self._jupiter.start()

        self._telegram = TelegramManager(self._config.telegram)
        await self._telegram.start(self)
        await self._telegram.send_message("<b>Solbot Sniper (Moonbag Mode) started!</b>")

        loop = asyncio.get_running_loop()
        self._monitor = PumpFunMonitor(self._config.pumpfun, loop)
        self._monitor.start()

        self._running = True
        
        # Main sniper processing loop
        asyncio.create_task(self._process_events())

    async def stop(self):
        self._running = False
        if self._monitor: self._monitor.stop()
        if self._pump_client: await self._pump_client.stop()
        if self._jupiter: await self._jupiter.stop()
        if self._telegram: await self._telegram.stop()
        logger.info("Solbot stopped")

    async def _process_events(self):
        while self._running:
            if self._paused:
                await asyncio.sleep(1)
                continue
            try:
                # Fast queue processing for DEGEN sniper
                token = await asyncio.wait_for(self._monitor.queue.get(), timeout=1.0)
                qualified, size = self._filter.is_qualified(token)
                if qualified:
                    # Non-blocking fire-and-forget sniper execution
                    asyncio.create_task(self._execute_snipe(token, size))
            except asyncio.TimeoutError:
                continue

    async def _execute_snipe(self, token: TokenEvent, size: float):
        # Calculate dynamic fee from filter (lamports -> SOL)
        fee_lamports = self._filter.get_dynamic_fee(token.mint)
        priority_fee_sol = fee_lamports / 1_000_000_000

        # Execute Buy
        result = await self._pump_client.execute_trade(
            token.mint, 
            action="buy",
            amount=size, 
            priority_fee=priority_fee_sol
        )
        self._trades.append(result)

        if result.success:
            pos = Position(
                mint=token.mint, 
                symbol=token.symbol,
                entry_price=token.market_cap_usd,
                size=size
            )
            self._positions[token.mint] = pos
            await self._telegram.send_message(f"✅ <b>BUY: {token.symbol}</b>\nSize: {size} SOL")
            
            # Spawn lightweight background position manager for this token
            asyncio.create_task(self._manage_moonbag(pos))
        else:
            logger.error(f"Snipe failed for {token.symbol}: {result.error}")

    async def _manage_moonbag(self, pos: Position):
        """Zero-bloat background task to handle TP and Moonbag protection."""
        logger.info(f"Monitoring moonbag for {pos.symbol}...")
        
        while self._running and pos.active:
            try:
                # 1. Fetch current price/market cap proxy
                current_price = await self._get_fast_price(pos.mint)
                
                if current_price <= 0:
                    await asyncio.sleep(5)
                    continue

                # 2. Risk Management: Breakeven Stop Loss
                if pos.tp_sold and current_price <= pos.entry_price:
                    await self._exit_position(pos, "Breakeven Protect", 1.0)
                    break

                # 3. Take Profit: Initial 2x (+100%) -> Sell 50%
                if not pos.tp_sold and current_price >= (pos.entry_price * 2.0):
                    await self._exit_position(pos, "2x Moonbag Trigger", 0.5)
                    pos.tp_sold = True

                # 4. Secondary TP (Optional): 10x Moonshot -> Exit all
                if pos.tp_sold and current_price >= (pos.entry_price * 10.0):
                    await self._exit_position(pos, "10x Moonshot Exit", 1.0)
                    break

                await asyncio.sleep(5)

            except Exception as e:
                logger.error(f"Error in moonbag manager for {pos.symbol}: {e}")
                await asyncio.sleep(10)

    async def _exit_position(self, pos: Position, reason: str, pct: float):
        """Execute a sell transaction via the client."""
        result = await self._pump_client.execute_trade(
            pos.mint, 
            action="sell", 
            amount=pct 
        )
        
        if result.success:
            if pct >= 1.0:
                pos.active = False
                if pos.mint in self._positions:
                    del self._positions[pos.mint]
            
            msg = f"🚨 <b>SELL: {pos.symbol}</b>\nReason: {reason}\nSize: {pct*100}%"
            await self._telegram.send_message(msg)
            logger.info(f"Sold {pct*100}% of {pos.symbol} due to {reason}")

    async def _get_fast_price(self, mint: str) -> float:
        """Placeholder for fast market cap/price updates."""
        return 0.0

async def run_bot():
    """Entry point: load config, wire up signal handling, and run."""
    config = BotConfig()
    bot = Solbot(config)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.stop()))

    await bot.start()
    while bot._running:
        await asyncio.sleep(1)
