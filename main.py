"""Main entry point for Solbot V3."""

import asyncio
import logging
import signal
from solbot.config import BotConfig
from solbot.db import Database
from solbot.telegram import TelegramController
from solbot.core.event_store import EventStore
from solbot.core.telemetry import TelemetryManager
from solbot.engines.creator_genome import CreatorGenomeEngine
from solbot.engines.wallet_graph import WalletGraphEngine
from solbot.storage.feature_store import FeatureStore
from solbot.storage.redis import RedisManager
from solbot.rpc_pool import RPCPool

async def main():
    # 1. Config & Logging
    config = BotConfig()
    logging.basicConfig(level=config.logging.level)
    logger = logging.getLogger("solbot.main")
    
    # 2. Infrastructure
    db = Database()
    await db.connect()
    
    redis = RedisManager() # Assuming default connection
    
    event_store = EventStore(db)
    await event_store.start()
    
    telemetry = TelemetryManager(db)
    await telemetry.start()
    
    rpc_pool = RPCPool([{"url": config.solana.rpc_url, "name": "primary"}])
    asyncio.create_task(rpc_pool.run_monitor())
    
    # 3. Engines
    creator_genome = CreatorGenomeEngine(db, event_store)
    wallet_graph = WalletGraphEngine(db)
    await wallet_graph.initialize()
    
    feature_store = FeatureStore(redis, db)
    
    # 4. Real Bot Instance
    from solbot.bot import Solbot
    bot = Solbot(
        config,
        event_store=event_store,
        telemetry=telemetry,
        creator_genome=creator_genome,
        wallet_graph=wallet_graph,
        feature_store=feature_store,
        rpc_pool=rpc_pool
    )
    
    # 5. Start the Real Bot
    await bot.start()
    
    # 6. Keep Alive
    stop_event = asyncio.Event()
    
    def handle_exit():
        logger.info("Shutdown signal received.")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(sig, handle_exit)
        
    await stop_event.wait()
    
    # 7. Cleanup
    await bot.stop()
    await event_store.stop()
    await telemetry.stop()
    logger.info("Solbot V3 Shutdown Complete.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

