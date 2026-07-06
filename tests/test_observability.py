import os

os.environ.setdefault("WALLET_PRIVATE_KEY", "test-key")

from solbot.observability import ObservabilityHub
from solbot.core.event_bus import EventType


class FakeDB:
    def __init__(self):
        self.trades = []
        self.signals = []

    async def log_trade_event(self, data):
        self.trades.append(data)

    async def log_signal_event(self, data):
        self.signals.append(data)


def test_record_trade_builds_metadata():
    import asyncio

    db = FakeDB()
    hub = ObservabilityHub(db)

    async def run():
        trade_id = await hub.record_trade(
            "buy",
            "mint123",
            symbol="TEST",
            size=0.01,
            success=True,
            tx_signature="sig",
        )
        assert trade_id
        assert len(db.trades) == 1
        assert db.trades[0]["trade_id"] == trade_id

    asyncio.run(run())