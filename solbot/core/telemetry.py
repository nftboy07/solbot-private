import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional
from solbot.db import Database

logger = logging.getLogger(__name__)

class TelemetryManager:
    def __init__(self, db: Database):
        self.db = db
        self.queue = asyncio.Queue()
        self._worker_task = None
        self._running = False

    async def start(self):
        """Start the background worker."""
        if self._worker_task is not None:
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._worker())
        logger.info("TelemetryManager worker started.")

    async def stop(self):
        """Stop the worker and process remaining items."""
        self._running = False
        if self._worker_task:
            await self.queue.join()
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
        logger.info("TelemetryManager worker stopped.")

    async def _worker(self):
        while self._running or not self.queue.empty():
            try:
                task_fn, data = await self.queue.get()
                try:
                    await task_fn(data)
                except Exception as e:
                    logger.error(f"Error executing telemetry write: {e}")
                finally:
                    self.queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Telemetry worker error: {e}")
                await asyncio.sleep(1)

    def _enqueue(self, task_fn, data: Dict[str, Any]):
        try:
            self.queue.put_nowait((task_fn, data))
        except asyncio.QueueFull:
            logger.warning("Telemetry queue full, dropping event.")

    def log_trade_event(self, trade_id: str, **kwargs):
        data = kwargs.copy()
        data['trade_id'] = trade_id
        if 'metadata' in data and isinstance(data['metadata'], dict):
            data['metadata'] = json.dumps(data['metadata'])
        self._enqueue(self.db.log_trade_event, data)

    _SIGNAL_COLUMNS = frozenset({
        "event_id", "signal_id", "mint", "creator", "wallet_signal",
        "confidence", "raw_signal_data", "timestamp",
    })

    def log_signal_event(self, event_id: str, signal_id: str, **kwargs):
        extra = {k: v for k, v in kwargs.items() if k not in self._SIGNAL_COLUMNS}
        data = {k: v for k, v in kwargs.items() if k in self._SIGNAL_COLUMNS}
        data["event_id"] = event_id
        data["signal_id"] = signal_id
        if "timestamp" not in data:
            data["timestamp"] = time.time()
        if extra:
            raw = data.get("raw_signal_data")
            if isinstance(raw, str):
                try:
                    merged = json.loads(raw)
                    if isinstance(merged, dict):
                        merged.update(extra)
                        data["raw_signal_data"] = json.dumps(merged)
                    else:
                        data["raw_signal_data"] = json.dumps({"payload": merged, **extra})
                except json.JSONDecodeError:
                    data["raw_signal_data"] = json.dumps(extra)
            elif isinstance(raw, dict):
                raw.update(extra)
                data["raw_signal_data"] = json.dumps(raw)
            elif isinstance(raw, list):
                data["raw_signal_data"] = json.dumps({"items": raw, **extra})
            else:
                data["raw_signal_data"] = json.dumps(extra)
        elif "raw_signal_data" in data and isinstance(data["raw_signal_data"], (dict, list)):
            data["raw_signal_data"] = json.dumps(data["raw_signal_data"])
        self._enqueue(self.db.log_signal_event, data)

    def log_rpc_call(self, request_id: str, provider: str, method: str, latency_ms: float, **kwargs):
        data = kwargs.copy()
        data.update({
            'request_id': request_id,
            'provider': provider,
            'method': method,
            'latency_ms': latency_ms,
            'timestamp': time.time()
        })
        self._enqueue(self.db.log_rpc_event, data)

    def log_proxy_call(self, proxy_id: str, proxy_url: str, latency_ms: float, **kwargs):
        data = kwargs.copy()
        data.update({
            'proxy_id': proxy_id,
            'proxy_url': proxy_url,
            'latency_ms': latency_ms,
            'timestamp': time.time()
        })
        self._enqueue(self.db.log_proxy_event, data)

    def capture_feature_snapshot(self, trade_id: Optional[str], signal_id: Optional[str], features: Dict[str, Any]):
        data = {
            'trade_id': trade_id,
            'signal_id': signal_id,
            'serialized_features': json.dumps(features),
            'timestamp': time.time()
        }
        self._enqueue(self.db.log_feature_snapshot, data)
