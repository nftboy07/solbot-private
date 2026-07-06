"""Tests for telemetry signal event schema normalization."""

import asyncio
import json

from solbot.core.telemetry import TelemetryManager


class CaptureDB:
    def __init__(self):
        self.rows = []

    async def log_signal_event(self, data):
        self.rows.append(data)


def test_log_signal_event_packs_extra_fields_into_raw_signal_data():
    db = CaptureDB()
    telemetry = TelemetryManager(db)

    telemetry.log_signal_event(
        "evt-1",
        "sig-1",
        mint="mint123",
        wallet_signal="pump_ws",
        confidence=52.0,
        ai_score=70.0,
        creator_score=50.0,
    )

    async def drain():
        telemetry._running = True
        while not telemetry.queue.empty():
            task_fn, data = await telemetry.queue.get()
            await task_fn(data)
            telemetry.queue.task_done()
        telemetry._running = False

    asyncio.run(drain())

    assert len(db.rows) == 1
    row = db.rows[0]
    assert "ai_score" not in row
    assert "creator_score" not in row
    raw = json.loads(row["raw_signal_data"])
    assert raw["ai_score"] == 70.0
    assert raw["creator_score"] == 50.0