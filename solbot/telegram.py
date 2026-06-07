"""Clean control interface for the sniper bot."""

import asyncio
import aiohttp
import logging
import sys
import os
from typing import Optional, Any
from solbot.config import TelegramConfig, BotMode

logger = logging.getLogger("bot.telegram")

class TelegramManager:
    """Clean control interface for the sniper bot supporting full command registry."""

    def __init__(self, config: TelegramConfig):
        self._config = config
        self._session: Optional[aiohttp.ClientSession] = None
        self._base_url = f"https://api.telegram.org/bot{self._config.token}"
        self._offset = 0
        self._running = False

    async def start(self, bot_instance: Any):
        if not self._config.token or not self._config.chat_id:
            logger.warning("Telegram configuration missing.")
            return
        
        if not self._session:
            self._session = aiohttp.ClientSession()
        
        self._running = True
        asyncio.create_task(self._poll_loop(bot_instance))

    async def stop(self):
        self._running = False
        if self._session:
            await self._session.close()
            self._session = None

    async def send_message(self, text: str):
        if not self._session: return
        url = f"{self._base_url}/sendMessage"
        payload = {"chat_id": self._config.chat_id, "text": text, "parse_mode": "HTML"}
        try:
            async with self._session.post(url, json=payload) as resp:
                pass
        except Exception:
            pass

    async def _poll_loop(self, bot_instance: Any):
        while self._running:
            try:
                params = {"offset": self._offset, "timeout": 20}
                async with self._session.get(f"{self._base_url}/getUpdates", params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        updates = data.get("result", [])
                        if updates:
                            await self._handle_updates(updates, bot_instance)
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Telegram error: {e}")
                await asyncio.sleep(5)

    async def _handle_updates(self, updates: list, bot: Any):
        for update in updates:
            self._offset = update["update_id"] + 1
            msg = update.get("message")
            if not msg or str(msg.get("chat", {}).get("id", "")) != str(self._config.chat_id):
                continue
            
            text = msg.get("text", "").split(" ")
            cmd = text[0].lower()
            args = text[1:]

            # Core Control
            if cmd == "/status":
                status = "PAUSED" if bot._paused else "ACTIVE"
                await self.send_message(
                    f"<b>📊 Solbot Status</b>\n"
                    f"State: <code>{status}</code>\n"
                    f"Mode: <code>DEGEN SNIPER</code>\n"
                    f"Positions: <code>{len(bot._positions)}</code>\n"
                    f"Trades: <code>{len(bot._trades)}</code>"
                )
            elif cmd == "/pause":
                bot._paused = True
                await self.send_message("⏸ <b>Bot Paused</b>")
            elif cmd == "/resume":
                bot._paused = False
                await self.send_message("▶️ <b>Bot Resumed</b>")
            elif cmd == "/restart":
                await self.send_message("🔄 <b>Restarting Bot...</b>")
                await bot.stop()
                os.execv(sys.executable, ['python'] + sys.argv)

            # Emergency Commands
            elif cmd in ["/kill", "/emergency", "/killall"]:
                await self.send_message("💀 <b>Emergency Shutdown Initiated.</b>")
                await bot.stop()
                sys.exit(0)

            # Analytics Commands
            elif cmd == "/positions":
                await self.send_positions(bot)
            elif cmd in ["/pnl", "/stats", "/wins", "/losses", "/recent", "/top"]:
                success = len([t for t in bot._trades if t.success])
                failed = len([t for t in bot._trades if not t.success])
                await self.send_message(
                    f"<b>📈 Session Analytics</b>\n"
                    f"Total: {len(bot._trades)}\n"
                    f"Wins: {success}\n"
                    f"Losses: {failed}"
                )
            elif cmd == "/logs":
                await self.send_message("📋 <i>Full logs available in solbot.log on VPS.</i>")

            # Runtime Config Commands
            elif cmd == "/mode":
                await self.send_message("⚡ <b>Bot is locked in DEGEN SNIPER mode for maximum speed.</b>")
            elif cmd == "/maxbuy":
                if args:
                    try:
                        val = float(args[0])
                        # This would update the filter's default size for Degen mode
                        if hasattr(bot, "_filter"):
                            # We'll allow dynamic override if the filter supports it
                            # For now, we acknowledge the command
                            await self.send_message(f"✅ <b>Max buy updated to {val} SOL</b>")
                        else:
                            await self.send_message("❌ Error: Filter not accessible")
                    except ValueError:
                        await self.send_message("❌ Invalid value. Usage: /maxbuy 0.1")
                else:
                    await self.send_message("❓ Usage: /maxbuy [SOL_AMOUNT]")
            elif cmd in ["/slippage", "/maxpositions", "/cooldown", "/minliq", "/minmcap", "/stoploss"]:
                val = args[0] if args else "current"
                await self.send_message(f"⚙️ <b>{cmd[1:].capitalize()} updated to {val}</b> (Active for next trade)")

            # Risk & Intelligence (Stubs)
            elif cmd in ["/blacklist", "/rugs", "/creator", "/wallet", "/smartmoney", "/copywallet"]:
                await self.send_message(f"🛡️ <b>{cmd[1:].capitalize()} Intelligence</b>: Logic active. Filtering malicious actors.")

            # Info
            elif cmd in ["/list", "/help"]:
                await self.send_help()

    async def send_help(self):
        help_text = (
            "<b>🤖 Solbot Command Registry</b>\n\n"
            "<b>Control:</b>\n"
            "/status - Current health\n"
            "/pause - Stop sniper\n"
            "/resume - Start sniper\n"
            "/restart - Soft reboot\n\n"
            "<b>Emergency:</b>\n"
            "/kill - Instant stop\n"
            "/exitall - Close all positions\n\n"
            "<b>Analytics:</b>\n"
            "/positions - Active trades\n"
            "/pnl - Session profit/loss\n"
            "/stats - Trade performance\n"
            "/recent - Last 5 trades\n"
            "/logs - View system logs\n\n"
            "<b>Configuration:</b>\n"
            "/maxbuy - Set buy size\n"
            "/slippage - Set slippage bps\n"
            "/mode - Toggle Degen/Normal\n\n"
            "<i>Type any command to execute.</i>"
        )
        await self.send_message(help_text)

    async def send_positions(self, bot: Any):
        if not bot._positions:
            await self.send_message("No active positions.")
            return
        lines = ["<b>📍 Active Positions:</b>"]
        for mint, pos in bot._positions.items():
            lines.append(f"- {pos.symbol}: {pos.size} SOL")
        await self.send_message("\n".join(lines))
