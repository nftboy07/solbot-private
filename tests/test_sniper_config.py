"""Sniper config defaults and wallet-key file loading."""

from solbot.config import BotConfig, SniperConfig, _read_secret_from_env_or_file


def test_sniper_defaults():
    cfg = SniperConfig()
    assert cfg.enabled is True
    assert cfg.bankroll_sol == 1.3
    assert cfg.clip_sol == 0.25
    assert cfg.max_open == 3
    assert cfg.fee_reserve_sol == 0.1
    assert cfg.scan_interval_seconds == 1.0
    assert "pumpfun" in cfg.sources
    assert cfg.delay_seconds is None
    rules = cfg.rules()
    assert rules.clip_sol == 0.25
    assert rules.max_open == 3


def test_botconfig_aligns_clip_and_positions():
    cfg = BotConfig()
    assert cfg.jupiter.buy_amount_sol == cfg.sniper.clip_sol
    assert cfg.strategy.max_active_positions == cfg.sniper.max_open


def test_wallet_key_from_file(tmp_path, monkeypatch):
    monkeypatch.delenv("WALLET_PRIVATE_KEY", raising=False)
    key_file = tmp_path / "wallet.key"
    key_file.write_text("# comment\nFILE_LOADED_SECRET\n", encoding="utf-8")
    monkeypatch.setenv("WALLET_PRIVATE_KEY_FILE", str(key_file))
    value = _read_secret_from_env_or_file("WALLET_PRIVATE_KEY", "WALLET_PRIVATE_KEY_FILE")
    assert value == "FILE_LOADED_SECRET"


def test_wallet_env_wins_over_file(tmp_path, monkeypatch):
    key_file = tmp_path / "wallet.key"
    key_file.write_text("FROM_FILE\n", encoding="utf-8")
    monkeypatch.setenv("WALLET_PRIVATE_KEY_FILE", str(key_file))
    monkeypatch.setenv("WALLET_PRIVATE_KEY", "FROM_ENV")
    value = _read_secret_from_env_or_file("WALLET_PRIVATE_KEY", "WALLET_PRIVATE_KEY_FILE")
    assert value == "FROM_ENV"


def test_sniper_clip_alias_wins_over_buy_amount(monkeypatch):
    monkeypatch.setenv("BUY_AMOUNT_SOL", "0.05")
    monkeypatch.setenv("SNIPER_CLIP_SOL", "0.25")
    cfg = BotConfig()
    assert cfg.sniper.clip_sol == 0.25
    assert cfg.jupiter.buy_amount_sol == 0.25


def test_sniper_env_overrides(monkeypatch):
    monkeypatch.setenv("SNIPER_BANKROLL_SOL", "2.0")
    monkeypatch.setenv("SNIPER_CLIP_SOL", "0.4")
    monkeypatch.setenv("SNIPER_MAX_OPEN", "2")
    monkeypatch.setenv("MIN_WALLET_SOL_RESERVE", "0.15")
    monkeypatch.setenv("SNIPER_SCAN_INTERVAL_SECONDS", "0.5")
    monkeypatch.setenv("SNIPER_SOURCES", "pumpfun")
    cfg = SniperConfig()
    assert cfg.bankroll_sol == 2.0
    assert cfg.clip_sol == 0.4
    assert cfg.max_open == 2
    assert cfg.fee_reserve_sol == 0.15
    assert cfg.scan_interval_seconds == 0.5
    assert cfg.sources == ["pumpfun"]
