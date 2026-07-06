"""Main entry point for Solbot V3."""

import asyncio
import logging
import signal
import sys
import os

from solbot.config import BotConfig
from solbot.db import Database
from solbot.core.event_bus import EventBus
from solbot.core.event_store import EventStore
from solbot.core.telemetry import TelemetryManager
from solbot.engines.creator_genome import CreatorGenomeEngine
from solbot.engines.wallet_graph import WalletGraphEngine
from solbot.storage.feature_store import FeatureStore
from solbot.storage.redis import RedisManager
from solbot.rpc_balancer import RPCBalancer


def _register_shutdown_handlers(loop: asyncio.AbstractEventLoop, stop_event: asyncio.Event, logger: logging.Logger):
    def handle_exit(*_args):
        logger.info("Shutdown signal received.")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_exit)
        except NotImplementedError:
            signal.signal(sig, handle_exit)


async def main():
    config = BotConfig()
    errors = config.validate()
    if errors:
        for err in errors:
            logging.error("Config error: %s", err)
        sys.exit(1)

    logging.basicConfig(level=config.logging.level)
    logger = logging.getLogger("solbot.main")

    db = Database()
    await db.connect()

    redis = RedisManager()
    try:
        await redis.connect()
    except Exception as exc:
        logger.warning("Redis unavailable (%s); feature store caching disabled.", exc)
        redis = None

    event_bus = EventBus()
    await event_bus.start()

    event_store = EventStore(db)
    await event_store.start()

    telemetry = TelemetryManager(db)
    await telemetry.start()

    rpc_pool_urls = os.getenv("SOLANA_RPC_POOL", "")
    nodes = []
    if rpc_pool_urls:
        for idx, url in enumerate(rpc_pool_urls.split(","), 1):
            url = url.strip()
            if url:
                nodes.append({"url": url, "name": f"node_{idx}"})
    if not nodes:
        nodes.append({"url": config.solana.rpc_url, "name": "primary"})

    rpc_pool = RPCBalancer(nodes)
    rpc_monitor_task = asyncio.create_task(rpc_pool.run_monitor())

    creator_genome = CreatorGenomeEngine(db, event_store)
    wallet_graph = WalletGraphEngine(db)
    await wallet_graph.initialize()

    feature_store = FeatureStore(redis, db) if redis else None

    from solbot.bot import Solbot

    bot = Solbot(
        config,
        db=db,
        event_store=event_store,
        event_bus=event_bus,
        telemetry=telemetry,
        creator_genome=creator_genome,
        wallet_graph=wallet_graph,
        feature_store=feature_store,
        rpc_pool=rpc_pool,
    )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    _register_shutdown_handlers(loop, stop_event, logger)

    bot_task = asyncio.create_task(bot.start())
    await stop_event.wait()

    await bot.stop()
    bot_task.cancel()
    try:
        await bot_task
    except asyncio.CancelledError:
        pass

    rpc_monitor_task.cancel()
    try:
        await rpc_monitor_task
    except asyncio.CancelledError:
        pass

    await event_bus.stop()
    await event_store.stop()
    await telemetry.stop()
    if redis:
        await redis.disconnect()
    logger.info("Solbot V3 Shutdown Complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass