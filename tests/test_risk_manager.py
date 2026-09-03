"""Tests for risk manager position sizing."""

from solbot.engines.risk_manager import RiskManager


def test_degen_floor_uses_higher_wallet_pct():
    rm = RiskManager()
    size = rm.calculate_position_size(52.0, 0.5, floor_sol=0.02, max_trade_pct=0.05)
    assert size == 0.02

    capped = rm.calculate_position_size(52.0, 0.3, floor_sol=0.02, max_trade_pct=0.02)
    assert capped == 0.006


def test_configured_clip_is_used_instead_of_hardcoded_ladder():
    rm = RiskManager()
    # High confidence used to force 0.02 SOL. Clip from env must win.
    size = rm.calculate_position_size(95.0, 1.3, floor_sol=0.25, max_trade_pct=0.20)
    assert size == 0.25