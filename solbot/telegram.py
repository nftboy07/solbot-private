"""Comprehensive Telegram control interface for Solbot."""

import asyncio
import aiohttp
import logging
import os
import sys
from typing import Optional, Any
from solbot.config import TelegramConfig

logger = logging.getLogger("bot.telegram")

class TelegramManager:
    """Enhanced control interface with full command registry."""

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
        logger.info("Telegram command listener started.")

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
                if resp.status != 200:
                    logger.error(f"Telegram send error: {await resp.text()}")
        except Exception as e:
            logger.error(f"Telegram exception: {e}")

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
            
            text = msg.get("text", "")
            if not text: continue

            cmd = text.split()[0].lower()
            
            if cmd == "/list" or cmd == "/help":
                await self._cmd_list()
            elif cmd == "/status":
                await self._cmd_status(bot)
            elif cmd == "/balance":
                await self.send_message("🔍 <b>SOL Balance:</b> Checking explorer... (Wallet balance monitoring in development)")
            elif cmd == "/portfolio" or cmd == "/positions":
                await self._cmd_portfolio(bot)
            elif cmd == "/history":
                await self._cmd_history(bot)
            elif cmd == "/profit":
                await self._cmd_profit(bot)
            elif cmd in ["/smart", "/whales", "/devs", "/scoring"]:
                await self._cmd_scoring(bot)
            elif cmd == "/risk":
                await self._cmd_risk(bot)
            elif cmd == "/pause":
                bot._paused = True
                await self.send_message("⏸ <b>Bot Paused</b>")
            elif cmd == "/resume":
                bot._paused = False
                await self.send_message("▶️ <b>Bot Resumed</b>")
            elif cmd == "/reload" or cmd == "/restart":
                await self.send_message("🔄 <b>Reloading configuration and restarting...</b>")
                await bot.stop()
                os.execv(sys.executable, ['python'] + sys.argv)
            elif cmd == "/exitall":
                await self._cmd_exitall(bot)

    async def _cmd_list(self):
        msg = (
            "<b>📜 Command Registry</b>\n"
            "/list - Show this list\n"
            "/status - Current bot state\n"
            "/balance - SOL balance\n"
            "/portfolio - Active holdings\n"
            "/positions - Alias for /portfolio\n"
            "/history - Last N trades\n"
            "/profit - Session P&L\n"
            "/smart - Smart wallet stats\n"
            "/whales - Whale tracker stats\n"
            "/devs - Developer reputation\n"
            "/scoring - Scoring system info\n"
            "/risk - Active risk settings\n"
            "/pause - Pause sniper\n"
            "/resume - Resume sniper\n"
            "/reload - Restart process\n"
            "/exitall - Emergency liquidation\n"
            "/help - Show help menu"
        )
        await self.send_message(msg)

    async def _cmd_status(self, bot: Any):
        state = "PAUSED" if bot._paused else "ACTIVE"
        msg = (
            "<b>📊 Solbot Status</b>\n"
            f"State: <code>{state}</code>\n"
            f"Mode: <code>DEGEN SNIPER</code>\n"
            f"Positions: <code>{len(bot._positions)}</code>\n"
            f"Trades: <code>{len(bot._trades)}</code>"
        )
        await self.send_message(msg)

    async def _cmd_portfolio(self, bot: Any):
        if not bot._positions:
            await self.send_message("No active positions.")
            return
        lines = ["<b>📍 Current Portfolio:</b>"]
        for mint, pos in bot._positions.items():
            lines.append(f"- {pos.symbol}: {pos.size} SOL (Entry: {pos.entry_price:.2f} MC)")
        await self.send_message("\n".join(lines))

    async def _cmd_history(self, bot: Any):
        if not bot._trades:
            await self.send_message("No trade history in this session.")
            return
        lines = ["<b>🕒 Recent History:</b>"]
        for trade in bot._trades[-10:]:
            status = "✅" if trade.success else "❌"
            lines.append(f"{status} {trade.token_mint[:8]}... | {trade.latency_ms:.0f}ms")
        await self.send_message("\n".join(lines))

    async def _cmd_profit(self, bot: Any):
        # Placeholder for real P&L calculation
        await self.send_message("<b>📈 Session P&L</b>\nEstimated: Calculating tracked exits...")

    async def _cmd_scoring(self, bot: Any):
        scores = len(getattr(bot._filter, 'wallet_metrics', {}))
        msg = (
            "<b>🐋 Wallet Intelligence</b>\n"
            f"Tracked Wallets: <code>{scores}</code>\n"
            "Smart Wallets: <code>0</code> (Building data...)"
        )
        await self.send_message(msg)

    async def _cmd_risk(self, bot: Any):
        conf = bot._config
        msg = (
            "<b>⚠️ Risk Settings</b>\n"
            f"Stop Loss: <code>-20%</code>\n"
            f"Slippage: <code>{conf.jupiter.slippage_bps} BPS</code>\n"
            f"Priority Fee: <code>{conf.fee.base_fee_lamports / 1e9} SOL</code>\n"
            f"Liquidity Exit: <code>30% Drop</code>"
        )
        await self.send_message(msg)

    async def _cmd_exitall(self, bot: Any):
        if not bot._positions:
            await self.send_message("No positions to exit.")
            return
        await self.send_message(f"⚠️ <b>Liquidating {len(bot._positions)} positions...</b>")
        for mint in list(bot._positions.keys()):
            asyncio.create_task(bot._exit_position(bot._positions[mint], "Manual Exit", 1.0))
