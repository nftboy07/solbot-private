import os

os.environ.setdefault("WALLET_PRIVATE_KEY", "test-key-placeholder")

from solbot.config import BotConfig
from solbot.agi_prebuy_filter import AGIPreBuyFilter


class DummyBot:
    class Filter:
        _wallet_scores = {}

    _filter = Filter()


def test_ai_fail_open_score_defaults_to_zero():
    config = BotConfig()
    assert config.ai.fail_open_score == 0


def test_agi_skips_without_smart_wallets():
    filt = AGIPreBuyFilter(DummyBot())
    action, score, _, reason = __import__("asyncio").run(
        filt.evaluate_token("mint", {"market_cap_usd": 50000, "liquidity_sol": 10, "creator": "creator"})
    )
    assert action == "SKIP"
    assert score == 0
    assert "smart-wallet" in reason.lower()