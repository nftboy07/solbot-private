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
from solbot.core.metrics import RuntimeMetrics

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
        self._metrics = RuntimeMetrics()

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
                    if results: self._offset = results[0]["update_id"] + 1
        except Exception as e: logger.error(f"Failed to flush Telegram updates: {e}")
        self._running = True
        logger.info("Performing initial SOL price fetch...")
        await self._update_sol_price()
        asyncio.create_task(self._poll_loop(bot_instance))
        asyncio.create_task(self._price_update_loop())
        logger.info("Telegram command listener started.")

    async def stop(self):
        self._running = False
        if self._session: await self._session.close(); self._session = None

    async def _update_sol_price(self):
        sol_mint = "So11111111111111111111111111111111111111112"
        sources = [
            {"name": "Jupiter", "url": f"https://api.jup.ag/price/v2?ids={sol_mint}", "path": ["data", sol_mint, "price"]},
            {"name": "DexScreener", "url": f"https://api.dexscreener.com/latest/dex/tokens/{sol_mint}", "path": ["pairs", 0, "priceUsd"]},
            {"name": "Birdeye", "url": f"https://public-api.birdeye.so/public/price?address={sol_mint}", "path": ["data", "value"]}
        ]
        if not self._session: return
        for source in sources:
            try:
                async with self._session.get(source["url"], timeout=5) as resp:
                    if resp.status == 200:
                        data = await resp.json(); price = data
                        for key in source["path"]:
                            if isinstance(price, list) and isinstance(key, int): price = price[key] if len(price) > key else None
                            elif isinstance(price, dict): price = price.get(key)
                            else: price = None
                            if price is None: break
                        if price:
                            new_price = float(price)
                            if new_price > 0:
                                self._sol_price = new_price
                                logger.info(f"SOL Price updated from {source['name']}: ${self._sol_price:.2f}")
                                return
            except Exception as e: logger.debug(f"Failed to fetch price from {source['name']}: {e}")
        
        if self._sol_price == 150.0:
            logger.error("Critical: All SOL price sources failed. Using fallback.")

    async def _price_update_loop(self):
        while self._running:
            try:
                await self._update_sol_price()
            except Exception as e:
                logger.error(f"Error in price update loop: {e}")
            await asyncio.sleep(60)

    async def send_message(self, text: str):
        if not self._session: return
        url = f"{self._base_url}/sendMessage"
        payload = {"chat_id": self._config.chat_id, "text": text, "parse_mode": "HTML"}
        try:
            async with self._session.post(url, json=payload) as resp:
                if resp.status != 200: logger.error(f"Telegram send error: {await resp.text()}")
        except Exception as e: logger.error(f"Telegram exception: {e}")

    async def _poll_loop(self, bot_instance: Any):
        while self._running:
            try:
                params = {"offset": self._offset, "timeout": 20}
                async with self._session.get(f"{self._base_url}/getUpdates", params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json(); updates = data.get("result", [])
                        if updates: await self._handle_updates(updates, bot_instance)
            except Exception as e: logger.error(f"Telegram error: {e}"); await asyncio.sleep(5)

    async def _handle_updates(self, updates: list, bot: Any):
        for update in updates:
            self._offset = update["update_id"] + 1
            msg = update.get("message")
            if not msg or str(msg.get("chat", {}).get("id", "")) != str(self._config.chat_id): continue
            text = msg.get("text", "")
            if text: asyncio.create_task(self._execute_command(text, bot))

    async def _execute_command(self, text: str, bot: Any):
        try:
            args = text.split(); 
            if not args: return
            cmd = args[0].lower()
            
            # Map /selftest (the bug was likely that it wasn't being picked up or executed)
            if cmd == "/selftest":
                await self._cmd_selftest(bot)
                return

            if cmd in ["/list", "/help"]: await self._cmd_list()
            elif cmd == "/status": await self._cmd_status(bot)
            elif cmd == "/dashboard": await self._cmd_status(bot)
            elif cmd in ["/balance", "/wallet"]: await self._cmd_balance(bot)
            elif cmd in ["/portfolio", "/positions"]: await self._cmd_portfolio(bot)
            elif cmd == "/metrics": await self._cmd_metrics(bot)
            elif cmd == "/pipeline": await self._cmd_pipeline(bot)
            elif cmd == "/brain": await self._cmd_brain(bot)
            elif cmd == "/version": await self.send_message("<b>Solbot v3.1.2-Hotfix</b>\nBuild: 20260611-PROD")
            elif cmd == "/history": await self._cmd_history(bot)
            elif cmd in ["/whales", "/smart"]: await self._cmd_whales(bot)
            elif cmd == "/kols": await self._cmd_kols(bot)
            elif cmd == "/profit": await self._cmd_profit(bot)
            elif cmd == "/follow": await self._cmd_follow(args, bot)
            elif cmd == "/unfollow": await self._cmd_unfollow(args, bot)
            elif cmd == "/blacklist": await self._cmd_blacklist(args, bot)
            elif cmd == "/devs": await self._cmd_devs(bot)
            elif cmd == "/mode": await self._cmd_mode(args, bot)
            elif cmd == "/autobuy": await self._cmd_autobuy(args, bot)
            elif cmd == "/proxy": await self._cmd_proxy(bot)
            elif cmd in ["/risk", "/kill", "/buy", "/max_position", "/drawdown"]: await self._cmd_risk(args, bot)
            elif cmd == "/pause": bot._paused = True; await self.send_message("⏸ <b>Bot Paused</b>")
            elif cmd == "/resume": bot._paused = False; await self.send_message("▶️ <b>Bot Resumed</b>")
            elif cmd in ["/reload", "/restart"]: await self.send_message("🔄 <b>Restarting...</b>"); os.execv(sys.executable, [sys.executable] + sys.argv)
            elif cmd == "/exitall": await self._cmd_exitall(bot)
            elif cmd == "/aitoggle": await self._cmd_aitoggle(bot)
            elif cmd == "/aiscore": await self._cmd_aiscore(args, bot)
        except Exception as e: logger.error(f"Error executing command '{text}': {e}"); logger.error(traceback.format_exc())

    async def _cmd_list(self):
        msg = ("<b>📜 Command Registry</b>\n/list - Commands\n/status - Bot state\n/dashboard - Status\n/metrics - Telemetry\n/pipeline - Filter/Audit\n/brain - AI Config\n/balance - SOL\n/portfolio - Active\n/risk - Profile\n/kill - Halt\n/version - Build\n/selftest - Diagnostic")
        await self.send_message(msg)

    async def _cmd_status(self, bot: Any):
        state = "PAUSED" if bot._paused else "ACTIVE"
        ai_state = "ENABLED" if bot._ai_enabled else "DISABLED"
        auto_state = "ON" if getattr(bot, "_autobuy_enabled", False) else "OFF"
        m = self._metrics.get_report()
        msg = (f"<b>📊 Solbot Status</b>\nState: {state}\nAuto-buy: {auto_state}\nAI Filter: {ai_state} (Min: {bot._ai_min_score})\nPositions: {len(bot._positions)}\nSOL Price: ${self._sol_price:.2f}\nSignals: {m['total_signals']}")
        await self.send_message(msg)

    async def _cmd_brain(self, bot: Any):
        await self.send_message(f"🧠 <b>AI Configuration</b>\nThreshold: <code>{bot._ai_min_score}</code>\nEnabled: <code>{bot._ai_enabled}</code>")

    async def _cmd_pipeline(self, bot: Any):
        msg = (f"<b>🛠 Pipeline Telemetry</b>\nEvents: {bot._events_count}\nSignals: {bot._signals_count}\nAI Rejects: {bot._ai_rejects_count}\nFilter Rejects: {bot._filter_rejects_count}")
        await self.send_message(msg)

    async def _cmd_metrics(self, bot: Any):
        m = self._metrics.get_report(); rm = getattr(bot, '_reject_metrics', {})
        msg = (f"<b>📈 Live Metrics</b>\nSOL Price: ${self._sol_price:.2f}\nTotal Signals: {m['total_signals']}\nBuy Rate: {m['buy_rate']:.1f}%\n\n<b>🚫 Rejects:</b>\nBlacklist: {rm.get('blacklist', 0)}\nFilter: {rm.get('filter', 0)}\nAI: {rm.get('ai', 0)}\nPos: {rm.get('positions', 0)}")
        await self.send_message(msg)

    async def _cmd_selftest(self, bot: Any):
        await self.send_message("🧪 <b>Diagnostic Self-Test...</b>")
        results = []
        try:
            score = await bot._ai_filter.score_token({"mint": "So11111111111111111111111111111111111111112", "symbol": "SOL"})
            results.append(f"AI Filter: ✅ (Test Score: {score})")
            
            bal = await bot._pump_client.get_sol_balance()
            results.append(f"RPC Connection: ✅ ({bal:.4f} SOL)")
            
            bot._save_state()
            results.append("State Persistence: ✅")
            
            results.append(f"Price Engine: {'✅' if self._sol_price != 150.0 else '⚠️'} (${self._sol_price:.2f})")
            
        except Exception as e:
            results.append(f"❌ Fault: {str(e)}")
            logger.error(f"Self-test failed: {e}")
            
        await self.send_message("\n".join(results))

    async def _cmd_balance(self, bot: Any):
        balance = await bot._pump_client.get_sol_balance()
        await self.send_message(f"<b>🔍 Balance</b>\n<code>{balance:.4f} SOL</code> (${balance * self._sol_price:,.2f})")

    async def _cmd_profit(self, bot: Any):
        now = datetime.now()
        today_trades = [t for t in bot._trades if hasattr(t, 'timestamp') and datetime.fromtimestamp(t.timestamp).date() == now.date()]
        total_pnl = sum([getattr(t, 'pnl_sol', 0) for t in today_trades])
        await self.send_message(f"<b>💰 Daily PnL</b>\nRealized: <code>{total_pnl:.4f} SOL</code> (${total_pnl * self._sol_price:,.2f})")

    async def _cmd_portfolio(self, bot: Any):
        if not bot._positions: await self.send_message("No positions."); return
        lines = ["<b>📍 Portfolio:</b>"]
        for mint, pos in bot._positions.items(): lines.append(f"- {pos.symbol}: ${pos.current_price:,.0f} MC")
        await self.send_message("\n".join(lines))

    async def _cmd_mode(self, args: list, bot: Any):
        if len(args) < 2: return
        mode = args[1].lower()
        if mode in ["degen", "normal"]:
            from solbot.config import BotMode
            object.__setattr__(bot._config.strategy, "mode", BotMode.DEGEN if mode == "degen" else BotMode.NORMAL)
            await self.send_message(f"✅ Mode: {mode.upper()}")

    async def _cmd_autobuy(self, args: list, bot: Any):
        if len(args) > 1:
            val = args[1].lower()
            if val == "on": bot._autobuy_enabled = True
            elif val == "off": bot._autobuy_enabled = False
        else: bot._autobuy_enabled = not bot._autobuy_enabled
        bot._save_state(); await self.send_message(f"🤖 Auto-buy: {'ON' if bot._autobuy_enabled else 'OFF'}")

    async def _cmd_risk(self, args: list, bot: Any):
        cmd = args[0].lower()
        if cmd == "/kill": bot._paused = True; bot._save_state(); await self.send_message("🚨 <b>KILL SWITCH ON</b>"); return
        if cmd == "/buy" and len(args) > 1:
            try: val = float(args[1]); object.__setattr__(bot._config.jupiter, "buy_amount_sol", val); bot._save_state(); await self.send_message(f"💰 Buy: {val} SOL")
            except: pass
        msg = (f"<b>🛡 RISK</b>\nMax Pos: {bot._config.jupiter.buy_amount_sol} SOL\nDrawdown: {bot._config.strategy.trailing_stop_pct*100:.1f}%")
        await self.send_message(msg)

    async def _cmd_proxy(self, bot: Any):
        if not bot._network_manager: return
        stats = await bot._network_manager.get_stats()
        await self.send_message(f"<b>🌐 Proxy Health</b>\nScore: {stats['health_score']:.1f}/100\nSuccess: {stats['success_rate']:.1f}%")

    async def _cmd_aiscore(self, args: list, bot: Any):
        if len(args) < 2: return
        try: score = int(args[1]); bot._ai_min_score = max(0, min(100, score)); bot._save_state(); await self.send_message(f"🎯 Min AI Score: {bot._ai_min_score}")
        except: pass

    async def _cmd_history(self, bot: Any):
        if not bot._trades: await self.send_message("No trades."); return
        lines = ["<b>🕒 Recent:</b>"]
        for t in bot._trades[-5:]: lines.append(f"{'✅' if t.success else '❌'} {t.token_mint[:6]}")
        await self.send_message("\n".join(lines))

    async def _cmd_exitall(self, bot: Any):
        await self.send_message("🚨 Liquidating..."); [asyncio.create_task(bot._exit_position(bot._positions[m], "Manual", 1.0)) for m in list(bot._positions.keys())]

    async def _cmd_aitoggle(self, bot: Any):
        bot._ai_enabled = not bot._ai_enabled; bot._save_state(); await self.send_message(f"🤖 AI: {'ENABLED' if bot._ai_enabled else 'DISABLED'}")

    async def _cmd_whales(self, bot: Any):
        if not bot._filter._copy_targets: await self.send_message("No whales."); return
        lines = ["<b>🐋 Whales:</b>"]
        for addr in list(bot._filter._copy_targets)[:10]: lines.append(f"- <code>{addr[:8]}...</code>")
        await self.send_message("\n".join(lines))

    async def _cmd_kols(self, bot: Any):
        if not bot._kol_tracker.wallets: await self.send_message("No KOLs."); return
        lines = ["<b>🔥 KOLs:</b>"]
        for a, l in list(bot._kol_tracker.wallets.items())[:10]: lines.append(f"- {l} (<code>{a[:6]}</code>)")
        await self.send_message("\n".join(lines))

    async def _cmd_follow(self, args: list, bot: Any):
        if len(args) < 2: return
        addr = args[1]; bot._filter.add_copy_target(addr); bot._save_state(); await self.send_message(f"✅ Following: {addr[:8]}")

    async def _cmd_unfollow(self, args: list, bot: Any):
        if len(args) < 2: return
        addr = args[1]; [bot._filter._copy_targets.remove(addr) if addr in bot._filter._copy_targets else None]; bot._save_state(); await self.send_message(f"🗑 Unfollowed")

    async def _cmd_blacklist(self, args: list, bot: Any):
        if len(args) < 3: return
        action, addr = args[1].lower(), args[2]
        if action == "add": bot._blacklisted_wallets.add(addr); bot._save_state(); await self.send_message(f"🚫 Blacklisted")

    async def _cmd_devs(self, bot: Any):
        lines = ["<b>👨‍💻 Devs:</b>"]
        for m, p in bot._positions.items(): lines.append(f"- {p.symbol}: <code>{p.creator[:8]}</code>")
        await self.send_message("\n".join(lines))

TelegramController = TelegramManager
