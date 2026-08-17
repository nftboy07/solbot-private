"""Configurable sniper filter profiles (safe / normal / degen)."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Dict


@dataclass(frozen=True)
class FilterProfile:
    name: str
    sniper_delay_seconds: float
    min_age_seconds: float
    max_age_seconds: float
    min_mcap_sol: float
    max_mcap_sol: float
    min_liquidity_sol: float
    min_initial_buy_sol: float
    max_initial_buy_sol: float
    max_creator_pct: float
    min_ai_score: int
    min_creator_genome_score: float
    heuristic_threshold: float
    ai_fallback_score: int
    require_metadata: bool
    require_authorities: bool
    require_holder_check: bool
    require_bundle_check: bool
    require_ev_positive: bool
    min_elite_wallets: int
    skip_agi_prebuy: bool
    require_ai_gate: bool
    skip_ai_safety_screen: bool
    enforce_creator_blacklist: bool
    auto_blacklist_after_rugs: int
    brain_rug_mcap_usd: float
    brain_scan_min_rugs: int
    buy_amount_sol: float
    trailing_stop_pct: float
    max_cluster_risk: float
    skip_creator_genome_check: bool
    skip_mayhem_check: bool
    use_jito: bool
    max_trade_pct_wallet: float
    max_rpc_latency_ms: float
    min_wallet_sol_reserve: float
    recycle_mode: bool
    tp1_multiplier: float
    tp1_sell_pct: float
    tp2_multiplier: float
    tp2_sell_pct: float
    stop_loss_pct: float
    stale_exit_minutes: float
    stale_min_gain: float
    max_hold_minutes: float
    trailing_activate_gain: float
    use_dynamic_position_cap: bool
    max_positions_cap: int


PROFILES: Dict[str, FilterProfile] = {
    "safe": FilterProfile(
        name="safe",
        sniper_delay_seconds=6.0,
        min_age_seconds=5.0,
        max_age_seconds=120.0,
        min_mcap_sol=25.0,
        max_mcap_sol=80.0,
        min_liquidity_sol=28.0,
        min_initial_buy_sol=0.5,
        max_initial_buy_sol=8.0,
        max_creator_pct=5.0,
        min_ai_score=85,
        min_creator_genome_score=40.0,
        heuristic_threshold=0.35,
        ai_fallback_score=0,
        require_metadata=True,
        require_authorities=True,
        require_holder_check=True,
        require_bundle_check=True,
        require_ev_positive=True,
        min_elite_wallets=2,
        skip_agi_prebuy=False,
        require_ai_gate=True,
        skip_ai_safety_screen=False,
        enforce_creator_blacklist=True,
        auto_blacklist_after_rugs=5,
        brain_rug_mcap_usd=15000.0,
        brain_scan_min_rugs=3,
        buy_amount_sol=0.01,
        trailing_stop_pct=0.05,
        max_cluster_risk=30.0,
        skip_creator_genome_check=False,
        skip_mayhem_check=False,
        use_jito=True,
        max_trade_pct_wallet=0.02,
        max_rpc_latency_ms=250.0,
        min_wallet_sol_reserve=0.05,
        recycle_mode=False,
        tp1_multiplier=2.0,
        tp1_sell_pct=0.25,
        tp2_multiplier=3.0,
        tp2_sell_pct=0.50,
        stop_loss_pct=0.15,
        stale_exit_minutes=20.0,
        stale_min_gain=1.05,
        max_hold_minutes=45.0,
        trailing_activate_gain=1.50,
        use_dynamic_position_cap=False,
        max_positions_cap=15,
    ),
    "normal": FilterProfile(
        name="normal",
        sniper_delay_seconds=4.0,
        min_age_seconds=3.0,
        max_age_seconds=180.0,
        min_mcap_sol=15.0,
        max_mcap_sol=150.0,
        min_liquidity_sol=15.0,
        min_initial_buy_sol=0.3,
        max_initial_buy_sol=10.0,
        max_creator_pct=7.0,
        min_ai_score=75,
        min_creator_genome_score=35.0,
        heuristic_threshold=0.25,
        ai_fallback_score=50,
        require_metadata=True,
        require_authorities=True,
        require_holder_check=True,
        require_bundle_check=True,
        require_ev_positive=True,
        min_elite_wallets=1,
        skip_agi_prebuy=False,
        require_ai_gate=True,
        skip_ai_safety_screen=False,
        enforce_creator_blacklist=True,
        auto_blacklist_after_rugs=10,
        brain_rug_mcap_usd=10000.0,
        brain_scan_min_rugs=5,
        buy_amount_sol=0.10,
        trailing_stop_pct=0.10,
        max_cluster_risk=35.0,
        skip_creator_genome_check=False,
        skip_mayhem_check=False,
        use_jito=True,
        max_trade_pct_wallet=0.02,
        max_rpc_latency_ms=300.0,
        min_wallet_sol_reserve=0.05,
        recycle_mode=False,
        tp1_multiplier=1.60,
        tp1_sell_pct=0.35,
        tp2_multiplier=2.20,
        tp2_sell_pct=0.60,
        stop_loss_pct=0.15,
        stale_exit_minutes=15.0,
        stale_min_gain=1.04,
        max_hold_minutes=30.0,
        trailing_activate_gain=1.35,
        use_dynamic_position_cap=True,
        max_positions_cap=20,
    ),
    "degen": FilterProfile(
        name="degen",
        sniper_delay_seconds=2.0,
        min_age_seconds=0.0,
        max_age_seconds=300.0,
        min_mcap_sol=5.0,
        max_mcap_sol=500.0,
        min_liquidity_sol=5.0,
        min_initial_buy_sol=0.0,
        max_initial_buy_sol=50.0,
        max_creator_pct=10.0,
        min_ai_score=0,
        min_creator_genome_score=0.0,
        heuristic_threshold=0.10,
        ai_fallback_score=70,
        require_metadata=False,
        require_authorities=False,
        require_holder_check=False,
        require_bundle_check=False,
        require_ev_positive=False,
        min_elite_wallets=0,
        skip_agi_prebuy=True,
        require_ai_gate=False,
        skip_ai_safety_screen=True,
        enforce_creator_blacklist=False,
        auto_blacklist_after_rugs=20,
        brain_rug_mcap_usd=3000.0,
        brain_scan_min_rugs=8,
        buy_amount_sol=0.02,
        trailing_stop_pct=0.12,
        max_cluster_risk=50.0,
        skip_creator_genome_check=True,
        skip_mayhem_check=False,
        use_jito=False,
        max_trade_pct_wallet=0.05,
        max_rpc_latency_ms=500.0,
        min_wallet_sol_reserve=0.05,
        recycle_mode=True,
        tp1_multiplier=1.35,
        tp1_sell_pct=0.55,
        tp2_multiplier=1.70,
        tp2_sell_pct=0.85,
        stop_loss_pct=0.12,
        stale_exit_minutes=10.0,
        stale_min_gain=1.03,
        max_hold_minutes=18.0,
        trailing_activate_gain=1.25,
        use_dynamic_position_cap=True,
        max_positions_cap=28,
    ),
    "alpha": FilterProfile(
        name="alpha",
        sniper_delay_seconds=4.0,
        min_age_seconds=4.0,
        max_age_seconds=120.0,
        min_mcap_sol=20.0,
        max_mcap_sol=150.0,
        min_liquidity_sol=20.0,
        min_initial_buy_sol=0.3,
        max_initial_buy_sol=6.0,
        max_creator_pct=4.0,
        min_ai_score=75,
        min_creator_genome_score=40.0,
        heuristic_threshold=0.30,
        ai_fallback_score=0,
        require_metadata=True,
        require_authorities=True,
        require_holder_check=True,
        require_bundle_check=True,
        require_ev_positive=True,
        min_elite_wallets=1,
        skip_agi_prebuy=False,
        require_ai_gate=True,
        skip_ai_safety_screen=False,
        enforce_creator_blacklist=True,
        auto_blacklist_after_rugs=3,
        brain_rug_mcap_usd=12000.0,
        brain_scan_min_rugs=3,
        buy_amount_sol=0.015,
        trailing_stop_pct=0.10,
        max_cluster_risk=30.0,
        skip_creator_genome_check=False,
        skip_mayhem_check=False,
        use_jito=True,
        max_trade_pct_wallet=0.01,
        max_rpc_latency_ms=250.0,
        min_wallet_sol_reserve=0.05,
        recycle_mode=False,
        tp1_multiplier=1.40,
        tp1_sell_pct=0.35,
        tp2_multiplier=2.00,
        tp2_sell_pct=0.65,
        stop_loss_pct=0.15,
        stale_exit_minutes=15.0,
        stale_min_gain=1.05,
        max_hold_minutes=30.0,
        trailing_activate_gain=1.30,
        use_dynamic_position_cap=False,
        max_positions_cap=4,
    ),
}


def get_profile(name: str) -> FilterProfile:
    key = (name or "alpha").lower()
    profile = PROFILES.get(key, PROFILES["alpha"])
    # RECYCLE_MODE lets an operator turn off capital rotation without editing a
    # profile. With it on, hitting the position cap force-sells an existing bag to
    # fund the next snipe, so positions are churned out within seconds and never
    # live long enough to reach the first take-profit rung.
    override = os.getenv("RECYCLE_MODE", "").strip().lower()
    if override in ("0", "false", "off", "no"):
        profile = replace(profile, recycle_mode=False)
    elif override in ("1", "true", "on", "yes"):
        profile = replace(profile, recycle_mode=True)
    return profile


def default_profile_name() -> str:
    return os.getenv("FILTER_PROFILE", "alpha").lower()