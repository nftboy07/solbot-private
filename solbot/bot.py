"""Main bot orchestrator for Solbot with Auto-Follow / Copytrade engine."""

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
    """High-speed DEGEN Sniper with Auto-Follow / Copytrade Engine."""

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
        logger.info("SOLBOT DEGEN SNIPER + COPYTRADE STARTING")

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
        await self._telegram.send_message("<b>Solbot Sniper (Copytrade Mode) started!</b>")

        loop = asyncio.get_running_loop()
        self._monitor = PumpFunMonitor(self._config.pumpfun, loop)
        self._monitor.start()

        self._running = True
        
        # Sniper & Copytrade detection loop
        asyncio.create_task(self._process_events())

    async def stop(self):
        self._running = False
        if self._monitor: self._monitor.stop()
        if self._pump_client: await self._pump_client.stop()
        if self._jupiter: await self._jupiter.stop()
        if self._telegram: await self._telegram.stop()
        logger.info("Solbot stopped")

    async def _process_events(self):
        """Processes events from the stream, splitting into Sniper and Copytrade paths."""
        while self._running:
            if self._paused:
                await asyncio.sleep(1)
                continue
            try:
                # Get event from pump.fun monitor queue
                token = await asyncio.wait_for(self._monitor.queue.get(), timeout=1.0)
                
                # Path A: Sniper (New Token Detection)
                qualified, size = self._filter.is_qualified(token)
                if qualified:
                    asyncio.create_task(self._execute_snipe(token, size, reason="Sniper"))
                
                # Path B: Copytrade (Detection of Smart Wallet/Whale activity on existing tokens)
                # In our architecture, the monitor also pushes trade events if we subscribe.
                # Here we check if the 'creator' (trader) is a target we want to follow.
                elif self._filter.is_copy_target(token.creator):
                    asyncio.create_task(self._execute_snipe(token, self._config.jupiter.buy_amount_sol, reason="Copytrade"))

            except asyncio.TimeoutError:
                continue

    async def _execute_snipe(self, token: TokenEvent, size: float, reason: str = "Sniper"):
        """Fast transaction execution via PumpPortal."""
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
            await self._telegram.send_message(f"✅ <b>BUY ({reason}): {token.symbol}</b>\nSize: {size} SOL")
            
            # Start background monitoring (TP/SL/Priority Exits)
            asyncio.create_task(self._position_manager(pos))
        else:
            logger.error(f"Execution failed for {token.symbol} ({reason}): {result.error}")

    async def _position_manager(self, pos: Position):
        """Lightweight background task for managing active positions."""
        while self._running and pos.active:
            # Monitoring logic (Dev dump, TP, Moonbag, etc)
            await asyncio.sleep(5)

    async def _exit_position(self, pos: Position, reason: str, pct: float):
        result = await self._pump_client.execute_trade(pos.mint, action="sell", amount=pct)
        if result.success:
            is_win = "TP" in reason or "Moonbag" in reason
            self._filter.update_score(pos.creator, is_win)
            if pct >= 1.0:
                pos.active = False
                if pos.mint in self._positions: del self._positions[pos.mint]
            await self._telegram.send_message(f"🚨 <b>SELL: {pos.symbol}</b>\nReason: {reason}")

async def run_bot():
    config = BotConfig()
    bot = Solbot(config)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.stop()))
    await bot.start()
    while bot._running:
        await asyncio.sleep(1)
