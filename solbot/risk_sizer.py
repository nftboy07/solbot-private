"""Fractional Kelly Criterion Sizer, ATR Trailing Stop, and Take-Profit Ladder."""

import logging
from dataclasses import dataclass
from typing import List, Tuple, Optional

logger = logging.getLogger("bot.risk_sizer")


@dataclass
class PositionSizeProposal:
    """Calculated position sizing recommendations."""
    recommended_sol: float
    kelly_fraction: float
    raw_kelly_pct: float
    adjusted_for_bankroll: float
    risk_level: str


@dataclass
class TPLadderStep:
    """Take-profit ladder step definition."""
    multiplier: float
    sell_fraction: float
    arm_breakeven_stop: bool


class DynamicRiskSizer:
    """Calculates position size using Fractional Kelly Criterion and volatility scaling."""

    def __init__(
        self,
        fractional_kelly: float = 0.25,
        min_buy_sol: float = 0.01,
        max_buy_sol: float = 1.0,
        max_bankroll_pct: float = 0.05,
    ):
        self.fractional_kelly = fractional_kelly
        self.min_buy_sol = min_buy_sol
        self.max_buy_sol = max_buy_sol
        self.max_bankroll_pct = max_bankroll_pct

    def calculate_kelly_size(
        self,
        bankroll_sol: float,
        win_prob: float,
        reward_risk_ratio: float = 2.5,
    ) -> PositionSizeProposal:
        """
        Kelly Criterion formula: K% = (p * b - q) / b
        where:
        - p = win probability (e.g. 0.60)
        - q = 1 - p (loss probability e.g. 0.40)
        - b = reward/risk ratio (e.g. 2.5x)
        """
        if win_prob <= 0.0 or reward_risk_ratio <= 0.0:
            return PositionSizeProposal(self.min_buy_sol, self.fractional_kelly, 0.0, self.min_buy_sol, "MIN_RISK")

        p = min(0.99, max(0.01, win_prob))
        q = 1.0 - p
        b = reward_risk_ratio

        raw_kelly = (p * b - q) / b
        if raw_kelly <= 0:
            return PositionSizeProposal(self.min_buy_sol, self.fractional_kelly, 0.0, self.min_buy_sol, "NEGATIVE_EV")

        # Apply fractional multiplier (e.g. quarter-Kelly)
        scaled_kelly = raw_kelly * self.fractional_kelly

        # Cap by maximum bankroll percentage per trade
        capped_kelly = min(scaled_kelly, self.max_bankroll_pct)

        calculated_sol = bankroll_sol * capped_kelly
        final_sol = max(self.min_buy_sol, min(self.max_buy_sol, calculated_sol))

        risk_level = "AGGRESSIVE" if scaled_kelly > 0.03 else "MODERATE" if scaled_kelly > 0.01 else "CONSERVATIVE"

        return PositionSizeProposal(
            recommended_sol=round(final_sol, 4),
            kelly_fraction=self.fractional_kelly,
            raw_kelly_pct=round(raw_kelly * 100.0, 2),
            adjusted_for_bankroll=round(calculated_sol, 4),
            risk_level=risk_level,
        )

    def calculate_atr_trailing_stop(
        self,
        current_price: float,
        highest_price: float,
        atr_sol: float,
        multiplier: float = 2.0,
        base_trailing_pct: float = 0.20,
    ) -> Tuple[float, bool]:
        """
        Calculates dynamic volatility-adjusted trailing stop.
        Returns: (stop_price, should_exit)
        """
        # Dynamic trailing stop distance expands with ATR volatility
        dynamic_pct = base_trailing_pct
        if highest_price > 0 and atr_sol > 0:
            atr_pct = (atr_sol / highest_price) * multiplier
            dynamic_pct = max(base_trailing_pct, min(0.40, atr_pct))

        stop_price = highest_price * (1.0 - dynamic_pct)
        should_exit = current_price <= stop_price
        return stop_price, should_exit

    def get_default_tp_ladder(self) -> List[TPLadderStep]:
        """Returns standard 4-stage Take-Profit ladder."""
        return [
            TPLadderStep(multiplier=2.0, sell_fraction=0.40, arm_breakeven_stop=True),
            TPLadderStep(multiplier=5.0, sell_fraction=0.30, arm_breakeven_stop=True),
            TPLadderStep(multiplier=10.0, sell_fraction=0.20, arm_breakeven_stop=True),
            TPLadderStep(multiplier=25.0, sell_fraction=0.10, arm_breakeven_stop=True),  # Moonbag
        ]
