"""Main bot orchestrator for Solbot with Coordinated KOL Tracking."""

import asyncio
import signal
import os
import sys
import json
from time import time
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, List, Any, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from solbot.telegram import TelegramManager

from solbot.config import BotConfig, BotMode
from solbot.filter_profiles import get_profile, default_profile_name
from solbot.paste_trade import PasteTradeClient
from solbot.capital_strategy import (
    RecycleSettings,
    active_position_count,
    dynamic_max_positions,
    pick_rotation_candidate,
    pick_rotation_candidates,
    should_block_buy,
)
from solbot.stats_tracker import StatsTracker
from solbot.filters import TokenFilter
from solbot.jupiter import JupiterClient
from solbot.logger import get_logger, setup_logger
from solbot.models import TokenEvent, TradeResult
from solbot.pumpfun import PumpFunMonitor
from solbot.pumpfun_client import PumpFunClient
from solbot.wallet import Wallet
from solbot.twitter import TwitterMonitor
from solbot.ai_filter import AIFilter
from solbot.go_monitor import GoMonitor
from solbot.raydium import RaydiumClient
from solbot.dexscreener import DexScreenerClient
from solbot.monitor_scraper import Monitor985Scraper
from solbot.tungscreener import TungscreenerScraper
from solbot.kol_tracker import KOLTracker
from solbot.pump_movers import PumpMovers
from solbot.geckoterminal import GeckoTerminalClient
from solbot.twitter_agents import TwitterAgentMonitor
from solbot.core.network import NetworkManager
from solbot.cluster_mapper import ClusterMapper
from solbot.ai_tuner import AITuner
from solbot.agi_prebuy_filter import AGIPreBuyFilter
from solbot.observability import ObservabilityHub
from solbot.ml.inference import InferenceEngine
from solbot.agi_brain import AGIBrain
from solbot.hummingbot_gateway import HummingbotGatewayClient
from solbot.hummingbot_pmm import HummingbotPMMManager
from solbot.missed_runner_engine import MissedRunnerEngine
from solbot.portfolio_guard import PortfolioGuard
from solbot.risk_sizer import DynamicRiskSizer

def _format_tokens(amount: float) -> str:
    if amount >= 1_000_000_000:
        return f"{amount / 1_000_000_000:.2f}B"
    if amount >= 1_000_000:
        return f"{amount / 1_000_000:.2f}M"
    if amount >= 1_000:
        return f"{amount / 1_000:.2f}K"
    return f"{amount:.2f}"

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
    is_mayhem: bool = False
    position_number: int = 0
    remaining_fraction: float = 1.0

