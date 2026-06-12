"""Telegram scraper for Solbot using Telethon."""

import asyncio
import re
import logging
import time
from typing import Optional

from telethon import TelegramClient, events
from solbot.config import TelegramConfig
from solbot.models import TokenEvent

logger = logging.getLogger("bot.telegram_scraper")

class TelegramScraper:
    """Monitors Telegram channels for Solana mints and pump.fun links."""

    def __init__(self, config: TelegramConfig, bot_instance):
        """
        Initialize the Telegram scraper.
        
        Args:
            config: Telegram configuration object containing API credentials.
            bot_instance: The main Solbot instance for executing trades.
        """
        self._config = config
        self._bot = bot_instance
        self._client: Optional[TelegramClient] = None
        self._running = False
        
        # Regex for Solana addresses and pump.fun links
        self._mint_regex = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")
        self._pump_regex = re.compile(r"pump\.fun/coin/([1-9A-HJ-NP-Za-km-z]{32,44})")

    async def start_monitoring(self):
        """Initialize and start the Telethon client."""
        if not self._config.api_id or not self._config.api_hash:
            logger.warning("TelegramScraper: api_id or api_hash missing. Skipping.")
            return

        # Initialize client with a persistent session file
        self._client = TelegramClient('session/solbot_scraper', int(self._config.api_id), self._config.api_hash)
        
        @self._client.on(events.NewMessage)
        async def handle_new_message(event):
            if not self._running:
                return
            
            text = event.message.message
            if not text:
                return

            # 1. Check for pump.fun links
            pump_match = self._pump_regex.search(text)
            mint = pump_match.group(1) if pump_match else None
            
            # 2. Check for raw mint addresses
            if not mint:
                mints = self._mint_regex.findall(text)
                for m in mints:
                    if m != "So11111111111111111111111111111111111111112":  # Skip native SOL
                        mint = m
                        break
            
            if mint:
                sender = await event.get_sender()
                sender_name = getattr(sender, 'title', getattr(sender, 'username', 'Unknown'))
                logger.info(f"📱 Telegram Match from {sender_name}: Found Mint {mint}. Forwarding to KOL mention handler.")
                
                # Route to sentiment aggregator
                asyncio.create_task(
                    self._bot._handle_kol_mention(
                        mint, 
                        f"TG: {sender_name}", 
                        text
                    )
                )

        logger.info("Starting Telethon client...")
        await self._client.start()
        self._running = True
        logger.info("Telegram Scraper is now active.")
        
        try:
            await self._client.run_until_disconnected()
        except Exception as e:
            logger.error(f"Telegram client error: {e}")
        finally:
            self._running = False

    async def stop(self):
        """Stop the Telethon client."""
        self._running = False
        if self._client:
            await self._client.disconnect()
        logger.info("Telegram Scraper stopped.")
