"""Telegram notification and command listener client for Solbot."""

import asyncio
import aiohttp
import logging
from typing import Optional, Any
from solbot.config import TelegramConfig, BotMode

logger = logging.getLogger("bot.telegram")

class TelegramManager:
    """Enhanced Telegram manager with V28 control commands."""

    def __init__(self, config: TelegramConfig):
        self._config = config
        self._session: Optional[aiohttp.ClientSession] = None
        self._base_url = f"https://api.telegram.org/bot{self._config.token}"
        self._offset = 0
        self._running = False

    async def start(self, bot_instance: Any):
        if not self._config.token or not self._config.chat_id:
            logger.warning("Telegram token or chat_id not configured.")
            return
        
        if not self._session:
            self._session = aiohttp.ClientSession()
        
        self._running = True
        # Start the polling loop in a background task
        asyncio.create_task(self._poll_loop(bot_instance))
        logger.info("Telegram command listener started.")

    async def stop(self):
        self._running = False
        if self._session:
            await self._session.close()
            self._session = None

    async def send_message(self, text: str):
        if not self._session or not self._config.token:
            return
            
        url = f"{self._base_url}/sendMessage"
        payload = {
            "chat_id": self._config.chat_id,
            "text": text,
            "parse_mode": "HTML"
        }
        
        try:
            async with self._session.post(url, json=payload) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    logger.error(f"Telegram send error: {err}")
        except Exception as e:
            logger.error(f"Telegram exception: {e}")

    async def _poll_loop(self, bot_instance: Any):
        logger.info("Entering Telegram poll loop...")
        while self._running:
            try:
                params = {"offset": self._offset, "timeout": 20}
                async with self._session.get(f"{self._base_url}/getUpdates", params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        updates = data.get("result", [])
                        if updates:
                            await self._handle_updates(updates, bot_instance)
                    elif resp.status == 401:
                        logger.error("Invalid Telegram token.")
                        self._running = False
                    else:
                        logger.warning(f"Telegram poll status: {resp.status}")
                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Telegram poll error: {e}")
                await asyncio.sleep(5)

    async def _handle_updates(self, updates: list, bot: Any):
        for update in updates:
            self._offset = update["update_id"] + 1
            msg = update.get("message")
            if not msg:
                continue
                
            from_id = str(msg.get("chat", {}).get("id", ""))
            if from_id != str(self._config.chat_id):
                logger.warning(f"Unauthorized access attempt from chat_id: {from_id}")
                continue
            
            text = msg.get("text", "")
            if not text:
                continue

            logger.info(f"Telegram command received: {text}")
            
            if text == "/status":
                await self.send_status(bot)
            elif text == "/positions":
                await self.send_positions(bot)
            elif text == "/pause":
                bot._paused = True
                await self.send_message("⏸ <b>Bot Paused</b>")
            elif text == "/resume":
                bot._paused = False
                await self.send_message("▶️ <b>Bot Resumed</b>")
            elif text.startswith("/mode "):
                try:
                    mode_str = text.split(" ")[1].upper()
                    if mode_str in ["DEGEN", "NORMAL"]:
                        # Access the filter directly to update its mode
                        if hasattr(bot, "_filter") and bot._filter:
                            bot._filter._mode = BotMode[mode_str]
                            await self.send_message(f"🔄 <b>Mode switched to {mode_str}</b>")
                        else:
                            await self.send_message("❌ <b>Error: Filter not initialized</b>")
                    else:
                        await self.send_message("❌ <b>Invalid mode. Use /mode degen or /mode normal</b>")
                except Exception as e:
                    await self.send_message(f"❌ <b>Error switching mode: {e}</b>")
            elif text == "/exitall":
                if not bot._positions:
                    await self.send_message("No positions to exit.")
                else:
                    for mint in list(bot._positions.keys()):
                        asyncio.create_task(bot._exit_position(bot._positions[mint], "Manual Exit", 1.0))
                    await self.send_message("⚠️ <b>Exiting all positions...</b>")

    async def send_status(self, bot: Any):
        mode = "UNKNOWN"
        if hasattr(bot, "_filter") and bot._filter:
            mode = bot._filter._mode.name
            
        msg = (
            "<b>📊 Solbot Status</b>\n"
            f"Mode: <code>{mode}</code>\n"
            f"Paused: <code>{bot._paused}</code>\n"
            f"Active Positions: <code>{len(bot._positions)}</code>\n"
            f"Total Trades: <code>{len(bot._trades)}</code>"
        )
        await self.send_message(msg)

    async def send_positions(self, bot: Any):
        if not bot._positions:
            await self.send_message("No active positions.")
            return
        lines = ["<b>📍 Active Positions:</b>"]
        for mint, pos in bot._positions.items():
            lines.append(f"- {pos.symbol}: {pos.size} SOL (Entry: {pos.entry_price:.2f})")
        await self.send_message("\n".join(lines))
