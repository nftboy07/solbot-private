"""Comprehensive Telegram control interface for Solbot."""

import asyncio
import aiohttp
import logging
import os
import sys
import traceback
from typing import Optional, Any, List, Dict
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
            if not args: return
            cmd = args[0].lower()
            
            # Protected Command Execution
            if cmd in ["/list", "/help"]: await self._cmd_list()
            elif cmd == "/status": await self._cmd_status(bot)
            elif cmd in ["/balance", "/wallet"]: await self._cmd_balance(bot)
            elif cmd in ["/portfolio", "/positions"]: await self._cmd_portfolio(bot)
            elif cmd == "/history": await self._cmd_history(bot)
            elif cmd in ["/whales", "/smart"]: await self._cmd_whales(bot)
            elif cmd == "/kols": await self._cmd_kols(bot)
            elif cmd == "/profit": await self._cmd_profit(bot)
            elif cmd == "/follow": await self._cmd_follow(args, bot)
            elif cmd == "/unfollow": await self._cmd_unfollow(args, bot)
            elif cmd == "/blacklist": await self._cmd_blacklist(args, bot)
            elif cmd == "/devs": await self._cmd_devs(bot)
            elif cmd == "/followtwitter": await self._cmd_follow_twitter(args, bot)
            elif cmd == "/unfollowtwitter": await self._cmd_unfollow_twitter(args, bot)
            elif cmd == "/mode": await self._cmd_mode(args, bot)
            elif cmd == "/autobuy": await self._cmd_autobuy(bot)
            elif cmd == "/proxy": await self._cmd_proxy(bot)
            elif cmd == "/pause":
                bot._paused = True
                await self.send_message("⏸ <b>Bot Paused</b>")
            elif cmd == "/resume":
                bot._paused = False
                await self.send_message("▶️ <b>Bot Resumed</b>")
            elif cmd in ["/reload", "/restart"]:
                await self.send_message("🔄 <b>Restarting...</b>")
                os.execv(sys.executable, [sys.executable] + sys.argv)
            elif cmd == "/exitall": await self._cmd_exitall(bot)
            elif cmd == "/aitoggle": await self._cmd_aitoggle(bot)
            elif cmd == "/aiscore": await self._cmd_aiscore(args, bot)
        except Exception as e:
            logger.error(f"Error executing command '{text}': {e}")
            logger.error(traceback.format_exc())

    async def _cmd_list(self):
        msg = (
            "<b>📜 Command Registry</b>\n"
            "/list - Show this list\n"
            "/status - Current bot state\n"
            "/balance - SOL balance\n"
            "/portfolio - Active holdings\n"
            "/mode <degen/normal> - Switch mode\n"
            "/autobuy - Toggle auto-buy\n"
            "/proxy - Network health telemetry\n"
            "/profit - Daily PnL report\n"
            "/follow <addr> <alias> - Follow wallet\n"
            "/unfollow <addr> - Unfollow wallet\n"
            "/blacklist <add/remove/list> <addr> - Manage blacklist\n"
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
        auto_state = "ON" if getattr(bot, "_autobuy_enabled", False) else "OFF"
        tracked_wallets = len(bot._filter._copy_targets) if bot._filter else 0
        msg = (
            f"<b>📊 Solbot Status</b>\n"
            f"State: {state}\n"
            f"Auto-buy: {auto_state}\n"
            f"AI Filter: {ai_state} (Min: {bot._ai_min_score})\n"
            f"Positions: {len(bot._positions)}\n"
            f"Tracked KOLs: {len(bot._kol_tracker.wallets)}\n"
            f"Tracked Whales: {tracked_wallets}\n"
            f"Blacklisted: {len(bot._blacklisted_wallets)}"
        )
        await self.send_message(msg)

    async def _cmd_mode(self, args: list, bot: Any):
        if len(args) < 2:
            current = getattr(bot._config.strategy, "mode", "unknown")
            await self.send_message(f"<b>Current Mode:</b> {current}")
            return
        
        mode = args[1].lower()
        if mode in ["degen", "normal"]:
            # Note: This requires BotMode enum from config
            from solbot.config import BotMode
            bot._config.strategy.mode = BotMode.DEGEN if mode == "degen" else BotMode.NORMAL
            await self.send_message(f"✅ <b>Mode switched to:</b> {mode.upper()}")
        else:
            await self.send_message("❌ Invalid mode. Use /mode degen or /mode normal")

    async def _cmd_autobuy(self, bot: Any):
        current = getattr(bot, "_autobuy_enabled", False)
        new_state = not current
        setattr(bot, "_autobuy_enabled", new_state)
        
        if hasattr(bot, "_save_state"):
            try:
                bot._save_state()
            except Exception as e:
                logger.error(f"Failed to save state on autobuy toggle: {e}")
                
        state_text = "ON" if new_state else "OFF"
        await self.send_message(f"🤖 <b>Auto-buy:</b> {state_text}")

    async def _cmd_proxy(self, bot: Any):
        """Display network health telemetry from NetworkManager."""
        nm = getattr(bot, "_network_manager", None)
        if not nm:
            await self.send_message("❌ <b>NetworkManager not initialized.</b>")
            return
        
        stats = await nm.get_stats()
        err = stats["errors"]
        
        msg = (
            f"<b>🌐 Proxy Health Report</b>\n"
            f"Total Proxies: {stats['total_proxies']}\n"
            f"Total Requests: {stats['total_requests']}\n"
            f"Success Rate: {stats['success_rate']:.1f}%\n"
            f"Avg Latency: {stats['avg_latency']:.2f}ms\n"
            f"Health Score: {stats['health_score']:.1f}/100\n\n"
            f"<b>🚫 Error Breakdown:</b>\n"
            f"403 (Forbidden): {err[403]}\n"
            f"407 (Auth Req): {err[407]}\n"
            f"429 (Rate Limit): {err[429]}\n"
            f"530 (Cloudflare): {err[530]}"
        )
        await self.send_message(msg)

    async def _cmd_kols(self, bot: Any):
        if not bot._kol_tracker or not bot._kol_tracker.wallets:
            await self.send_message("No KOLs currently tracked. Tip: Add 'KOL' to an alias when using /follow.")
            return
            
        lines = ["<b>🔥 Active KOL Tracklist:</b>"]
        for addr, alias in bot._kol_tracker.wallets.items():
            lines.append(f"- {alias} (<code>{addr[:6]}...{addr[-4:]}</code>)")
            
        await self.send_message("\n".join(lines))

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

    async def _cmd_balance(self, bot: Any):
        balance = await bot._pump_client.get_sol_balance()
        await self.send_message(f"<b>🔍 Balance</b>\n<code>{balance:.4f} SOL</code>")

    async def _cmd_portfolio(self, bot: Any):
        if not bot._positions:
            await self.send_message("No active positions.")
            return
        lines = ["<b>📍 Current Portfolio:</b>"]
        for mint, pos in bot._positions.items():
            lines.append(f"- {pos.symbol}: ${pos.current_price:,.0f} MC")
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
            if any(term in alias for term in ["KOL", "VineWallet", "SmartWallet"]):
                bot._kol_tracker.add_wallet(addr, alias)
        bot._save_state()
        await self.send_message(f"✅ Following whale: {alias or addr}")

    async def _cmd_unfollow(self, args: list, bot: Any):
        if len(args) < 2: return
        addr = args[1]
        if addr in bot._filter._copy_targets:
            bot._filter._copy_targets.remove(addr)
            if addr in bot._kol_tracker.wallets:
                del bot._kol_tracker.wallets[addr]
            bot._save_state()
            await self.send_message(f"🗑 Unfollowed: {addr}")

    async def _cmd_blacklist(self, args: List[str], bot: Any):
        if len(args) < 2:
            await self.send_message("Usage: /blacklist <add/remove/list> [address]")
            return
        
        action = args[1].lower()
        if action == "list":
            if not bot._blacklisted_wallets:
                await self.send_message("Blacklist is empty.")
                return
            msg = "🚫 Blacklisted Wallets:\n" + "\n".join([f"<code>{a}</code>" for a in bot._blacklisted_wallets])
            await self.send_message(msg)
        elif action == "add":
            if len(args) < 3: return
            addr = args[2]
            bot._blacklisted_wallets.add(addr)
            bot._save_state()
            await self.send_message(f"✅ Blacklisted: {addr}")
        elif action == "remove":
            if len(args) < 3: return
            addr = args[2]
            if addr in bot._blacklisted_wallets:
                bot._blacklisted_wallets.remove(addr)
                bot._save_state()
                await self.send_message(f"🗑 Removed: {addr}")

    async def _cmd_devs(self, bot: Any):
        lines = ["<b>👨‍💻 Active Position Devs:</b>"]
        for mint, pos in bot._positions.items():
            lines.append(f"- {pos.symbol}: <code>{pos.creator}</code>")
        await self.send_message("\n".join(lines))

    async def _cmd_history(self, bot: Any):
        if not bot._trades:
            await self.send_message("No trades.")
            return
        lines = ["<b>🕒 Recent History:</b>"]
        for t in bot._trades[-10:]:
            lines.append(f"{'✅' if t.success else '❌'} {t.token_mint[:8]}")
        await self.send_message("\n".join(lines))

    async def _cmd_exitall(self, bot: Any):
        await self.send_message("🚨 Liquidating all positions...")
        for mint in list(bot._positions.keys()):
            asyncio.create_task(bot._exit_position(bot._positions[mint], "Manual Exit", 1.0))

    async def _cmd_aitoggle(self, bot: Any):
        bot._ai_enabled = not bot._ai_enabled
        bot._save_state()
        state = "ENABLED" if bot._ai_enabled else "DISABLED"
        await self.send_message(f"🤖 AI Filter: {state}")

    async def _cmd_aiscore(self, args: list, bot: Any):
        if len(args) < 2: return
        try:
            score = int(args[1])
            bot._ai_min_score = max(0, min(100, score))
            bot._save_state()
            await self.send_message(f"🎯 Min AI Score: {bot._ai_min_score}")
        except:
            pass

# V3 Compatibility Alias
TelegramController = TelegramManager
