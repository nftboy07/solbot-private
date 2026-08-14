"""Unit tests for Milestone 1: Ultra-Low Latency Execution & MEV Defense."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from solbot.core.cu_optimizer import ComputeUnitOptimizer
from solbot.core.simulation_cache import SimulationCache
from solbot.jito import JitoClient


class TestExecutionAndMEV(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cu_optimizer = ComputeUnitOptimizer()
        self.sim_cache = SimulationCache(ttl_seconds=1.0)
        self.mock_config = MagicMock()
        self.jito_client = JitoClient(self.mock_config)

    def test_cu_optimizer_pump_fun_buy(self):
        budget = self.cu_optimizer.get_optimal_budget("pump_fun_buy", priority_fee_sol=0.0001)
        assert budget.compute_unit_limit > 100_000
        assert budget.micro_lamports_per_cu > 0
        assert budget.estimated_fee_sol > 0

    def test_cu_optimizer_simulated_override(self):
        budget = self.cu_optimizer.get_optimal_budget("custom", priority_fee_sol=0.0005, simulated_units=80_000)
        # Should be 80,000 * 1.15 = 92,000
        assert budget.compute_unit_limit == 92_000
        assert "Simulated" in budget.reason

    def test_simulation_cache_hit_and_expiry(self):
        tx_bytes = b"fake_transaction_payload_data_here"
        self.sim_cache.put(tx_bytes, units_consumed=75_000, logs=["Program return: 1"])
        
        cached = self.sim_cache.get(tx_bytes)
        assert cached is not None
        assert cached.units_consumed == 75_000
        assert cached.is_valid is True

    async def test_jito_tip_rotation(self):
        acc1 = self.jito_client.get_tip_account()
        acc2 = self.jito_client.get_tip_account()
        assert acc1 in self.jito_client.TIP_ACCOUNTS
        assert acc2 in self.jito_client.TIP_ACCOUNTS
        assert acc1 != acc2

    async def test_jito_send_bundle_parallel(self):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"result": "bundle_signature_123"})

        class MockContext:
            async def __aenter__(self):
                return mock_resp
            async def __aexit__(self, exc_type, exc, tb):
                pass

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=MockContext())

        with patch("aiohttp.ClientSession", return_value=mock_session):
            bundle_id = await self.jito_client.send_bundle(["tx1", "tx2"], session=mock_session)
            assert bundle_id == "bundle_signature_123"
