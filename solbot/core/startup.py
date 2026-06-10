import asyncio
import logging
from typing import List
from solbot.storage.redis import RedisManager
from solbot.collectors.pump import PumpCollector

logger = logging.getLogger(__name__)

class SolBotApp:
    def __init__(self):
        self.redis_manager = RedisManager()
        self.collectors = []
        self.background_tasks = set()

    async def initialize(self):
        """Initialize all components and connections."""
        logger.info("Starting SolBot V2 Migration initialization...")
        await self.redis_manager.connect()
        
        # Initialize collectors
        pump_collector = PumpCollector(self.redis_manager)
        self.collectors.append(pump_collector)
        
        logger.info("Initialization complete.")

    async def start(self):
        """Start collectors as background asyncio tasks."""
        for collector in self.collectors:
            task = asyncio.create_task(collector.start())
            self.background_tasks.add(task)
            task.add_done_callback(self.background_tasks.discard)
            
        logger.info(f"Started {len(self.collectors)} collectors.")
        
    async def shutdown(self):
        """Graceful shutdown of all services."""
        logger.info("Shutting down SolBot...")
        await self.redis_manager.disconnect()
        for task in self.background_tasks:
            task.cancel()
        if self.background_tasks:
            await asyncio.gather(*self.background_tasks, return_exceptions=True)
        logger.info("Shutdown complete.")

async def main():
    app = SolBotApp()
    try:
        await app.initialize()
        await app.start()
        # Keep the app running
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        await app.shutdown()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
