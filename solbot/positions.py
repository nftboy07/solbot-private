"""Position manager with auto-sell logic.

Handles:
- Position tracking per token (open, monitor, close)
- Stop loss execution
- Take profit tiers (partial sells at configurable levels)
- Trailing stop (locks in gains after peak)
- Max concurrent positions enforcement
- Cooldown between buys
- Price monitoring loop for auto-sell triggers

All operations are async-safe and persist to SQLite.
"""

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from time import time
from typing import Callable, Coroutine, Optional

from solbot.database import Database
from solbot.logger import get_logger

logger = get_logger("positions")


class SellReason(Enum):
    """Why a position was sold."""
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT_1 = "take_profit_1"
    TAKE_PROFIT_2 = "take_profit_2"
    TAKE_PROFIT_3 = "take_profit_3"
    TRAILING_STOP = "trailing_stop"
    MANUAL = "manual"
    EMERGENCY = "emergency"
    RUG_DETECTED = "rug_detected"


@dataclass
class TakeProfitTier:
    """A single take-profit tier."""
    multiplier: float       # e.g., 2.0 = 2x entry price
    sell_pct: float         # e.g., 0.25 = sell 25% of position
    triggered: bool = False


@dataclass
class Position:
    """In-memory representation of an active position."""
    mint: str
    symbol: str
    name: str
    creator: str
    entry_price_sol: float
    entry_amount_tokens: float
    entry_tx: str
    confidence: str
    composite_score: float
    opened_at: float = field(default_factory=time)

    # Tracking state
    current_price_sol: float = 0.0
    highest_price_sol: float = 0.0
    remaining_tokens: float = 0.0  # After partial TP sells
    take_profit_tiers: list[TakeProfitTier] = field(default_factory=list)

    def __post_init__(self):
        if self.current_price_sol == 0.0:
            self.current_price_sol = self.entry_price_sol
        if self.highest_price_sol == 0.0:
            self.highest_price_sol = self.entry_price_sol
        if self.remaining_tokens == 0.0:
            self.remaining_tokens = self.entry_amount_tokens

    @property
    def pnl_pct(self) -> float:
        """Current P&L percentage."""
        if self.entry_price_sol <= 0:
            return 0.0
        return ((self.current_price_sol - self.entry_price_sol) / self.entry_price_sol) * 100

    @property
    def pnl_sol(self) -> float:
        """Estimated P&L in SOL (simplified)."""
        if self.entry_price_sol <= 0:
            return 0.0
        ratio = self.current_price_sol / self.entry_price_sol
        return (ratio - 1.0) * self.entry_price_sol

    @property
    def drawdown_from_high_pct(self) -> float:
        """How far current price has dropped from highest."""
        if self.highest_price_sol <= 0:
            return 0.0
        return ((self.highest_price_sol - self.current_price_sol) / self.highest_price_sol) * 100

    @property
    def age_seconds(self) -> float:
        return time() - self.opened_at


@dataclass
class TradingConfig:
    """Trading parameters - will be populated from config.py."""
    # Stop loss
    stop_loss_pct: float = 30.0  # Sell if down X%

    # Take profit tiers
    tp1_multiplier: float = 2.0   # 2x
    tp1_sell_pct: float = 0.30    # Sell 30%
    tp2_multiplier: float = 3.0   # 3x
    tp2_sell_pct: float = 0.30    # Sell 30%
    tp3_multiplier: float = 5.0   # 5x
    tp3_sell_pct: float = 0.40    # Sell remaining 40%

    # Trailing stop
    trailing_stop_pct: float = 20.0         # Activate after peak, sell if drops X% from high
    trailing_stop_activation_pct: float = 50.0  # Only activate trailing stop after X% gain

    # Position limits
    max_concurrent_positions: int = 5
    buy_cooldown_seconds: float = 10.0  # Min seconds between buys

    # Price check interval
    price_check_interval_seconds: float = 5.0


# Type alias for sell execution callback
SellCallback = Callable[[str, float, str], Coroutine]  # (mint, pct_to_sell, reason) -> TradeResult


