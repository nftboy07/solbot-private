import asyncio
import logging
import statistics
from typing import Any, Dict, List, Optional
from solbot.db import Database
from solbot.core.event_store import EventStore, Event

logger = logging.getLogger(__name__)

class CreatorGenomeEngine:
    def __init__(self, db: Database, event_store: EventStore):
        self.db = db
        self.event_store = event_store
        # In-memory cache for fast lookups if needed, but we rely on DB for persistence
        self._creator_stats: Dict[str, Dict[str, Any]] = {}

    async def process_launch(self, creator_address: str, token_data: Dict[str, Any]):
        """Process a new token launch by a creator."""
        creator = await self.db.get_creator(creator_address)
        
        if not creator:
            creator = {
                "address": creator_address,
                "token_count": 0,
                "avg_ath": 0.0,
                "rug_count": 0,
                "blacklist_score": 0.0,
                "median_roi": 0.0,
                "survival_time_avg": 0.0,
                "whale_participation": 0.0,
                "liquidity_quality": 0.0,
                "creator_score": 50.0 # Default starting score
            }
        
        # We need to extend the creators table if not already done, 
        # but for Phase 2 we assume we handle extended stats in our logic
        # and update the DB accordingly.
        
        # Calculate new stats (simplified logic for Phase 2)
        new_token_count = creator.get("token_count", 0) + 1
        
        # Log launch event
        event = Event(
            type="creator_activity",
            payload={
                "action": "launch",
                "creator": creator_address,
                "mint": token_data.get("mint"),
                "initial_liquidity": token_data.get("initial_liquidity")
            },
            source="creator_genome_engine"
        )
        self.event_store.append(event)
        
        # Update creator score
        await self._recalculate_creator_score(creator_address, creator)

    async def _recalculate_creator_score(self, address: str, current_stats: Dict[str, Any]):
        """
        Dynamic Creator Score calculation based on:
        - Launches
        - Median ATH / ROI
        - Survival Time
        - Whale Participation
        - Liquidity Quality
        """
        # Logic to derive a score between 0-100
        score = current_stats.get("creator_score", 50.0)
        
        # Example dynamic adjustment
        if current_stats.get("rug_count", 0) > 0:
            score -= 20 * current_stats["rug_count"]
        
        score = max(0.0, min(100.0, score))
        
        # Log score change event
        if score != current_stats.get("creator_score"):
            event = Event(
                type="telemetry",
                payload={
                    "metric": "creator_score_change",
                    "creator": address,
                    "old_score": current_stats.get("creator_score"),
                    "new_score": score
                },
                source="creator_genome_engine"
            )
            self.event_store.append(event)
            
            # Update DB
            await self.db.update_creator(address, creator_score=score)

    async def get_genome(self, address: str) -> Optional[Dict[str, Any]]:
        """Retrieve full creator genome stats."""
        return await self.db.get_creator(address)

    async def track_trade_outcome(self, creator_address: str, roi: float, ath: float, survival_time: float):
        """Update creator stats based on a trade outcome."""
        creator = await self.db.get_creator(creator_address)
        if not creator:
            return

        # Update running averages/medians (In a real system, we'd pull all past tokens)
        # For Phase 2, we simulate the update
        new_avg_ath = (creator.get("avg_ath", 0.0) + ath) / 2
        
        await self.db.update_creator(
            creator_address, 
            avg_ath=new_avg_ath,
            # In a full impl, we'd update median_roi, survival_time etc.
        )
        
        await self._recalculate_creator_score(creator_address, creator)
