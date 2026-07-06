import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class CreatorReputationEngine:
    """Calculates and tracks creator reputation scores."""

    def __init__(self, db_connection=None):
        self.db = db_connection
        self.score_cache: Dict[str, float] = {}

    async def get_creator_score(self, creator_address: str) -> float:
        if creator_address in self.score_cache:
            return self.score_cache[creator_address]

        score = 50.0
        if self.db:
            try:
                row = await self.db.get_creator(creator_address)
                if row:
                    score = float(row.get("creator_score") or row.get("blacklist_score") or 50.0)
                    if score <= 1.0:
                        score *= 100.0
            except Exception as exc:
                logger.debug("Creator score lookup failed for %s: %s", creator_address, exc)

        self.score_cache[creator_address] = score
        return score

    async def update_score(self, creator_address: str, outcome: str):
        current = await self.get_creator_score(creator_address)
        delta = 5.0 if outcome == "win" else -10.0 if outcome == "loss" else 0.0
        updated = max(0.0, min(100.0, current + delta))
        self.score_cache[creator_address] = updated
        if self.db:
            try:
                await self.db.update_creator(creator_address, creator_score=updated)
            except Exception as exc:
                logger.debug("Creator score update failed: %s", exc)
        logger.info("Creator %s score %.1f -> %.1f (%s)", creator_address[:8], current, updated, outcome)