class PositionManager:
    """Manages all open positions with auto-sell logic.

    The position manager runs a background monitoring loop that
    checks prices and triggers sells based on:
    1. Stop loss (hard floor)
    2. Take profit tiers (partial sells at milestones)
    3. Trailing stop (locks in gains after peak)
    """

    def __init__(self, config: TradingConfig, db: Database):
        self._config = config
        self._db = db
        self._positions: dict[str, Position] = {}  # mint -> Position
        self._lock = asyncio.Lock()
        self._last_buy_time: float = 0.0
        self._monitoring = False
        self._monitor_task: Optional[asyncio.Task] = None
        self._sell_callback: Optional[SellCallback] = None

    @property
    def open_count(self) -> int:
        """Number of currently open positions."""
        return len(self._positions)

    @property
    def positions(self) -> dict[str, Position]:
        """Access to all open positions."""
        return self._positions

    def set_sell_callback(self, callback: SellCallback):
        """Set the callback function for executing sells.

        The callback signature is: async def sell(mint, pct_to_sell, reason) -> TradeResult
        """
        self._sell_callback = callback

    async def initialize(self):
        """Load open positions from database on startup."""
        rows = await self._db.get_open_positions()
        async with self._lock:
            for row in rows:
                pos = Position(
                    mint=row["mint"],
                    symbol=row["symbol"],
                    name=row["name"],
                    creator=row["creator"],
                    entry_price_sol=row["entry_price_sol"],
                    entry_amount_tokens=row["entry_amount_tokens"],
                    entry_tx=row["entry_tx"],
                    confidence=row["confidence"],
                    composite_score=row["composite_score"],
                    opened_at=row["opened_at"],
                    current_price_sol=row["current_price_sol"],
                    highest_price_sol=row["highest_price_sol"],
                )
                pos.take_profit_tiers = self._create_tp_tiers()
                self._positions[row["mint"]] = pos

        count = len(self._positions)
        if count > 0:
            logger.info(f"Restored {count} open positions from database")

    async def start_monitoring(self):
        """Start the background price monitoring loop."""
        if self._monitoring:
            return
        self._monitoring = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("Position monitoring started")

    async def stop_monitoring(self):
        """Stop the background monitoring loop."""
        self._monitoring = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        logger.info("Position monitoring stopped")

    def can_buy(self) -> bool:
        """Check if we can open a new position (limits + cooldown)."""
        # Max positions check
        if self.open_count >= self._config.max_concurrent_positions:
            logger.debug(
                f"Max positions reached ({self.open_count}/{self._config.max_concurrent_positions})"
            )
            return False

        # Cooldown check
        elapsed = time() - self._last_buy_time
        if elapsed < self._config.buy_cooldown_seconds:
            remaining = self._config.buy_cooldown_seconds - elapsed
            logger.debug(f"Buy cooldown active ({remaining:.1f}s remaining)")
            return False

        return True

    def has_position(self, mint: str) -> bool:
        """Check if we already have a position in this token."""
        return mint in self._positions

    async def open_position(
        self,
        mint: str,
        symbol: str,
        name: str,
        creator: str,
        entry_price_sol: float,
        entry_amount_tokens: float,
        entry_tx: str,
        confidence: str,
        composite_score: float,
    ) -> Position:
        """Open a new position and persist it.

        Args:
            mint: Token mint address.
            symbol: Token symbol.
            name: Token name.
            creator: Creator address.
            entry_price_sol: Amount of SOL spent.
            entry_amount_tokens: Tokens received.
            entry_tx: Buy transaction signature.
            confidence: Confidence level (HIGH/MEDIUM/LOW).
            composite_score: Composite score from scoring engine.

        Returns:
            The new Position object.
        """
        async with self._lock:
            pos = Position(
                mint=mint,
                symbol=symbol,
                name=name,
                creator=creator,
                entry_price_sol=entry_price_sol,
                entry_amount_tokens=entry_amount_tokens,
                entry_tx=entry_tx,
                confidence=confidence,
                composite_score=composite_score,
            )
            pos.take_profit_tiers = self._create_tp_tiers()
            self._positions[mint] = pos
            self._last_buy_time = time()

        # Persist to database
        await self._db.insert_position(
            mint=mint,
            symbol=symbol,
            name=name,
            creator=creator,
            entry_price_sol=entry_price_sol,
            entry_amount_tokens=entry_amount_tokens,
            entry_tx=entry_tx,
            confidence=confidence,
            composite_score=composite_score,
        )

        logger.info(
            f"POSITION OPENED: {symbol} | entry={entry_price_sol:.4f} SOL | "
            f"tokens={entry_amount_tokens:.0f} | positions={self.open_count}/"
            f"{self._config.max_concurrent_positions}"
        )
        return pos

    async def close_position(
        self,
        mint: str,
        exit_price_sol: float,
        exit_amount_sol: float,
        exit_tx: str,
        reason: SellReason,
    ):
        """Close a position and persist the result.

        Args:
            mint: Token mint to close.
            exit_price_sol: Price at exit.
            exit_amount_sol: SOL received from sell.
            exit_tx: Sell transaction signature.
            reason: Why the position was closed.
        """
        async with self._lock:
            pos = self._positions.pop(mint, None)

        if not pos:
            logger.warning(f"Attempted to close unknown position: {mint[:12]}...")
            return

        pnl_sol = exit_amount_sol - pos.entry_price_sol
        pnl_pct = ((exit_amount_sol / pos.entry_price_sol) - 1.0) * 100 if pos.entry_price_sol > 0 else 0.0

        await self._db.close_position(
            mint=mint,
            exit_price_sol=exit_price_sol,
            exit_amount_sol=exit_amount_sol,
            exit_tx=exit_tx,
            pnl_sol=pnl_sol,
            pnl_pct=pnl_pct,
            sell_reason=reason.value,
        )

        logger.info(
            f"POSITION CLOSED: {pos.symbol} | reason={reason.value} | "
            f"pnl={pnl_pct:+.1f}% ({pnl_sol:+.4f} SOL) | "
            f"held={pos.age_seconds:.0f}s"
        )

    async def update_price(self, mint: str, current_price_sol: float):
        """Update the current price for a position.

        Called by the price monitoring loop.
        """
        async with self._lock:
            pos = self._positions.get(mint)
            if not pos:
                return

            pos.current_price_sol = current_price_sol
            if current_price_sol > pos.highest_price_sol:
                pos.highest_price_sol = current_price_sol

        # Persist price update
        await self._db.update_position_price(
            mint=mint,
            current_price_sol=current_price_sol,
            highest_price_sol=pos.highest_price_sol,
        )

    async def emergency_close_all(self) -> list[str]:
        """Emergency: close all positions immediately.

        Returns:
            List of mints that were closed.
        """
        logger.warning("EMERGENCY CLOSE ALL POSITIONS")
        mints = list(self._positions.keys())

        for mint in mints:
            if self._sell_callback:
                try:
                    await self._sell_callback(mint, 1.0, SellReason.EMERGENCY.value)
                except Exception as e:
                    logger.error(f"Emergency sell failed for {mint[:12]}...: {e}")

        return mints

    async def force_sell_by_creator(self, creator: str, reason: SellReason) -> list[str]:
        """Sell all positions from a specific creator (e.g., on rug detection).

        Args:
            creator: Creator address whose tokens to sell.
            reason: Sell reason.

        Returns:
            List of mints that were sold.
        """
        mints_to_sell = []
        async with self._lock:
            for mint, pos in self._positions.items():
                if pos.creator == creator:
                    mints_to_sell.append(mint)

        for mint in mints_to_sell:
            if self._sell_callback:
                try:
                    await self._sell_callback(mint, 1.0, reason.value)
                except Exception as e:
                    logger.error(f"Force sell failed for {mint[:12]}...: {e}")

        return mints_to_sell

    # ── Private: Monitoring Loop ────────────────────────────────────────

    async def _monitor_loop(self):
        """Background loop that checks positions for sell triggers."""
        while self._monitoring:
            try:
                await self._check_all_positions()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Monitor loop error: {e}")

            await asyncio.sleep(self._config.price_check_interval_seconds)

    async def _check_all_positions(self):
        """Check all open positions for sell triggers."""
        # Snapshot positions to avoid holding lock during sells
        async with self._lock:
            positions_snapshot = list(self._positions.items())

        for mint, pos in positions_snapshot:
            action = self._evaluate_position(pos)
            if action:
                sell_reason, sell_pct = action
                if self._sell_callback:
                    try:
                        await self._sell_callback(mint, sell_pct, sell_reason.value)
                    except Exception as e:
                        logger.error(f"Auto-sell failed for {pos.symbol}: {e}")

    def _evaluate_position(self, pos: Position) -> Optional[tuple[SellReason, float]]:
        """Evaluate a position against all sell triggers.

        Returns:
            (SellReason, sell_pct) if a sell should be triggered, None otherwise.
        """
        if pos.entry_price_sol <= 0 or pos.current_price_sol <= 0:
            return None

        current_multiple = pos.current_price_sol / pos.entry_price_sol

        # 1. STOP LOSS (highest priority)
        if pos.pnl_pct <= -self._config.stop_loss_pct:
            logger.warning(
                f"STOP LOSS triggered: {pos.symbol} | pnl={pos.pnl_pct:.1f}%"
            )
            return (SellReason.STOP_LOSS, 1.0)  # Sell 100%

        # 2. TRAILING STOP (only if activated)
        gain_pct = ((pos.highest_price_sol - pos.entry_price_sol) / pos.entry_price_sol) * 100
        if gain_pct >= self._config.trailing_stop_activation_pct:
            if pos.drawdown_from_high_pct >= self._config.trailing_stop_pct:
                logger.info(
                    f"TRAILING STOP triggered: {pos.symbol} | "
                    f"peak_gain={gain_pct:.1f}% | drawdown={pos.drawdown_from_high_pct:.1f}%"
                )
                return (SellReason.TRAILING_STOP, 1.0)  # Sell 100%

        # 3. TAKE PROFIT TIERS (sell partial amounts)
        for i, tier in enumerate(pos.take_profit_tiers):
            if tier.triggered:
                continue
            if current_multiple >= tier.multiplier:
                tier.triggered = True
                reason = [SellReason.TAKE_PROFIT_1, SellReason.TAKE_PROFIT_2, SellReason.TAKE_PROFIT_3][min(i, 2)]
                logger.info(
                    f"TAKE PROFIT {i+1} triggered: {pos.symbol} | "
                    f"{current_multiple:.1f}x | selling {tier.sell_pct*100:.0f}%"
                )
                return (reason, tier.sell_pct)

        return None

    def _create_tp_tiers(self) -> list[TakeProfitTier]:
        """Create take-profit tiers from config."""
        return [
            TakeProfitTier(
                multiplier=self._config.tp1_multiplier,
                sell_pct=self._config.tp1_sell_pct,
            ),
            TakeProfitTier(
                multiplier=self._config.tp2_multiplier,
                sell_pct=self._config.tp2_sell_pct,
            ),
            TakeProfitTier(
                multiplier=self._config.tp3_multiplier,
                sell_pct=self._config.tp3_sell_pct,
            ),
        ]

    # ── Utility ─────────────────────────────────────────────────────────

    def get_summary(self) -> dict:
        """Get a summary of all open positions."""
        total_invested = sum(p.entry_price_sol for p in self._positions.values())
        total_pnl = sum(p.pnl_sol for p in self._positions.values())
        return {
            "open_positions": self.open_count,
            "max_positions": self._config.max_concurrent_positions,
            "total_invested_sol": total_invested,
            "total_unrealized_pnl_sol": total_pnl,
            "positions": [
                {
                    "symbol": p.symbol,
                    "mint": p.mint[:12] + "...",
                    "entry": p.entry_price_sol,
                    "pnl_pct": p.pnl_pct,
                    "age_s": p.age_seconds,
                }
                for p in self._positions.values()
            ],
        }
