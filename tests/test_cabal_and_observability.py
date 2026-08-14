"""Unit tests for Milestone 4: Cabal Graph Cluster Engine & Observability Exporter."""

import unittest
from solbot.cabal_graph import WalletGraphEngine
from solbot.observability_exporter import HistoricalReplayBacktester


class TestCabalAndObservability(unittest.TestCase):
    def setUp(self):
        self.graph = WalletGraphEngine(max_cabal_cluster_size=3)
        self.backtester = HistoricalReplayBacktester()

    def test_cabal_cluster_detection(self):
        # Create a funding ring between 3 wallets
        self.graph.add_transfer("DeployerWallet", "WalletA")
        self.graph.add_transfer("DeployerWallet", "WalletB")
        self.graph.add_transfer("WalletA", "WalletC")

        res = self.graph.analyze_holders(["WalletA", "WalletB", "WalletC", "RandomCleanWallet"])
        assert res.cabal_detected is True
        assert res.largest_cluster_size >= 3
        assert res.risk_score >= 75

    def test_blacklist_propagation(self):
        self.graph.add_transfer("Scammer1", "ChildWallet1")
        self.graph.add_transfer("ChildWallet1", "GrandChildWallet1")

        full_blacklist = self.graph.propagate_blacklist({"Scammer1"}, max_hops=2)
        assert "Scammer1" in full_blacklist
        assert "ChildWallet1" in full_blacklist
        assert "GrandChildWallet1" in full_blacklist

    def test_historical_backtest_take_profit(self):
        ticks = [
            {"timestamp": 1000.0, "price": 1.0},
            {"timestamp": 1010.0, "price": 1.5},
            {"timestamp": 1020.0, "price": 2.1},  # Hits > 100% TP
        ]
        outcome = self.backtester.run_replay("MintTP", ticks, buy_amount_sol=1.0, take_profit_pct=1.0)
        assert outcome is not None
        assert outcome.exit_reason == "TAKE_PROFIT"
        assert outcome.pnl_sol > 0.9

    def test_historical_backtest_stop_loss(self):
        ticks = [
            {"timestamp": 1000.0, "price": 1.0},
            {"timestamp": 1010.0, "price": 0.75},  # Drops -25% < -20% SL
        ]
        outcome = self.backtester.run_replay("MintSL", ticks, buy_amount_sol=1.0, stop_loss_pct=0.20)
        assert outcome is not None
        assert outcome.exit_reason == "STOP_LOSS"
        assert outcome.pnl_sol < 0
