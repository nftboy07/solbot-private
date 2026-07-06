"""Configurable sniper filter profiles (safe / normal / degen)."""

from __future__ import annotations

import os
from dataclasses import dataclass
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
        trailing_stop_pct=0.20,
        max_cluster_risk=50.0,
        skip_creator_genome_check=True,
        skip_mayhem_check=True,
        use_jito=False,
        max_trade_pct_wallet=0.05,
    ),
}


def get_profile(name: str) -> FilterProfile:
    key = (name or "degen").lower()
    return PROFILES.get(key, PROFILES["degen"])


def default_profile_name() -> str:
    return os.getenv("FILTER_PROFILE", "degen").lower()