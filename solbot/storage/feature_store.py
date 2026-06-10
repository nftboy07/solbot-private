import json
import logging
import time
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, Optional, List

from solbot.storage.redis import RedisManager
from solbot.db import Database

logger = logging.getLogger(__name__)

@dataclass
class FeatureVector:
    """Deterministic feature vector for a token launch."""
    mint: str
    timestamp: float = field(default_factory=time.time)
    
    # Creator Metrics
    creator_score: float = 0.0
    dev_holdings: float = 0.0
    
    # Cluster & Holder Metrics
    wallet_cluster: float = 0.0
    top_holder_pct: float = 0.0
    whale_overlap: float = 0.0
    
    # Velocity & Acceleration Metrics
    holder_growth: float = 0.0
    buy_acceleration: float = 0.0
    liquidity_velocity: float = 0.0
    telegram_velocity: float = 0.0
    marketcap_velocity: float = 0.0
    volume_acceleration: float = 0.0
    
    # Content & Social Metrics
    narrative_embedding: List[float] = field(default_factory=list)
    
    # Placeholder for additional features (100+ planned)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, data: str) -> "FeatureVector":
        d = json.loads(data)
        return cls(**d)

class FeatureStore:
    """
    Async Feature Store for Solbot V3.
    Handles caching (Redis), immutable storage, and historical snapshots (SQLite/Postgres).
    """

    def __init__(self, redis: RedisManager, db: Database, cache_ttl: int = 86400):
        self.redis = redis
        self.db = db
        self.cache_ttl = cache_ttl
        self._prefix = "fs:v1:"

    async def store(self, features: FeatureVector, trade_id: Optional[str] = None, signal_id: Optional[str] = None):
        """
        Store features immutably. Caches in Redis and persists to DB snapshot.
        """
        key = f"{self._prefix}{features.mint}"
        
        # 1. Check if already exists in cache to ensure immutability/no-recompute
        existing = await self.redis.client.get(key)
        if existing:
            logger.debug(f"Features for {features.mint} already exist. Skipping store.")
            return

        serialized = features.to_json()

        # 2. Cache in Redis
        await self.redis.client.set(key, serialized, ex=self.cache_ttl)

        # 3. Persist to Database for historical retraining
        snapshot_data = {
            "trade_id": trade_id,
            "signal_id": signal_id,
            "serialized_features": serialized,
            "timestamp": features.timestamp
        }
        await self.db.log_feature_snapshot(snapshot_data)
        
        logger.info(f"Stored immutable features for {features.mint}")

    async def get(self, mint: str) -> Optional[FeatureVector]:
        """
        Retrieve features from cache or database.
        """
        key = f"{self._prefix}{mint}"
        
        # Try Cache
        cached = await self.redis.client.get(key)
        if cached:
            return FeatureVector.from_json(cached)

        # Try DB (Fallback for historical/retraining)
        # We query the feature_snapshots table using the serialized JSON content.
        # This matches the schema provided in solbot/db.py
        rows = await self.db._execute_read(
            "SELECT serialized_features FROM feature_snapshots WHERE serialized_features LIKE ? LIMIT 1",
            (f'%"{mint}"%',)
        )
        
        if rows:
            fv = FeatureVector.from_json(rows[0]["serialized_features"])
            # Backfill cache
            await self.redis.client.set(key, rows[0]["serialized_features"], ex=self.cache_ttl)
            return fv

        return None

    async def get_batch(self, mints: List[str]) -> Dict[str, Optional[FeatureVector]]:
        """
        Batch retrieval for optimized inference.
        """
        results = {}
        # Try MGET for efficiency
        keys = [f"{self._prefix}{m}" for m in mints]
        cached_list = await self.redis.client.mget(keys)
        
        for mint, cached in zip(mints, cached_list):
            if cached:
                results[mint] = FeatureVector.from_json(cached)
            else:
                results[mint] = await self.get(mint) # Fallback to single get (DB check)
        
        return results
