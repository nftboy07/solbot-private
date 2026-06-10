import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set, Type
from enum import Enum

class EventType(Enum):
    MARKET_DATA = "market_data"
    SIGNAL = "signal"
    TRADE_REQUEST = "trade_request"
    TRADE_EXECUTION = "trade_execution"
    RISK_CHECK = "risk_check"
    WALLET_UPDATE = "wallet_update"
    SYSTEM_LOG = "system_log"

@dataclass
class Event:
    type: EventType
    data: Dict[str, Any]
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=datetime.utcnow)
    source: Optional[str] = None

class EventBus:
    def __init__(self, max_queue_size: int = 1000):
        self._queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=max_queue_size)
        self._subscribers: Dict[EventType, Set[Callable]] = {}
        self._running = False
        self._processing_task: Optional[asyncio.Task] = None

    async def publish(self, event: Event) -> bool:
        try:
            await self._queue.put(event)
            return True
        except asyncio.QueueFull:
            return False

    def subscribe(self, event_type: EventType, callback: Callable) -> None:
        if event_type not in self._subscribers:
            self._subscribers[event_type] = set()
        self._subscribers[event_type].add(callback)

    def unsubscribe(self, event_type: EventType, callback: Callable) -> None:
        if event_type in self._subscribers:
            self._subscribers[event_type].discard(callback)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._processing_task = asyncio.create_task(self._process_events())

    async def stop(self) -> None:
        self._running = False
        if self._processing_task:
            self._processing_task.cancel()
            try:
                await self._processing_task
            except asyncio.CancelledError:
                pass

    async def _process_events(self) -> None:
        while self._running:
            event = await self._queue.get()
            subscribers = self._subscribers.get(event.type, set())
            if subscribers:
                tasks = [asyncio.create_task(callback(event)) for callback in subscribers]
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
            self._queue.task_done()
