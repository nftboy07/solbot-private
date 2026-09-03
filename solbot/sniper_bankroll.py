"""Config-driven meme-sniper bankroll: clip size, open-bag cap, fee reserve.

These helpers do not invent a safety oracle. They only size and gate buys
using values from env/config so the VPS operator can change bankroll without
editing code.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from solbot.filter_profiles import FilterProfile


@dataclass(frozen=True)
class BankrollRules:
    bankroll_sol: float = 1.3
    clip_sol: float = 0.25
    max_open: int = 3
    fee_reserve_sol: float = 0.1

    @property
    def spendable_sol(self) -> float:
        return max(0.0, self.bankroll_sol - self.fee_reserve_sol)


def clip_for_buy(
    rules: BankrollRules,
    open_count: int,
    open_exposure_sol: float,
    wallet_sol: float,
) -> tuple[float, Optional[str]]:
    """Return (clip_size, None) or (0.0, reject_reason)."""
    if rules.clip_sol <= 0:
        return 0.0, "clip size is not positive"
    if rules.max_open > 0 and open_count >= rules.max_open:
        return 0.0, f"max open positions reached ({open_count}/{rules.max_open})"
    if wallet_sol < rules.fee_reserve_sol:
        return 0.0, (
            f"wallet below fee reserve ({wallet_sol:.4f} < {rules.fee_reserve_sol:.4f} SOL)"
        )
    remaining_bankroll = rules.bankroll_sol - open_exposure_sol
    if remaining_bankroll < rules.clip_sol + rules.fee_reserve_sol:
        return 0.0, (
            f"bankroll exhausted "
            f"(open {open_exposure_sol:.4f} + clip {rules.clip_sol:.4f} + "
            f"reserve {rules.fee_reserve_sol:.4f} > {rules.bankroll_sol:.4f})"
        )
    wallet_room = wallet_sol - rules.fee_reserve_sol
    if wallet_room < rules.clip_sol:
        return 0.0, (
            f"insufficient spendable SOL ({wallet_room:.4f} < {rules.clip_sol:.4f})"
        )
    return rules.clip_sol, None


def overlay_sniper_bankroll(profile: "FilterProfile", rules: BankrollRules, delay_seconds: Optional[float] = None):
    """Apply env bankroll knobs on top of a filter profile. Env/config wins."""
    updates = {
        "buy_amount_sol": rules.clip_sol,
        "max_positions_cap": rules.max_open,
        "min_wallet_sol_reserve": rules.fee_reserve_sol,
    }
    if delay_seconds is not None:
        updates["sniper_delay_seconds"] = float(delay_seconds)
    return replace(profile, **updates)
