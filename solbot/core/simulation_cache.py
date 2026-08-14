"""High-speed LRU Simulation and Transaction Cache for Solana."""

import hashlib
import time
from dataclasses import dataclass
from typing import Dict, Optional, Any


@dataclass
class SimulationResult:
    """Cached pre-flight simulation result."""
    signature_hash: str
    units_consumed: int
    logs: list
    err: Optional[Any]
    timestamp: float
    is_valid: bool


class SimulationCache:
    """In-memory cache for transaction simulation and bytecode parsing."""

    def __init__(self, ttl_seconds: float = 3.0, max_size: int = 500):
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._cache: Dict[str, SimulationResult] = {}

    def _hash_tx(self, tx_bytes: bytes) -> str:
        return hashlib.sha256(tx_bytes).hexdigest()[:16]

    def get(self, tx_bytes: bytes) -> Optional[SimulationResult]:
        """Retrieve unexpired simulation result."""
        key = self._hash_tx(tx_bytes)
        result = self._cache.get(key)
        if result and (time.time() - result.timestamp) < self._ttl:
            return result
        elif result:
            self._cache.pop(key, None)
        return None

    def put(self, tx_bytes: bytes, units_consumed: int, logs: list, err: Optional[Any] = None) -> SimulationResult:
        """Store simulation outcome."""
        if len(self._cache) >= self._max_size:
            # Purge oldest 20%
            keys_to_remove = list(self._cache.keys())[: int(self._max_size * 0.2)]
            for k in keys_to_remove:
                self._cache.pop(k, None)

        key = self._hash_tx(tx_bytes)
        res = SimulationResult(
            signature_hash=key,
            units_consumed=units_consumed,
            logs=logs,
            err=err,
            timestamp=time.time(),
            is_valid=(err is None),
        )
        self._cache[key] = res
        return res
