"""Unit tests for Milestone 2: Multi-Agent AI Consensus and Feature Store."""

import pytest
import unittest
from unittest.mock import AsyncMock, MagicMock

from solbot.ai_consensus import MultiAgentConsensusEngine, AgentScore
from solbot.token_auditor import TokenAuditor
from solbot.ml.feature_store import RealTimeFeatureStore


class TestAIAndFeatures(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_ai_filter = MagicMock()
        self.mock_ai_filter._config.ai.openai_api_key = "test-key"
        self.mock_ai_filter._config.ai.gemini_api_key = "test-key"
        self.mock_ai_filter._config.ai.openrouter_api_key = "test-key"
        self.consensus = MultiAgentConsensusEngine(self.mock_ai_filter)
        self.auditor = TokenAuditor()
        self.store = RealTimeFeatureStore()

    async def test_consensus_unanimous_pass(self):
        self.mock_ai_filter._analyze_safety_with_openai = AsyncMock(return_value={"score": 90, "is_honeypot": False, "is_premine": False, "reason": "Good"})
        self.mock_ai_filter.detect_rug_risks = AsyncMock(return_value={"score": 85, "is_honeypot": False, "is_premine": False, "reason": "Safe"})
        self.mock_ai_filter._analyze_safety_with_openrouter = AsyncMock(return_value={"score": 88, "is_honeypot": False, "is_premine": False, "reason": "Solid"})

        verdict = await self.consensus.evaluate_token("Mint111", {"symbol": "TEST", "name": "TestToken"})
        assert verdict.passed is True
        assert verdict.weighted_score >= 85
        assert verdict.is_hard_rug is False
        assert len(verdict.agent_votes) == 3

    async def test_consensus_hard_rug_rejection(self):
        # One agent flags honeypot
        self.mock_ai_filter._analyze_safety_with_openai = AsyncMock(return_value={"score": 20, "is_honeypot": True, "is_premine": False, "reason": "Honeypot trap"})
        self.mock_ai_filter.detect_rug_risks = AsyncMock(return_value={"score": 80, "is_honeypot": False, "is_premine": False, "reason": "Looked ok"})
        self.mock_ai_filter._analyze_safety_with_openrouter = AsyncMock(return_value={"score": 75, "is_honeypot": False, "is_premine": False, "reason": "Pass"})

        verdict = await self.consensus.evaluate_token("Mint111", {"symbol": "TEST", "name": "TestToken"})
        assert verdict.passed is False
        assert verdict.is_hard_rug is True

    async def test_token_auditor_copycat_detection(self):
        meta = {"name": "SOLANA REWARD TOKEN", "symbol": "SOLREWARD", "website": "https://fake.com"}
        audit = await self.auditor.audit_token("MintFake", meta)
        assert audit.copycat_risk is True
        assert audit.audit_score < 70

    def test_feature_store_vpin_and_momentum(self):
        mint = "MintAlpha111"
        # Record 4 buys of 1.0 SOL each at rising prices
        self.store.record_trade(mint, is_buy=True, amount_sol=1.0, price_sol=0.001, buyer_wallet="WalletA")
        self.store.record_trade(mint, is_buy=True, amount_sol=1.0, price_sol=0.0011, buyer_wallet="WalletB")
        self.store.record_trade(mint, is_buy=True, amount_sol=1.0, price_sol=0.0012, buyer_wallet="WalletC")
        self.store.record_trade(mint, is_buy=False, amount_sol=0.5, price_sol=0.0012)

        features = self.store.get_features(mint)
        assert features.volume_1m_sol == 3.5
        assert features.buy_ratio_1m > 0.8
        assert features.unique_buyers_count == 3
        assert features.price_momentum_pct > 0.0
        assert 0.0 <= features.vpin_toxicity <= 1.0
