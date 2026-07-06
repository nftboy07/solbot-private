"""Central observability bridge for telemetry, features, and events."""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Dict, Optional

from solbot.core.event_bus import Event, EventBus, EventType
from solbot.storage.feature_store import FeatureStore, FeatureVector

logger = logging.getLogger("solbot.observability")


class ObservabilityHub:
    def __init__(
        self,
        db,
        telemetry=None,
        feature_store: Optional[FeatureStore] = None,
        event_bus: Optional[EventBus] = None,
        risk_manager=None,
    ):
        self.db = db
        self.telemetry = telemetry
        self.feature_store = feature_store
        self.event_bus = event_bus
        self.risk_manager = risk_manager

    async def publish(self, event_type: EventType, data: Dict[str, Any], source: str = "solbot"):
        if not self.event_bus:
            return
        await self.event_bus.publish(Event(type=event_type, data=data, source=source))

    async def record_signal_async(self, mint: str, source: str, **kwargs):
        event_id = str(uuid.uuid4())
        signal_id = kwargs.pop("signal_id", event_id)
        data = {
            "event_id": event_id,
            "signal_id": signal_id,
            "mint": mint,
            "wallet_signal": source,
            "confidence": float(kwargs.get("confidence", kwargs.get("ai_score", 0)) or 0),
            "raw_signal_data": json.dumps(kwargs),
            "timestamp": time.time(),
        }
        if self.telemetry:
            self.telemetry.log_signal_event(event_id, signal_id, mint=mint, wallet_signal=source, **kwargs)
        else:
            await self.db.log_signal_event(data)
        await self.publish(EventType.SIGNAL, {"mint": mint, "source": source, **kwargs})

    async def record_trade(
        self,
        action: str,
        mint: str,
        *,
        symbol: str = "",
        size: float = 0.0,
        success: bool = False,
        tx_signature: Optional[str] = None,
        error: Optional[str] = None,
        reason: Optional[str] = None,
        pnl: float = 0.0,
        latency_ms: float = 0.0,
    ) -> str:
        trade_id = str(uuid.uuid4())
        now = time.time()
        metadata = {
            "action": action,
            "mint": mint,
            "symbol": symbol,
            "size": size,
            "success": success,
            "tx_signature": tx_signature,
            "error": error,
            "reason": reason,
            "latency_ms": latency_ms,
        }
        row = {
            "trade_id": trade_id,
            "detect_ts": now,
            "tx_submit_ts": now,
            "pnl": pnl,
            "metadata": json.dumps(metadata),
        }
        if self.telemetry:
            self.telemetry.log_trade_event(trade_id, **row)
        else:
            await self.db.log_trade_event(row)
        await self.publish(
            EventType.TRADE_EXECUTION,
            {"trade_id": trade_id, "action": action, "mint": mint, "success": success},
        )
        return trade_id

    def record_rpc(self, provider: str, method: str, latency_ms: float, success: bool, status_code: Optional[int] = None):
        request_id = str(uuid.uuid4())
        payload = {
            "endpoint": provider,
            "success": int(success),
            "slot": 0,
        }
        if self.telemetry:
            self.telemetry.log_rpc_call(request_id, provider, method, latency_ms, **payload)
        else:
            import asyncio
            row = {
                "request_id": request_id,
                "provider": provider,
                "endpoint": provider,
                "method": method,
                "latency_ms": latency_ms,
                "slot": 0,
                "success": int(success),
                "timestamp": time.time(),
            }
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self.db.log_rpc_event(row))
            except RuntimeError:
                pass
        if self.risk_manager:
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self.risk_manager.update_rpc_latency(latency_ms))
            except RuntimeError:
                pass

    async def capture_features(self, mint: str, **metrics):
        if not self.feature_store or not self.feature_store.redis or not self.feature_store.redis.client:
            return
        vector = FeatureVector(
            mint=mint,
            creator_score=float(metrics.get("creator_score", 0.0)),
            wallet_cluster=float(metrics.get("wallet_cluster", 0.0)),
            top_holder_pct=float(metrics.get("top_holder_pct", 0.0)),
            metadata=metrics,
        )
        try:
            await self.feature_store.store(vector)
            if self.telemetry:
                self.telemetry.capture_feature_snapshot(None, None, metrics)
        except Exception as exc:
            logger.debug("Feature capture skipped for %s: %s", mint, exc)