"""Main bot orchestrator for Solbot with Dev Dump Protection & Copytrade."""

import asyncio
import signal
import os
import sys
import json
from time import time
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, List, Any, Set

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
    tp_targets_hit: List[float] = field(default_factory=list)
    start_time: float = field(default_factory=time)
    highest_price: float = 0.0
    current_price: float = 0.0

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
        self._state_file = "data/state.json"

    def _save_state(self):
        """Persist positions and trades to a JSON file."""
        try:
            os.makedirs(os.path.dirname(self._state_file), exist_ok=True)
            state = {
                "positions": {mint: asdict(pos) for mint, pos in self._positions.items()},
                "trades": [asdict(t) for t in self._trades]
            }
            with open(self._state_file, "w") as f:
                json.dump(state, f, indent=2)
            logger.debug("State persisted successfully")
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def _load_state(self):
        """Load positions and trades from the JSON file."""
        if not os.path.exists(self._state_file):
            return
        try:
            with open(self._state_file, "r") as f:
                state = json.load(f)
            
            # Restore positions
            for mint, data in state.get("positions", {}).items():
                # Check for legacy tp_sold and migrate to tp_targets_hit
                if "tp_sold" in data:
                    tp_sold = data.pop("tp_sold")
                    if tp_sold and not data.get("tp_targets_hit"):
                        data["tp_targets_hit"] = [0.0] # Dummy value to indicate at least one TP hit
                
                self._positions[mint] = Position(**data)
            
            # Restore trades (limited to last 100 for memory)
            raw_trades = state.get("trades", [])
            self._trades = [TradeResult(**t) for t in raw_trades[-100:]]
            
            logger.info(f"Loaded {len(self._positions)} positions and {len(self._trades)} trades from state")
        except Exception as e:
            logger.error(f"Failed to load state: {e}")

    async def _sync_existing_holdings(self):
        """Detect SPL and Token-2022 tokens in wallet with metadata enrichment."""
        logger.info("Scanning wallet for existing holdings (SPL & Token-2022)...")
        try:
            tokens = await self._pump_client.get_all_token_balances()
            for mint, data in tokens.items():
                if mint not in self._positions and data["balance"] > 0:
                    # Enrich with real metadata
                    meta = await self._pump_client.get_token_metadata(mint)
                    symbol = meta.get("symbol", "SYNCED")
                    mcap_sol = float(meta.get("market_cap_sol", 0))
                    price_usd = mcap_sol * 150 # Est price
                    
                    # Estimate "size" in SOL based on current balance and price
                    # This is a rough estimation for UI purposes
                    size_sol = data["balance"] * price_usd / 150 if price_usd > 0 else 0.0

                    logger.info(f"Detected holding: {symbol} ({mint}) | Balance: {data['balance']}")
                    pos = Position(
                        mint=mint,
                        symbol=symbol,
                        entry_price=price_usd, 
                        entry_liq=float(meta.get("liquidity_sol", 0)),
                        creator=meta.get("creator", "unknown"),
                        size=size_sol,
                        active=True
                    )
                    pos.current_price = price_usd
                    pos.highest_price = price_usd
                    self._positions[mint] = pos
            self._save_state()
        except Exception as e:
            logger.error(f"Failed to sync holdings: {e}")

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
        
        # Load persisted state before starting monitors
        self._load_state()
        
        # Sync manual holdings
        await self._sync_existing_holdings()
        
        await self._telegram.send_message("<b>Solbot Sniper (Dev Protection) started!</b>")

        loop = asyncio.get_running_loop()
        self._monitor = PumpFunMonitor(self._config.pumpfun, loop)
        self._monitor.start()

        self._running = True
        asyncio.create_task(self._process_events())
        
        # Resume position managers for loaded/synced active positions
        for pos in self._positions.values():
            if pos.active:
                asyncio.create_task(self._position_manager(pos))

    async def stop(self):
        self._running = False
        self._save_state()
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
        mcap_sol = data.get("marketCapSol")

        if not trader or not mint:
            return

        # Update price feed for positions (including synced ones)
        if mint in self._positions and mcap_sol:
            price_usd = float(mcap_sol) * 150 
            pos = self._positions[mint]
            pos.current_price = price_usd
            
            # For synced positions, we might not have entry_price
            if pos.entry_price == 0:
                pos.entry_price = price_usd # Set first seen price as "entry" for strategy tracking
            
            if price_usd > pos.highest_price:
                pos.highest_price = price_usd
                self._save_state() # Save peaks

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
            asyncio.create_task(self._execute_sninipe(token, self._config.jupiter.buy_amount_sol, "Copytrade"))

    def _parse_token_event(self, data: dict) -> TokenEvent:
        return TokenEvent(
            mint=data.get("mint"),
            name=data.get("name", "Unknown"),
            symbol=data.get("symbol", "???"),
            creator=data.get("traderPublicKey") or data.get("creator"),
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
            self._trades.append(result)
            pos = Position(
                mint=token.mint, symbol=token.symbol,
                entry_price=token.market_cap_usd, entry_liq=token.liquidity_sol,
                creator=token.creator, size=size
            )
            pos.current_price = token.market_cap_usd
            pos.highest_price = token.market_cap_usd
            self._positions[token.mint] = pos
            self._save_state()
            
            await self._telegram.send_message(f"✅ <b>BUY ({reason}): {token.symbol}</b>")
            asyncio.create_task(self._position_manager(pos))

    async def _position_manager(self, pos: Position):
        """Position management task (TP/SL/Moonbag)."""
        strat = self._config.strategy
        while self._running and pos.active:
            if pos.current_price == 0:
                await asyncio.sleep(1)
                continue

            # Absolute Market Cap Take Profit
            if pos.current_price >= self._config.strategy.mcap_tp_target_usd:
                await self._exit_position(pos, f"MCAP TP @ {pos.current_price:.0f}", 1.0)
                return

            # Calculate multipliers
            gain = pos.current_price / pos.entry_price if pos.entry_price > 0 else 1.0
            drawdown = (pos.highest_price - pos.current_price) / pos.highest_price if pos.highest_price > 0 else 0.0

            # 1. Take Profit (TP) Targets (Incremental Sells)
            for tp in strat.tp_targets:
                mult = tp["multiplier"]
                if gain >= mult and mult not in pos.tp_targets_hit:
                    logger.info(f"🎯 TP TARGET HIT: {pos.symbol} at {gain:.2f}x (Target: {mult}x)")
                    await self._exit_position(pos, f"TP {mult}x", tp["sell_pct"])
                    pos.tp_targets_hit.append(mult)
                    self._save_state()
                    # Continue checking other targets in same loop if gain is huge
            
            # 2. Stop Loss (SL)
            if gain <= (1.0 - strat.stop_loss_pct):
                logger.warning(f"🛑 STOP LOSS HIT: {pos.symbol} at {gain:.2f}x")
                await self._exit_position(pos, "STOP LOSS", 1.0)
                break

            # 3. Trailing Stop
            if drawdown >= strat.trailing_stop_pct:
                logger.warning(f"📉 TRAILING STOP HIT: {pos.symbol} at {drawdown*100:.1f}% drawdown")
                await self._exit_position(pos, "TRAILING STOP", 1.0)
                break

            await asyncio.sleep(5)

    async def _exit_position(self, pos: Position, reason: str, pct: float):
        if not pos.active: return
        
        token_balance = await self._pump_client.get_token_balance(pos.mint)
        if token_balance <= 0:
            logger.warning(f"No balance found for {pos.symbol}, marking as inactive.")
            pos.active = False
            if pos.mint in self._positions: del self._positions[pos.mint]
            self._save_state()
            return

        sell_amount = token_balance * pct
        
        result = await self._pump_client.execute_trade(
            pos.mint, 
            action="sell", 
            amount=sell_amount,
            denominated_in_sol=False
        )
        
        if result.success:
            self._trades.append(result)
            if pct >= 0.99: # Close to 100%
                pos.active = False
                if pos.mint in self._positions: del self._positions[pos.mint]
            
            self._save_state()
            await self._telegram.send_message(f"🚨 <b>SELL ({pct*100:.0f}%): {pos.symbol}</b>\nReason: {reason}")
        else:
            logger.error(f"Failed to sell {pos.symbol}: {result.error}")

async def run_bot():
    config = BotConfig()
    bot = Solbot(config)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.stop()))
    await bot.start()
    while bot._running: await asyncio.sleep(1)
