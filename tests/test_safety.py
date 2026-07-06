import os

os.environ.setdefault("WALLET_PRIVATE_KEY", "test-key-placeholder")

from solbot.config import BotConfig
from solbot.ai_filter import AIFilter


def test_ai_fail_open_score_defaults_to_zero():
    config = BotConfig()
    assert config.ai.fail_open_score == 0


def test_ai_filter_uses_config_fail_score(monkeypatch):
    monkeypatch.setenv("AI_FAIL_OPEN_SCORE", "0")
    filt = AIFilter(BotConfig())
    assert filt._config.ai.fail_open_score == 0