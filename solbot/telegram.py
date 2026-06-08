"""Comprehensive Telegram control interface for Solbot."""

import asyncio
import aiohttp
import logging
import os
import sys
from typing import Optional, Any
from datetime import datetime
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
        sol_mint = "So11111111111111111111111111111111111111112"
        url = f"https://api.jup.ag/price/v2?ids={sol_mint}"
        while self._running:
            try:
                if self._session:
                    async with self._session.get(url) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            price = data.get("data", {}).get(sol_mint, {}).get("price")
                            if price: self._sol_price = float(price)
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
                        if updates: await self._handle_updates(updates, bot_instance)
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
            if text: asyncio.create_task(self._execute_command(text, bot))

    async def _execute_command(self, text: str, bot: Any):
        try:
            args = text.split()
            cmd = args[0].lower()
            if cmd == "/list" or cmd == "/help": await self._cmd_list()
            elif cmd == "/status": await self._cmd_status(bot)
            elif cmd == "/balance": await self._cmd_balance(bot)
            elif cmd == "/portfolio" or cmd == "/positions": await self._cmd_portfolio(bot)
            elif cmd == "/history": await self._cmd_history(bot)
            elif cmd == "/whales" or cmd == "/smart": await self._cmd_whales(bot)
            elif cmd == "/kols": await self._cmd_kols(bot)
            elif cmd == "/profit": await self._cmd_profit(bot)
            elif cmd == "/follow": await self._cmd_follow(args, bot)
            elif cmd == "/unfollow": await self._cmd_unfollow(args, bot)
            elif cmd == "/followtwitter": await self._cmd_follow_twitter(args, bot)
            elif cmd == "/unfollowtwitter": await self._cmd_unfollow_twitter(args, bot)
            elif cmd == "/pause":
                bot._paused = True
                await self.send_message("⏸ <b>Bot Paused</b>")
            elif cmd == "/resume":
                bot._paused = False
                await self.send_message("▶️ <b>Bot Resumed</b>")
            elif cmd == "/reload" or cmd == "/restart":
                await self.send_message("🔄 <b>Restarting...</b>")
                try:
                    await asyncio.wait_for(bot.stop(), timeout=5.0)
                except asyncio.TimeoutError:
                    logger.warning("Bot stop timed out, forcing restart")
                except Exception as e:
                    logger.error(f"Error during bot stop: {e}")
                os.execv(sys.executable, [sys.executable] + sys.argv)
            elif cmd == "/exitall": await self._cmd_exitall(bot)
            elif cmd == "/aitoggle": await self._cmd_aitoggle(bot)
            elif cmd == "/aiscore": await self._cmd_aiscore(args, bot)
        except Exception as e:
            logger.error(f"Error executing command '{text}': {e}")

    async def _cmd_list(self):
        msg = (
            "<b>📜 Command Registry</b>\n"
            "/list - Show this list\n"
            "/status - Current bot state\n"
            "/balance - SOL balance\n"
            "/portfolio - Active holdings\n"
            "/whales - List tracked wallets\n"
            "/kols - List tracked KOLs\n"
            "/profit - Daily PnL report\n"
            "/follow <addr> <alias> - Follow wallet\n"
            "/unfollow <addr> - Unfollow wallet\n"
            "/followtwitter <handle> - Track Twitter\n"
            "/unfollowtwitter <handle> - Untrack Twitter\n"
            "/pause - Pause sniper\n"
            "/resume - Resume sniper\n"
            "/reload - Restart process\n"
            "/exitall - Liquidate everything\n"
            "/aitoggle - Toggle AI filter\n"
            "/aiscore <value> - Set min AI score"
        )
        await self.send_message(msg)

    async def _cmd_status(self, bot: Any):
        state = "PAUSED" if bot._paused else "ACTIVE"
        ai_state = "ENABLED" if bot._ai_enabled else "DISABLED"
        tracked_wallets = len(bot._filter._copy_targets) if bot._filter else 0
        msg = (
            f"<b>📊 Solbot Status</b>\n"
            f"State: {state}\n"
            f"AI Filter: {ai_state} (Min: {bot._ai_min_score})\n"
            f"Positions: {len(bot._positions)}\n"
            f"Twitter: {len(bot._twitter._handles) if bot._twitter else 0}\n"
            f"Tracked Wallets: {tracked_wallets}"
        )
        await self.send_message(msg)

    async def _cmd_profit(self, bot: Any):
        now = datetime.now()
        today_trades = [t for t in bot._trades if hasattr(t, 'timestamp') and datetime.fromtimestamp(t.timestamp).date() == now.date()]
        
        total_trades = len(today_trades)
        wins = len([t for t in today_trades if t.success and getattr(t, 'pnl_sol', 0) > 0])
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        total_pnl = sum([getattr(t, 'pnl_sol', 0) for t in today_trades])
        
        lines = [
            "<b>💰 Daily Profit Report</b>",
            f"Date: {now.strftime('%A, %b %d, %Y')}",
            f"Total Trades: {total_trades}",
            f"Win Rate: {win_rate:.1f}%",
            f"Realized PnL: <code>{total_pnl:.4f} SOL</code>",
            "",
            f"<b>📍 Active Positions ({len(bot._positions)}):</b>"
        ]
        
        for mint, pos in bot._positions.items():
            gain = (pos.current_price / pos.entry_price - 1) * 100 if pos.entry_price > 0 else 0
            lines.append(f"- {pos.symbol}: {gain:+.2f}% (${pos.current_price:,.0f} MC)")
            
        await self.send_message("\n".join(lines))

    async def _cmd_kols(self, bot: Any):
        if not bot._filter or not bot._filter._wallet_scores:
            await self.send_message("No KOL data available.")
            return
            
        lines = ["<b>🔥 Tracked KOLs & Smart Wallets:</b>"]
        count = 0
        for addr, score in bot._filter._wallet_scores.items():
            alias = score.alias if hasattr(score, 'alias') and score.alias else "Unknown"
            if any(term in alias for term in ["VineWallet", "SmartWallet", "KOL"]):
                lines.append(f"- {alias} (<code>{addr[:6]}...{addr[-4:]}</code>)")
                count += 1
        
        if count == 0:
            await self.send_message("No addresses labeled as KOL, SmartWallet, or VineWallet found.")
        else:
            await self.send_message("\n".join(lines))

    async def _cmd_balance(self, bot: Any):
        balance = await bot._pump_client.get_sol_balance()
        await self.send_message(f"<b>🔍 Balance</b>\n<code>{balance:.4f} SOL</code>")

    async def _cmd_portfolio(self, bot: Any):
        if not bot._positions:
            await self.send_message("No active positions.")
            return
        mints = sorted(bot._positions.keys())
        tasks = [bot._pump_client.get_token_metadata(mint) for mint in mints]
        metadata = await asyncio.gather(*tasks, return_exceptions=True)
        lines = ["<b>📍 Current Portfolio:</b>"]
        for mint, meta in zip(mints, metadata):
            if isinstance(meta, Exception): continue
            pos = bot._positions[mint]
            mc_usd = float(meta.get("market_cap_sol", 0)) * self._sol_price
            lines.append(f"- {pos.symbol}: ${mc_usd:,.0f} MC")
        await self.send_message("\n".join(lines))

    async def _cmd_whales(self, bot: Any):
        targets = bot._filter._copy_targets
        if not targets:
            await self.send_message("No whales tracked.")
            return
        lines = ["<b>🐋 Tracked Whales:</b>"]
        for addr in targets:
            score = bot._filter._wallet_scores.get(addr)
            alias = score.alias if score and hasattr(score, 'alias') else "No Alias"
            lines.append(f"- {alias} (<code>{addr[:6]}...</code>)")
        await self.send_message("\n".join(lines))

    async def _cmd_follow(self, args: list, bot: Any):
        if len(args) < 2:
            await self.send_message("Usage: /follow <address> [alias]")
            return
        addr, alias = args[1], args[2] if len(args) > 2 else None
        if len(addr) < 32 or len(addr) > 44:
            await self.send_message("❌ Invalid Solana address.")
            return
        bot._filter.add_copy_target(addr)
        if alias:
            from solbot.filters import WalletScore
            score = bot._filter._wallet_scores.get(addr, WalletScore(addr))
            score.alias = alias
            bot._filter._wallet_scores[addr] = score
        bot._save_state()
        await self.send_message(f"✅ Following whale: {alias or addr}")

    async def _cmd_unfollow(self, args: list, bot: Any):
        if len(args) < 2: return
        addr = args[1]
        if addr in bot._filter._copy_targets:
            bot._filter._copy_targets.remove(addr)
            bot._save_state()
            await self.send_message(f"🗑 Unfollowed: {addr}")

    async def _cmd_follow_twitter(self, args: list, bot: Any):
        if len(args) < 2: return
        handle = args[1].lstrip("@")
        bot._twitter.add_handle(handle)
        bot._save_state()
        await self.send_message(f"🐦 Now tracking Twitter: @{handle}")

    async def _cmd_unfollow_twitter(self, args: list, bot: Any):
        if len(args) < 2: return
        handle = args[1].lstrip("@")
        bot._twitter.remove_handle(handle)
        bot._save_state()
        await self.send_message(f"🗑 Stopped tracking Twitter: @{handle}")

    async def _cmd_history(self, bot: Any):
        if not bot._trades:
            await self.send_message("No trades.")
            return
        lines = ["<b>🕒 Recent History:</b>"]
        for t in bot._trades[-5:]:
            lines.append(f"{'✅' if t.success else '❌'} {t.token_mint[:8]}")
        await self.send_message("\n".join(lines))

    async def _cmd_exitall(self, bot: Any):
        await self.send_message("🚨 <b>Liquidating all positions...</b>")
        for mint in list(bot._positions.keys()):
            asyncio.create_task(bot._exit_position(bot._positions[mint], "Manual Exit", 1.0))

    async def _cmd_aitoggle(self, bot: Any):
        bot._ai_enabled = not bot._ai_enabled
        bot._save_state()
        state = "ENABLED" if bot._ai_enabled else "DISABLED"
        await self.send_message(f"🤖 <b>AI Filter: {state}</b>")

    async def _cmd_aiscore(self, args: list, bot: Any):
        if len(args) < 2:
            await self.send_message("Usage: /aiscore <value>")
            return
        try:
            score = int(args[1])
            bot._ai_min_score = max(0, min(100, score))
            bot._save_state()
            await self.send_message(f"🎯 <b>Min AI Score set to: {bot._ai_min_score}</b>")
        except ValueError:
            await self.send_message("❌ Invalid score value.")
