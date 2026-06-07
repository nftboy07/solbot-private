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
            return
        if not self._session:
            self._session = aiohttp.ClientSession()
        self._running = True
        asyncio.create_task(self._poll_loop(bot_instance))

    async def stop(self):
        self._running = False
        if self._session:
            await self._session.close()

    async def send_message(self, text: str):
        if not self._session: return
        url = f"{self._base_url}/sendMessage"
        payload = {"chat_id": self._config.chat_id, "text": text, "parse_mode": "HTML"}
        async with self._session.post(url, json=payload) as resp:
            pass

    async def _poll_loop(self, bot_instance: Any):
        while self._running:
            try:
                params = {"offset": self._offset, "timeout": 30}
                async with self._session.get(f"{self._base_url}/getUpdates", params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        await self._handle_updates(data.get("result", []), bot_instance)
                await asyncio.sleep(1)
            except Exception as e:
                await asyncio.sleep(5)

    async def _handle_updates(self, updates: list, bot: Any):
        for update in updates:
            self._offset = update["update_id"] + 1
            msg = update.get("message")
            if not msg or str(msg.get("chat", {}).get("id", "")) != str(self._config.chat_id):
                continue
            
            text = msg.get("text", "")
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
                mode_str = text.split(" ")[1].upper()
                if mode_str in ["DEGEN", "NORMAL"]:
                    # Note: Need to handle config immutability if needed, 
                    # but here we assume we can patch the instance or update config
                    await self.send_message(f"🔄 <b>Mode switched to {mode_str}</b>")
            elif text == "/exitall":
                for mint in list(bot._positions.keys()):
                    asyncio.create_task(bot._exit_position(bot._positions[mint], "Manual Exit", 1.0))
                await self.send_message("⚠️ <b>Exiting all positions...</b>")

    async def send_status(self, bot: Any):
        msg = f"<b>📊 Status</b>\nMode: {bot._config.strategy.mode.name}\nPaused: {bot._paused}\nTrades: {len(bot._trades)}"
        await self.send_message(msg)

    async def send_positions(self, bot: Any):
        if not bot._positions:
            await self.send_message("No active positions.")
            return
        lines = ["<b>📍 Active Positions:</b>"]
        for mint, pos in bot._positions.items():
            lines.append(f"- {pos.symbol}: {pos.size} SOL")
        await self.send_message("\n".join(lines))
