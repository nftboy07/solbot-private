"""Telegram notification and command listener client for Solbot."""

import asyncio
import aiohttp
import logging
from typing import Optional, Any

from solbot.config import TelegramConfig

logger = logging.getLogger("bot.telegram")

class TelegramManager:
    """Async Telegram manager for sending notifications and listening for commands."""

    def __init__(self, config: TelegramConfig):
        self._config = config
        self._session: Optional[aiohttp.ClientSession] = None
        self._base_url = f"https://api.telegram.org/bot{self._config.token}"
        self._offset = 0
        self._running = False

    async def start(self, bot_instance: Any):
        """Initialize the session and start the polling loop as a background task."""
        if not self._config.token or not self._config.chat_id:
            logger.warning("Telegram not configured - listener will not start")
            return

        if not self._session:
            self._session = aiohttp.ClientSession()
        
        self._running = True
        # Start the listener in the background
        asyncio.create_task(self._poll_loop(bot_instance))
        logger.info("Telegram listener started in background")

    async def stop(self):
        """Stop the listener and close the session."""
        self._running = False
        if self._session:
            await self._session.close()
            self._session = None
            logger.info("Telegram manager stopped")

    async def send_message(self, text: str):
        """Send a message to the configured Telegram chat."""
        if not self._config.token or not self._config.chat_id:
            return

        if not self._session:
            self._session = aiohttp.ClientSession()

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
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")

    async def _poll_loop(self, bot_instance: Any):
        """Main polling loop using long polling via getUpdates."""
        while self._running:
            try:
                params = {
                    "offset": self._offset,
                    "timeout": 30
                }
                url = f"{self._base_url}/getUpdates"
                async with self._session.get(url, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        updates = data.get("result", [])
                        await self._handle_updates(updates, bot_instance)
                    elif resp.status == 401:
                        logger.error("Invalid Telegram token")
                        self._running = False
                    else:
                        await asyncio.sleep(10)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Telegram polling error: {e}")
                await asyncio.sleep(10)

    async def _handle_updates(self, updates: list, bot_instance: Any):
        """Process incoming Telegram updates."""
        for update in updates:
            self._offset = update["update_id"] + 1
            
            message = update.get("message")
            if not message:
                continue
                
            text = message.get("text", "")
            from_chat_id = str(message.get("chat", {}).get("id", ""))
            authorized_chat_id = str(self._config.chat_id)

            # Security: Only respond to the authorized chat ID
            if from_chat_id != authorized_chat_id:
                logger.warning(f"Ignored message from unauthorized chat: {from_chat_id}")
                continue

            if text == "/status":
                await self.send_status(bot_instance)

    async def send_status(self, bot_instance: Any):
        """Compose and send a status report using bot instance data."""
        trades = getattr(bot_instance, "_trades", [])
        successful = [t for t in trades if t.success]
        failed = [t for t in trades if not t.success]
        
        # Calculate latency
        avg_latency = 0
        if trades:
            avg_latency = sum(t.latency_ms for t in trades) / len(trades)

        # Get token count from filter
        seen_count = 0
        if hasattr(bot_instance, "_filter") and bot_instance._filter:
            seen_count = getattr(bot_instance._filter, "seen_count", 0)

        msg = (
            "<b>📊 Solbot Status Report</b>\n"
            "----------------------------\n"
            f"✅ <b>Success:</b> {len(successful)}\n"
            f"❌ <b>Failed:</b>  {len(failed)}\n"
            f"⏱ <b>Avg Latency:</b> {avg_latency:.0f}ms\n"
            f"🔍 <b>Tokens Seen:</b> {seen_count}\n"
            "----------------------------\n"
            "<i>Monitoring Pump.fun live...</i>"
        )
        await self.send_message(msg)
