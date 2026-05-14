"""Async token scoring engine for risk assessment.

Evaluates tokens on multiple dimensions:
- Liquidity depth scoring
- Creator reputation heuristics
- Buy pressure analysis
- Anti-rug detection
- Confidence classification (LOW/MEDIUM/HIGH)
"""

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from time import time
from typing import Optional

from solbot.config import ScoringConfig
from solbot.logger import get_logger
from solbot.models import TokenEvent

logger = get_logger("scoring")


class Confidence(Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass
class TokenScore:
    """Composite score for a token across all dimensions."""
    token: TokenEvent
    liquidity_score: float = 0.0       # 0-100
    creator_score: float = 0.0         # 0-100
    buy_pressure_score: float = 0.0    # 0-100
    anti_rug_score: float = 0.0        # 0-100
    composite_score: float = 0.0       # 0-100 weighted average
    confidence: Confidence = Confidence.LOW
    flags: list[str] = field(default_factory=list)
    scored_at: float = field(default_factory=time)

    @property
    def is_tradeable(self) -> bool:
        """Token passes minimum thresholds for trading."""
        return self.confidence in (Confidence.MEDIUM, Confidence.HIGH)

    def summary(self) -> str:
        flags_str = f" | flags=[{', '.join(self.flags)}]" if self.flags else ""
        return (
            f"{self.token.symbol} | composite={self.composite_score:.1f} | "
            f"conf={self.confidence.value} | liq={self.liquidity_score:.1f} | "
            f"creator={self.creator_score:.1f} | pressure={self.buy_pressure_score:.1f} | "
            f"rug={self.anti_rug_score:.1f}{flags_str}"
        )


class ScoringEngine:
    """Async scoring engine that evaluates tokens across multiple heuristics.

    All scoring methods are async-ready for future integration with
    on-chain lookups (creator history, holder distribution, etc.).
    """

    def __init__(self, config: ScoringConfig):
        self._config = config
        self._creator_cache: dict[str, float] = {}  # creator -> reputation score

    async def score_token(self, token: TokenEvent) -> TokenScore:
        """Score a token across all dimensions concurrently.

        Args:
            token: The incoming token event to evaluate.

        Returns:
            TokenScore with all dimensions filled and confidence classified.
        """
        # Run all scoring dimensions concurrently
        liq_score, creator_score, pressure_score, rug_score, flags = (
            await asyncio.gather(
                self._score_liquidity(token),
                self._score_creator(token),
                self._score_buy_pressure(token),
                self._score_anti_rug(token),
                self._detect_flags(token),
            )
        )

        # Weighted composite
        composite = (
            liq_score * self._config.weight_liquidity
            + creator_score * self._config.weight_creator
            + pressure_score * self._config.weight_buy_pressure
            + rug_score * self._config.weight_anti_rug
        )

        # Classify confidence
        confidence = self._classify_confidence(composite, flags)

        score = TokenScore(
            token=token,
            liquidity_score=liq_score,
            creator_score=creator_score,
            buy_pressure_score=pressure_score,
            anti_rug_score=rug_score,
            composite_score=composite,
            confidence=confidence,
            flags=flags,
        )

        logger.info(f"SCORED: {score.summary()}")
        return score

    async def _score_liquidity(self, token: TokenEvent) -> float:
        """Score liquidity depth (0-100).

        Higher liquidity = higher score. Uses logarithmic scaling.
        """
        if token.liquidity_sol <= 0:
            return 0.0

        # Logarithmic scaling: 5 SOL = ~50, 20 SOL = ~75, 100 SOL = ~95
        import math
        raw = math.log10(max(token.liquidity_sol, 0.1)) * 40
        return min(max(raw, 0.0), 100.0)

    async def _score_creator(self, token: TokenEvent) -> float:
        """Score creator reputation (0-100).

        Heuristics:
        - Known creators get cached scores
        - New creators start at 50 (neutral)
        - Penalize if creator address looks suspicious (short activity)
        """
        if not token.creator:
            return 30.0  # No creator info = suspicious

        # Check cache first
        if token.creator in self._creator_cache:
            return self._creator_cache[token.creator]

        # Base score for new creators
        score = 50.0

        # Heuristic: if initial buy is significant, creator has skin in the game
        if token.initial_buy_sol >= 1.0:
            score += 15.0
        elif token.initial_buy_sol >= 0.5:
            score += 10.0
        elif token.initial_buy_sol < 0.01:
            score -= 20.0  # Dust buy = likely rug setup

        # Cache the result
        score = min(max(score, 0.0), 100.0)
        self._creator_cache[token.creator] = score
        return score

    async def _score_buy_pressure(self, token: TokenEvent) -> float:
        """Score buy pressure (0-100).

        Evaluates the ratio of initial buy to liquidity as a
        proxy for early demand/momentum.
        """
        if token.liquidity_sol <= 0:
            return 0.0

        # Buy pressure ratio: initial_buy / liquidity
        ratio = token.initial_buy_sol / token.liquidity_sol

        if ratio >= 0.1:
            return min(90.0, 50.0 + ratio * 200)  # Strong pressure
        elif ratio >= 0.05:
            return 60.0
        elif ratio >= 0.01:
            return 40.0
        else:
            return 20.0  # Low/no initial buy pressure

    async def _score_anti_rug(self, token: TokenEvent) -> float:
        """Anti-rug heuristic scoring (0-100).

        Higher score = LESS likely to be a rug pull.

        Checks:
        - Liquidity too low = suspicious
        - Market cap wildly disproportionate to liquidity
        - Token age (too new with high mcap = manufactured)
        """
        score = 70.0  # Start optimistic

        # Red flag: liquidity < 2 SOL
        if token.liquidity_sol < 2.0:
            score -= 30.0

        # Red flag: mcap/liquidity ratio too high (inflated)
        if token.liquidity_sol > 0:
            mcap_liq_ratio = token.market_cap_usd / (token.liquidity_sol * 150)
            if mcap_liq_ratio > 50:
                score -= 25.0  # Massively inflated
            elif mcap_liq_ratio > 20:
                score -= 15.0

        # Red flag: very young token with high market cap
        if token.age_seconds < 10 and token.market_cap_usd > 50000:
            score -= 20.0

        # Green flag: reasonable liquidity with proportional mcap
        if 5.0 <= token.liquidity_sol <= 50.0:
            score += 10.0

        return min(max(score, 0.0), 100.0)

    async def _detect_flags(self, token: TokenEvent) -> list[str]:
        """Detect specific red/green flags for a token."""
        flags = []

        # Red flags
        if token.liquidity_sol < 1.0:
            flags.append("VERY_LOW_LIQUIDITY")
        if token.initial_buy_sol < 0.01:
            flags.append("DUST_INITIAL_BUY")
        if not token.creator:
            flags.append("NO_CREATOR_INFO")
        if token.market_cap_usd > 100000 and token.age_seconds < 5:
            flags.append("INSTANT_HIGH_MCAP")

        # Green flags
        if token.liquidity_sol >= 20.0:
            flags.append("STRONG_LIQUIDITY")
        if token.initial_buy_sol >= 2.0:
            flags.append("HIGH_CREATOR_BUY")

        return flags

    def _classify_confidence(self, composite: float, flags: list[str]) -> Confidence:
        """Classify confidence based on composite score and flags."""
        # Hard rejections
        critical_flags = {"VERY_LOW_LIQUIDITY", "INSTANT_HIGH_MCAP", "DUST_INITIAL_BUY"}
        if critical_flags & set(flags):
            return Confidence.LOW

        if composite >= self._config.high_confidence_threshold:
            return Confidence.HIGH
        elif composite >= self._config.medium_confidence_threshold:
            return Confidence.MEDIUM
        else:
            return Confidence.LOW

    def clear_cache(self):
        """Clear creator reputation cache."""
        self._creator_cache.clear()
