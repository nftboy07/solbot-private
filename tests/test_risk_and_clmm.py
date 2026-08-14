"""Unit tests for Milestone 3: Risk Sizer, Portfolio Guard, and CLMM Quoter."""

import unittest
from solbot.risk_sizer import DynamicRiskSizer
from solbot.portfolio_guard import PortfolioGuard
from solbot.clmm_quoter import ConcentratedLiquidityQuoter


class TestRiskAndCLMM(unittest.TestCase):
    def setUp(self):
        self.sizer = DynamicRiskSizer(fractional_kelly=0.25, min_buy_sol=0.01, max_buy_sol=1.0)
        self.guard = PortfolioGuard(max_daily_drawdown_pct=0.15, max_creator_exposure_sol=0.50)
        self.clmm = ConcentratedLiquidityQuoter()

    def test_kelly_sizing_positive_ev(self):
        # 60% win rate with 2.5x payoff -> Kelly positive
        proposal = self.sizer.calculate_kelly_size(bankroll_sol=10.0, win_prob=0.60, reward_risk_ratio=2.5)
        assert proposal.recommended_sol > 0.01
        assert proposal.risk_level in ("MODERATE", "AGGRESSIVE")

    def test_kelly_sizing_negative_ev(self):
        # 20% win rate with 1.0x payoff -> Negative EV
        proposal = self.sizer.calculate_kelly_size(bankroll_sol=10.0, win_prob=0.20, reward_risk_ratio=1.0)
        assert proposal.recommended_sol == 0.01
        assert proposal.risk_level == "NEGATIVE_EV"

    def test_portfolio_guard_drawdown_tripping(self):
        self.guard.update_starting_balance(10.0)
        # Balance dropped from 10 to 8 SOL -> 20% loss > 15% limit
        status = self.guard.check_buy_allowed(
            current_wallet_balance_sol=8.0,
            creator="Dev111",
            buy_amount_sol=0.1,
            active_positions={},
        )
        assert status.circuit_breaker_tripped is True
        assert "drawdown" in status.reason.lower()

    def test_clmm_range_proposal(self):
        proposal = self.clmm.calculate_range_proposal(
            mid_price=100.0,
            range_width_pct=0.10,
            bin_step_bps=25,
            total_deposit_sol=1.0,
            curve_type="gaussian",
        )
        assert proposal.lower_price == 90.0
        assert proposal.upper_price == 110.0
        assert proposal.num_bins > 0
        total_sol = sum(b["sol_amount"] for b in proposal.bin_allocations)
        assert abs(total_sol - 1.0) < 0.01
