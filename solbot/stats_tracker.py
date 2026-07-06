"""In-memory session counters for sniper pipeline diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import time
from typing import Dict


@dataclass
class StatsTracker:
    started_at: float = field(default_factory=time)
    tokens_seen: int = 0
    skip_blacklist: int = 0
    skip_position_limit: int = 0
    skip_creator_genome: int = 0
    skip_ai: int = 0
    skip_heuristic: int = 0
    skip_filter: int = 0
    skip_mayhem: int = 0
    skip_risk: int = 0
    skip_trading_blocked: int = 0
    qualified: int = 0
    snipes_started: int = 0
    buys_success: int = 0
    buys_failed: int = 0
    filter_reasons: Dict[str, int] = field(default_factory=dict)

    def bump(self, field_name: str, amount: int = 1) -> None:
        current = getattr(self, field_name, 0)
        setattr(self, field_name, current + amount)

    def record_filter_skip(self, reason: str) -> None:
        self.skip_filter += 1
        key = reason[:80]
        self.filter_reasons[key] = self.filter_reasons.get(key, 0) + 1

    def uptime_seconds(self) -> float:
        return max(0.0, time() - self.started_at)

    def top_filter_reasons(self, limit: int = 5) -> list[tuple[str, int]]:
        return sorted(self.filter_reasons.items(), key=lambda x: x[1], reverse=True)[:limit]