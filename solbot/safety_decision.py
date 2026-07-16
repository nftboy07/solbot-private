"""Decision helpers for AI safety scan results."""

from dataclasses import dataclass
from typing import Any, Mapping


_DEGRADED_STATUSES = {"degraded", "fallback", "unavailable", "rate_limited"}


@dataclass(frozen=True)
class SafetyDecision:
    """Normalized outcome for a pre-buy safety scan."""

    allowed: bool
    reason_code: str
    message: str
    score: int
    min_score: int
    degraded: bool = False
    hard_risk: bool = False


def evaluate_safety_analysis(
    analysis: Mapping[str, Any] | None,
    min_score: int,
) -> SafetyDecision:
    """Return the buy decision for an AI rug/honeypot analysis result.

    Degraded fallback scans mean the upstream AI APIs were unavailable. Treat
    those as neutral only when the analyzer did not assert a concrete risk flag.
    """

    data = analysis or {}
    score = _int_value(data.get("score"), 80)
    min_score = _int_value(min_score, 75)
    is_honeypot = bool(data.get("is_honeypot"))
    is_premine = bool(data.get("is_premine"))
    degraded = _is_degraded_scan(data)

    if is_honeypot:
        return SafetyDecision(
            allowed=False,
            reason_code="honeypot",
            message="AI safety scan flagged honeypot risk.",
            score=score,
            min_score=min_score,
            degraded=degraded,
            hard_risk=True,
        )

    if is_premine:
        return SafetyDecision(
            allowed=False,
            reason_code="premine",
            message="AI safety scan flagged premine or supply concentration risk.",
            score=score,
            min_score=min_score,
            degraded=degraded,
            hard_risk=True,
        )

    if degraded:
        return SafetyDecision(
            allowed=True,
            reason_code="degraded_fallback",
            message=(
                "AI safety APIs are unavailable; allowing neutral fallback "
                "because no hard rug flags were found."
            ),
            score=score,
            min_score=min_score,
            degraded=True,
        )

    if score < min_score:
        return SafetyDecision(
            allowed=False,
            reason_code="low_score",
            message=f"AI safety score {score}/100 is below required {min_score}/100.",
            score=score,
            min_score=min_score,
        )

    return SafetyDecision(
        allowed=True,
        reason_code="passed",
        message=f"AI safety score {score}/100 passed required {min_score}/100.",
        score=score,
        min_score=min_score,
    )


def _is_degraded_scan(data: Mapping[str, Any]) -> bool:
    if bool(data.get("is_fallback")):
        return True

    status = str(data.get("scan_status", "")).strip().lower().replace("-", "_")
    if status in _DEGRADED_STATUSES:
        return True

    reason = str(data.get("reason", "")).lower()
    return "fallback" in reason and (
        "rate-limited" in reason
        or "rate limited" in reason
        or "unavailable" in reason
    )


def _int_value(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
