"""Main entry point for Solbot V3."""

import asyncio
import logging
import signal
from solbot.config import BotConfig
from solbot.database import DatabaseManager
from telegram_updated import TelegramController
from solbot.bot import Solbot

async def main():
    # 1. Config & Logging
    config = BotConfig()
    logging.basicConfig(level=config.logging.level)
    logger = logging.getLogger("solbot.main")
    
    # 2. Infrastructure
    # DatabaseManager initializes itself on creation
    db = DatabaseManager()
    
    # 3. Bot Instance
    bot = Solbot(config)
    
    # 4. Telegram Controller (V3 Redesign)
    tg_controller = TelegramController(config.telegram, bot)
    bot._telegram = tg_controller
    await tg_controller.start()
    
    # 5. Bot Startup
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
    await tg_controller.stop()
    logger.info("Solbot V3 Shutdown Complete.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
