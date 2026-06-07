"""Telegram notification client for Solbot."""

import aiohttp
import logging
from typing import Optional

from solbot.config import TelegramConfig

logger = logging.getLogger("bot.telegram")

class TelegramClient:
    """Async Telegram client for sending notifications using aiohttp."""

    def __init__(self, config: TelegramConfig):
        self._config = config
        self._session: Optional[aiohttp.ClientSession] = None
        self._base_url = f"https://api.telegram.org/bot{self._config.token}"

    async def start(self):
        """Initialize the aiohttp session."""
        if not self._session:
            self._session = aiohttp.ClientSession()
            logger.info("Telegram client started")

    async def stop(self):
        """Close the aiohttp session."""
        if self._session:
            await self._session.close()
            self._session = None
            logger.info("Telegram client stopped")

    async def send_message(self, text: str):
        """Send a message to the configured Telegram chat."""
        if not self._config.token or not self._config.chat_id:
            logger.warning("Telegram not configured - skipping message")
            return

        if not self._session:
            await self.start()

        url = f"{self._base_url}/sendMessage"
        payload = {
            "chat_id": self._config.chat_id,
            "text": text,
            "parse_mode": "HTML"
        }

        try:
            async with self._session.post(url, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Telegram API error ({response.status}): {error_text}")
                else:
                    logger.debug("Telegram message sent successfully")
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
