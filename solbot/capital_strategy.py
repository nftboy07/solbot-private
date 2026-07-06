"""Capital recycling helpers — keep the bot trading without fresh deposits."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, List, Optional, Protocol, Set


class PositionLike(Protocol):
    mint: str
    symbol: str
    active: bool
    entry_price: float
    current_price: float
    highest_price: float
    start_time: float


@dataclass(frozen=True)
class RecycleSettings:
    enabled: bool = False
    min_wallet_sol_reserve: float = 0.05
    tp1_multiplier: float = 1.35
    tp1_sell_pct: float = 0.55
    tp2_multiplier: float = 1.70
    tp2_sell_pct: float = 0.85
    stop_loss_pct: float = 0.12
    stale_exit_minutes: float = 10.0
    stale_min_gain: float = 1.03
    max_hold_minutes: float = 18.0
    trailing_activate_gain: float = 1.25
    use_dynamic_position_cap: bool = True
    max_positions_cap: int = 28


def default_min_wallet_reserve() -> float:
    return float(os.getenv("MIN_WALLET_SOL_RESERVE", "0.05"))


def spendable_balance(wallet_sol: float, reserve: float) -> float:
    return max(0.0, wallet_sol - reserve)


def dynamic_max_positions(
    wallet_sol: float,
    buy_amount_sol: float,
    reserve: float,
    hard_cap: int,
) -> int:
    if buy_amount_sol <= 0:
        return hard_cap
    slots = int(spendable_balance(wallet_sol, reserve) / buy_amount_sol)
    return max(1, min(hard_cap, slots))


def should_block_buy(wallet_sol: float, buy_size: float, reserve: float) -> Optional[str]:
    if wallet_sol < reserve:
        return f"wallet below reserve ({wallet_sol:.4f} < {reserve:.4f} SOL)"
    if spendable_balance(wallet_sol, reserve) < buy_size:
        return f"insufficient spendable SOL ({spendable_balance(wallet_sol, reserve):.4f} < {buy_size:.4f})"
    return None


def position_gain(pos: PositionLike) -> float:
    if pos.entry_price <= 0:
        return 1.0
    price = pos.current_price or pos.entry_price
    return price / pos.entry_price


def pick_rotation_candidate(
    positions: dict[str, PositionLike],
    now_ts: float,
    settings: RecycleSettings,
    exclude_mints: Optional[Set[str]] = None,
    aggressive: bool = False,
) -> Optional[PositionLike]:
    candidates = pick_rotation_candidates(
        positions, now_ts, settings, exclude_mints=exclude_mints, aggressive=aggressive,
    )
    return candidates[0] if candidates else None


def pick_rotation_candidates(
    positions: dict[str, PositionLike],
    now_ts: float,
    settings: RecycleSettings,
    exclude_mints: Optional[Set[str]] = None,
    aggressive: bool = False,
) -> List[PositionLike]:
    skip = exclude_mints or set()
    active = [
        p for p in positions.values()
        if getattr(p, "active", True)
        and p.mint not in skip
        and not getattr(p, "is_mayhem", False)
    ]
    if not active:
        return []

    min_hold_losers = 1.0 if aggressive else 5.0
    stale = []
    losers = []
    for pos in active:
        hold_min = (now_ts - pos.start_time) / 60.0
        gain = position_gain(pos)
        if hold_min >= settings.stale_exit_minutes and gain < settings.stale_min_gain:
            stale.append((hold_min, gain, pos))
        elif hold_min >= min_hold_losers:
            losers.append((gain, hold_min, pos))

    ordered: List[PositionLike] = []
    if stale:
        stale.sort(key=lambda x: (-x[0], x[1]))
        ordered.extend(x[2] for x in stale)
    if losers:
        losers.sort(key=lambda x: (x[0], -x[1]))
        for _, _, pos in losers:
            if pos not in ordered:
                ordered.append(pos)

    oldest = sorted(active, key=lambda p: p.start_time)
    for pos in oldest:
        if pos not in ordered:
            ordered.append(pos)
    return ordered


def active_position_count(positions: dict[str, PositionLike]) -> int:
    return sum(1 for p in positions.values() if getattr(p, "active", True))