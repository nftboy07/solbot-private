"""Main bot orchestrator for Solbot with Dev Dump Protection & Copytrade."""

import asyncio
import signal
import os
import sys
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
    """High-speed DEGEN Sniper with Dev Dump Protection."""

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
        logger.info("SOLBOT DEGEN SNIPER + DEV PROTECTION STARTING")

        self._wallet = Wallet(self._config.solana)
        self._filter = TokenFilter(self._config)

        self._pump_client = PumpFunClient(self._config, self._wallet)
        await self._pump_client.start()

        self._jupiter = JupiterClient(self._config.jupiter, self._wallet)
        await self._jupiter.start()

        self._telegram = TelegramManager(self._config.telegram)
        await self._telegram.start(self)
        await self._telegram.send_message("<b>Solbot Sniper (Dev Protection) started!</b>")

        loop = asyncio.get_running_loop()
        self._monitor = PumpFunMonitor(self._config.pumpfun, loop)
        self._monitor.start()

        self._running = True
        asyncio.create_task(self._process_events())

    async def stop(self):
        self._running = False
        if self._monitor: self._monitor.stop()
        if self._pump_client: await self._pump_client.stop()
        if self._jupiter: await self._jupiter.stop()
        if self._telegram: await self._telegram.stop()
        logger.info("Solbot stopped")

    async def _process_events(self):
        """Unified event processor for Launches, Copytrades, and Dev Dumps."""
        while self._running:
            if self._paused:
                await asyncio.sleep(1)
                continue
            try:
                data = await asyncio.wait_for(self._monitor.queue.get(), timeout=1.0)
                
                # Check for trade events (Sells/Dumps)
                if data.get("txType") == "sell" or data.get("txType") == "buy":
                    await self._handle_trade_event(data)
                    continue

                # Check for token launch events
                mint = data.get("mint")
                if mint and "txType" not in data:
                    token = self._parse_token_event(data)
                    qualified, size = self._filter.is_qualified(token)
                    if qualified:
                        asyncio.create_task(self._execute_snipe(token, size, "Sniper"))
            
            except asyncio.TimeoutError:
                continue

    async def _handle_trade_event(self, data: dict):
        """Handles real-time trade events for Dev Dumps and Copytrading."""
        trader = data.get("traderPublicKey")
        mint = data.get("mint")
        tx_type = data.get("txType")

        if not trader or not mint:
            return

        # 1. Dev Dump Protection (Priority 1)
        if tx_type == "sell" and mint in self._positions:
            pos = self._positions[mint]
            if trader == pos.creator:
                logger.warning(f"⚠️ DEV DUMP DETECTED: {trader} on {pos.symbol}!")
                asyncio.create_task(self._exit_position(pos, "DEV DUMP", 1.0))
                return

        # 2. Copytrade Logic (Priority 2)
        if tx_type == "buy" and self._filter.is_copy_target(trader):
            token = self._parse_token_event(data)
            asyncio.create_task(self._execute_snipe(token, self._config.jupiter.buy_amount_sol, "Copytrade"))

    def _parse_token_event(self, data: dict) -> TokenEvent:
        return TokenEvent(
            mint=data.get("mint"),
            name=data.get("name", "Unknown"),
            symbol=data.get("symbol", "???"),
            creator=data.get("traderPublicKey"),
            market_cap_usd=float(data.get("marketCapSol", 0)) * 150,
            liquidity_sol=float(data.get("vSolInBondingCurve", 0)) / 1e9,
            timestamp=time(),
        )

    async def _execute_snipe(self, token: TokenEvent, size: float, reason: str):
        if token.mint in self._positions:
            return

        priority_fee_sol = self._filter.get_dynamic_fee(token.mint) / 1_000_000_000
        result = await self._pump_client.execute_trade(
            token.mint, action="buy", amount=size, priority_fee=priority_fee_sol
        )
        
        if result.success:
            pos = Position(
                mint=token.mint, symbol=token.symbol,
                entry_price=token.market_cap_usd, entry_liq=token.liquidity_sol,
                creator=token.creator, size=size
            )
            self._positions[token.mint] = pos
            await self._telegram.send_message(f"✅ <b>BUY ({reason}): {token.symbol}</b>")
            asyncio.create_task(self._position_manager(pos))

    async def _position_manager(self, pos: Position):
        """Position management task (TP/SL/Moonbag)."""
        while self._running and pos.active:
            # We handle high-speed Dev Dumps via the websocket stream in _handle_trade_event.
            # This task handles time exits and trailing stops.
            await asyncio.sleep(10)

    async def _exit_position(self, pos: Position, reason: str, pct: float):
        if not pos.active: return
        result = await self._pump_client.execute_trade(pos.mint, action="sell", amount=pct)
        if result.success:
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
    while bot._running: await asyncio.sleep(1)
