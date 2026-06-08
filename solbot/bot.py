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
from solbot.twitter import TwitterMonitor
from solbot.ai_filter import AIFilter
from solbot.go_monitor import GoMonitor

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
        self._twitter: Optional[TwitterMonitor] = None
        self._running = False
        self._trades: List[TradeResult] = []
        self._positions: Dict[str, Position] = {}
        self._paused = False
        self._state_file = "data/state.json"
        self._ai_enabled = True
        self._ai_min_score = 75
        self._ai_filter = AIFilter()
        self._go_monitor = None

    def _save_state(self):
        """Persist positions, trades, and intelligence to a JSON file."""
        try:
            os.makedirs(os.path.dirname(self._state_file), exist_ok=True)
            state = {
                "positions": {mint: asdict(pos) for mint, pos in self._positions.items()},
                "trades": [asdict(t) for t in self._trades],
                "copy_targets": list(self._filter._copy_targets),
                "wallet_scores": {addr: asdict(score) for addr, score in self._filter._wallet_scores.items()},
                "twitter_handles": list(self._twitter._handles) if self._twitter else [],
                "ai_enabled": self._ai_enabled,
                "ai_min_score": self._ai_min_score
            }
            with open(self._state_file, "w") as f:
                json.dump(state, f, indent=2)
            logger.debug("State persisted successfully")
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def _load_state(self):
        """Load positions, trades, and intelligence from the JSON file."""
        if not os.path.exists(self._state_file):
            return
        try:
            with open(self._state_file, "r") as f:
                state = json.load(f)
            
            # Restore positions
            for mint, data in state.get("positions", {}).items():
                if "tp_sold" in data:
                    data.pop("tp_sold")
                    if not data.get("tp_targets_hit"):
                        data["tp_targets_hit"] = [0.0]
                self._positions[mint] = Position(**data)
            
            # Restore intelligence
            if self._filter:
                self._filter._copy_targets = set(state.get("copy_targets", []))
                for addr, score_data in state.get("wallet_scores", {}).items():
                    from solbot.filters import WalletScore
                    self._filter._wallet_scores[addr] = WalletScore(**score_data)
            
            # Restore Twitter handles
            if self._twitter:
                for handle in state.get("twitter_handles", []):
                    self._twitter.add_handle(handle)
            
            # Restore trades
            raw_trades = state.get("trades", [])
            self._trades = [TradeResult(**t) for t in raw_trades[-100:]]
            
            # Restore AI settings
            self._ai_enabled = state.get("ai_enabled", True)
            self._ai_min_score = state.get("ai_min_score", 75)
            
            logger.info(f"Loaded {len(self._positions)} positions and {len(self._filter._copy_targets)} whales")
        except Exception as e:
            logger.error(f"Failed to load state: {e}")

    async def start(self):
        setup_logger(self._config.logging)
        logger.info("SOLBOT DEGEN SNIPER STARTING")

        self._wallet = Wallet(self._config.solana)
        self._filter = TokenFilter(self._config)
        self._pump_client = PumpFunClient(self._config, self._wallet)
        await self._pump_client.start()
        self._jupiter = JupiterClient(self._config.jupiter, self._wallet)
        await self._jupiter.start()
        self._telegram = TelegramManager(self._config.telegram)
        await self._telegram.start(self)
        
        # Twitter Monitor Initialization
        self._twitter = TwitterMonitor(self._config, self)
        await self._twitter.start()
        
        self._load_state()
        await self._sync_existing_holdings()
        
        await self._telegram.send_message("<b>Solbot Sniper (Twitter Tracking) started!</b>")

        loop = asyncio.get_running_loop()
        self._monitor = PumpFunMonitor(self._config.pumpfun, loop)
        self._monitor.start()

        # Pump.fun GO Monitor
        self._go_monitor = GoMonitor(self)
        asyncio.create_task(self._go_monitor.start_monitoring())

        self._running = True
        asyncio.create_task(self._process_events())
        
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
        if self._twitter: await self._twitter.stop()
        if self._go_monitor: await self._go_monitor.stop()
        logger.info("Solbot stopped")

    async def _process_events(self):
        while self._running:
            if self._paused:
                await asyncio.sleep(1)
                continue
            try:
                data = await asyncio.wait_for(self._monitor.queue.get(), timeout=1.0)
                if data.get("txType") in ["sell", "buy"]:
                    await self._handle_trade_event(data)
                elif data.get("mint") and "txType" not in data:
                    token = self._parse_token_event(data)
                    qualified, size = self._filter.is_qualified(token)
                    if qualified:
                        if self._ai_enabled:
                            token_data = {
                                'mint': token.mint, 'symbol': token.symbol, 'name': token.name, 'creator': token.creator
                            }
                            score = await self._ai_filter.score_token(token_data)
                            if score < self._ai_min_score:
                                logger.warning(f"AI score {score} < {self._ai_min_score}, skipping {token.symbol}")
                                continue
                        asyncio.create_task(self._execute_snipe(token, size, "Sniper"))
            except asyncio.TimeoutError:
                continue

    async def _handle_trade_event(self, data: dict):
        trader = data.get("traderPublicKey")
        mint = data.get("mint")
        tx_type = data.get("txType")
        mcap_sol = data.get("marketCapSol")
        if not trader or not mint: return

        if mint in self._positions and mcap_sol:
            price_usd = float(mcap_sol) * self._telegram._sol_price
            pos = self._positions[mint]
            pos.current_price = price_usd
            if price_usd > pos.highest_price:
                pos.highest_price = price_usd
                self._save_state()

        if tx_type == "sell" and mint in self._positions:
            pos = self._positions[mint]
            if trader == pos.creator:
                asyncio.create_task(self._exit_position(pos, "DEV DUMP", 1.0))

        if tx_type == "buy" and self._filter.is_copy_target(trader):
            token = self._parse_token_event(data)
            alias = self._filter._wallet_scores.get(trader, {}).alias or trader[:8]
            asyncio.create_task(self._execute_snipe(token, self._config.jupiter.buy_amount_sol, f"Copytrade [{alias}]"))

    def _parse_token_event(self, data: dict) -> TokenEvent:
        return TokenEvent(
            mint=data.get("mint"),
            name=data.get("name", "Unknown"),
            symbol=data.get("symbol", "???"),
            creator=data.get("traderPublicKey") or data.get("creator"),
            market_cap_usd=float(data.get("marketCapSol", 0)) * self._telegram._sol_price,
            liquidity_sol=float(data.get("vSolInBondingCurve", 0)) / 1e9,
            timestamp=time(),
        )

    async def _execute_snipe(self, token: TokenEvent, size: float, reason: str):
        if token.mint in self._positions: return
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
        strat = self._config.strategy
        while self._running and pos.active:
            if pos.current_price == 0:
                await asyncio.sleep(1)
                continue
            if hasattr(self._config.strategy, "mcap_tp_target_usd") and pos.current_price >= self._config.strategy.mcap_tp_target_usd:
                await self._exit_position(pos, f"MCAP TP @ {pos.current_price:.0f}", 1.0)
                return
            gain = pos.current_price / pos.entry_price if pos.entry_price > 0 else 1.0
            drawdown = (pos.highest_price - pos.current_price) / pos.highest_price if pos.highest_price > 0 else 0.0
            for tp in strat.tp_targets:
                mult = tp["multiplier"]
                if gain >= mult and mult not in pos.tp_targets_hit:
                    await self._exit_position(pos, f"TP {mult}x", tp["sell_pct"])
                    pos.tp_targets_hit.append(mult)
                    self._save_state()
            if gain <= (1.0 - strat.stop_loss_pct):
                await self._exit_position(pos, "STOP LOSS", 1.0)
                break
            if drawdown >= strat.trailing_stop_pct:
                await self._exit_position(pos, "TRAILING STOP", 1.0)
                break
            await asyncio.sleep(5)

    async def _exit_position(self, pos: Position, reason: str, pct: float):
        if not pos.active: return
        token_balance = await self._pump_client.get_token_balance(pos.mint)
        if token_balance <= 0:
            pos.active = False
            if pos.mint in self._positions: del self._positions[pos.mint]
            self._save_state()
            return
        sell_amount = token_balance * pct
        result = await self._pump_client.execute_trade(pos.mint, action="sell", amount=sell_amount, denominated_in_sol=False)
        if result.success:
            self._trades.append(result)
            if pct >= 0.99:
                pos.active = False
                if pos.mint in self._positions: del self._positions[pos.mint]
            self._save_state()
            await self._telegram.send_message(f"🚨 <b>SELL ({pct*100:.0f}%): {pos.symbol}</b>\nReason: {reason}")

    async def _sync_existing_holdings(self):
        try:
            tokens = await self._pump_client.get_all_token_balances()
            for mint, data in tokens.items():
                if mint not in self._positions and data["balance"] > 0:
                    meta = await self._pump_client.get_token_metadata(mint)
                    symbol = meta.get("symbol", "SYNCED")
                    price_usd = float(meta.get("market_cap_sol", 0)) * self._telegram._sol_price
                    pos = Position(
                        mint=mint, symbol=symbol, entry_price=price_usd,
                        entry_liq=float(meta.get("liquidity_sol", 0)),
                        creator=meta.get("creator", "unknown"),
                        size=0.0, active=True
                    )
                    pos.current_price = price_usd
                    pos.highest_price = price_usd
                    self._positions[mint] = pos
            self._save_state()
        except Exception as e:
            logger.error(f"Failed to sync holdings: {e}")

async def run_bot():
    config = BotConfig()
    bot = Solbot(config)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.stop()))
    await bot.start()
    while bot._running: await asyncio.sleep(1)
