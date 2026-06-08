"""Comprehensive Telegram control interface for Solbot."""

import asyncio
import aiohttp
import logging
import os
import sys
import base58
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
        self._sol_price = 150.0

    async def start(self, bot_instance: Any):
        if not self._config.token or not self._config.chat_id:
            logger.warning("Telegram configuration missing.")
            return
        
        if not self._session:
            timeout = aiohttp.ClientTimeout(total=10)
            self._session = aiohttp.ClientSession(timeout=timeout)
        
        try:
            async with self._session.get(f"{self._base_url}/getUpdates", params={"offset": -1, "limit": 1}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results = data.get("result", [])
                    if results:
                        self._offset = results[0]["update_id"] + 1
                        logger.info(f"Skipped old Telegram updates. New offset: {self._offset}")
        except Exception as e:
            logger.error(f"Failed to flush Telegram updates: {e}")

        self._running = True
        asyncio.create_task(self._poll_loop(bot_instance))
        asyncio.create_task(self._update_sol_price())
        logger.info("Telegram command listener started.")

    async def stop(self):
        self._running = False
        if self._session:
            await self._session.close()
            self._session = None

    async def _update_sol_price(self):
        """Periodically update the SOL price from Jupiter Price API v2."""
        sol_mint = "So11111111111111111111111111111111111111112"
        url = f"https://api.jup.ag/price/v2?ids={sol_mint}"
        while self._running:
            try:
                if self._session:
                    async with self._session.get(url) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            price = data.get("data", {}).get(sol_mint, {}).get("price")
                            if price:
                                self._sol_price = float(price)
            except Exception as e:
                logger.error(f"Failed to fetch SOL price: {e}")
            await asyncio.sleep(60)

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
            asyncio.create_task(self._execute_command(text, bot))

    async def _execute_command(self, text: str, bot: Any):
        try:
            parts = text.split()
            if not parts: return
            cmd = parts[0].lower()
            
            if cmd == "/list" or cmd == "/help":
                await self._cmd_list()
            elif cmd == "/status":
                await self._cmd_status(bot)
            elif cmd == "/balance":
                await self._cmd_balance(bot)
            elif cmd == "/portfolio" or cmd == "/positions":
                await self._cmd_portfolio(bot)
            elif cmd == "/history":
                await self._cmd_history(bot)
            elif cmd == "/profit":
                await self._cmd_profit(bot)
            elif cmd in ["/smart", "/whales"]:
                await self._cmd_whales(bot)
            elif cmd == "/follow":
                await self._cmd_follow(bot, parts[1:])
            elif cmd == "/unfollow":
                await self._cmd_unfollow(bot, parts[1:])
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
        except Exception as e:
            logger.error(f"Error executing command '{text}': {e}")

    async def _cmd_list(self):
        msg = (
            "<b>📜 Command Registry</b>\n"
            "/list - Show this list\n"
            "/status - Current bot state\n"
            "/balance - SOL balance\n"
            "/portfolio - Active holdings\n"
            "/whales - Followed wallets\n"
            "/follow <addr> [alias] - Copytrade wallet\n"
            "/unfollow <addr> - Stop following\n"
            "/history - Last N trades\n"
            "/profit - Session P&L\n"
            "/risk - Active risk settings\n"
            "/pause - Pause sniper\n"
            "/resume - Resume sniper\n"
            "/exitall - Emergency liquidation"
        )
        await self.send_message(msg)

    async def _cmd_status(self, bot: Any):
        state = "PAUSED" if bot._paused else "ACTIVE"
        msg = (
            "<b>📊 Solbot Status</b>\n"
            f"State: <code>{state}</code>\n"
            f"Positions: <code>{len(bot._positions)}</code>\n"
            f"Followed: <code>{len(bot._filter._copy_targets)}</code>"
        )
        await self.send_message(msg)

    async def _cmd_balance(self, bot: Any):
        balance = await bot._pump_client.get_sol_balance()
        msg = (
            "<b>🔍 Wallet Balance</b>\n"
            f"Address: <code>{bot._wallet.pubkey_str}</code>\n"
            f"Balance: <code>{balance:.4f} SOL</code>"
        )
        await self.send_message(msg)

    async def _is_valid_solana_address(self, address: str) -> bool:
        try:
            if len(address) < 32 or len(address) > 44: return False
            base58.b58decode(address)
            return True
        except: return False

    async def _cmd_follow(self, bot: Any, args: list):
        if not args:
            await self.send_message("Usage: <code>/follow <address> [alias]</code>")
            return
        addr = args[0]
        if not await self._is_valid_solana_address(addr):
            await self.send_message("❌ Invalid Solana address.")
            return
        alias = args[1] if len(args) > 1 else None
        bot._filter.add_copy_target(addr, alias)
        bot._save_state()
        await self.send_message(f"✅ Following <code>{addr[:8]}...</code> as <b>{alias or 'Whale'}</b>")

    async def _cmd_unfollow(self, bot: Any, args: list):
        if not args:
            await self.send_message("Usage: <code>/unfollow <address></code>")
            return
        addr = args[0]
        bot._filter.remove_copy_target(addr)
        bot._save_state()
        await self.send_message(f"❌ Unfollowed <code>{addr[:8]}...</code>")

    async def _cmd_whales(self, bot: Any):
        targets = bot._filter._copy_targets
        if not targets:
            await self.send_message("No followed wallets.")
            return
        lines = ["<b>🐋 Followed Wallets:</b>"]
        for addr in targets:
            score_obj = bot._filter._wallet_scores.get(addr)
            alias = score_obj.alias if score_obj else "Unknown"
            score = score_obj.score if score_obj else 0
            lines.append(f"- <b>{alias}</b>: <code>{addr[:8]}...</code> (Score: {score})")
        await self.send_message("\n".join(lines))

    async def _cmd_portfolio(self, bot: Any):
        if not bot._positions:
            await self.send_message("No active positions.")
            return
        all_balances = await bot._pump_client.get_all_token_balances()
        mints = sorted(bot._positions.keys())
        tasks = [bot._pump_client.get_token_metadata(mint) for mint in mints]
        metadata_results = await asyncio.gather(*tasks, return_exceptions=True)
        lines = ["<b>📍 Current Portfolio:</b>"]
        for mint, meta in zip(mints, metadata_results):
            if isinstance(meta, Exception): continue
            pos = bot._positions[mint]
            balance = all_balances.get(mint, {}).get("balance", 0.0)
            current_mc_sol = float(meta.get("market_cap_sol", 0))
            current_mc_usd = current_mc_sol * self._sol_price
            if pos.symbol == "SYNCED" and meta.get("symbol"): pos.symbol = meta["symbol"]
            pos.current_price = current_mc_usd
            if current_mc_usd > pos.highest_price: pos.highest_price = current_mc_usd
            sol_v = balance * (current_mc_sol / 1e9)
            lines.append(f"- {pos.symbol} (<code>{mint[:4]}...</code>)\n  Val: <code>{sol_v:.4f} SOL</code> | MC: <code>${current_mc_usd:,.0f}</code>")
        await self.send_message("\n".join(lines))

    async def _cmd_history(self, bot: Any):
        if not bot._trades:
            await self.send_message("No trade history.")
            return
        lines = ["<b>🕒 Recent History:</b>"]
        for trade in bot._trades[-10:]:
            status = "✅" if trade.success else "❌"
            lines.append(f"{status} {trade.token_mint[:8]}... | {trade.latency_ms:.0f}ms")
        await self.send_message("\n".join(lines))

    async def _cmd_profit(self, bot: Any):
        await self.send_message("<b>📈 Session P&L</b>\nCalculating tracked exits...")

    async def _cmd_risk(self, bot: Any):
        conf = bot._config
        msg = (
            "<b>⚠️ Risk Settings</b>\n"
            f"Stop Loss: <code>-20%</code>\n"
            f"Slippage: <code>{conf.jupiter.slippage_bps} BPS</code>\n"
            f"Priority Fee: <code>{conf.fee.base_fee_lamports / 1e9} SOL</code>"
        )
        await self.send_message(msg)

    async def _cmd_exitall(self, bot: Any):
        if not bot._positions:
            await self.send_message("No positions to exit.")
            return
        await self.send_message(f"⚠️ <b>Liquidating {len(bot._positions)} positions...</b>")
        for mint in list(bot._positions.keys()):
            asyncio.create_task(bot._exit_position(bot._positions[mint], "Manual Exit", 1.0))
