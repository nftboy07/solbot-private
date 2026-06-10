import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from solbot.db import Database

logger = logging.getLogger(__name__)

@dataclass
class Event:
    type: str
    payload: Dict[str, Any]
    source: Optional[str] = None
    strategy_version: Optional[str] = None
    git_commit: Optional[str] = None
    model_hash: Optional[str] = None
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp_ns: int = field(default_factory=lambda: time.time_ns())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "timestamp_ns": self.timestamp_ns,
            "type": self.type,
            "payload": json.dumps(self.payload),
            "source": self.source,
            "strategy_version": self.strategy_version,
            "git_commit": self.git_commit,
            "model_hash": self.model_hash
        }

class EventStore:
    def __init__(self, db: Database):
        self.db = db
        self.queue = asyncio.Queue()
        self._worker_task = None
        self._running = False

    async def start(self):
        """Initialize table and start background worker."""
        schema = """
        CREATE TABLE IF NOT EXISTS event_ledger (
            event_id TEXT PRIMARY KEY,
            timestamp_ns INTEGER NOT NULL,
            type TEXT NOT NULL,
            payload TEXT NOT NULL,
            source TEXT,
            strategy_version TEXT,
            git_commit TEXT,
            model_hash TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_event_type ON event_ledger(type);
        CREATE INDEX IF NOT EXISTS idx_event_timestamp ON event_ledger(timestamp_ns);
        """
        await self.db._execute_write(schema)
        
        if self._worker_task is not None:
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._worker())
        logger.info("EventStore ledger worker started.")

    async def stop(self):
        self._running = False
        if self._worker_task:
            await self.queue.join()
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

    async def _worker(self):
        while self._running or not self.queue.empty():
            try:
                event = await self.queue.get()
                try:
                    await self._persist_event(event)
                except Exception as e:
                    logger.error(f"Error persisting event to ledger: {e}")
                finally:
                    self.queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"EventStore worker error: {e}")
                await asyncio.sleep(1)

    async def _persist_event(self, event: Event):
        data = event.to_dict()
        cols = ", ".join(data.keys())
        placeholders = ", ".join(["?" for _ in data])
        query = f"INSERT INTO event_ledger ({cols}) VALUES ({placeholders})"
        await self.db._execute_write(query, tuple(data.values()))

    def append(self, event: Event):
        """Append an event asynchronously without blocking."""
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("EventStore queue full, dropping event.")

    async def query_by_type(self, event_type: str, limit: int = 100) -> List[Dict[str, Any]]:
        query = "SELECT * FROM event_ledger WHERE type = ? ORDER BY timestamp_ns DESC LIMIT ?"
        rows = await self.db._execute_read(query, (event_type, limit))
        return [dict(r) for r in rows]

    async def query_time_range(self, start_ns: int, end_ns: int) -> List[Dict[str, Any]]:
        query = "SELECT * FROM event_ledger WHERE timestamp_ns >= ? AND timestamp_ns <= ? ORDER BY timestamp_ns ASC"
        rows = await self.db._execute_read(query, (start_ns, end_ns))
        return [dict(r) for r in rows]
