"""Unit tests for Hummingbot Pure Market Making (PMM) and Grid Trading Engine."""

import pytest
import unittest
from unittest.mock import MagicMock

from solbot.config import HummingbotConfig
from solbot.hummingbot_pmm import HummingbotPMMManager


class TestHummingbotPMMEngine(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = HummingbotConfig(
            enabled=True,
            pmm_default_spread_bps=200,  # 2.0% spread
            pmm_order_refresh_seconds=5.0,
            pmm_max_inventory_sol=0.5,
        )
        self.mock_bot = MagicMock()
        self.manager = HummingbotPMMManager(self.mock_bot, self.config)

    async def asyncTearDown(self):
        await self.manager.stop()

    def test_balanced_inventory_order_proposals(self):
        # 50/50 balance: 1000 tokens @ 0.001 SOL = 1 SOL token value, 1 SOL cash
        proposals = self.manager.calculate_order_proposals(
            mid_price=0.001,
            base_spread_bps=200,
            order_amount_sol=0.1,
            current_token_balance=1000.0,
            current_sol_balance=1.0,
            target_ratio=0.5,
            grid_levels=2,
        )

        assert proposals.mid_price == 0.001
        assert abs(proposals.inventory_skew) < 0.05
        # Bid should be ~ 1% below mid (0.00099)
        assert proposals.bid_price < proposals.mid_price
        # Ask should be ~ 1% above mid (0.00101)
        assert proposals.ask_price > proposals.mid_price
        assert len(proposals.grid_bids) == 2
        assert len(proposals.grid_asks) == 2

    def test_heavy_inventory_skew(self):
        # Heavy token inventory: 5000 tokens @ 0.001 SOL = 5 SOL token value, 0.5 SOL cash
        proposals = self.manager.calculate_order_proposals(
            mid_price=0.001,
            base_spread_bps=200,
            order_amount_sol=0.1,
            current_token_balance=5000.0,
            current_sol_balance=0.5,
            target_ratio=0.5,
        )

        # Skew should be positive (> 0)
        assert proposals.inventory_skew > 0
        # Ask spread should be narrower than bid spread (lowering ask price to offload inventory)
        bid_distance = proposals.mid_price - proposals.bid_price
        ask_distance = proposals.ask_price - proposals.mid_price
        assert bid_distance > ask_distance

    async def test_session_lifecycle(self):
        await self.manager.start()
        session = await self.manager.start_session(
            mint="TestMint111111111111111111111111111111111",
            symbol="TEST",
            base_spread_bps=150,
            order_amount_sol=0.2,
        )
        assert session.active is True
        assert session.base_spread_bps == 150
        assert session.order_amount_sol == 0.2

        sessions = self.manager.get_sessions()
        assert len(sessions) == 1
        assert sessions[0].mint == "TestMint111111111111111111111111111111111"

        stopped = await self.manager.stop_session("TestMint111111111111111111111111111111111")
        assert stopped is True
        assert len(self.manager.get_sessions()) == 0
