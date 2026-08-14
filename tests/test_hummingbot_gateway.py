"""Unit tests for Hummingbot Gateway REST API client."""

import pytest
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from solbot.config import HummingbotConfig
from solbot.hummingbot_gateway import HummingbotGatewayClient


class TestHummingbotGatewayClient(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = HummingbotConfig(
            enabled=True,
            gateway_url="http://127.0.0.1:15888",
            network="mainnet-beta",
            timeout_seconds=5.0,
        )
        self.client = HummingbotGatewayClient(self.config)

    async def asyncTearDown(self):
        await self.client.close()

    async def test_is_healthy_success(self):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"status": "ok"})

        class MockContext:
            async def __aenter__(self):
                return mock_resp
            async def __aexit__(self, exc_type, exc, tb):
                pass

        mock_session = MagicMock()
        mock_session.request = MagicMock(return_value=MockContext())
        mock_session.closed = False

        with patch.object(self.client, "get_session", AsyncMock(return_value=mock_session)):
            healthy = await self.client.is_healthy()
            assert healthy is True

    async def test_get_status_unreachable(self):
        with patch.object(self.client, "_request", AsyncMock(return_value=None)):
            status = await self.client.get_status()
            assert status["reachable"] is False
            assert status["gateway_url"] == "http://127.0.0.1:15888"

    async def test_get_balances_success(self):
        mock_data = {
            "network": "mainnet-beta",
            "balances": {"SOL": 2.5, "USDC": 150.0}
        }
        with patch.object(self.client, "_request", AsyncMock(return_value=mock_data)):
            balances = await self.client.get_balances("TestWallet1111111111111111111111111111111111111")
            assert balances["SOL"] == 2.5
            assert balances["USDC"] == 150.0

    async def test_get_price_success(self):
        mock_data = {"price": 142.50}
        with patch.object(self.client, "_request", AsyncMock(return_value=mock_data)):
            price = await self.client.get_price("raydium", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "SOL")
            assert price == 142.50

    async def test_get_quote_and_swap(self):
        mock_quote = {"expectedOutput": "0.15", "price": "142.50", "gasEstimate": 5000}
        mock_swap = {"txHash": "5abc...123", "status": "pending"}
        with patch.object(self.client, "_request", AsyncMock(side_effect=[mock_quote, mock_swap])):
            quote = await self.client.get_quote("meteora", "TOKEN111", "SOL", amount=0.1, side="BUY")
            assert quote["expectedOutput"] == "0.15"

            swap = await self.client.execute_swap(
                "meteora", "TOKEN111", "SOL", amount=0.1, side="BUY", wallet_address="TestAddr"
            )
            assert swap["txHash"] == "5abc...123"
