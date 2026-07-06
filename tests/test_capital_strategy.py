"""Tests for capital recycling helpers."""

from time import time

from solbot.capital_strategy import (
    active_position_count,
    dynamic_max_positions,
    pick_rotation_candidate,
    pick_rotation_candidates,
    should_block_buy,
    spendable_balance,
)
from solbot.filter_profiles import PROFILES


class FakePos:
    def __init__(self, mint, symbol, gain, hold_min, active=True):
        self.mint = mint
        self.symbol = symbol
        self.active = active
        self.entry_price = 100.0
        self.current_price = 100.0 * gain
        self.highest_price = self.current_price
        self.start_time = time() - hold_min * 60


def test_spendable_balance_respects_reserve():
    assert spendable_balance(0.79, 0.05) == 0.74


def test_should_block_buy_below_reserve():
    reason = should_block_buy(0.04, 0.02, 0.05)
    assert reason is not None
    assert "reserve" in reason


def test_dynamic_max_positions_scales_with_wallet():
    assert dynamic_max_positions(0.79, 0.02, 0.05, 28) == 28
    assert dynamic_max_positions(0.20, 0.02, 0.05, 28) == 7


def test_pick_rotation_prefers_stale_loser():
    from solbot.capital_strategy import RecycleSettings

    settings = RecycleSettings(stale_exit_minutes=10, stale_min_gain=1.03)
    positions = {
        "a": FakePos("a", "WIN", 1.5, 3),
        "b": FakePos("b", "STALE", 1.01, 12),
    }
    picked = pick_rotation_candidate(positions, time(), settings)
    assert picked.mint == "b"


def test_pick_rotation_candidates_returns_multiple():
    from solbot.capital_strategy import RecycleSettings

    settings = RecycleSettings()
    positions = {
        "a": FakePos("a", "A", 0.9, 8),
        "b": FakePos("b", "B", 0.8, 9),
        "c": FakePos("c", "C", 1.2, 2),
    }
    picks = pick_rotation_candidates(positions, time(), settings, aggressive=True)
    assert len(picks) >= 2


def test_pick_rotation_skips_mayhem_positions():
    from solbot.capital_strategy import RecycleSettings

    settings = RecycleSettings()
    mayhem = FakePos("m", "MAYHEM", 0.5, 15)
    mayhem.is_mayhem = True
    positions = {
        "m": mayhem,
        "b": FakePos("b", "B", 0.8, 9),
    }
    picks = pick_rotation_candidates(positions, time(), settings, aggressive=True)
    assert all(p.mint != "m" for p in picks)


def test_active_position_count():
    positions = {"a": FakePos("a", "A", 1.0, 1), "b": FakePos("b", "B", 1.0, 1, active=False)}
    assert active_position_count(positions) == 1


def test_degen_profile_has_recycle_enabled():
    degen = PROFILES["degen"]
    assert degen.recycle_mode is True
    assert degen.min_wallet_sol_reserve == 0.05
    assert degen.tp1_multiplier == 1.35