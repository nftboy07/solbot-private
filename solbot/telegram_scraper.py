"""Telegram scraper for Solbot using Telethon."""

import asyncio
import re
import logging
import time
from typing import Optional, List, Set

from telethon import TelegramClient, events
from solbot.config import TelegramConfig
from solbot.models import TokenEvent

logger = logging.getLogger("bot.telegram_scraper")

class TelegramScraper:
    """Monitors Telegram channels for Solana mints and pump.fun links."""

    def __init__(self, config: TelegramConfig, bot_instance):
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
                    if m != "So11111111111111111111111111111111111111112": # Skip SOL
                        mint = m
                        break
            
            if mint:
                sender = await event.get_sender()
                sender_name = getattr(sender, 'title', getattr(sender, 'username', 'Unknown'))
                logger.info(f"📱 Telegram Match from {sender_name}: Found Mint {mint}")
                
                # Create token event and trigger snipe
                token = TokenEvent(
                    mint=mint,
                    name=f"TG: {sender_name}",
                    symbol="TG_SCRAPE",
                    creator="telegram_scraper",
                    market_cap_usd=0,
                    liquidity_sol=0,
                    timestamp=time.time()
                )
                
                # Execute snipe via bot instance
                asyncio.create_task(self._bot._execute_snipe(token, self._bot._config.jupiter.buy_amount_sol, f"Telegram Scraper ({sender_name})"))

        logger.info("Starting Telethon client...")
        await self._client.start()
        self._running = True
        logger.info("Telegram Scraper is now active.")
        await self._client.run_until_disconnected()

    async def stop(self):
        """Stop the Telethon client."""
        self._running = False
        if self._client:
            await self._client.disconnect()
        logger.info("Telegram Scraper stopped.")
