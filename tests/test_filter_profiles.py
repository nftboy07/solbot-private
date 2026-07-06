"""Tests for sniper filter profiles."""

from solbot.filter_profiles import PROFILES, get_profile, default_profile_name
from solbot.filters import TokenFilter
from solbot.models import TokenEvent
from solbot.config import BotConfig


def test_degen_profile_wider_than_safe():
    safe = PROFILES["safe"]
    degen = PROFILES["degen"]
    assert degen.min_age_seconds < safe.min_age_seconds
    assert degen.max_mcap_sol > safe.max_mcap_sol
    assert degen.min_liquidity_sol < safe.min_liquidity_sol
    assert degen.skip_agi_prebuy is True
    assert degen.require_ai_gate is False
    assert degen.skip_ai_safety_screen is True
    assert degen.enforce_creator_blacklist is False
    assert degen.auto_blacklist_after_rugs >= 15
    assert degen.skip_creator_genome_check is True
    assert degen.skip_mayhem_check is True
    assert degen.use_jito is False
    assert degen.max_trade_pct_wallet >= 0.05
    assert degen.recycle_mode is True
    assert degen.min_wallet_sol_reserve == 0.05


def test_get_profile_unknown_defaults_to_degen():
    profile = get_profile("unknown_mode")
    assert profile.name == "degen"


def test_token_filter_uses_profile_age_range():
    config = BotConfig()
    filt = TokenFilter(config)
    filt.set_profile(PROFILES["degen"])
    token = TokenEvent(
        mint="mint123",
        name="Test",
        symbol="TST",
        initial_buy_sol=1.0,
        market_cap_usd=1500.0,
        liquidity_sol=10.0,
    )
    assert filt.profile.min_age_seconds == 0.0
    assert filt.profile.max_age_seconds == 300.0