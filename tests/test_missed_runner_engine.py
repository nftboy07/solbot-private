"""Unit tests for MissedRunnerEngine and Pattern Harvester."""

import unittest
from solbot.missed_runner_engine import MissedRunnerEngine, PumpedTokenRecord


class TestMissedRunnerEngine(unittest.TestCase):
    def setUp(self):
        self.engine = MissedRunnerEngine()

    def test_seed_runners_loaded(self):
        # Verify PARKIFY, GENTLE, BULLWHALE, TOADZ, Modi are seeded
        assert "7Syw6tu4Jx692uhoryjok5yBwTqje1oftit9E3LHpump" in self.engine._pumped_tokens
        assert "Gepjas79VptWRYEVM4cUvET9RAyEEFrF4XhukZakpump" in self.engine._pumped_tokens
        assert "B4G24zZRUjZcuu4d5QTLpGFcaptXUUNmrLL4VBEmpump" in self.engine._pumped_tokens
        assert len(self.engine._pumped_tokens) >= 5

    def test_pattern_matching_success(self):
        # Candidate inside sweet-spot MCAP ($250k), strong buy ratio (75%), 30 buyers
        matches, score, reason = self.engine.matches_runner_pattern(
            mcap_usd=250_000.0,
            buy_ratio=0.75,
            unique_buyers=30,
            dev_holding_pct=0.01,
        )
        assert matches is True
        assert score >= 70.0

    def test_pattern_matching_failure_low_buy_pressure(self):
        # Candidate with low buy pressure (40%) and low buyer count (5)
        matches, score, reason = self.engine.matches_runner_pattern(
            mcap_usd=250_000.0,
            buy_ratio=0.40,
            unique_buyers=5,
            dev_holding_pct=0.05,
        )
        assert matches is False
        assert score < 60.0

    def test_add_missed_token_recalculates(self):
        self.engine.add_missed_token(
            symbol="NEW5X",
            name="New 5x Gem",
            mint="NewMint111111111111111111111111111111111",
            alert_mcap=150_000.0,
            current_mcap=750_000.0,
            multiplier=5.0,
            elapsed_mins=60,
            early_wallets=["WhaleWalletA111111111111111111111111111111"],
        )
        assert "NewMint111111111111111111111111111111111" in self.engine._pumped_tokens
        assert "WhaleWalletA111111111111111111111111111111" in self.engine._smart_early_wallets
