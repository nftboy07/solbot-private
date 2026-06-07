"""Main bot orchestrator and Sniper for Solbot with Auto-Sell Priority."""

import asyncio
import signal
import json
from time import time
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any

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
    entry_price: float
    entry_liq: float
    creator: str
    size: float
    active: bool = True
    tp_sold: bool = False
    start_time: float = field(default_factory=time)
    highest_price: float = 0.0

class Solbot:
    """High-speed DEGEN Sniper with Auto-Sell Priority & Smart Wallet Strategy."""

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
        # address -> set(mint) to track whale positions
        self._whale_watches: Dict[str, set] = {}

    async def start(self):
        setup_logger(self._config.logging)
        logger.info("SOLBOT DEGEN SNIPER + AUTO-SELL STARTING")

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
        await self._telegram.send_message("<b>Solbot Sniper (Auto-Sell Mode) started!</b>")

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
                token = await asyncio.wait_for(self._monitor.queue.get(), timeout=1.0)
                qualified, size = self._filter.is_qualified(token)
                if qualified:
                    asyncio.create_task(self._execute_snipe(token, size))
            except asyncio.TimeoutError:
                continue

    async def _execute_snipe(self, token: TokenEvent, size: float):
        fee_lamports = self._filter.get_dynamic_fee(token.mint)
        priority_fee_sol = fee_lamports / 1_000_000_000

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
                entry_liq=token.liquidity_sol,
                creator=token.creator or "",
                size=size,
                highest_price=token.market_cap_usd
            )
            self._positions[token.mint] = pos
            await self._telegram.send_message(f"✅ <b>BUY: {token.symbol}</b>\nSize: {size} SOL")
            
            # Start background monitoring
            asyncio.create_task(self._position_manager(pos))
        else:
            logger.error(f"Snipe failed for {token.symbol}: {result.error}")

    async def _position_manager(self, pos: Position):
        """Monitors an active trade for AUTO-SELL conditions."""
        logger.info(f"Manager started for {pos.symbol}")
        
        while self._running and pos.active:
            try:
                # 1. Fetch live market data (placeholder)
                market = await self._get_market_snapshot(pos.mint)
                if not market:
                    await asyncio.sleep(5)
                    continue
                
                price = market['price'] # mcap
                liq = market['liquidity']
                pos.highest_price = max(pos.highest_price, price)

                # --- AUTO-SELL PRIORITY CHECK ---

                # 1. Dev Dump (Simple proxy: price drops > 50% in seconds or known wallet dump)
                if price < pos.entry_price * 0.4:
                    await self._exit_position(pos, "DEV DUMP", 1.0)
                    break

                # 2. Liquidity Drain
                if liq < pos.entry_liq * 0.7:
                    await self._exit_position(pos, "LIQUIDITY DRAIN", 1.0)
                    break

                # 3. Emergency Stop Loss
                if price < pos.entry_price * 0.8:
                    await self._exit_position(pos, "EMERGENCY STOP LOSS", 1.0)
                    break

                # 4. Take Profit (Moon Bag)
                if not pos.tp_sold and price >= (pos.entry_price * 2.0):
                    await self._exit_position(pos, "TP: SECURED INITIAL (2x)", 0.5)
                    pos.tp_sold = True

                # 5. Breakeven Protection
                if pos.tp_sold and price <= pos.entry_price:
                    await self._exit_position(pos, "MOONBAG BREAKEVEN", 1.0)
                    break

                # 6. Time Exit (30m stagnation)
                if time() - pos.start_time > 1800:
                    await self._exit_position(pos, "TIME TIMEOUT", 1.0)
                    break

                await asyncio.sleep(5) # Slow poll for safety
            except Exception as e:
                logger.error(f"Manager Error {pos.symbol}: {e}")
                await asyncio.sleep(10)

    async def _exit_position(self, pos: Position, reason: str, pct: float):
        """Executes sell and updates smart wallet scores."""
        result = await self._pump_client.execute_trade(
            pos.mint, action="sell", amount=pct
        )
        
        if result.success:
            # Smart Wallet Strategy: Update score based on outcome
            is_win = reason.startswith("TP") or reason.startswith("MOONBAG")
            self._filter.update_score(pos.creator, is_win)

            if pct >= 1.0:
                pos.active = False
                if pos.mint in self._positions:
                    del self._positions[pos.mint]
            
            await self._telegram.send_message(f"🚨 <b>SELL: {pos.symbol}</b>\nReason: {reason}\nSize: {pct*100}%")

    async def _get_market_snapshot(self, mint: str) -> Optional[Dict]:
        """Placeholder for fast price/liquidity fetching."""
        return None

async def run_bot():
    config = BotConfig()
    bot = Solbot(config)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.stop()))
    await bot.start()
    while bot._running:
        await asyncio.sleep(1)
