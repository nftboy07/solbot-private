"""Portfolio Risk Guard, Max Drawdown Circuit Breakers, and Emergency Liquidation."""

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

logger = logging.getLogger("bot.portfolio_guard")


@dataclass
class GuardStatus:
    """Current portfolio safety and circuit breaker status."""
    circuit_breaker_tripped: bool
    daily_drawdown_pct: float
    max_drawdown_allowed_pct: float
    active_exposure_sol: float
    creator_exposure_sol: Dict[str, float] = field(default_factory=dict)
    reason: str = "NORMAL"


class PortfolioGuard:
    """Monitors total capital at risk and enforces circuit breakers."""

    def __init__(
        self,
        max_daily_drawdown_pct: float = 0.15,
        max_creator_exposure_sol: float = 0.50,
        min_wallet_reserve_sol: float = 0.05,
    ):
        self.max_daily_drawdown_pct = max_daily_drawdown_pct
        self.max_creator_exposure_sol = max_creator_exposure_sol
        self.min_wallet_reserve_sol = min_wallet_reserve_sol
        self._starting_day_balance_sol: float = 0.0
        self._last_day_reset: float = time.time()
        self._circuit_tripped: bool = False

    def update_starting_balance(self, balance_sol: float):
        """Set or reset starting daily balance."""
        now = time.time()
        if self._starting_day_balance_sol <= 0.0 or (now - self._last_day_reset) >= 86400:
            self._starting_day_balance_sol = max(balance_sol, 0.01)
            self._last_day_reset = now
            self._circuit_tripped = False
            logger.info("PortfolioGuard: Reset daily starting balance to %.4f SOL", balance_sol)

    def check_buy_allowed(
        self,
        current_wallet_balance_sol: float,
        creator: str,
        buy_amount_sol: float,
        active_positions: Dict[str, Any],
    ) -> GuardStatus:
        """Evaluate whether a new buy is permitted under risk rules."""
        self.update_starting_balance(current_wallet_balance_sol)

        # 1. Check daily drawdown
        drawdown_pct = 0.0
        if self._starting_day_balance_sol > 0:
            loss_sol = max(0.0, self._starting_day_balance_sol - current_wallet_balance_sol)
            drawdown_pct = loss_sol / self._starting_day_balance_sol

        if drawdown_pct >= self.max_daily_drawdown_pct:
            self._circuit_tripped = True
            return GuardStatus(
                circuit_breaker_tripped=True,
                daily_drawdown_pct=drawdown_pct,
                max_drawdown_allowed_pct=self.max_daily_drawdown_pct,
                active_exposure_sol=0.0,
                reason=f"Daily drawdown {drawdown_pct*100:.1f}% exceeded max limit {self.max_daily_drawdown_pct*100:.1f}%",
            )

        # 2. Check wallet reserve
        if (current_wallet_balance_sol - buy_amount_sol) < self.min_wallet_reserve_sol:
            return GuardStatus(
                circuit_breaker_tripped=True,
                daily_drawdown_pct=drawdown_pct,
                max_drawdown_allowed_pct=self.max_daily_drawdown_pct,
                active_exposure_sol=0.0,
                reason=f"Insufficient reserve (balance would drop below {self.min_wallet_reserve_sol:.4f} SOL)",
            )

        # 3. Check creator exposure cap
        creator_exposure = 0.0
        for pos in active_positions.values():
            if getattr(pos, "creator", "") == creator and getattr(pos, "active", True):
                creator_exposure += getattr(pos, "size", 0.0)

        if (creator_exposure + buy_amount_sol) > self.max_creator_exposure_sol:
            return GuardStatus(
                circuit_breaker_tripped=True,
                daily_drawdown_pct=drawdown_pct,
                max_drawdown_allowed_pct=self.max_daily_drawdown_pct,
                active_exposure_sol=creator_exposure,
                reason=f"Creator exposure ({creator_exposure + buy_amount_sol:.2f} SOL) exceeds cap ({self.max_creator_exposure_sol:.2f} SOL)",
            )

        return GuardStatus(
            circuit_breaker_tripped=False,
            daily_drawdown_pct=drawdown_pct,
            max_drawdown_allowed_pct=self.max_daily_drawdown_pct,
            active_exposure_sol=creator_exposure,
            reason="NORMAL",
        )

    def reset_circuit_breaker(self):
        """Manually reset circuit breaker."""
        self._circuit_tripped = False
        logger.info("PortfolioGuard: Circuit breaker manually reset.")
