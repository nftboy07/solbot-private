"""Bankroll clip sizing and profile overlay for the meme sniper."""

from solbot.filter_profiles import PROFILES, get_profile
from solbot.sniper_bankroll import BankrollRules, clip_for_buy, overlay_sniper_bankroll


def test_default_rules_match_requested_bankroll():
    rules = BankrollRules()
    assert rules.bankroll_sol == 1.3
    assert rules.clip_sol == 0.25
    assert rules.max_open == 3
    assert rules.fee_reserve_sol == 0.1
    assert rules.spendable_sol == 1.2


def test_clip_allowed_when_wallet_and_slots_open():
    size, reason = clip_for_buy(BankrollRules(), open_count=0, open_exposure_sol=0.0, wallet_sol=1.3)
    assert reason is None
    assert size == 0.25


def test_clip_blocks_at_max_open():
    size, reason = clip_for_buy(BankrollRules(), open_count=3, open_exposure_sol=0.75, wallet_sol=1.3)
    assert size == 0.0
    assert "max open" in reason


def test_clip_blocks_when_reserve_would_be_breached():
    size, reason = clip_for_buy(BankrollRules(), open_count=0, open_exposure_sol=0.0, wallet_sol=0.08)
    assert size == 0.0
    assert "fee reserve" in reason


def test_clip_blocks_when_bankroll_plus_reserve_exhausted():
    # 1.05 deployed + 0.25 clip + 0.1 reserve = 1.4 > 1.3
    size, reason = clip_for_buy(BankrollRules(), open_count=2, open_exposure_sol=1.05, wallet_sol=1.3)
    assert size == 0.0
    assert "bankroll exhausted" in reason


def test_three_clips_fit_default_bankroll():
    rules = BankrollRules()
    wallet = 1.3
    exposure = 0.0
    for opened in range(3):
        size, reason = clip_for_buy(rules, opened, exposure, wallet)
        assert reason is None
        assert size == 0.25
        exposure += size
        wallet -= size
    size, reason = clip_for_buy(rules, 3, exposure, wallet)
    assert size == 0.0
    assert "max open" in reason


def test_overlay_applies_env_clip_over_profile():
    profile = overlay_sniper_bankroll(PROFILES["alpha"], BankrollRules(), delay_seconds=1.0)
    assert profile.buy_amount_sol == 0.25
    assert profile.max_positions_cap == 3
    assert profile.min_wallet_sol_reserve == 0.1
    assert profile.sniper_delay_seconds == 1.0
    # Unrelated safety flags stay on the profile — we don't invent a new oracle.
    assert profile.require_holder_check is True
    assert get_profile("alpha").buy_amount_sol != 0.25