class Solbot:
    """High-speed DEGEN Sniper with Dev Dump Protection & KOL Coordinated Trading."""

    def __init__(self, config: BotConfig, db=None, event_store=None, event_bus=None, telemetry=None, creator_genome=None, wallet_graph=None, feature_store=None, rpc_pool=None):
        self._config = config
        self._event_store = event_store
        self._event_bus = event_bus
        self._telemetry = telemetry
        self._obs: Optional[ObservabilityHub] = None
        self._inference = InferenceEngine()
        self._creator_genome = creator_genome
        self._wallet_graph = wallet_graph
        self._feature_store = feature_store
        self._rpc_pool = rpc_pool
        self._wallet: Optional[Wallet] = None
        self._monitor: Optional[PumpFunMonitor] = None
        self._pump_client: Optional[PumpFunClient] = None
        self._jupiter: Optional[JupiterClient] = None
        self._telegram: Optional["TelegramManager"] = None
        self._filter: Optional[TokenFilter] = None
        self._twitter: Optional[TwitterMonitor] = None
        self._running = False
        self._trades: List[TradeResult] = []
        self._positions: Dict[str, Position] = {}
        self._active_buys: Set[str] = set()
        self._processed_mints: Set[str] = set()
        self._paused = False
        self._state_file = "data/state.json"
        self._ai_enabled = True
        self._ai_min_score = 75
        self._autobuy_enabled = True
        self._autorunner_enabled = False
        self._autorunner_amount = 0.01
        self._ai_filter = AIFilter(config)
        self._ai_filter._bot = self
        self._go_monitor = None
        self._raydium = None
        self._dexscreener = DexScreenerClient()
        self._monitor_scraper = None
        self._tungscreener = None
        self._blacklisted_wallets: Set[str] = set()
        self._kol_tracker = KOLTracker()
        self._pump_movers = PumpMovers(self)
        self._gecko = GeckoTerminalClient()
        self._agent_monitor = TwitterAgentMonitor(self)
        self._network_manager = NetworkManager(config.proxy_list_path)
        from solbot.db import Database
        from solbot.engines.risk_manager import RiskManager
        self._db = db or Database()
        self._owns_db = db is None
        self._brain = AGIBrain(self._db, self._config)
        self._risk_manager = RiskManager()
        self._position_manager_tasks: Dict[str, asyncio.Task] = {}
        # Missed entry tracker: mint -> {symbol, alert_price_usd, alert_time, notified_milestones}
        self._missed_runners: Dict[str, Dict] = {}
        # Daily runners tracking state
        self._daily_runner_buys: Dict[str, Dict] = {}
        self._daily_runners: Dict[str, Dict] = {}
        # KOL Alpha & Sentiment Aggregator State
        self._kol_mentions: Dict[str, Dict] = {}
        self._kol_threshold = 3
        # Dynamic priority fee & Jito tip auto-scaling state
        self._congestion_level = "low"
        self._dynamic_priority_fee = 0.00001
        self._dynamic_jito_tip = 0.001

        # Dynamic pre-buy filters config (with settings button adjustment capability)
        self._min_liquidity_sol = 2.0
        self._min_mcap_sol = 2.0
        self._max_top10_pct = 40.0
        self._max_creator_pct = 10.0
        self._max_largest_holder_pct = 15.0
        self._cabal_block_enabled = True

        # Stalkchain / KOLscan integrations controller
        from solbot.kols_controller import KOLsController
        self._kols_controller = KOLsController(self)
        
        # AI Tuner & Cluster Mapper
        self._cluster_mapper = ClusterMapper(self)
        self._ai_tuner = AITuner(self)
        self._agi_prebuy_filter = AGIPreBuyFilter(self)
        self._autotune_poller = None
        
        # AGI Watch Queue
        self._watch_queue: Dict[str, Dict] = {}
        self._pending_evaluations: Set[str] = set()
        self._filter_profile_name = default_profile_name()
        self._stats = StatsTracker()
        self._position_counter = 0
        self._paste_trade = PasteTradeClient(
            key=self._config.paste_trade.api_key,
            url=self._config.paste_trade.api_url,
            handle=self._config.paste_trade.handle,
        )
        self._hummingbot_gateway = HummingbotGatewayClient(self._config.hummingbot)
        self._hummingbot_pmm = HummingbotPMMManager(self, self._config.hummingbot)
        self._missed_runner_engine = MissedRunnerEngine(self)
        self._portfolio_guard = PortfolioGuard()
        self._risk_sizer = DynamicRiskSizer()

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
                "ai_min_score": self._ai_min_score,
                "autobuy_enabled": self._autobuy_enabled,
                "autorunner_enabled": self._autorunner_enabled,
                "autorunner_amount": self._autorunner_amount,
                "blacklisted_wallets": list(self._blacklisted_wallets),
                "kol_threshold": self._kol_threshold,
                "filter_profile": self._filter_profile_name,
                "paper_mode": getattr(self._telegram, "_paper_mode", False) if self._telegram else False,
                "kill_switch": getattr(self._telegram, "_kill_switch", False) if self._telegram else False,
                "min_liquidity_sol": self._min_liquidity_sol,
                "min_mcap_sol": self._min_mcap_sol,
                "max_top10_pct": self._max_top10_pct,
                "max_creator_pct": self._max_creator_pct,
                "max_largest_holder_pct": self._max_largest_holder_pct,
                "cabal_block_enabled": self._cabal_block_enabled,
                "config_overrides": {
                    "buy_amount_sol": self._config.jupiter.buy_amount_sol,
                    "trailing_stop_pct": getattr(self._config.strategy, "trailing_stop_pct", 0.20),
                    "slippage_bps": self._config.jupiter.slippage_bps
                }
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
                valid_fields = Position.__dataclass_fields__.keys()
                filtered_data = {k: v for k, v in data.items() if k in valid_fields}
                self._positions[mint] = Position(**filtered_data)
            
            # Restore intelligence
            if self._filter:
                self._filter._copy_targets = set(state.get("copy_targets", []))
                for addr, score_data in state.get("wallet_scores", {}).items():
                    from solbot.filters import WalletScore
                    valid_keys = WalletScore.__dataclass_fields__.keys()
                    filtered_data = {k: v for k, v in score_data.items() if k in valid_keys}
                    score_obj = WalletScore(**filtered_data)
                    self._filter._wallet_scores[addr] = score_obj
                    
                    # Also load into KOL Tracker if it matches KOL labels
                    alias = getattr(score_obj, 'alias', '') or ''
                    if any(term in alias for term in ["KOL", "VineWallet", "SmartWallet"]):
                        self._kol_tracker.add_wallet(addr, alias)
            
            # Restore Twitter handles
            if self._twitter:
                for handle in state.get("twitter_handles", []):
                    self._twitter.add_handle(handle)
            
            # Restore trades
            raw_trades = state.get("trades", [])
            valid_trade_fields = TradeResult.__dataclass_fields__.keys()
            self._trades = [
                TradeResult(**{k: v for k, v in t.items() if k in valid_trade_fields})
                for t in raw_trades[-100:]
            ]
            
            # Restore AI settings
            self._ai_enabled = state.get("ai_enabled", True)
            self._ai_min_score = state.get("ai_min_score", 75)
            self._autobuy_enabled = state.get("autobuy_enabled", False)
            self._autorunner_enabled = state.get("autorunner_enabled", False)
            self._autorunner_amount = state.get("autorunner_amount", 0.01)
            self._kol_threshold = state.get("kol_threshold", 3)
            self._filter_profile_name = state.get("filter_profile", default_profile_name())
            if self._telegram:
                self._telegram._paper_mode = bool(state.get("paper_mode", False))
                self._telegram._kill_switch = bool(state.get("kill_switch", False))
            self._min_liquidity_sol = state.get("min_liquidity_sol", 2.0)
            self._min_mcap_sol = state.get("min_mcap_sol", 2.0)
            self._max_top10_pct = state.get("max_top10_pct", 40.0)
            self._max_creator_pct = state.get("max_creator_pct", 10.0)
            self._max_largest_holder_pct = state.get("max_largest_holder_pct", 15.0)
            self._cabal_block_enabled = state.get("cabal_block_enabled", True)
            
            # Restore config overrides
            if "config_overrides" in state:
                overrides = state["config_overrides"]
                if "buy_amount_sol" in overrides:
                    object.__setattr__(self._config.jupiter, "buy_amount_sol", overrides["buy_amount_sol"])
                if "trailing_stop_pct" in overrides:
                    object.__setattr__(self._config.strategy, "trailing_stop_pct", overrides["trailing_stop_pct"])
                if "slippage_bps" in overrides:
                    object.__setattr__(self._config.jupiter, "slippage_bps", overrides["slippage_bps"])
            
            # Restore Blacklist
            self._blacklisted_wallets = set(state.get("blacklisted_wallets", []))
            
            logger.info(f"Loaded {len(self._positions)} positions, {len(self._filter._copy_targets)} whales, and {len(self._kol_tracker.wallets)} KOLs")
        except Exception as e:
            logger.error(f"Failed to load state: {e}")

    async def start(self):
        setup_logger(self._config.logging)
        logger.info("SOLBOT DEGEN SNIPER STARTING")

        if self._owns_db:
            await self._db.connect()
        self._wallet = Wallet(
            self._config.solana,
            allow_ephemeral=getattr(self._config.strategy, "dry_run", False),
        )
        self._filter = TokenFilter(self._config)
        self._pump_client = PumpFunClient(self._config, self._wallet)
        self._pump_client._network_manager = self._network_manager
        if self._rpc_pool:
            self._pump_client._rpc_pool = self._rpc_pool
        self._obs = ObservabilityHub(
            self._db,
            telemetry=self._telemetry,
            feature_store=self._feature_store,
            event_bus=self._event_bus,
            risk_manager=self._risk_manager,
        )
        self._pump_client._observability = self._obs
        await self._pump_client.start()
        try:
            bal = await self._pump_client.get_sol_balance()
            self._risk_manager.bankroll_sol = max(1.0, bal)
            logger.info(f"Initialized RiskManager bankroll to {bal:.4f} SOL")
        except Exception as e:
            logger.error(f"Failed to fetch initial wallet balance for RiskManager: {e}")
        self._jupiter = JupiterClient(self._config.jupiter, self._wallet, self._config.solana)
        self._jupiter._rpc_pool = self._rpc_pool
        self._jupiter._observability = self._obs
        await self._jupiter.start()
        await self._kols_controller.start()
        
        # Inject bot reference into WalletGraphEngine if available
        if self._wallet_graph:
            self._wallet_graph.bot = self
            
        from solbot.telegram import TelegramManager
        self._telegram = TelegramManager(self._config.telegram, self)
        await self._telegram.start(self)
        
        # New Module Starts
        await self._gecko.start()
        await self._agent_monitor.start()
        
        # Twitter Monitor Initialization
        self._twitter = TwitterMonitor(self._config, self)
        await self._twitter.start()
        
        self._load_state()
        self._filter.set_skip_callback(self._stats.record_filter_skip)
        self.ensure_live_trading()
        self._processed_mints.update(self._positions.keys())
        try:
            rows = await self._db._execute_read(
                "SELECT mint, status, entry_price, size, timestamp, reason FROM positions"
            )
            db_statuses = {}
            db_rows = {}
            for r in rows:
                self._processed_mints.add(r['mint'])
                db_statuses[r['mint']] = r['status']
                db_rows[r['mint']] = r
            logger.info(f"Loaded {len(self._processed_mints)} historically traded mints into memory.")
            reconciled_count = 0
            for mint, pos in list(self._positions.items()):
                status = db_statuses.get(mint)
                if status == 'closed':
                    pos.active = False
                    self._positions.pop(mint, None)
                    reconciled_count += 1

            restored_count = self._restore_orphaned_open_positions(db_statuses, db_rows)

            if reconciled_count > 0 or restored_count > 0:
                logger.info(f"Reconciled {reconciled_count} positions against database (marked closed).")
                self._save_state()
            self._position_counter = len(self._processed_mints)
        except Exception as e:
            logger.error(f"Failed to load historically traded mints: {e}")
            self._position_counter = max(len(self._processed_mints), len(self._positions))
            
        logger.info("Running on-chain position reconcile...")
        await self._reconcile_positions_with_chain()
        await self._sync_existing_holdings()
        profile = self._filter.profile if self._filter else get_profile(self._filter_profile_name)
        logger.info("Enforcing startup position cap...")
        await self._enforce_position_cap_on_startup(profile)

        for pos in self._positions.values():
            if pos.active:
                if getattr(self._pump_client, "paper_enabled", False) and pos.mint not in self._pump_client._paper_tokens:
                    qty = pos.size * getattr(pos, "remaining_fraction", 1.0) * self._pump_client.PAPER_TOKENS_PER_SOL
                    self._pump_client._paper_tokens[pos.mint] = qty
                    self._pump_client._paper_basis[pos.mint] = pos.size * getattr(pos, "remaining_fraction", 1.0)
                    if pos.entry_price > 0:
                        self._pump_client.set_paper_mark(pos.mint, pos.current_price / pos.entry_price)
                self._spawn_position_manager(pos)
                
        # Start watch queue manager
        asyncio.create_task(self._watch_queue_manager())
        
        # Pre-provision paste.trade key if needed
        if hasattr(self, '_paste_trade') and self._paste_trade:
            asyncio.create_task(self._paste_trade.ensure_key())
        
        await self._telegram.send_message("<b>Solbot Sniper (Coordinated KOL Tracking) started!</b>")

        loop = asyncio.get_running_loop()
        self._monitor = PumpFunMonitor(self._config.pumpfun, loop)
        self._monitor.start()

        # Pump.fun GO Monitor
        self._go_monitor = GoMonitor(self)
        self._raydium = RaydiumClient(self)
        asyncio.create_task(self._raydium.start())
        asyncio.create_task(self._go_monitor.start_monitoring())

        # 985monitor Scraper
        self._monitor_scraper = Monitor985Scraper(self)
        asyncio.create_task(self._monitor_scraper.start_monitoring())

        # Tungscreener Scraper
        self._tungscreener = TungscreenerScraper(self)
        asyncio.create_task(self._tungscreener.start_monitoring())

        # Pump.fun Movers Monitor
        asyncio.create_task(self._pump_movers.start_monitoring())

        self._running = True
        asyncio.create_task(self._process_events())
        self._brain_tracker = asyncio.create_task(self._brain_tracker_loop())
        self._wallet_scanner = asyncio.create_task(self._daily_wallet_scanner_loop())
        self._sentiment_adapter = asyncio.create_task(self._market_sentiment_adapter_loop())
        self._missed_tracker = asyncio.create_task(self._missed_entry_tracker_loop())
        self._congestion_poller = asyncio.create_task(self._poll_network_congestion())
        self._autotune_poller = asyncio.create_task(self._ai_autotune_loop())
        asyncio.create_task(self._component_heartbeat_loop())
        if self._config.hummingbot.enabled:
            await self._hummingbot_pmm.start()

    def _sentiment_for_mint(self, mint: str) -> str:
        info = self._kol_mentions.get(mint)
        if not info:
            return ""
        snippets = []
        for mention in info.get("mentions", [])[-5:]:
            text = (mention.get("text") or "").strip()
            source = mention.get("source", "unknown")
            if text:
                snippets.append(f"[{source}] {text[:280]}")
        return "\n".join(snippets)

    async def _component_heartbeat_loop(self):
        while self._running:
            try:
                await self._risk_manager.update_component_heartbeat("pump_ws")
                await self._risk_manager.update_component_heartbeat("telegram")
                await self._risk_manager.update_component_heartbeat("rpc_pool")
            except Exception as exc:
                logger.debug("Heartbeat error: %s", exc)
            await asyncio.sleep(10)

    def _restore_orphaned_open_positions(self, db_statuses: Dict[str, str], db_rows: Dict[str, Any]) -> int:
        """Bring back open positions the database knows about but state.json lost.

        state.json is not the only record of an open bag. If it is truncated, lost,
        or written during a crash, any position missing from it never gets a
        position manager, so no stop-loss, take-profit or trailing stop can ever
        evaluate it and the bag can never be exited. The on-chain reconcile that
        runs straight after this drops anything no longer actually held.
        """
        restored = 0
        for mint, status in db_statuses.items():
            if status != 'open' or mint in self._positions:
                continue
            row = db_rows.get(mint)
            if row is None:
                continue
            try:
                self._positions[mint] = Position(
                    mint=mint,
                    symbol=mint[:8],
                    entry_price=float(row['entry_price'] or 0.0),
                    entry_liq=0.0,
                    creator="",
                    size=float(row['size'] or 0.0),
                    start_time=float(row['timestamp'] or time()),
                )
                restored += 1
            except Exception as e:
                logger.error(f"Could not restore orphaned position {mint[:8]}: {e}")
        if restored:
            logger.warning(
                f"Restored {restored} open positions present in the database but missing from "
                f"{self._state_file} — these had no position manager and could not be exited."
            )
        return restored

    def _spawn_position_manager(self, pos: Position):
        existing = self._position_manager_tasks.get(pos.mint)
        if existing and not existing.done():
            return
        self._position_manager_tasks[pos.mint] = asyncio.create_task(self._position_manager(pos))

    def _trading_blocked(self) -> Optional[str]:
        if self._telegram and getattr(self._telegram, "_kill_switch", False):
            return "kill switch active"
        if self._telegram and getattr(self._telegram, "_paper_mode", False):
            is_dry = bool(getattr(self._config.strategy, "dry_run", False) or (self._pump_client and self._pump_client.paper_enabled))
            if not is_dry:
                return "paper mode enabled"
        if self._paused:
            return "bot paused"
        if self._risk_manager and self._risk_manager.state.kill_switch_active:
            return "risk manager kill switch active"
        return None

    def _profile_recycle_settings(self, profile) -> RecycleSettings:
        return RecycleSettings(
            enabled=profile.recycle_mode,
            min_wallet_sol_reserve=profile.min_wallet_sol_reserve,
            tp1_multiplier=profile.tp1_multiplier,
            tp1_sell_pct=profile.tp1_sell_pct,
            tp2_multiplier=profile.tp2_multiplier,
            tp2_sell_pct=profile.tp2_sell_pct,
            stop_loss_pct=profile.stop_loss_pct,
            stale_exit_minutes=profile.stale_exit_minutes,
            stale_min_gain=profile.stale_min_gain,
            max_hold_minutes=profile.max_hold_minutes,
            trailing_activate_gain=profile.trailing_activate_gain,
            use_dynamic_position_cap=profile.use_dynamic_position_cap,
            max_positions_cap=profile.max_positions_cap,
        )

    async def _effective_max_positions(self, profile) -> int:
        if not profile.use_dynamic_position_cap:
            return getattr(self._config.strategy, "max_active_positions", 100)
        if not self._pump_client:
            return profile.max_positions_cap
        balance = await self._pump_client.get_sol_balance()
        return dynamic_max_positions(
            balance,
            profile.buy_amount_sol,
            profile.min_wallet_sol_reserve,
            profile.max_positions_cap,
        )

    async def _reject_mayhem_token(
        self, mint: str, symbol: str, raw_hint: Optional[dict] = None,
    ) -> bool:
        """Return True if token is mayhem (blocked)."""
        if not self._pump_client:
            return False
        try:
            if await self._pump_client.is_mayhem_token(mint, hint=raw_hint):
                self._stats.bump("skip_mayhem")
                logger.warning(
                    "SKIPPING %s (%s): Mayhem Mode — unsellable on pump.fun",
                    symbol, mint,
                )
                return True
        except Exception as exc:
            logger.debug("Mayhem check failed for %s: %s", mint, exc)
        return False

    async def _ensure_buy_capital(self, profile, needed_sol: float) -> tuple[bool, Optional[str]]:
        if not self._pump_client:
            return True, None
        if getattr(self._pump_client, "paper_enabled", False):
            paper_bal = getattr(self._pump_client, "_paper_sol", 5.0)
            if paper_bal < needed_sol:
                return False, f"Paper balance insufficient: {paper_bal:.4f} < {needed_sol:.4f} SOL"
            return True, None
        settings = self._profile_recycle_settings(profile)
        balance = await self._pump_client.get_sol_balance()
        block = should_block_buy(balance, needed_sol, settings.min_wallet_sol_reserve)
        if not block:
            return True, None
        if not settings.enabled:
            return False, block
        exclude: Set[str] = set()
        for _ in range(8):
            balance = await self._pump_client.get_sol_balance()
            block = should_block_buy(balance, needed_sol, settings.min_wallet_sol_reserve)
            if not block:
                return True, None
            candidates = pick_rotation_candidates(
                self._positions, time(), settings, exclude_mints=exclude, aggressive=True,
            )
            if not candidates:
                break
            candidate = candidates[0]
            exclude.add(candidate.mint)
            if getattr(candidate, "is_mayhem", False):
                candidate.active = False
                self._positions.pop(candidate.mint, None)
                self._stats.bump("ghosts_purged")
                continue
            token_bal = await self._pump_client.get_token_balance(candidate.mint)
            if token_bal <= 0:
                candidate.active = False
                self._positions.pop(candidate.mint, None)
                self._stats.bump("ghosts_purged")
                continue
            self._stats.bump("capital_rotations")
            logger.info(
                "Rotating %s to free SOL for snipe (wallet=%.4f)",
                candidate.symbol, balance,
            )
            await self._exit_position(candidate, "CAPITAL ROTATION (free SOL for new snipe)", 1.0)
            await asyncio.sleep(1.0)
        balance = await self._pump_client.get_sol_balance()
        block = should_block_buy(balance, needed_sol, settings.min_wallet_sol_reserve)
        if block:
            return False, f"{block} after rotation"
        return True, None

    async def _log_trade_event(self, event_type: str, payload: Dict[str, Any]):
        if not self._obs:
            return
        await self._obs.record_trade(
            event_type,
            payload.get("mint", ""),
            symbol=payload.get("symbol", ""),
            size=float(payload.get("size", 0.0) or 0.0),
            success=bool(payload.get("success")),
            tx_signature=payload.get("tx"),
            error=payload.get("error"),
            reason=payload.get("reason"),
            latency_ms=float(payload.get("latency_ms", 0.0) or 0.0),
        )

    async def _reconcile_positions_with_chain(self):
        """Drop tracked positions that have no on-chain token balance."""
        if not self._pump_client:
            return
        try:
            on_chain = await self._pump_client.get_all_token_balances()
        except Exception as exc:
            logger.error("On-chain position reconcile failed: %s", exc)
            return

        purged = []
        for mint, pos in list(self._positions.items()):
            if not pos.active:
                continue
            chain = on_chain.get(mint, {})
            balance = float(chain.get("balance", 0) or 0)
            # Token-2022 on its own is not a problem — pump.fun mints new tokens
            # under it. Only specific extensions actually block a sell.
            block_reason = None
            if chain.get("program") == "Token-2022":
                block_reason = await self._pump_client.token_2022_block_reason(mint)
            if block_reason:
                pos.is_mayhem = True
                pos.active = False
                self._positions.pop(mint, None)
                purged.append(mint)
                logger.warning(
                    "Untracking mayhem bag %s (%s) — %s",
                    pos.symbol, mint[:8], block_reason,
                )
                asyncio.create_task(self._db.update_position_pnl(mint, 0.0, "closed"))
            elif balance <= 0:
                pos.active = False
                self._positions.pop(mint, None)
                purged.append(mint)
                asyncio.create_task(self._db.update_position_pnl(mint, 0.0, "closed"))
            elif pos.size <= 0:
                pos.size = balance

        inactive = [m for m, p in self._positions.items() if not p.active]
        for mint in inactive:
            self._positions.pop(mint, None)

        if purged:
            self._stats.bump("ghosts_purged", len(purged))
            logger.info(
                "Purged %s ghost positions (no on-chain balance). Holdings on-chain: %s",
                len(purged), len(on_chain),
            )
            self._save_state()

    async def _rotate_until_under_cap(
        self,
        profile,
        target: int,
        reason: str,
        exclude_mint: Optional[str] = None,
        aggressive: bool = False,
    ) -> int:
        if not profile.recycle_mode or not self._pump_client:
            return 0
        settings = self._profile_recycle_settings(profile)
        exclude: Set[str] = {exclude_mint} if exclude_mint else set()
        rotated = 0
        max_attempts = min(30, max(5, active_position_count(self._positions) - target + 3))

        for _ in range(max_attempts):
            if active_position_count(self._positions) <= target:
                break
            candidates = pick_rotation_candidates(
                self._positions, time(), settings,
                exclude_mints=exclude, aggressive=aggressive,
            )
            if not candidates:
                break
            candidate = candidates[0]
            exclude.add(candidate.mint)
            balance = await self._pump_client.get_token_balance(candidate.mint)
            if getattr(candidate, "is_mayhem", False) or balance <= 0:
                candidate.active = False
                self._positions.pop(candidate.mint, None)
                self._stats.bump("ghosts_purged")
                rotated += 1
                continue
            self._stats.bump("capital_rotations")
            logger.info(
                "Rotating %s (%s) — %s [%s/%s slots]",
                candidate.symbol, candidate.mint[:8], reason,
                active_position_count(self._positions), target,
            )
            await self._exit_position(candidate, reason, 1.0)
            await asyncio.sleep(0.8)
            rotated += 1
        return rotated

    async def _enforce_position_cap_on_startup(self, profile) -> None:
        target = await self._effective_max_positions(profile)
        active = active_position_count(self._positions)
        if active <= target:
            logger.info("Position cap OK: %s/%s active", active, target)
            return
        logger.warning(
            "Startup position cap exceeded (%s/%s); running multi-rotation cleanup",
            active, target,
        )
        freed = await self._rotate_until_under_cap(
            profile, target, "STARTUP CAP CLEANUP", aggressive=True,
        )
        remaining = active_position_count(self._positions)
        logger.info(
            "Startup cap cleanup done: rotated/purged %s, remaining %s/%s",
            freed, remaining, target,
        )
        self._save_state()

    async def stop(self):
        self._running = False
        self._save_state()
        if hasattr(self, '_paste_trade') and self._paste_trade:
            await self._paste_trade.close()
        if self._autotune_poller:
            self._autotune_poller.cancel()
        await self._kols_controller.stop()
        if self._monitor: self._monitor.stop()
        if self._pump_client: await self._pump_client.stop()
        if self._jupiter: await self._jupiter.stop()
        if self._telegram: await self._telegram.stop()
        if self._twitter: await self._twitter.stop()
        if self._gecko: await self._gecko.stop()
        if self._agent_monitor: await self._agent_monitor.stop()
        if self._go_monitor: await self._go_monitor.stop()
        if self._raydium: await self._raydium.stop()
        if self._monitor_scraper: await self._monitor_scraper.stop()
        if self._tungscreener: await self._tungscreener.stop()
        if self._pump_movers: await self._pump_movers.stop()
        if hasattr(self, '_brain_tracker') and self._brain_tracker:
            self._brain_tracker.cancel()
        if hasattr(self, '_wallet_scanner') and self._wallet_scanner:
            self._wallet_scanner.cancel()
        if hasattr(self, '_sentiment_adapter') and self._sentiment_adapter:
            self._sentiment_adapter.cancel()
        if hasattr(self, '_missed_tracker') and self._missed_tracker:
            self._missed_tracker.cancel()
        logger.info("Solbot stopped")

    def is_blacklisted(self, address: str) -> bool:
        """Check if an address is blacklisted."""
        return address in self._blacklisted_wallets

    def apply_risk_preset(self, preset: str):
        """Apply a full risk/filter preset (safe, normal, degen)."""
        profile = get_profile(preset)
        self._filter_profile_name = profile.name
        if self._filter:
            self._filter.set_profile(profile)
        self._ai_min_score = profile.min_ai_score
        self._ai_filter._fallback_score = profile.ai_fallback_score
        object.__setattr__(self._config.jupiter, "buy_amount_sol", profile.buy_amount_sol)
        object.__setattr__(self._config.strategy, "trailing_stop_pct", profile.trailing_stop_pct)
        object.__setattr__(self._config.strategy, "stop_loss_pct", profile.stop_loss_pct)
        logger.info(
            "Applied %s preset: delay=%.1fs age=[%.1f,%.1f] mcap=[%.0f,%.0f] ai_min=%d blacklist=%s recycle=%s reserve=%.3f",
            profile.name,
            profile.sniper_delay_seconds,
            profile.min_age_seconds,
            profile.max_age_seconds,
            profile.min_mcap_sol,
            profile.max_mcap_sol,
            profile.min_ai_score,
            "enforce" if profile.enforce_creator_blacklist else "soft",
            "ON" if profile.recycle_mode else "OFF",
            profile.min_wallet_sol_reserve,
        )
        return profile

    def ensure_live_trading(self) -> None:
        """Force trading defaults on startup (autobuy on, respects DRY_RUN)."""
        is_dry = bool(getattr(self._config.strategy, "dry_run", False) or os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes", "on"))
        self._autobuy_enabled = True
        self._paused = False
        if self._telegram:
            self._telegram._paper_mode = is_dry
            self._telegram._kill_switch = False
        if self._pump_client:
            self._pump_client._paper_enabled = is_dry
        if self._risk_manager:
            self._risk_manager.state.kill_switch_active = False
            if is_dry:
                self._risk_manager.bankroll_sol = float(getattr(self._config.strategy, "dry_run_start_sol", 5.0))
        self.apply_risk_preset(self._filter_profile_name or "degen")
        self._save_state()
        logger.info("Trading mode enforced: autobuy=ON paper=%s kill=OFF profile=%s", "ON" if is_dry else "OFF", self._filter_profile_name)

    async def _refresh_token_metrics(self, token: TokenEvent) -> None:
        """Refresh mcap/liquidity after sniper delay."""
        sol_price = 150.0
        if self._telegram and getattr(self._telegram, "_sol_price", 0) > 0:
            sol_price = self._telegram._sol_price
        if not self._pump_client:
            return
        try:
            mcap_usd = await self._pump_client.get_bonding_curve_mcap(token.mint, sol_price)
            if mcap_usd > 0:
                token.market_cap_usd = mcap_usd
            meta = await self._pump_client.get_token_metadata(token.mint)
            v_sol = float(
                meta.get("virtual_sol_reserves")
                or meta.get("vSolInBondingCurve")
                or meta.get("liquidity_sol")
                or 0
            )
            if v_sol > 1e6:
                v_sol /= 1e9
            if v_sol > 0:
                token.liquidity_sol = v_sol
            mcap_sol = float(meta.get("market_cap_sol") or meta.get("marketCapSol") or 0)
            if mcap_sol > 0:
                token.market_cap_usd = mcap_sol * sol_price
            init_buy = float(meta.get("initialBuy") or meta.get("solAmount") or 0)
            if init_buy > 1e6:
                init_buy /= 1e9
            if init_buy > 0:
                token.initial_buy_sol = init_buy
        except Exception as e:
            logger.debug("Could not refresh metrics for %s: %s", token.symbol, e)

    async def _schedule_token_evaluation(self, token: TokenEvent, raw_data: dict):
        """Wait for sniper delay, refresh metrics, then evaluate filters."""
        mint = token.mint
        if not mint or mint in self._pending_evaluations:
            return
        if mint in self._processed_mints or mint in self._active_buys:
            return

        profile = self._filter.profile if self._filter else get_profile(self._filter_profile_name)
        self._stats.bump("tokens_seen")

        if not profile.skip_mayhem_check:
            if await self._reject_mayhem_token(token.mint, token.symbol, raw_hint=raw_data):
                return

        max_active_positions = await self._effective_max_positions(profile)
        active_count = active_position_count(self._positions)
        if max_active_positions > 0 and active_count >= max_active_positions:
            if profile.recycle_mode:
                await self._rotate_until_under_cap(
                    profile,
                    max(0, max_active_positions - 1),
                    f"CAPITAL ROTATION (make room for {token.symbol})",
                    exclude_mint=token.mint,
                    aggressive=True,
                )
                active_count = active_position_count(self._positions)
            if active_count >= max_active_positions:
                self._stats.bump("skip_position_limit")
                logger.warning(
                    "SKIPPING %s: Active positions limit (%s/%s) reached.",
                    token.symbol, active_count, max_active_positions,
                )
                return

        if profile.enforce_creator_blacklist and self.is_blacklisted(token.creator):
            self._stats.bump("skip_blacklist")
            logger.warning("SKIPPING %s: Creator %s is blacklisted", token.symbol, token.creator)
            return

        self._pending_evaluations.add(mint)
        try:
            if profile.sniper_delay_seconds > 0:
                logger.debug(
                    "Scheduling %s for evaluation in %.1fs (%s profile)",
                    token.symbol, profile.sniper_delay_seconds, profile.name,
                )
                await asyncio.sleep(profile.sniper_delay_seconds)

            if not self._running:
                return
            if mint in self._processed_mints or mint in self._active_buys:
                return
            if token.age_seconds > profile.max_age_seconds:
                logger.info(
                    "Skipping %s: exceeded max age %.1fs (%s)",
                    token.symbol, token.age_seconds, profile.name,
                )
                return

            await self._refresh_token_metrics(token)
            await self._evaluate_token_for_snipe(token, raw_data)
        finally:
            self._pending_evaluations.discard(mint)

    async def _evaluate_token_for_snipe(self, token: TokenEvent, raw_data: dict):
        """Run the full filter chain after sniper delay."""
        profile = self._filter.profile if self._filter else get_profile(self._filter_profile_name)
        genome = {}

        c_score = 50.0
        if hasattr(self, "_creator_genome") and self._creator_genome:
            genome = await self._creator_genome.get_genome(token.creator) or {}
            if genome:
                c_score = genome.get("creator_score", 50.0)
                if (
                    not profile.skip_creator_genome_check
                    and c_score < profile.min_creator_genome_score
                ):
                    self._stats.bump("skip_creator_genome")
                    logger.warning(
                        "Creator Genome Score %.1f < %.1f, skipping %s (%s)",
                        c_score, profile.min_creator_genome_score, token.symbol, profile.name,
                    )
                    return

        ai_score = float(profile.ai_fallback_score)
        if self._ai_enabled and profile.require_ai_gate:
            token_data = {
                "mint": token.mint,
                "symbol": token.symbol,
                "name": token.name,
                "creator": token.creator,
                "uri": token.uri,
                "sentiment_text": self._sentiment_for_mint(token.mint),
            }
            ai_score = await self._ai_filter.score_token(token_data)
            if ai_score < self._ai_min_score:
                self._stats.bump("skip_ai")
                logger.warning(
                    "AI score %s < %s, skipping %s (%s)",
                    ai_score, self._ai_min_score, token.symbol, profile.name,
                )
                return
            heuristic = self._inference.predict({
                "ai_score": ai_score,
                "creator_score": c_score,
                "liquidity_sol": token.liquidity_sol,
            })
            if heuristic < profile.heuristic_threshold:
                self._stats.bump("skip_heuristic")
                logger.info(
                    "Heuristic inference below threshold for %s (%.2f < %.2f, %s)",
                    token.symbol, heuristic, profile.heuristic_threshold, profile.name,
                )
                return
        elif self._ai_enabled:
            logger.debug(
                "AI gate bypassed for %s (%s profile); using fallback score %.0f",
                token.symbol, profile.name, ai_score,
            )

        qualified, default_size, confidence_score = await self._filter.is_qualified(
            token, sol_price=self._telegram._sol_price, ai_score=ai_score, creator_score=c_score
        )

        if not qualified:
            return

        if not profile.skip_mayhem_check:
            if await self._reject_mayhem_token(token.mint, token.symbol):
                return

        self._stats.bump("qualified")
        wallet_balance = await self._pump_client.get_sol_balance()
        size = self._risk_manager.calculate_position_size(
            confidence_score,
            wallet_balance,
            floor_sol=profile.buy_amount_sol,
            max_trade_pct=profile.max_trade_pct_wallet,
        )
        if size <= 0.0:
            self._stats.bump("skip_risk")
            logger.info(
                "Skipping %s: Size calculated to 0.0 SOL (Confidence: %.1f)",
                token.symbol, confidence_score,
            )
            return

        allowed, reason = await self._risk_manager.can_trade(
            token.mint,
            size,
            wallet_balance,
            max_trade_pct=profile.max_trade_pct_wallet,
            max_rpc_latency_ms=profile.max_rpc_latency_ms,
        )
        if not allowed:
            self._stats.bump("skip_risk")
            logger.warning("SKIPPING %s: Risk check failed: %s", token.symbol, reason)
            return

        capital_ok, cap_reason = await self._ensure_buy_capital(profile, size)
        if not capital_ok:
            self._stats.bump("skip_low_balance")
            logger.warning("SKIPPING %s: %s", token.symbol, cap_reason)
            return

        if self._obs:
            await self._obs.record_signal_async(
                token.mint,
                "pump_ws",
                ai_score=ai_score,
                creator_score=c_score,
                confidence=confidence_score,
            )
            await self._obs.capture_features(
                token.mint,
                ai_score=ai_score,
                creator_score=c_score,
                liquidity_sol=token.liquidity_sol,
                market_cap_usd=token.market_cap_usd,
            )

        if c_score >= 85 and confidence_score >= 85:
            from telethon import Button
            buttons = [
                [Button.inline("Buy 0.1 SOL 🟢", f"buy_0.1_{token.mint}")],
                [Button.inline("Buy 0.3 SOL 🟡", f"buy_0.3_{token.mint}")],
                [Button.inline("Buy 0.5 SOL 🟠", f"buy_0.5_{token.mint}")],
                [Button.inline("Buy 1.0 SOL 🔥", f"buy_1.0_{token.mint}")],
            ]
            market_cap_sol = (
                token.market_cap_usd / self._telegram._sol_price
                if (self._telegram and self._telegram._sol_price > 0)
                else float(raw_data.get("marketCapSol", 0) or 0.0)
            )
            alert_msg = (
                f"🚨 <b>10x/100x POTENTIAL RUNNER DETECTED!</b> 🚨\n\n"
                f"Token: <b>{token.symbol}</b> ({token.name})\n"
                f"Mint: <code>{token.mint}</code>\n"
                f"Market Cap: <code>{market_cap_sol:.1f} SOL</code> (${token.market_cap_usd:,.0f})\n"
                f"Creator Genome Score: <code>{c_score:.1f}/100</code>\n"
                f"Confidence: <code>{confidence_score:.1f}%</code>\n"
                f"Reason: <i>High-quality developer profile (Avg ATH: {genome.get('avg_ath', 0.0):.1f}x)</i>\n\n"
                f"👉 <a href='https://pump.fun/{token.mint}'>Buy on pump.fun</a>"
            )
            asyncio.create_task(self._telegram.send_message(alert_msg, buttons=buttons))
            if not self._autobuy_enabled:
                self._missed_runners[token.mint] = {
                    "symbol": token.symbol,
                    "name": token.name,
                    "alert_price_usd": token.market_cap_usd,
                    "alert_time": time(),
                    "notified_milestones": set(),
                    "c_score": c_score,
                    "confidence": confidence_score,
                }

        if self._autobuy_enabled:
            self._active_buys.add(token.mint)
            self._stats.bump("snipes_started")
            asyncio.create_task(
                self._execute_snipe(token, size, f"Sniper [{profile.name}] (Conf: {confidence_score:.1f}%)")
            )
        elif not (c_score >= 85 and confidence_score >= 85):
            await self._telegram.send_message(
                f"🔔 <b>Qualified Token (Auto-buy OFF):</b> {token.symbol}\n"
                f"Mint: <code>{token.mint}</code>\n"
                f"Profile: <code>{profile.name}</code>\n"
                f"Confidence: <code>{confidence_score:.1f}%</code>"
            )
            self._missed_runners.setdefault(token.mint, {
                "symbol": token.symbol,
                "name": token.name,
                "alert_price_usd": token.market_cap_usd,
                "alert_time": time(),
                "notified_milestones": set(),
                "c_score": c_score,
                "confidence": confidence_score,
            })

    async def _process_events(self):
        while self._running:
            if self._paused:
                await asyncio.sleep(1)
                continue
            try:
                data = await asyncio.wait_for(self._monitor.queue.get(), timeout=1.0)
                tx_type = data.get("txType")
                if tx_type in ["sell", "buy"]:
                    await self._handle_trade_event(data)
                elif data.get("mint") and (tx_type == "create" or tx_type is None):
                    token = self._parse_token_event(data)
                    asyncio.create_task(self._db_log_launch(token))
                    asyncio.create_task(self._schedule_token_evaluation(token, data))

            except asyncio.TimeoutError:
                continue

    async def _handle_trade_event(self, data: dict):
        trader = data.get("traderPublicKey")
        mint = data.get("mint")
        tx_type = data.get("txType")
        mcap_sol = data.get("marketCapSol")
        sol_amount = float(data.get("solAmount", 0) or 0.0)
        if not trader or not mint: return

        pass

        # Blacklist check
        if self.is_blacklisted(trader):
            logger.warning(f"IGNORING event from blacklisted wallet: {trader}")
            return

        # Feed to Wallet Graph
        if tx_type == "buy" and hasattr(self, '_wallet_graph') and self._wallet_graph:
            asyncio.create_task(self._wallet_graph.record_activity(trader, mint, data))

        # Track PnL for Daily Smart Wallet Scanner
        if tx_type == "buy":
            if not hasattr(self, '_active_trader_buys'):
                self._active_trader_buys = {}
            if trader not in self._active_trader_buys:
                self._active_trader_buys[trader] = {}
            self._active_trader_buys[trader][mint] = sol_amount
        elif tx_type == "sell":
            if hasattr(self, '_active_trader_buys') and trader in self._active_trader_buys and mint in self._active_trader_buys[trader]:
                entry_sol = self._active_trader_buys[trader][mint]
                pnl_sol = sol_amount - entry_sol
                is_win = 1 if pnl_sol > 0 else 0
                asyncio.create_task(self._db_update_wallet_pnl(trader, pnl_sol, is_win))
                del self._active_trader_buys[trader][mint]
                if not self._active_trader_buys[trader]:
                    del self._active_trader_buys[trader]

        # Feed to KOL Tracker
        if trader in self._kol_tracker.wallets:
            kol_event = {
                'wallet': trader,
                'action': tx_type,
                'token': mint,
                'amount': sol_amount
            }
            await self._kol_tracker.process_event(kol_event, self)

        # Daily Runner Check: Track big buys >= $1000 & KOL buys
        if tx_type == "buy" and hasattr(self, '_daily_runner_buys'):
            sol_price = getattr(self._telegram, '_sol_price', 150.0)
            usd_amount = sol_amount * sol_price
            
            if mint not in self._daily_runner_buys:
                self._daily_runner_buys[mint] = {'buys': [], 'notified': False}
                
            if usd_amount >= 1000.0:
                self._daily_runner_buys[mint]['buys'].append((sol_amount, time()))
            
            big_buys_count = len(self._daily_runner_buys[mint]['buys'])
            kol_buys_count = len(self._kol_tracker.active_buys.get(mint, set()))
            
            # Check if we reached 4+ big buys OR 3+ KOL buys and haven't notified yet
            if not self._daily_runner_buys[mint]['notified']:
                reason = None
                if big_buys_count >= 4 and kol_buys_count >= 3:
                    reason = "Detected 4+ big buys above $1000 & 3+ active KOL buys"
                elif big_buys_count >= 4:
                    reason = "Detected 4+ big buys above $1000"
                elif kol_buys_count >= 3:
                    reason = f"Detected Coordinated KOL Interest ({kol_buys_count}+ active KOLs)"
                
                if reason:
                    self._daily_runner_buys[mint]['notified'] = True
                    asyncio.create_task(self._trigger_daily_runner_alert(mint, mcap_sol, reason))

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

        # Copytrade Sniping (with race condition protection)
        if tx_type == "buy" and self._filter.is_copy_target(trader):
            if mint not in self._processed_mints and mint not in self._active_buys:
                self._active_buys.add(mint)
                token = self._parse_token_event(data)
                alias = self._filter._wallet_scores.get(trader, {}).alias or trader[:8]
                asyncio.create_task(self._execute_snipe(token, self._config.jupiter.buy_amount_sol, f"Copytrade [{alias}]"))

        # V4: Sniper only triggers on creation events, 100k trade event sniping deactivated.
        pass

    def _parse_token_event(self, data: dict) -> TokenEvent:
        v_sol = float(data.get("vSolInBondingCurve", 0))
        if v_sol > 1e6:
            v_sol = v_sol / 1e9
        initial_buy = float(data.get("initialBuy", 0) or data.get("solAmount", 0) or 0.0)
        if initial_buy > 1e6:
            initial_buy = initial_buy / 1e9
        return TokenEvent(
            mint=data.get("mint"),
            name=data.get("name", "Unknown"),
            symbol=data.get("symbol", "???"),
            uri=data.get("uri"),
            creator=data.get("traderPublicKey") or data.get("creator"),
            initial_buy_sol=initial_buy,
            market_cap_usd=float(data.get("marketCapSol", 0)) * self._telegram._sol_price,
            liquidity_sol=v_sol if v_sol > 0 else float(data.get("liquidity_sol", 0)),
            timestamp=time(),
        )

    async def execute_kol_snipe(self, mint: str, reason: str):
        """Specifically used by KOLTracker for coordinated buys."""
        if mint in self._positions or mint in self._processed_mints or mint in self._active_buys: return
        self._active_buys.add(mint)
        try:
            profile = self._filter.profile if self._filter else get_profile(self._filter_profile_name)
            meta = await self._pump_client.get_token_metadata(mint)
            if not profile.skip_mayhem_check:
                if await self._reject_mayhem_token(mint, meta.get("symbol", "KOL_PICK"), raw_hint=meta):
                    return
            token = TokenEvent(
                mint=mint,
                name=meta.get("name", "Unknown"),
                symbol=meta.get("symbol", "KOL_PICK"),
                creator=meta.get("creator", ""),
                market_cap_usd=float(meta.get("market_cap_sol", 0)) * self._telegram._sol_price,
                liquidity_sol=float(meta.get("liquidity_sol", 0)),
                timestamp=time()
            )
            await self._execute_snipe(token, self._config.jupiter.buy_amount_sol, reason)
        finally:
            self._active_buys.discard(mint)

    async def _evaluate_and_snipe_from_trade(self, token: TokenEvent, size: float, reason: str):
        try:
            # Active positions limit check
            max_active_positions = getattr(self._config.strategy, "max_active_positions", 100)
            active_count = sum(1 for p in self._positions.values() if p.active)
            if max_active_positions > 0 and active_count >= max_active_positions:
                logger.warning(f"SKIPPING {token.symbol}: Active positions limit ({active_count}/{max_active_positions}) reached.")
                return

            # Blacklist check
            if self.is_blacklisted(token.creator):
                logger.warning(f"SKIPPING {token.symbol}: Creator {token.creator} is blacklisted")
                return

            # Portfolio Guard & Drawdown Check
            if hasattr(self, '_portfolio_guard') and self._portfolio_guard:
                paper_active = getattr(self._pump_client, "paper_enabled", False) if self._pump_client else False
                current_bal = self._pump_client._paper_sol if paper_active else getattr(self._risk_manager, 'bankroll_sol', 1.0)
                guard_status = self._portfolio_guard.check_buy_allowed(
                    current_wallet_balance_sol=current_bal,
                    creator=token.creator,
                    buy_amount_sol=size,
                    active_positions=self._positions,
                )
                if guard_status.circuit_breaker_tripped:
                    logger.warning(f"SKIPPING {token.symbol}: PortfolioGuard circuit tripped ({guard_status.reason})")
                    return

            # Missed Runner Pattern Check
            is_runner_clone = False
            if hasattr(self, '_missed_runner_engine') and self._missed_runner_engine:
                is_runner, r_score, r_reason = self._missed_runner_engine.matches_runner_pattern(
                    mcap_usd=token.market_cap_usd,
                    buy_ratio=0.70,
                    unique_buyers=25,
                    dev_holding_pct=0.01,
                )
                if is_runner:
                    is_runner_clone = True
                    logger.info(f"🏆 MISSED RUNNER CLONE PATTERN DETECTED ({r_score:.0f}/100) for {token.symbol}: {r_reason}")
                    reason = f"Missed Runner Clone Pattern ({r_score:.0f} pts)"

            # Check Creator Genome Score and adjust size/eligibility
            c_score = 50.0
            genome = None
            if hasattr(self, '_creator_genome') and self._creator_genome:
                genome = await self._creator_genome.get_genome(token.creator)
                if genome:
                    c_score = genome.get("creator_score", 50.0)
                    if c_score < 40.0:
                        logger.warning(f"Creator Genome Score {c_score} < 40, skipping {token.symbol}")
                        return
                    elif c_score >= 80.0:
                        logger.info(f"High Creator Genome Score {c_score}! Scaling up trade size.")
                        size = size * 1.5

            if self._ai_enabled:
                token_data = {
                    "mint": token.mint,
                    "symbol": token.symbol,
                    "name": token.name,
                    "creator": token.creator,
                    "sentiment_text": self._sentiment_for_mint(token.mint),
                }
                score = await self._ai_filter.score_token(token_data)
                if score < self._ai_min_score:
                    logger.warning(f"AI score {score} < {self._ai_min_score}, skipping {token.symbol}")
                    return

            # 10x/100x Potential Runner Alert Trigger
            if c_score >= 85 and genome:
                from telethon import Button
                buttons = [
                    [Button.inline("Buy 0.1 SOL 🟢", f"buy_0.1_{token.mint}")],
                    [Button.inline("Buy 0.3 SOL 🟡", f"buy_0.3_{token.mint}")],
                    [Button.inline("Buy 0.5 SOL 🟠", f"buy_0.5_{token.mint}")],
                    [Button.inline("Buy 1.0 SOL 🔥", f"buy_1.0_{token.mint}")]
                ]
                market_cap_sol = token.market_cap_usd / self._telegram._sol_price if (self._telegram and self._telegram._sol_price > 0) else 0.0
                alert_msg = (
                    f"🚨 <b>10x/100x POTENTIAL RUNNER DETECTED!</b> 🚨\n\n"
                    f"Token: <b>{token.symbol}</b> ({token.name})\n"
                    f"Mint: <code>{token.mint}</code>\n"
                    f"Market Cap: <code>{market_cap_sol:.1f} SOL</code> (${token.market_cap_usd:,.0f})\n"
                    f"Creator Genome Score: <code>{c_score:.1f}/100</code>\n"
                    f"Reason: <i>High-quality developer profile (Avg ATH of past launches: {genome.get('avg_ath', 0.0):.1f}x)</i>\n\n"
                    f"👉 <a href='https://pump.fun/{token.mint}'>Buy on pump.fun</a>"
                )
                asyncio.create_task(self._telegram.send_message(alert_msg, buttons=buttons))

            # Snipe only if autobuy is enabled
            if self._autobuy_enabled:
                self._processed_mints.add(token.mint)
                await self._execute_snipe(token, size, reason)
            else:
                await self._telegram.send_message(f"🔔 <b>Qualified Token (Auto-buy OFF):</b> {token.symbol}\nMint: <code>{token.mint}</code>\nMCAP: <code>${token.market_cap_usd:,.0f}</code>")
        except Exception as e:
            logger.error(f"Error in _evaluate_and_snipe_from_trade: {e}")
        finally:
            self._active_buys.discard(token.mint)

    async def _execute_snipe(self, token: TokenEvent, size: float, reason: str, status_msg=None, manual_override=False):
        if token.mint in self._positions:
            self._active_buys.discard(token.mint)
            return
        blocked = self._trading_blocked()
        if blocked:
            self._stats.bump("skip_trading_blocked")
            logger.warning("Trade blocked for %s: %s", token.symbol, blocked)
            if self._telegram:
                await self._telegram.send_message(
                    f"⛔️ <b>Trade Blocked:</b> {token.symbol}\nReason: <code>{blocked}</code>"
                )
            return
        self._active_buys.add(token.mint)
        self._processed_mints.add(token.mint)
        try:
            exec_profile = self._filter.profile if self._filter else get_profile(self._filter_profile_name)
            if not exec_profile.skip_mayhem_check:
                if await self._reject_mayhem_token(token.mint, token.symbol):
                    self._processed_mints.discard(token.mint)
                    self._active_buys.discard(token.mint)
                    return
            capital_ok, cap_reason = await self._ensure_buy_capital(exec_profile, size)
            if not capital_ok:
                self._stats.bump("skip_low_balance")
                logger.warning("Trade blocked for %s: %s", token.symbol, cap_reason)
                self._processed_mints.discard(token.mint)
                self._active_buys.discard(token.mint)
                return
            # Advanced AI Safety & Honeypot Screen
            if self._ai_enabled and not manual_override and not exec_profile.skip_ai_safety_screen:
                holders = []
                try:
                    rpc_url = await self._pump_client._get_rpc_url()
                    payload = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getTokenLargestAccounts",
                        "params": [token.mint]
                    }
                    import aiohttp
                    async with aiohttp.ClientSession() as session:
                        async with session.post(rpc_url, json=payload, timeout=5) as resp:
                            if resp.status == 200:
                                res = await resp.json()
                                accounts = res.get("result", {}).get("value", [])
                                # Default supply for pump.fun tokens is 1B
                                total_supply = 1_000_000_000
                                holders = [
                                    {
                                        'account': acc.get("address"),
                                        'share_pct': (float(acc.get("amount", 0)) / (total_supply * 1e6)) * 100.0 if "amount" in acc else 0.0
                                    }
                                    for acc in accounts
                                ]
                except Exception as e:
                    logger.error(f"Failed to fetch largest accounts for safety screen: {e}")

                creator_history = []
                try:
                    rows = await self._db._execute_read(
                        "SELECT mint, max_marketcap, roi FROM ticks WHERE creator = ? LIMIT 5",
                        (token.creator,)
                    )
                    creator_history = [
                        {
                            "mint": r["mint"],
                            "peak_mcap_usd": r["max_marketcap"],
                            "rugged": r["roi"] < -0.8 if r["roi"] is not None else False
                        }
                        for r in rows
                    ]
                except Exception as e:
                    logger.error(f"Failed to fetch creator history for safety screen: {e}")

                analysis = await self._ai_filter.detect_rug_risks(
                    token.mint, token.creator, holders, creator_history
                )
                
                # Dynamic Developer Cluster Mapping check
                cluster_risk = 0.0
                cluster_size = 0
                try:
                    cluster_risk, cluster_size, _ = await self._cluster_mapper.analyze_token_cluster(
                        token.mint, rpc_url
                    )
                except Exception as e:
                    logger.error(f"Cluster analysis failed: {e}")

                max_cluster = 30.0
                if self._filter:
                    max_cluster = self._filter.profile.max_cluster_risk
                if cluster_risk >= max_cluster:
                    cluster_reason = f"Stealth developer wallet cluster detected. Clustered wallets control {cluster_risk/2:.1f}% of supply."
                    logger.warning(f"❌ AI SAFETY CLUSTER SCREEN FAILED for {token.symbol}: Cluster Risk={cluster_risk:.1f}% | Size={cluster_size} | Reason: {cluster_reason}")
                    if status_msg:
                        try:
                            await status_msg.edit(f"⚡️ <b>TG Manual Buy Clicked!</b>\nTarget: <code>{token.mint}</code>\nAmount: <code>{size} SOL</code>\nStatus: <code>🚫 REJECTED (AI Safety: Cluster Risk={cluster_risk:.1f}%)</code>", parse_mode='html')
                        except Exception:
                            pass
                    if self._telegram:
                        await self._telegram.send_message(
                            f"🚫 <b>AI Safety Filtered Clustered Launch:</b> {token.symbol}\n"
                            f"Reason: <i>{cluster_reason}</i>\n"
                            f"Cluster Risk Score: <code>{cluster_risk:.1f}/100</code> (size={cluster_size})"
                        )
                    self._processed_mints.discard(token.mint)
                    self._active_buys.discard(token.mint)
                    return

                if analysis.get("score", 0) < self._ai_min_score or analysis.get("is_honeypot") or analysis.get("is_premine"):
                    logger.warning(f"❌ AI SAFETY SCREEN FAILED for {token.symbol}: Score={analysis.get('score')} | Honeypot={analysis.get('is_honeypot')} | Premine={analysis.get('is_premine')} | Reason: {analysis.get('reason')}")
                    if status_msg:
                        try:
                            await status_msg.edit(f"⚡️ <b>TG Manual Buy Clicked!</b>\nTarget: <code>{token.mint}</code>\nAmount: <code>{size} SOL</code>\nStatus: <code>🚫 REJECTED (AI Score={analysis.get('score')}/100)</code>", parse_mode='html')
                        except Exception:
                            pass
                    if self._telegram:
                        await self._telegram.send_message(
                            f"🚫 <b>AI Safety Filtered Rug/Honeypot:</b> {token.symbol}\n"
                            f"Reason: <i>{analysis.get('reason')}</i>\n"
                            f"Score: <code>{analysis.get('score')}/100</code>"
                        )
                    self._processed_mints.discard(token.mint)
                    self._active_buys.discard(token.mint)
                    return

                skip_agi = self._filter.profile.skip_agi_prebuy if self._filter else False
                if not skip_agi:
                    agi_action, agi_score, _, agi_reason = await self._agi_prebuy_filter.evaluate_token(token.mint, {
                        "market_cap_usd": token.market_cap_usd,
                        "liquidity_sol": token.liquidity_sol,
                        "creator": token.creator,
                    })

                    if agi_action == "SKIP":
                        logger.warning(f"❌ AGI PRE-BUY FILTER FAILED for {token.symbol}: {agi_reason}")
                        if self._telegram:
                            await self._telegram.send_message(
                                f"🚫 <b>AGI Pre-Buy Filter Rejected:</b> {token.symbol}\nReason: <i>{agi_reason}</i>"
                            )
                        self._processed_mints.discard(token.mint)
                        self._active_buys.discard(token.mint)
                        return
                    if agi_action == "WATCH":
                        logger.info(f"👀 AGI PRE-BUY FILTER WATCHING {token.symbol}: {agi_reason}")
                        self._watch_queue[token.mint] = {
                            "token": token,
                            "size": size,
                            "reason": reason,
                            "added_at": time.time(),
                        }
                        if self._telegram:
                            await self._telegram.send_message(
                                f"👀 <b>AGI Pre-Buy Added to WATCH Queue:</b> {token.symbol}\n"
                                f"Score: <code>{agi_score}</code>\nReason: <i>{agi_reason}</i>"
                            )
                        self._active_buys.discard(token.mint)
                        return
                    if agi_action == "BUY_HALF":
                        logger.info(f"⚖️ AGI PRE-BUY FILTER BUY_HALF for {token.symbol}: {agi_reason}")
                        size = size * 0.5
                    elif agi_action == "BUY_FULL":
                        logger.info(f"✅ AGI PRE-BUY FILTER BUY_FULL for {token.symbol}: {agi_reason}")
                else:
                    logger.info(
                        "AGI pre-buy filter skipped (%s profile) for %s",
                        self._filter.profile.name, token.symbol,
                    )
            elif exec_profile.skip_ai_safety_screen:
                logger.info(
                    "AI safety screen skipped (%s profile) for %s",
                    exec_profile.name, token.symbol,
                )

            priority_fee_sol = self._dynamic_priority_fee
            jito_tip_sol = self._dynamic_jito_tip
            use_jito = exec_profile.use_jito
            logger.info(
                "Initiating snipe for %s (%s) | Size: %s SOL | Dynamic Fee: %s SOL | Jito: %s | Reason: %s",
                token.symbol, token.mint, size, f"{priority_fee_sol:.6f}",
                f"tip {jito_tip_sol:.5f}" if use_jito else "direct RPC",
                reason,
            )
            result = await self._pump_client.execute_trade(
                token.mint,
                action="buy",
                amount=size,
                priority_fee=priority_fee_sol,
                jito_tip=jito_tip_sol,
                use_jito=use_jito,
            )
            await self._risk_manager.report_tx_result(result.success)
            if result.success:
                self._stats.bump("buys_success")
                self._trades.append(result)
                self._position_counter += 1
                pos = Position(
                    mint=token.mint, symbol=token.symbol,
                    entry_price=token.market_cap_usd, entry_liq=token.liquidity_sol,
                    creator=token.creator,
                    size=size,
                    position_number=self._position_counter,
                    remaining_fraction=1.0
                )
                pos.current_price = token.market_cap_usd
                pos.highest_price = token.market_cap_usd
                self._positions[token.mint] = pos
                asyncio.create_task(self._db.save_position(token.mint, token.market_cap_usd, size, "open", reason))
                if hasattr(self, '_paste_trade') and self._paste_trade:
                    asyncio.create_task(self._paste_trade.post_trade(
                        ticker=token.symbol,
                        direction="long",
                        author_price=token.market_cap_usd,
                        thesis=reason
                    ))
                self._spawn_position_manager(pos)
                await self._log_trade_event("buy", {
                    "mint": token.mint,
                    "symbol": token.symbol,
                    "size": size,
                    "reason": reason,
                    "tx": result.tx_signature,
                    "success": True,
                })

                # Track in RiskManager
                await self._risk_manager.on_position_opened(token.mint, size)
                
                # Record process launch in Creator Genome Engine
                if hasattr(self, '_creator_genome') and self._creator_genome:
                    asyncio.create_task(self._creator_genome.process_launch(token.creator, {
                        'mint': token.mint,
                        'initial_liquidity': token.liquidity_sol
                    }))

                self._save_state()
                if status_msg:
                    try:
                        await status_msg.edit(f"⚡️ <b>TG Manual Buy Clicked!</b>\nTarget: <code>{token.mint}</code>\nAmount: <code>{size} SOL</code>\nStatus: <code>🟢 SUCCESS (Tx: <a href='https://solscan.io/tx/{result.tx_signature}'>{result.tx_signature[:8]}...</a>)</code>", parse_mode='html', link_preview=False)
                    except Exception:
                        pass
                
                # Wait 1.5 seconds for transaction indexing to fetch exact tokens got
                await asyncio.sleep(1.5)
                token_balance = await self._pump_client.get_token_balance(token.mint)
                got_str = _format_tokens(token_balance)
                
                sol_price = getattr(self._telegram, "_sol_price", 150.0) or 150.0
                mc_sol = token.market_cap_usd / sol_price
                
                if token.market_cap_usd >= 1_000_000:
                    usd_mc_str = f"${token.market_cap_usd / 1_000_000:.1f}M"
                else:
                    usd_mc_str = f"${token.market_cap_usd / 1_000:.1f}K"
                    
                total_fee = priority_fee_sol + jito_tip_sol + 0.00005
                short_mint = f"{token.mint[:6]}...{token.mint[-4:]}"
                
                bought_msg = (
                    f"✅ <b>Bought {short_mint}</b>\n"
                    f"<code>{token.mint}</code>\n\n"
                    f"💰 Spent: <b>{size:.4f} SOL</b> (+ {total_fee:.5f} fee)\n"
                    f"🌐 Got: <b>{got_str} tokens</b>\n"
                    f"📈 MC at entry: <b>{mc_sol:.2f} SOL ({usd_mc_str})</b>\n"
                    f"📍 Position #{pos.position_number}\n"
                    f"📊 Phase: <i>submitted</i> . `<a href='https://solscan.io/tx/{result.tx_signature}'>{result.tx_signature[:8]}...</a>`"
                )
                
                from telethon import Button
                buttons = [
                    [
                        Button.inline("Sell 25%", f"sell_0.25_{token.mint}".encode("utf-8")),
                        Button.inline("Sell 50%", f"sell_0.50_{token.mint}".encode("utf-8")),
                        Button.inline("Sell ALL", f"sell_1.0_{token.mint}".encode("utf-8"))
                    ]
                ]
                await self._telegram.send_message(bought_msg, buttons=buttons)
            else:
                self._stats.bump("buys_failed")
                logger.error(f"Snipe failed for {token.symbol} ({token.mint}): {result.error}")
                await self._log_trade_event("buy", {
                    "mint": token.mint,
                    "symbol": token.symbol,
                    "size": size,
                    "reason": reason,
                    "success": False,
                    "error": result.error,
                })
                self._processed_mints.discard(token.mint)
                if status_msg:
                    try:
                        await status_msg.edit(f"⚡️ <b>TG Manual Buy Clicked!</b>\nTarget: <code>{token.mint}</code>\nAmount: <code>{size} SOL</code>\nStatus: <code>❌ FAILED ({result.error})</code>", parse_mode='html')
                    except Exception:
                        pass
                if self._telegram:
                    await self._telegram.send_message(f"❌ <b>Snipe Failed ({reason}): {token.symbol}</b>\nError: <code>{result.error}</code>")
        except Exception as e:
            logger.error(f"Error in _execute_snipe: {e}")
            self._processed_mints.discard(token.mint)
            if status_msg:
                try:
                    await status_msg.edit(f"⚡️ <b>TG Manual Buy Clicked!</b>\nTarget: <code>{token.mint}</code>\nAmount: <code>{size} SOL</code>\nStatus: <code>❌ ERROR ({e})</code>", parse_mode='html')
                except Exception:
                    pass
        finally:
            self._active_buys.discard(token.mint)

    async def _watch_queue_manager(self):
        """Continuously polls tokens in the watch queue for execution."""
        import time
        while self._running:
            try:
                current_time = time.time()
                for mint in list(self._watch_queue.keys()):
                    data = self._watch_queue.get(mint)
                    if not data:
                        continue
                    
                    elapsed = current_time - data["added_at"]
                    token = data["token"]
                    size = data["size"]
                    reason = data["reason"]

                    # If in queue for > 3 minutes, discard
                    if elapsed > 180:
                        logger.info(f"🗑️ Dropping {token.symbol} from watch queue (timed out).")
                        self._watch_queue.pop(mint, None)
                        if self._telegram:
                            await self._telegram.send_message(f"🗑️ <b>Dropped from Watch Queue:</b> {token.symbol} (timed out)")
                        continue

                    # Re-evaluate
                    agi_action, agi_score, _, agi_reason = await self._agi_prebuy_filter.evaluate_token(mint, {
                        "market_cap_usd": token.market_cap_usd,
                        "liquidity_sol": token.liquidity_sol,
                        "creator": token.creator
                    })

                    if agi_action == "SKIP":
                        logger.info(f"🗑️ Dropping {token.symbol} from watch queue (score dropped).")
                        self._watch_queue.pop(mint, None)
                        continue
                    elif agi_action in ["BUY_FULL", "BUY_HALF"]:
                        logger.info(f"🚀 Executing Snipe for watched token {token.symbol} (Score: {agi_score})")
                        self._watch_queue.pop(mint, None)
                        
                        adj_size = size
                        if agi_action == "BUY_HALF":
                            adj_size = size * 0.5
                            
                        # Execute buy
                        priority_fee_sol = self._dynamic_priority_fee
                        jito_tip_sol = self._dynamic_jito_tip
                        watch_profile = self._filter.profile if self._filter else get_profile(self._filter_profile_name)
                        result = await self._pump_client.execute_trade(
                            mint,
                            action="buy",
                            amount=adj_size,
                            priority_fee=priority_fee_sol,
                            jito_tip=jito_tip_sol,
                            use_jito=watch_profile.use_jito,
                        )
                        if result.success:
                            self._trades.append(result)
                            pos = Position(
                                mint=mint, symbol=token.symbol,
                                entry_price=token.market_cap_usd, entry_liq=token.liquidity_sol,
                                creator=token.creator,
                                size=adj_size
                            )
                            pos.current_price = token.market_cap_usd
                            pos.highest_price = token.market_cap_usd
                            self._positions[mint] = pos
                            asyncio.create_task(self._db.save_position(mint, token.market_cap_usd, adj_size, "open", f"{reason} (Watch Queue: {agi_score})"))
                            if hasattr(self, '_paste_trade') and self._paste_trade:
                                asyncio.create_task(self._paste_trade.post_trade(
                                    ticker=token.symbol,
                                    direction="long",
                                    author_price=token.market_cap_usd,
                                    thesis=f"{reason} (Watch Queue: {agi_score})"
                                ))
                            asyncio.create_task(self._position_manager(pos))
                            await self._risk_manager.on_position_opened(mint, adj_size)
                            self._save_state()
                            await self._telegram.send_message(f"📡 <b>WATCH BUY ({agi_action}): {token.symbol}</b>\nScore: {agi_score}\nMCAP: <code>${token.market_cap_usd:,.0f}</code>\nReason: <i>{agi_reason}</i>")
                        else:
                            logger.error(f"Watch snipe failed for {token.symbol}: {result.error}")

            except Exception as e:
                logger.error(f"Error in _watch_queue_manager: {e}")
            await asyncio.sleep(10)

    async def _position_manager(self, pos: Position):
        strat = self._config.strategy
        last_poll_time = 0
        last_moonbag_check_time = 0
        
        # Add dynamic flag attributes if not exists
        if not hasattr(pos, 'is_moonbag'):
            pos.is_moonbag = False
        if not hasattr(pos, 'trailing_stop_activated'):
            pos.trailing_stop_activated = None
            
        while self._running and pos.active:
            if getattr(pos, "is_mayhem", False):
                logger.warning(
                    "Stopping position manager for mayhem bag %s — unsellable",
                    pos.symbol,
                )
                pos.active = False
                self._positions.pop(pos.mint, None)
                self._save_state()
                break
            if self._pump_client and await self._pump_client.is_mayhem_token(pos.mint):
                pos.is_mayhem = True
                logger.warning(
                    "Mayhem detected on held %s — untracking (cannot sell on pump.fun)",
                    pos.symbol,
                )
                pos.active = False
                self._positions.pop(pos.mint, None)
                self._save_state()
                break
            now_ts = time()
            # Poll real-time price from RPC every 15 seconds
            if now_ts - last_poll_time >= 15:
                last_poll_time = now_ts
                try:
                    sol_p = getattr(self._telegram, "_sol_price", 150.0) or 150.0
                    price_usd = await self._pump_client.get_bonding_curve_mcap(pos.mint, sol_p)
                    if price_usd <= 0:
                        metrics = await self._dexscreener.get_price_metrics(pos.mint)
                        if metrics:
                            price_usd = float(metrics.get("market_cap_usd") or 0.0)
                    
                    if price_usd > 0:
                        if getattr(pos, 'entry_price', 0) <= 0:
                            logger.info(f"Backfilling missing entry price for {pos.symbol}: {price_usd}")
                            pos.entry_price = price_usd
                            
                        pos.current_price = price_usd
                        if price_usd > pos.highest_price:
                            pos.highest_price = price_usd
                            self._save_state()
                except Exception as e:
                    logger.error(f"Error polling price for {pos.symbol}: {e}")

            if pos.current_price == 0:
                await asyncio.sleep(1)
                continue

            gain = pos.current_price / pos.entry_price if pos.entry_price > 0 else 1.0
            drawdown = (pos.highest_price - pos.current_price) / pos.highest_price if pos.highest_price > 0 else 0.0

            # V4 exit logic: check if it is already a moonbag or not
            if pos.is_moonbag:
                # Trailing stop for moonbags
                # After 5x: activate 25% trailing stop. After 10x: activate 20% trailing stop.
                if gain >= 10.0:
                    pos.trailing_stop_activated = 0.20
                elif gain >= 5.0 and pos.trailing_stop_activated is None:
                    pos.trailing_stop_activated = 0.25
                
                # Check trailing stop hit
                if pos.trailing_stop_activated is not None and drawdown >= pos.trailing_stop_activated:
                    await self._exit_position(pos, f"Moonbag Trailing Stop ({pos.trailing_stop_activated*100:.0f}% hit)", 1.0)
                    break
                    
                # Periodic checks: AI trend reverses or distribution changes
                if now_ts - last_moonbag_check_time >= 60:
                    last_moonbag_check_time = now_ts
                    token_data = {
                        "mint": pos.mint,
                        "symbol": pos.symbol,
                        "sentiment_text": self._sentiment_for_mint(pos.mint),
                    }
                    current_score = await self._ai_filter.score_token(token_data)
                    if current_score < 50:
                        await self._exit_position(pos, f"Moonbag exit: AI Trend Reverse ({current_score})", 1.0)
                        break
            else:
                profile = self._filter.profile if self._filter else get_profile(self._filter_profile_name)
                if profile.recycle_mode:
                    hold_min = (now_ts - pos.start_time) / 60.0
                    if hold_min >= profile.max_hold_minutes:
                        await self._exit_position(pos, f"MAX HOLD ({profile.max_hold_minutes:.0f}m)", 1.0)
                        break
                    if hold_min >= profile.stale_exit_minutes and gain < profile.stale_min_gain:
                        await self._exit_position(
                            pos, f"STALE EXIT ({hold_min:.0f}m @ {gain:.2f}x)", 1.0,
                        )
                        break
                    if (
                        gain >= profile.tp2_multiplier
                        and profile.tp2_multiplier not in pos.tp_targets_hit
                    ):
                        await self._exit_position(
                            pos, f"RECYCLE TP2 ({profile.tp2_multiplier:.2f}x)", profile.tp2_sell_pct,
                        )
                        pos.tp_targets_hit.append(profile.tp2_multiplier)
                        self._save_state()
                    elif (
                        gain >= profile.tp1_multiplier
                        and profile.tp1_multiplier not in pos.tp_targets_hit
                    ):
                        await self._exit_position(
                            pos, f"RECYCLE TP1 ({profile.tp1_multiplier:.2f}x)", profile.tp1_sell_pct,
                        )
                        pos.tp_targets_hit.append(profile.tp1_multiplier)
                        self._save_state()
                    elif gain >= profile.trailing_activate_gain:
                        if drawdown >= profile.trailing_stop_pct:
                            await self._exit_position(
                                pos,
                                f"TRAILING STOP ({profile.trailing_stop_pct*100:.0f}% from peak)",
                                1.0,
                            )
                            break
                    elif gain <= (1.0 - profile.stop_loss_pct):
                        await self._exit_position(
                            pos, f"STOP LOSS ({profile.stop_loss_pct*100:.0f}%)", 1.0,
                        )
                        break
                    await asyncio.sleep(5)
                    continue

                # Not a moonbag yet. Check TP Presets (Aggressive by default, or Conservative)
                preset = getattr(strat, "tp_preset", "aggressive")
                
                if preset == "conservative":
                    # Conservative: 2x (sell 25%), 3x (sell 25%), 5x (sell 25%), leave 25% moonbag
                    if gain >= 2.0 and 2.0 not in pos.tp_targets_hit:
                        await self._exit_position(pos, "Conservative TP 2x (Sell 25% of initial)", 0.25)
                        pos.tp_targets_hit.append(2.0)
                        self._save_state()
                    elif gain >= 3.0 and 3.0 not in pos.tp_targets_hit:
                        # 25% of initial = 33.3% of remaining 75%
                        await self._exit_position(pos, "Conservative TP 3x (Sell 25% of initial)", 0.3333)
                        pos.tp_targets_hit.append(3.0)
                        self._save_state()
                    elif gain >= 5.0 and 5.0 not in pos.tp_targets_hit:
                        # 25% of initial = 50% of remaining 50%
                        await self._exit_position(pos, "Conservative TP 5x (Sell 25% of initial, entering Moonbag)", 0.50)
                        pos.tp_targets_hit.append(5.0)
                        pos.is_moonbag = True
                        pos.trailing_stop_activated = 0.25
                        self._save_state()
                else:
                    # Aggressive: 3x (sell 20%), 5x (sell 30%), 10x (sell 20%), leave 30% moonbag
                    if gain >= 3.0 and 3.0 not in pos.tp_targets_hit:
                        await self._exit_position(pos, "Aggressive TP 3x (Sell 20% of initial)", 0.20)
                        pos.tp_targets_hit.append(3.0)
                        self._save_state()
                    elif gain >= 5.0 and 5.0 not in pos.tp_targets_hit:
                        # 30% of initial = 37.5% of remaining 80%
                        await self._exit_position(pos, "Aggressive TP 5x (Sell 30% of initial)", 0.375)
                        pos.tp_targets_hit.append(5.0)
                        self._save_state()
                    elif gain >= 10.0 and 10.0 not in pos.tp_targets_hit:
                        # 20% of initial = 40% of remaining 50%
                        await self._exit_position(pos, "Aggressive TP 10x (Sell 20% of initial, entering Moonbag)", 0.40)
                        pos.tp_targets_hit.append(10.0)
                        pos.is_moonbag = True
                        pos.trailing_stop_activated = 0.20
                        self._save_state()

            # 3. Stop loss & Break-even checks
            if any(tp in pos.tp_targets_hit for tp in (2.0, 3.0)):
                if gain <= 1.05:
                    await self._exit_position(pos, "Break-even Stop (+5% Capital Preserved)", 1.0)
                    break
            elif gain <= (1.0 - strat.stop_loss_pct):
                await self._exit_position(pos, "Stop-loss", 1.0)
                break
                    
            await asyncio.sleep(5)

    async def _exit_position(self, pos: Position, reason: str, pct: float):
        if not pos.active: return
        token_balance = await self._pump_client.get_token_balance(pos.mint)
        if token_balance <= 0 and getattr(self._pump_client, "paper_enabled", False):
            token_balance = pos.size * getattr(pos, "remaining_fraction", 1.0) * self._pump_client.PAPER_TOKENS_PER_SOL
            self._pump_client._paper_tokens[pos.mint] = token_balance
            self._pump_client._paper_basis[pos.mint] = pos.size * getattr(pos, "remaining_fraction", 1.0)

        if token_balance <= 0:
            pos.active = False
            if pos.mint in self._positions: del self._positions[pos.mint]
            asyncio.create_task(self._db.update_position_pnl(pos.mint, 0.0, "closed"))
            self._save_state()
            return
        sell_amount = token_balance * pct
        # We increase priority fee for exits triggered by KOL sales
        priority_fee = 0.01 if "KOL EXIT" in reason else 0.001
        # Track actual fraction of initial position being sold
        actual_pct_sold = pct * getattr(pos, "remaining_fraction", 1.0)
        pos.remaining_fraction = max(0.0, getattr(pos, "remaining_fraction", 1.0) - actual_pct_sold)
        
        sell_profile = self._filter.profile if self._filter else get_profile(self._filter_profile_name)
        # In paper mode the client has no price feed of its own; hand it the live
        # ROI so simulated proceeds track the real market.
        if getattr(self._pump_client, "paper_enabled", False) and pos.entry_price > 0:
            current = getattr(pos, "current_price", 0.0) or pos.entry_price
            self._pump_client.set_paper_mark(pos.mint, current / pos.entry_price)
        result = await self._pump_client.execute_trade(
            pos.mint,
            action="sell",
            amount=sell_amount,
            denominated_in_sol=False,
            priority_fee=priority_fee,
            use_jito=sell_profile.use_jito,
        )
        await self._risk_manager.report_tx_result(result.success)
        if result.success:
            self._trades.append(result)
            await self._log_trade_event("sell", {
                "mint": pos.mint,
                "symbol": pos.symbol,
                "size": sell_amount,
                "success": True,
                "tx": result.tx_signature,
                "reason": reason,
                "latency_ms": result.latency_ms,
            })
            if hasattr(self, '_paste_trade') and self._paste_trade:
                current_price = getattr(pos, "current_price", 0.0) or pos.entry_price
                asyncio.create_task(self._paste_trade.post_trade(
                    ticker=pos.symbol,
                    direction="short",
                    author_price=current_price,
                    thesis=reason
                ))
            # Update active position size in RiskManager for partial sells
            if pos.active:
                self._risk_manager.state.active_positions[pos.mint] = pos.size * pos.remaining_fraction
            
            # ROI calculation
            roi = pos.current_price / pos.entry_price if pos.entry_price > 0 else 1.0
            
            # Check if this closes the position
            if pct >= 0.99 or pos.remaining_fraction <= 0.01:
                pos.active = False
                if pos.mint in self._positions: del self._positions[pos.mint]
                
                # Update Creator Genome outcome!
                if hasattr(self, '_creator_genome') and self._creator_genome:
                    ath = pos.highest_price / pos.entry_price if pos.entry_price > 0 else 1.0
                    survival_time = time() - pos.start_time
                    asyncio.create_task(self._creator_genome.track_trade_outcome(pos.creator, roi, ath, survival_time))
                
                # Update position status in DB
                pnl = roi - 1.0
                asyncio.create_task(self._db.update_position_pnl(pos.mint, pnl, "closed"))
                
                # Track closed position in RiskManager
                pnl_sol = pos.size * (roi - 1.0)
                await self._risk_manager.on_position_closed(pos.mint, pnl_sol)
                
                # Check for 100 trades retraining
                if len(self._trades) > 0 and len(self._trades) % 100 == 0:
                    asyncio.create_task(self._retrain_brain_weights())
            
            self._save_state()
            # Format the output notification
            # Estimated SOL received: pos.size * actual_pct_sold * roi
            sol_received = pos.size * actual_pct_sold * roi
            
            # Format custom message depending on the reason/exit trigger
            pos_id_str = f" #{pos.position_number}" if getattr(pos, "position_number", 0) > 0 else ""
            short_mint = f"{pos.mint[:6]}...{pos.mint[-4:]}"
            
            if "take-profit tp1" in reason.lower() or "tp1" in reason.lower():
                emoji = "🎯"
                title = f"Take-profit TP1 fired on{pos_id_str} ({short_mint})"
            elif "trailing-stop" in reason.lower() or "trailing stop" in reason.lower():
                emoji = "📉"
                title = f"Trailing-stop fired on{pos_id_str} ({short_mint})"
            elif "stop-loss" in reason.lower() or "stop loss" in reason.lower():
                emoji = "🚨"
                title = f"Stop-loss fired on{pos_id_str} ({short_mint})"
            else:
                emoji = "💸"
                title = f"Sell ({pct*100:.0f}%) executed on{pos_id_str} ({short_mint})"
                
            tx_part = f"`<a href='https://solscan.io/tx/{result.tx_signature}'>{result.tx_signature[:8]}...</a>`" if result.tx_signature else "N/A"
            msg = (
                f"{emoji} <b>{title}</b>\n\n"
                f"💰 Received: <b>{sol_received:.4f} SOL</b>\n"
                f"{tx_part}"
            )
            await self._telegram.send_message(msg)
        else:
            await self._log_trade_event("sell", {
                "mint": pos.mint,
                "symbol": pos.symbol,
                "size": sell_amount,
                "success": False,
                "error": result.error,
                "reason": reason,
            })

    async def _sync_existing_holdings(self):
        """Refresh balances for tracked positions only (do not import entire wallet)."""
        try:
            tokens = await self._pump_client.get_all_token_balances()
            updated = 0
            for mint, pos in list(self._positions.items()):
                if not pos.active:
                    continue
                balance = float(tokens.get(mint, {}).get("balance", 0) or 0)
                if balance > 0:
                    pos.size = max(pos.size, balance)
                    updated += 1
            logger.info(
                "Synced holdings for %s tracked positions (%s total on-chain accounts)",
                updated, len(tokens),
            )
            self._save_state()
        except Exception as e:
            logger.error(f"Failed to sync holdings: {e}")

    async def _db_log_launch(self, token: TokenEvent):
        """Log the launch of a new token to the ticks database and update creator launch stats."""
        try:
            # Check if token already exists to avoid duplicate logic
            existing = await self._db.get_tick(token.mint)
            if existing:
                return

            # Add to ticks
            await self._db.add_tick({
                'mint': token.mint,
                'creator': token.creator,
                'initial_liquidity': token.liquidity_sol,
                'max_marketcap': token.market_cap_usd or 10000.0,
                'exit_marketcap': token.market_cap_usd or 10000.0,
                'roi': 1.0,
                'timestamp': int(token.timestamp),
                'holder_data': {}
            })
            
            # Update creator profile in the creators table
            creator_profile = await self._db.get_creator(token.creator)
            if not creator_profile:
                await self._db.update_creator(
                    token.creator,
                    token_count=1,
                    avg_ath=token.market_cap_usd or 10000.0,
                    rug_count=0,
                    blacklist_score=0.0
                )
            else:
                new_count = (creator_profile.get('token_count') or 0) + 1
                new_ath = ((creator_profile.get('avg_ath') or 0.0) * (new_count - 1) + (token.market_cap_usd or 10000.0)) / new_count
                await self._db.update_creator(
                    token.creator,
                    token_count=new_count,
                    avg_ath=new_ath
                )
        except Exception as e:
            logger.error(f"Failed to log launch in DB: {e}")

    async def _brain_tracker_loop(self):
        """Autonomous Performance Tracker Loop (Central Powerhouse / Brain).
        Periodically checks token metrics on DexScreener/GeckoTerminal, classifies deployers,
        and auto-configures blacklist/smart wallets dynamically.
        """
        logger.info("Brain Performance Tracker Loop started.")
        while self._running:
            try:
                # Wait 5 minutes between checks to avoid rate limits
                await asyncio.sleep(300)
                
                # Fetch recent unresolved ticks (e.g. launched between 10 minutes and 2 hours ago)
                from time import time
                now = int(time())
                ten_mins_ago = now - 600
                two_hours_ago = now - 7200
                
                # Query ticks that are still pending check
                rows = await self._db._execute_read(
                    "SELECT mint, creator, max_marketcap, timestamp FROM ticks WHERE timestamp BETWEEN ? AND ?",
                    (two_hours_ago, ten_mins_ago)
                )
                
                for row in rows:
                    mint = row['mint']
                    creator = row['creator']
                    initial_cap = row['max_marketcap'] or 10000.0
                    
                    # Fetch current metrics from DexScreener
                    metrics = await self._dexscreener.get_price_metrics(mint)
                    
                    # If not indexed on DexScreener, try GeckoTerminal
                    if not metrics:
                        gecko_info = await self._gecko.get_token_info(mint)
                        if gecko_info:
                            fdv = gecko_info.get("fdv_usd")
                            if fdv:
                                metrics = {
                                    "market_cap_usd": float(fdv),
                                    "price_usd": float(gecko_info.get("price_usd", 0))
                                }
                                
                    # If metrics are available, classify the token
                    if metrics:
                        mcap = float(metrics.get("market_cap_usd") or 0.0)
                        if mcap > 0:
                            roi = mcap / initial_cap
                            
                            # Update tick performance and track peak market cap
                            await self._db._execute_write(
                                "UPDATE ticks SET exit_marketcap = ?, max_marketcap = CASE WHEN ? > max_marketcap THEN ? ELSE max_marketcap END, roi = ? WHERE mint = ?",
                                (mcap, mcap, mcap, roi, mint)
                            )
                            
                            profile = self._filter.profile if self._filter else get_profile(self._filter_profile_name)
                            rug_threshold = profile.brain_rug_mcap_usd
                            # Classification Logic
                            if mcap < rug_threshold:
                                await self._handle_detected_rug(creator, mint)
                            elif mcap >= 50000.0:
                                # Classify as RUNNER
                                await self._handle_detected_runner(creator, mint, mcap)
                                
                                # Send Telegram alert if not already notified
                                if mint not in self._daily_runners:
                                    sol_price = getattr(self._telegram, '_sol_price', 150.0)
                                    mcap_sol = mcap / sol_price if sol_price > 0 else 0.0
                                    asyncio.create_task(self._trigger_daily_runner_alert(mint, mcap_sol))
                    # No metrics yet — do not auto-count as rug (was over-blacklisting)
                        
            except Exception as e:
                logger.error(f"Error in brain tracker loop: {e}")

    async def _handle_detected_rug(self, creator: str, mint: str):
        """Update creator profile for a rug and auto-blacklist if threshold met."""
        if not creator or creator == "unknown": return
        try:
            profile = await self._db.get_creator(creator)
            rug_count = 1
            if profile:
                rug_count = (profile.get('rug_count') or 0) + 1
                await self._db.update_creator(creator, rug_count=rug_count)
            else:
                await self._db.update_creator(creator, token_count=1, avg_ath=10000.0, rug_count=1, blacklist_score=1.0)
                
            profile = self._filter.profile if self._filter else get_profile(self._filter_profile_name)
            if rug_count > profile.auto_blacklist_after_rugs:
                if creator not in self._blacklisted_wallets:
                    self._blacklisted_wallets.add(creator)
                    self._save_state()
                    logger.warning(f"🚨 BRAIN AUTO-BLACKLISTED RUGGER DEPLOYER: {creator} (Rugs: {rug_count})")
        except Exception as e:
            logger.error(f"Error handling detected rug: {e}")

    async def _handle_detected_runner(self, creator: str, mint: str, peak_mcap: float):
        """Update creator profile for a successful launch and auto-add to smart wallets."""
        if not creator or creator == "unknown": return
        try:
            profile = await self._db.get_creator(creator)
            if profile:
                rug_count = profile.get('rug_count') or 0
                token_count = profile.get('token_count') or 1
                # If they have a successful launch and no rugs, add as copy target / smart wallet!
                if rug_count == 0:
                    if self._filter and creator not in self._filter._copy_targets:
                        self._filter.add_copy_target(creator)
                        # Initialize wallet score
                        from solbot.filters import WalletScore
                        score = WalletScore(address=creator, alias=f"Smart_Maker_{creator[:4]}", score=90, total_trades=token_count, win_rate=1.0)
                        self._filter._wallet_scores[creator] = score
                        if hasattr(self, '_kol_tracker') and self._kol_tracker:
                            self._kol_tracker.add_wallet(creator, score.alias)
                        self._save_state()
                        logger.info(f"💎 BRAIN AUTO-ADDED SMART WALLET: {creator} (ATH: ${peak_mcap:,.0f})")
        except Exception as e:
            logger.error(f"Error handling detected runner: {e}")

    async def _trigger_daily_runner_alert(self, mint: str, mcap_sol: Optional[float] = None, reason: Optional[str] = None):
        try:
            # Fetch fresh metadata
            profile = self._filter.profile if self._filter else get_profile(self._filter_profile_name)
            meta = await self._pump_client.get_token_metadata(mint)
            if not profile.skip_mayhem_check:
                if await self._reject_mayhem_token(mint, meta.get("symbol", "RUNNER"), raw_hint=meta):
                    return
            name = meta.get("name", "Unknown")
            symbol = meta.get("symbol", "RUNNER")
            creator = meta.get("creator", "unknown")
            
            sol_price = getattr(self._telegram, '_sol_price', 150.0)
            
            if mcap_sol is None:
                mcap_sol = float(meta.get("market_cap_sol", 0.0) or 0.0)
            mcap_usd = float(mcap_sol) * sol_price

            # Save in daily runners candidates
            self._daily_runners[mint] = {
                'symbol': symbol,
                'name': name,
                'mcap_sol': mcap_sol,
                'detected_time': time(),
                'buys_count': len(self._daily_runner_buys.get(mint, {}).get('buys', []))
            }
            
            # Register in missed entry tracker
            self._missed_runners[mint] = {
                'symbol': symbol,
                'name': name,
                'alert_price_usd': mcap_usd,
                'alert_time': time(),
                'notified_milestones': set()
            }
            self._save_state()

            # Construct TokenEvent for sniping
            token = TokenEvent(
                mint=mint,
                name=name,
                symbol=symbol,
                creator=creator,
                market_cap_usd=mcap_usd,
                liquidity_sol=float(meta.get("liquidity_sol", 0.0) or 0.0),
                timestamp=time()
            )

            if reason is None:
                reason = "Detected 4+ big buys above $1000"

            if self._autorunner_enabled:
                logger.info(f"Auto-buying runner {symbol} ({mint}) | Size: {self._autorunner_amount} SOL")
                asyncio.create_task(self._execute_snipe(token, self._autorunner_amount, f"AutoRunner ({reason})"))
                
                alert_msg = (
                    f"🏃‍♂️ <b>DAILY RUNNER CANDIDATE DETECTED!</b> 🏃‍♂️\n\n"
                    f"Token: <b>{symbol}</b> ({name})\n"
                    f"Mint: <code>{mint}</code>\n"
                    f"Market Cap: <code>{mcap_sol:.1f} SOL</code> (${mcap_usd:,.0f})\n"
                    f"Daily Runner Reason: <i>{reason}</i>\n\n"
                    f"🤖 <b>Auto-Buy Triggered:</b> <code>{self._autorunner_amount} SOL</code>\n\n"
                    f"👉 <a href='https://pump.fun/{mint}'>Buy on pump.fun</a>"
                )
                if self._telegram:
                    await self._telegram.send_message(alert_msg)
            else:
                # Construct Telethon inline buttons for manual buy
                from telethon import Button
                buttons = [
                    [
                        Button.inline("Buy 0.1 SOL 🟢", f"buy_0.1_{mint}"),
                        Button.inline("Buy 0.3 SOL 🟡", f"buy_0.3_{mint}")
                    ],
                    [
                        Button.inline("Buy 0.5 SOL 🟠", f"buy_0.5_{mint}"),
                        Button.inline("Buy 1.0 SOL 🔥", f"buy_1.0_{mint}")
                    ]
                ]
                alert_msg = (
                    f"🏃‍♂️ <b>DAILY RUNNER CANDIDATE DETECTED!</b> 🏃‍♂️\n\n"
                    f"Token: <b>{symbol}</b> ({name})\n"
                    f"Mint: <code>{mint}</code>\n"
                    f"Market Cap: <code>{mcap_sol:.1f} SOL</code> (${mcap_usd:,.0f})\n"
                    f"Daily Runner Reason: <i>{reason}</i>\n\n"
                    f"👉 <a href='https://pump.fun/{mint}'>Buy on pump.fun</a>"
                )
                if self._telegram:
                    await self._telegram.send_message(alert_msg, buttons=buttons)
                
            logger.info(f"🏃‍♂️ DAILY RUNNER DETECTED: {symbol} ({mint}) | Reason: {reason}")
        except Exception as e:
            logger.error(f"Error triggering daily runner alert: {e}")

    async def _db_update_wallet_pnl(self, address: str, pnl_sol: float, is_win: int):
        """Track and update raw retail trader profit and loss statistics."""
        try:
            wallet = await self._db.get_wallet(address)
            if not wallet:
                # First trade tracked
                win_rate = 1.0 if is_win else 0.0
                await self._db.upsert_wallet(
                    address,
                    label="tracked_trader",
                    tier="retail",
                    win_rate=win_rate,
                    historical_roi=pnl_sol
                )
            else:
                current_roi = wallet.get('historical_roi') or 0.0
                current_win_rate = wallet.get('win_rate') or 0.0
                new_roi = current_roi + pnl_sol
                new_win_rate = (current_win_rate * 9 + is_win) / 10
                await self._db.upsert_wallet(
                    address,
                    label=wallet.get('label') or "tracked_trader",
                    tier=wallet.get('tier') or "retail",
                    win_rate=new_win_rate,
                    historical_roi=new_roi
                )
        except Exception as e:
            logger.error(f"Failed to update wallet PnL in database: {e}")

    async def _daily_wallet_scanner_loop(self):
        """Autonomously scans the database for retail wallets making profits daily
        and promotes them to Smart Wallets / Copy Targets.
        """
        logger.info("Daily Wallet Scanner Loop started.")
        while self._running:
            try:
                # Scan every 1 hour (can be adjusted)
                await asyncio.sleep(3600)
                
                # Fetch wallets with win_rate > 70% and positive PnL (historical_roi > 0.5 SOL)
                rows = await self._db._execute_read(
                    "SELECT address, win_rate, historical_roi FROM wallets WHERE win_rate >= 0.7 AND historical_roi >= 0.5 AND tier = 'retail'"
                )
                
                for row in rows:
                    addr = row['address']
                    win_rate = row['win_rate']
                    roi = row['historical_roi']
                    
                    # Promote to copy target
                    if self._filter and addr not in self._filter._copy_targets:
                        self._filter.add_copy_target(addr)
                        # Update database tier and label
                        await self._db.upsert_wallet(
                            addr,
                            label="smart_wallet",
                            tier="smart",
                            win_rate=win_rate,
                            historical_roi=roi
                        )
                        # Add to wallet scores
                        from solbot.filters import WalletScore
                        score = WalletScore(address=addr, alias=f"Smart_Scanner_{addr[:4]}", score=95, total_trades=10, win_rate=win_rate)
                        self._filter._wallet_scores[addr] = score
                        if hasattr(self, '_kol_tracker') and self._kol_tracker:
                            self._kol_tracker.add_wallet(addr, score.alias)
                        
                        logger.info(f"🚀 DAILY SCANNER AUTO-PROMOTED WALLET TO SMART WALLET: {addr} (Win Rate: {win_rate*100:.1f}%, ROI: {roi:.2f} SOL)")
            except Exception as e:
                logger.error(f"Error in daily wallet scanner loop: {e}")

    async def _market_sentiment_adapter_loop(self):
        """AGI Market Sentiment Adapter.
        Periodically checks the success rate of recent token launches in the database,
        determines the overall market state, and autonomously adjusts trading size,
        stop losses, and Jito transaction priorities to maximize land-rates and win-rates.
        """
        logger.info("AGI Market Sentiment Adapter started.")
        while self._running:
            try:
                # Run evaluation every 10 minutes
                await asyncio.sleep(600)
                
                # Query the database for the 50 most recent token launches
                rows = await self._db._execute_read(
                    "SELECT exit_marketcap, max_marketcap FROM ticks ORDER BY timestamp DESC LIMIT 50"
                )
                
                if len(rows) < 10:
                    # Not enough historical database data yet, skip adapter execution
                    continue
                    
                total = len(rows)
                runners = 0
                for row in rows:
                    row_dict = dict(row)
                    max_cap = row_dict.get('max_marketcap') or 0.0
                    exit_cap = row_dict.get('exit_marketcap') or 0.0
                    peak = max(max_cap, exit_cap)
                    if peak >= 50000.0:
                        runners += 1
                        
                success_rate = runners / total
                logger.info(f"AGI Market sentiment evaluation: Success Rate = {success_rate*100:.1f}% ({runners}/{total} runners)")
                
                current_buy = self._config.jupiter.buy_amount_sol
                current_stop = self._config.strategy.trailing_stop_pct
                
                # Autonomously shift risk mode based on success rate
                if success_rate >= 0.25:
                    # Hot market (Meme season) -> DEGEN Mode
                    new_buy = 0.02
                    new_stop = 0.30
                    mode_str = "DEGEN (Bullish Meme Season 🌋)"
                elif success_rate >= 0.10:
                    # Standard market -> NORMAL Mode
                    new_buy = 0.005
                    new_stop = 0.20
                    mode_str = "NORMAL (Stable Market ⚖️)"
                else:
                    # Dangerous market (Rug fest) -> SAFE Mode
                    new_buy = 0.001
                    new_stop = 0.10
                    mode_str = "SAFE (High Rug Danger 🛡)"
                    
                # Query the bot's own recent trades for micro-performance adaptation
                own_trades = await self._db._execute_read(
                    "SELECT pnl FROM positions WHERE status = 'closed' ORDER BY timestamp DESC LIMIT 10"
                )
                
                # Check micro-performance
                if len(own_trades) >= 5:
                    wins = sum(1 for r in own_trades if (dict(r).get('pnl') or 0.0) > 0.0)
                    own_win_rate = wins / len(own_trades)
                    logger.info(f"AGI Self-performance evaluation: Own Win Rate = {own_win_rate*100:.1f}% ({wins}/{len(own_trades)} wins)")
                    
                    # Self-correcting risk adaptations
                    if own_win_rate < 0.30:
                        # Underperforming -> increase safety, reduce size
                        self._ai_min_score = min(90, self._ai_min_score + 5)
                        new_buy = new_buy * 0.5
                        mode_str += " [Self-Correction: Tightening Risk 🛡]"
                    elif own_win_rate >= 0.60:
                        # Outperforming -> reduce safety slightly, increase size
                        self._ai_min_score = max(65, self._ai_min_score - 5)
                        new_buy = new_buy * 1.5
                        mode_str += " [Self-Correction: Aggressive Scaling 🌋]"
                
                # Update config properties if they differ from current settings
                if new_buy != current_buy or new_stop != current_stop:
                    object.__setattr__(self._config.jupiter, "buy_amount_sol", new_buy)
                    object.__setattr__(self._config.strategy, "trailing_stop_pct", new_stop)
                    self._save_state()
                    
                    logger.info(f"🧠 AGI AUTO-ADJUSTED RISK: Shifted to {mode_str}. Size: {new_buy} SOL, Trailing Stop: {new_stop*100:.0f}%")
                    if self._telegram and os.getenv("AGI_NOTIFICATIONS", "true").lower() == "true":
                        await self._telegram.send_message(
                            f"🧠 <b>AGI Sentiment Shift & Self-Correction</b>\n"
                            f"Evaluated last 50 launches: <code>{success_rate*100:.1f}%</code> success rate.\n"
                            f"Autonomously shifted bot mode to **{mode_str}**.\n"
                            f"Default Snipe Size: <code>{new_buy} SOL</code>\n"
                            f"Trailing Stop Limit: <code>{new_stop*100:.0f}%</code>\n"
                            f"AI Filter Min Score: <code>{self._ai_min_score}</code>"
                        )
            except Exception as e:
                logger.error(f"Error in AGI sentiment adapter loop: {e}")

    async def _missed_entry_tracker_loop(self):
        """AGI Missed Entry Regret Engine.
        Tracks every runner alert that was sent but not bought.
        Monitors their price and fires a TG regret notification
        at 5x, 10x, and 100x milestones so you feel the miss.
        Tokens are tracked for up to 24 hours then discarded.
        """
        logger.info("AGI Missed Entry Tracker started — watching for regret opportunities.")
        MILESTONES = [
            (5.0,   "5x",   "⚠️"),
            (10.0,  "10x",  "💀"),
            (100.0, "100x", "🚀"),
        ]
        MAX_AGE_SECS = 86400  # 24 hours
        POLL_INTERVAL = 300   # 5 minutes

        while self._running:
            await asyncio.sleep(POLL_INTERVAL)
            if not self._missed_runners:
                continue
            try:
                now = time()
                stale = [mint for mint, info in self._missed_runners.items()
                         if now - info['alert_time'] > MAX_AGE_SECS]
                for mint in stale:
                    self._missed_runners.pop(mint, None)

                for mint, info in list(self._missed_runners.items()):
                    # Skip if the user ended up buying it manually
                    if mint in self._positions:
                        self._missed_runners.pop(mint, None)
                        continue

                    alert_price = info.get('alert_price_usd', 0.0)
                    if alert_price <= 0:
                        continue

                    # Fetch current price
                    current_price = 0.0
                    try:
                        metrics = await self._dexscreener.get_price_metrics(mint)
                        if metrics:
                            current_price = float(metrics.get('market_cap_usd') or 0.0)
                        if not current_price:
                            gecko = await self._gecko.get_token_info(mint)
                            if gecko:
                                current_price = float(gecko.get('fdv_usd') or 0.0)
                        if not current_price:
                            # Last resort: pump.fun bonding curve
                            sol_price = self._telegram._sol_price if self._telegram else 150.0
                            current_price = await self._pump_client.get_bonding_curve_mcap(mint, sol_price)
                    except Exception:
                        continue

                    if current_price <= 0:
                        continue

                    gain = current_price / alert_price
                    elapsed_mins = int((now - info['alert_time']) / 60)

                    for threshold, label, emoji in MILESTONES:
                        if gain >= threshold and label not in info['notified_milestones']:
                            info['notified_milestones'].add(label)
                            self._missed_runner_engine.add_missed_token(
                                symbol=info['symbol'],
                                name=info['name'],
                                mint=mint,
                                alert_mcap=alert_price,
                                current_mcap=current_price,
                                multiplier=gain,
                                elapsed_mins=elapsed_mins,
                            )
                            missed_sol = (current_price - alert_price) / (self._telegram._sol_price if self._telegram and self._telegram._sol_price > 0 else 150.0)
                            msg = (
                                f"{emoji} <b>YOU MISSED {label} PROFIT! {emoji}</b>\n\n"
                                f"Token: <b>{info['symbol']}</b> ({info['name']})\n"
                                f"Mint: <code>{mint}</code>\n"
                                f"Alert Price: <code>${alert_price:,.0f} MCAP</code>\n"
                                f"Current Price: <code>${current_price:,.0f} MCAP</code>\n"
                                f"Gain: <code>{gain:.1f}x</code> in <code>{elapsed_mins} min</code>\n"
                                f"Missed Profit (1 SOL): <code>≈ {gain - 1:.1f} SOL</code>\n"
                                f"Missed Profit (5 SOL): <code>≈ {(gain - 1) * 5:.1f} SOL</code>\n\n"
                                f"📚 Saved to /brain for AGI learning.\n"
                                f"👉 <a href='https://pump.fun/{mint}'>View on pump.fun</a>"
                            )
                            if self._telegram:
                                await self._telegram.send_message(msg)
                            logger.warning(f"MISSED ENTRY: {info['symbol']} hit {label} ({gain:.1f}x) — entry was ${alert_price:,.0f}")

                            # Log to brain_events table
                            try:
                                import uuid
                                await self._db._execute_write(
                                    "INSERT INTO brain_events (event_id, command, details, timestamp) VALUES (?, ?, ?, ?)",
                                    (str(uuid.uuid4()), 'missed_entry',
                                     f"{info['symbol']}|{mint}|{label}|{gain:.2f}x|${current_price:,.0f}",
                                     now)
                                )
                            except Exception:
                                pass

            except Exception as e:
                logger.error(f"Error in missed entry tracker: {e}")

    async def execute_manual_buy(self, mint: str, amount: float, status_msg=None):
        """Executes a manual buy triggered from Telegram UI buttons."""
        logger.info(f"Manual buy triggered via TG button for {mint} | Size: {amount} SOL")
        try:
            meta = await self._pump_client.get_token_metadata(mint)
            symbol = meta.get("symbol", "MANUAL")
            token = TokenEvent(
                mint=mint,
                name=meta.get("name", "Unknown"),
                symbol=symbol,
                creator=meta.get("creator", "unknown"),
                market_cap_usd=float(meta.get("market_cap_sol", 0)) * self._telegram._sol_price,
                liquidity_sol=float(meta.get("liquidity_sol", 0)),
                timestamp=time()
            )
            # Execute buy using client
            await self._execute_snipe(token, amount, f"TG Manual Button", status_msg, manual_override=True)
        except Exception as e:
            logger.error(f"Error executing manual buy: {e}")
            if status_msg:
                try:
                    await status_msg.edit(f"⚡️ <b>TG Manual Buy Clicked!</b>\nTarget: <code>{mint}</code>\nAmount: <code>{amount} SOL</code>\nStatus: <code>❌ FAILED ({e})</code>", parse_mode='html')
                except Exception:
                    pass
            elif self._telegram:
                await self._telegram.send_message(f"❌ <b>Manual Buy Failed:</b> <code>{e}</code>")

    async def execute_manual_sell(self, mint: str, pct: float, status_msg=None):
        """Executes a manual sell triggered from Telegram UI buttons."""
        logger.info(f"Manual sell triggered via TG button for {mint} | Pct: {pct*100:.0f}%")
        try:
            pos = self._positions.get(mint)
            if not pos:
                raise ValueError("Position not found or already closed.")
            
            await self._exit_position(pos, "TG Manual Button", pct)
            if status_msg:
                try:
                    await status_msg.edit(
                        f"⚡️ <b>TG Manual Sell Clicked!</b>\n"
                        f"Target: <code>{mint}</code>\n"
                        f"Percent: <code>{pct*100:.0f}%</code>\n"
                        f"Status: <code>🟢 SUCCESS</code>",
                        parse_mode='html'
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"Error executing manual sell: {e}")
            if status_msg:
                try:
                    await status_msg.edit(
                        f"⚡️ <b>TG Manual Sell Clicked!</b>\n"
                        f"Target: <code>{mint}</code>\n"
                        f"Percent: <code>{pct*100:.0f}%</code>\n"
                        f"Status: <code>❌ FAILED ({e})</code>",
                        parse_mode='html'
                    )
                except Exception:
                    pass

    async def _retrain_brain_weights(self):
        """Autonomously adapts AGI parameters after every 100 completed trades."""
        logger.info("🧠 AGI BRAIN: Retraining weights and adjusting parameters...")
        try:
            rows = await self._db._execute_read(
                "SELECT * FROM positions WHERE status = 'closed' ORDER BY timestamp DESC LIMIT 100"
            )
            trades = [dict(r) for r in rows]
            if len(trades) < 10:
                logger.info("AGI BRAIN: Not enough completed trades for retraining yet.")
                return
                
            wins = [t for t in trades if (t.get('pnl') or 0.0) > 0.0]
            win_rate = len(wins) / len(trades)
            
            # Adapt thresholds
            old_ai_threshold = self._ai_min_score
            if win_rate < 0.35:
                # Tighten rules to avoid losses
                self._ai_min_score = min(90, self._ai_min_score + 5)
            elif win_rate >= 0.55:
                # Relax rules to capture more opportunities
                self._ai_min_score = max(65, self._ai_min_score - 5)
                
            # Log to Telegram
            if self._telegram and os.getenv("AGI_NOTIFICATIONS", "true").lower() == "true":
                await self._telegram.send_message(
                    f"🧠 <b>SOLBOT AGI BRAIN V4 RETRAINED</b>\n\n"
                    f"Processed Trades: <code>{len(trades)}</code>\n"
                    f"Win Rate: <code>{win_rate*100:.1f}%</code>\n"
                    f"AI Threshold: <code>{old_ai_threshold}</code> ➔ <code>{self._ai_min_score}</code>"
                 )
        except Exception as e:
            logger.error(f"Error retraining brain weights: {e}")

    async def _ai_autotune_loop(self):
        """Asynchronous background loop for AI Autotuning."""
        logger.info("AI Autotune Loop started.")
        while self._running:
            try:
                # Wait 4 hours between autotunes (14400s)
                await asyncio.sleep(14400)
                if self._ai_enabled:
                    logger.info("Running scheduled AI Autotuning...")
                    success, report = await self._ai_tuner.autotune()
                    if success and self._telegram and os.getenv("AGI_NOTIFICATIONS", "true").lower() == "true":
                        await self._telegram.send_message(report)
                    
                    if getattr(self._config.brain, "enabled", False):
                        logger.info("Running AGI Brain scheduled autotune/retraining...")
                        brain_success, brain_report = await self._brain.autotune()
                        if brain_success and self._telegram:
                            await self._telegram.send_message(f"🧠 <b>AGI Brain Autotuner:</b> {brain_report}")
            except Exception as e:
                logger.error(f"Error in AI autotune loop: {e}")
                await asyncio.sleep(60)

    async def _poll_network_congestion(self):
        """Polls network congestion and Jito tips to dynamically scale transaction fees."""
        import aiohttp
        while self._running:
            try:
                # 1. Fetch Jito tip estimation
                url = "https://bundles.jito.wtf/api/v1/bundles/tip_floor"
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=5) as resp:
                        if resp.status == 200:
                            tips_data = await resp.json()
                            tip_info = {}
                            if isinstance(tips_data, list) and len(tips_data) > 0:
                                tip_info = tips_data[0]
                            elif isinstance(tips_data, dict):
                                tip_info = tips_data
                                
                            if tip_info:
                                p50 = float(tip_info.get("landed_tips_50th_percentile", 0.001))
                                p75 = float(tip_info.get("landed_tips_75th_percentile", 0.002))
                                p95 = float(tip_info.get("landed_tips_95th_percentile", 0.005))
                                
                                # Scale tip size based on congestion bands
                                if p95 > 0.008:
                                    self._congestion_level = "high"
                                    self._dynamic_jito_tip = max(0.003, min(0.01, p75))
                                elif p95 > 0.003:
                                    self._congestion_level = "medium"
                                    self._dynamic_jito_tip = max(0.0015, min(0.004, p50))
                                else:
                                    self._congestion_level = "low"
                                    self._dynamic_jito_tip = max(0.0005, min(0.002, p50))
                            else:
                                logger.warning("Invalid Jito tips payload format.")
                        else:
                            logger.warning(f"Jito tips API returned status {resp.status}")
                
                # 2. Fetch RPC prioritized fee
                rpc_url = await self._pump_client._get_rpc_url()
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getRecentPrioritizationFees",
                    "params": [[]]
                }
                async with aiohttp.ClientSession() as session:
                    async with session.post(rpc_url, json=payload, timeout=5) as resp:
                        if resp.status == 200:
                            res = await resp.json()
                            fees = res.get("result", [])
                            if fees:
                                # Use median of recent fees
                                recent_fees = [f.get("prioritizationFee", 0) for f in fees[-20:]]
                                recent_fees.sort()
                                median_fee = recent_fees[len(recent_fees)//2]
                                # Convert micro-lamports per compute unit to dynamic fee in SOL (assuming 200,000 CU limit)
                                calculated_fee_sol = (median_fee * 200000) / 1e15
                                self._dynamic_priority_fee = max(0.00001, min(0.002, calculated_fee_sol))
                                
                logger.info(f"⚡ Congestion Poller: Level={self._congestion_level.upper()} | Dynamic Jito Tip={self._dynamic_jito_tip:.5f} SOL | Priority Fee={self._dynamic_priority_fee:.5f} SOL")
            except Exception as e:
                logger.error(f"Error in network congestion polling: {e}")
            await asyncio.sleep(15)

    async def _handle_kol_mention(self, mint: str, source_name: str, text: str):
        """Aggregates mentions from various KOL sources (channels/Twitter handles) before buying."""
        if not mint or len(mint) < 32 or len(mint) > 44:
            return
        
        now = time()
        
        # Initialize token tracker
        if mint not in self._kol_mentions:
            self._kol_mentions[mint] = {
                'sources': set(),
                'mentions': [],
                'notified': False
            }
            
        # Clean expired mentions (older than 3 hours / 10800s)
        self._kol_mentions[mint]['mentions'] = [
            m for m in self._kol_mentions[mint]['mentions']
            if now - m['timestamp'] < 10800
        ]
        
        # Rebuild sources set based on active mentions
        self._kol_mentions[mint]['sources'] = {m['source'] for m in self._kol_mentions[mint]['mentions']}
        
        # Add new mention
        if source_name not in self._kol_mentions[mint]['sources']:
            self._kol_mentions[mint]['mentions'].append({
                'source': source_name,
                'timestamp': now,
                'text': text
            })
            self._kol_mentions[mint]['sources'].add(source_name)
            
        unique_sources_count = len(self._kol_mentions[mint]['sources'])
        logger.info(f"📢 KOL Mention: {mint} mentioned by {source_name}. Total unique sources in 3h: {unique_sources_count}/{self._kol_threshold}")
        
        # Check if threshold reached and not yet notified/acted
        if unique_sources_count >= self._kol_threshold and not self._kol_mentions[mint]['notified']:
            self._kol_mentions[mint]['notified'] = True
            
            try:
                meta = await self._pump_client.get_token_metadata(mint)
                symbol = meta.get("symbol", "KOL_PICK")
                name = meta.get("name", "KOL Coordinated Token")
                creator = meta.get("creator", "unknown")
                mcap_sol = float(meta.get("market_cap_sol", 0.0) or 0.0)
                sol_price = getattr(self._telegram, '_sol_price', 150.0)
                mcap_usd = mcap_sol * sol_price
                
                token = TokenEvent(
                    mint=mint,
                    name=name,
                    symbol=symbol,
                    creator=creator,
                    market_cap_usd=mcap_usd,
                    liquidity_sol=float(meta.get("liquidity_sol", 0.0) or 0.0),
                    timestamp=now
                )
                
                mentions_list = ", ".join(self._kol_mentions[mint]['sources'])
                reason = f"Coordinated KOL Mentions ({unique_sources_count} sources: {mentions_list})"
                
                logger.warning(f"🔥 COORDINATED KOL SENTIMENT TRIGGERED for {symbol} ({mint}) with {unique_sources_count} sources.")
                
                if self._autobuy_enabled:
                    asyncio.create_task(self._execute_snipe(token, self._config.jupiter.buy_amount_sol, reason))
                else:
                    from telethon import Button
                    buttons = [
                        [
                            Button.inline("Buy 0.1 SOL 🟢", f"buy_0.1_{mint}"),
                            Button.inline("Buy 0.3 SOL 🟡", f"buy_0.3_{mint}")
                        ],
                        [
                            Button.inline("Buy 0.5 SOL 🟠", f"buy_0.5_{mint}"),
                            Button.inline("Buy 1.0 SOL 🔥", f"buy_1.0_{mint}")
                        ]
                    ]
                    alert_msg = (
                        f"📢 <b>COORDINATED KOL SENTIMENT DETECTED!</b> 📢\n\n"
                        f"Token: <b>{symbol}</b> ({name})\n"
                        f"Mint: <code>{mint}</code>\n"
                        f"Market Cap: <code>{mcap_sol:.1f} SOL</code> (${mcap_usd:,.0f})\n"
                        f"Sources: <i>{mentions_list}</i>\n\n"
                        f"👉 <a href='https://pump.fun/{mint}'>Buy on pump.fun</a>"
                    )
                    if self._telegram:
                        await self._telegram.send_message(alert_msg, buttons=buttons)
                        
            except Exception as e:
                logger.error(f"Error handling coordinated KOL mention trigger: {e}")

    async def stop(self):
        """Gracefully stop Solbot and all child services."""
        self._running = False
        if hasattr(self, "_hummingbot_pmm") and self._hummingbot_pmm:
            await self._hummingbot_pmm.stop()
        if hasattr(self, "_hummingbot_gateway") and self._hummingbot_gateway:
            await self._hummingbot_gateway.close()
        if hasattr(self, "_paste_trade") and self._paste_trade:
            await self._paste_trade.close()
        if self._monitor:
            self._monitor.stop()
        if self._pump_client:
            await self._pump_client.close()
        if self._jupiter:
            await self._jupiter.close()
        if self._telegram:
            await self._telegram.stop()
        self._save_state()
        logger.info("Solbot stopped gracefully.")

async def run_bot():
    config = BotConfig()
    bot = Solbot(config)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.stop()))
    await bot.start()
    while bot._running: await asyncio.sleep(1)
