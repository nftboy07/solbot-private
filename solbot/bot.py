"""Main bot orchestrator and Position Manager for Solbot."""

import asyncio
import signal
from time import time
from dataclasses import dataclass, field
from typing import Optional, Dict, List

from solbot.config import BotConfig, BotMode
from solbot.filters import TokenFilter, WalletMetrics
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
    peak_price: float = 0.0
    start_time: float = field(default_factory=time)
    tp_count: int = 0
    active: bool = True

class Solbot:
    """Enhanced Solbot with V28 logic and Position Management."""

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
        logger.info("SOLBOT V28 STARTING")

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
        await self._telegram.send_message("<b>Solbot V28 started!</b>")

        loop = asyncio.get_running_loop()
        self._monitor = PumpFunMonitor(self._config.pumpfun, loop)
        self._monitor.start()

        self._running = True
        
        # Start background tasks
        asyncio.create_task(self._process_events())
        asyncio.create_task(self._position_manager())

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
                    asyncio.create_task(self._execute_trade(token, size))
            except asyncio.TimeoutError:
                continue

    async def _execute_trade(self, token: TokenEvent, size: float):
        fee = self._filter.get_dynamic_fee(token.mint)
        result = await self._pump_client.execute_trade(token.mint, amount=size, fee=fee)
        self._trades.append(result)

        if result.success:
            self._positions[token.mint] = Position(
                mint=token.mint, symbol=token.symbol,
                entry_price=token.market_cap_usd, # Using mcap as price proxy
                entry_liq=token.liquidity_sol,
                creator=token.creator or "",
                size=size
            )
            self._filter.update_wallet_score(token.creator, True)
            await self._telegram.send_message(f"✅ <b>BUY: {token.symbol}</b>\nSize: {size} SOL")

    async def _position_manager(self):
        while self._running:
            for mint in list(self._positions.keys()):
                pos = self._positions[mint]
                if not pos.active: continue

                # Simplified: current stats from monitor or client
                current_price, current_liq = await self._get_market_data(mint)
                if current_price > pos.peak_price: pos.peak_price = current_price

                # 1. Dev Dump
                metrics = self._filter.wallet_metrics.get(pos.creator, WalletMetrics())
                if metrics.score < self._config.strategy.dev_dump_score_threshold:
                    await self._exit_position(pos, "Dev Dump", 1.0)
                    continue

                # 2. Liquidity Drain
                if current_liq < pos.entry_liq * (1 - self._config.strategy.liquidity_drop_threshold):
                    await self._exit_position(pos, "Liquidity Drain", 1.0)
                    continue

                # 3. Trailing Stop
                if current_price <= pos.peak_price * (1 - self._config.strategy.trailing_stop_pct):
                    await self._exit_position(pos, "Trailing Stop", 1.0)
                    continue

                # 4. Take Profit
                if pos.tp_count < len(self._config.strategy.tp_targets):
                    target = self._config.strategy.tp_targets[pos.tp_count]
                    if current_price >= pos.entry_price * target["multiplier"]:
                        await self._exit_position(pos, f"TP {pos.tp_count+1}", target["sell_pct"])
                        pos.tp_count += 1

                # 5. Time Exit
                if time() - pos.start_time > (self._config.strategy.momentum_timeout_minutes * 60):
                    if current_price < pos.entry_price * 1.1:
                        await self._exit_position(pos, "Time Exit", 1.0)

            await asyncio.sleep(10)

    async def _exit_position(self, pos: Position, reason: str, pct: float):
        result = await self._pump_client.execute_trade(pos.mint, action="sell", amount=pct)
        if result.success:
            self._filter.update_wallet_score(pos.creator, False)
            if pct >= 1.0:
                pos.active = False
                del self._positions[pos.mint]
            await self._telegram.send_message(f"🚨 <b>SELL: {pos.symbol}</b>\nReason: {reason}\nSize: {pct*100}%")

    async def _get_market_data(self, mint: str):
        # Placeholder for actual market data lookup
        return 0, 0
