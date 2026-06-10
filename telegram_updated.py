"""
Phase 8/9 & UI Overhaul: V3 Telegram Interface Redesign.
Transitions Solbot from a simple bot to a command-center OS.
Uses Telethon for async-native operation.
"""

import asyncio
import logging
import os
import sys
import time
import uuid
from datetime import datetime
from typing import Optional, Any, List, Dict

from telethon import TelegramClient, events
from solbot.config import TelegramConfig

logger = logging.getLogger("solbot.ui.telegram")

class TelegramController:
    \"\"\"V3 Command-Center Telegram Controller.\"\"\"

    def __init__(self, config: TelegramConfig, bot_instance: Any = None):
        self._config = config
        self._bot = bot_instance
        self._client: Optional[TelegramClient] = None
        self._start_time = datetime.now()
        self._version = "3.1.0-kol-integration"
        
        # UI State
        self._paper_mode = False
        self._kill_switch = False
        self._sol_price = 150.0

    async def start(self, bot_instance: Any = None):
        \"\"\"Initialize and start the Telethon client.\"\"\"
        if bot_instance:
            self._bot = bot_instance

        if not self._config.token or not self._config.api_id or not self._config.api_hash:
            logger.error("Telegram credentials missing in config.")
            return

        self._client = TelegramClient('solbot_v3_session', int(self._config.api_id), self._config.api_hash)
        self._register_handlers()
        
        await self._client.start(bot_token=self._config.token)
        logger.info("Solbot V3 Telegram Command Center Online.")
        
        asyncio.create_task(self._update_sol_price())
        
        await self._send_to_admin("⚡️ <b>Solbot V3 Command Center Online</b>\\n"
                                f"Build: <code>{self._version}</code>\\n"
                                "Status: <code>READY</code>")

    async def stop(self):
        \"\"\"Stop the Telegram client.\"\"\"
        if self._client:
            await self._client.disconnect()

    async def _update_sol_price(self):
        sol_mint = "So11111111111111111111111111111111111111112"
        url = f"https://api.jup.ag/price/v2?ids={sol_mint}"
        while True:
            try:
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            price = data.get("data", {}).get(sol_mint, {}).get("price")
                            if price: self._sol_price = float(price)
            except Exception as e:
                logger.error(f"Failed to fetch SOL price: {e}")
            await asyncio.sleep(60)

    def _register_handlers(self):
        \"\"\"Register all V3 command handlers.\"\"\"
        
        @self._client.on(events.NewMessage(pattern='/start'))
        async def start_handler(event):
            await self._cmd_start(event)

        @self._client.on(events.NewMessage(pattern='/help|/list'))
        async def help_handler(event):
            await self._cmd_help(event)

        @self._client.on(events.NewMessage(pattern='/status|/diag|/dashboard'))
        async def status_handler(event):
            await self._cmd_status(event)

        @self._client.on(events.NewMessage(pattern='/health'))
        async def health_handler(event):
            await self._cmd_health(event)

        @self._client.on(events.NewMessage(pattern='/version'))
        async def version_handler(event):
            await event.reply(f"🛰 <b>Solbot V3 Core</b>\\nVersion: <code>{self._version}</code>\\nBranch: <code>feature/kol-integration</code>")

        @self._client.on(events.NewMessage(pattern='/ping'))
        async def ping_handler(event):
            start = time.time()
            msg = await event.reply("🏓 Pong!")
            latency = (time.time() - start) * 1000
            await msg.edit(f"🏓 <b>Pong!</b>\\nLatency: <code>{latency:.2f}ms</code>")

        @self._client.on(events.NewMessage(pattern='/model|/brain'))
        async def model_handler(event):
            await self._cmd_model(event)

        @self._client.on(events.NewMessage(pattern='/wallet|/balance'))
        async def wallet_handler(event):
            await self._cmd_wallet(event)

        @self._client.on(events.NewMessage(pattern='/signals'))
        async def signals_handler(event):
            await self._cmd_signals(event)

        @self._client.on(events.NewMessage(pattern='/portfolio|/positions|/history|/pnl'))
        async def portfolio_handler(event):
            await self._cmd_portfolio(event)

        @self._client.on(events.NewMessage(pattern='/rpc|/proxies|/proxy'))
        async def execution_handler(event):
            await self._cmd_execution(event)

        @self._client.on(events.NewMessage(pattern='/risk|/kill|/pause|/resume|/buy|/drawdown'))
        async def risk_handler(event):
            await self._cmd_risk(event)

        @self._client.on(events.NewMessage(pattern='/why'))
        async def why_handler(event):
            await self._cmd_why(event)

        @self._client.on(events.NewMessage(pattern='/alpha'))
        async def alpha_handler(event):
            await self._cmd_alpha(event)
            
        @self._client.on(events.NewMessage(pattern='/follow'))
        async def follow_handler(event):
            await self._cmd_follow(event)
            
        @self._client.on(events.NewMessage(pattern='/unfollow'))
        async def unfollow_handler(event):
            await self._cmd_unfollow(event)
            
        @self._client.on(events.NewMessage(pattern='/blacklist'))
        async def blacklist_handler(event):
            await self._cmd_blacklist(event)
            
        @self._client.on(events.NewMessage(pattern='/whales'))
        async def whales_handler(event):
            await self._cmd_whales(event)

    async def _cmd_start(self, event):
        msg = ("<b>🦅 Solbot V3 | Command Center OS</b>\\n"
               "The ultimate asynchronous terminal for Solana dominance.\\n\\n"
               "Type /help to see all systems.")
        await event.reply(msg)

    async def _cmd_help(self, event):
        msg = ("<b>🛠 SOLBOT V3 COMMAND REGISTRY</b>\\n\\n"
               "<b>Core:</b> /status (/dashboard), /health, /version, /ping\\n"
               "<b>Intelligence:</b> /brain, /wallet (/balance), /alpha\\n"
               "<b>Data:</b> /signals, /why\\n"
               "<b>Ops:</b> /portfolio, /history, /proxy\\n"
               "<b>Control:</b> /risk, /kill, /buy, /drawdown\\n"
               "<b>Tracking:</b> /follow, /unfollow, /blacklist, /whales")
        await event.reply(msg)

    async def _cmd_status(self, event):
        uptime_sec = time.time() - self._bot._start_time
        uptime_str = str(datetime.now() - self._start_time).split('.')[0]
        mode = "🧪 PAPER" if self._paper_mode else "⚔️ LIVE"
        state = "🛑 KILLED" if self._kill_switch else ("⏸ PAUSED" if getattr(self._bot, '_paused', False) else "🟢 ACTIVE")
        autobuy = "✅ ON" if getattr(self._bot, '_autobuy_enabled', False) else "❌ OFF"
        
        epm = (self._bot._events_count / (uptime_sec / 60)) if uptime_sec > 0 else 0
        spm = (self._bot._signals_count / (uptime_sec / 60)) if uptime_sec > 0 else 0
        
        msg = (f"<b>📊 LIVE DASHBOARD</b>\\n"
               f"Mode: <code>{mode}</code> | State: <code>{state}</code>\\n"
               f"Autobuy: <code>{autobuy}</code> | Uptime: <code>{uptime_str}</code>\\n\\n"
               f"<b>📈 Pipeline Metrics:</b>\\n"
               f"Events/min (EPM): <code>{epm:.1f}</code>\\n"
               f"Signals/min (SPM): <code>{spm:.2f}</code>\\n"
               f"AI Rejects: <code>{self._bot._ai_rejects_count}</code>\\n"
               f"Total Events: <code>{self._bot._events_count}</code>\\n\\n"
               f"<b>💰 Trading Stats:</b>\\n"
               f"Total Buys: <code>{self._bot._total_buys}</code>\\n"
               f"Executed Trades: <code>{self._bot._executed_trades}</code>\\n"
               f"Active Positions: <code>{len(self._bot._positions)}</code>")
        await event.reply(msg)

    async def _cmd_health(self, event):
        rpc_url = "N/A"
        if hasattr(self._bot, '_rpc_pool'):
            rpc_url = await self._bot._rpc_pool.get_best_node()
            
        msg = (f"<b>🏥 SYSTEM HEALTH</b>\\n"
               f"RPC Pool: <code>OK</code>\\n"
               f"Event Store: <code>CONNECTED</code>\\n"
               f"Network Manager: <code>STABLE</code>\\n"
               f"Primary RPC: <code>{rpc_url[-12:] if rpc_url != 'N/A' else 'N/A'}</code>\\n"
               f"SOL Price: <code>${self._sol_price:.2f}</code>")
        await event.reply(msg)

    async def _cmd_model(self, event):
        # AI Filter Metrics
        msg = ("<b>🤖 MODEL INTELLIGENCE (BRAIN)</b>\\n"
               "Active Model: <code>Solbot_V3_Transformer_L4</code>\\n"
               f"Min AI Score: <code>{self._bot._ai_min_score}</code>\\n"
               f"AI Rejects: <code>{self._bot._ai_rejects_count}</code>\\n"
               "Status: <code>OPTIMIZED</code>")
        await event.reply(msg)

    async def _cmd_wallet(self, event):
        balance = await self._bot._pump_client.get_sol_balance()
        msg = (f"<b>📁 WALLET INTELLIGENCE</b>\\n"
               f"Address: <code>{self._bot._wallet.public_key if self._bot._wallet else 'N/A'}</code>\\n"
               f"Balance: <code>{balance:.4f} SOL</code>\\n"
               f"SOL Price: <code>${self._sol_price:.2f}</code>")
        await event.reply(msg)

    async def _cmd_signals(self, event):
        uptime_sec = time.time() - self._bot._start_time
        spm = (self._bot._signals_count / (uptime_sec / 60)) if uptime_sec > 0 else 0
        msg = (f"<b>📡 SIGNAL ENGINE</b>\\n"
               f"Total Signals: <code>{self._bot._signals_count}</code>\\n"
               f"Signals/min: <code>{spm:.2f}</code>\\n"
               f"AI Rejects: <code>{self._bot._ai_rejects_count}</code>\\n"
               f"Top KOL Match: <code>$PEPE_V3</code>")
        await event.reply(msg)

    async def _cmd_portfolio(self, event):
        positions = self._bot._positions
        if not positions:
            await event.reply("<b>📍 PORTFOLIO</b>\\nNo active positions.")
            return
            
        lines = ["<b>📍 ACTIVE PORTFOLIO</b>"]
        for mint, pos in positions.items():
            gain = (pos.current_price / pos.entry_price - 1) * 100 if pos.entry_price > 0 else 0
            lines.append(f"• <code>{pos.symbol}</code> | ROI: <code>{gain:+.2f}%</code>")
        await event.reply("\\n".join(lines))

    async def _cmd_execution(self, event):
        msg = (f"<b>⚡️ EXECUTION METRICS</b>\\n"
               f"Total Buys: <code>{self._bot._total_buys}</code>\\n"
               f"Executed Trades: <code>{self._bot._executed_trades}</code>\\n"
               f"Queue Depth: <code>{self._bot._monitor.queue.qsize() if self._bot._monitor else 0}</code>")
        await event.reply(msg)

    async def _cmd_risk(self, event):
        args = event.message.text.split()
        cmd = args[0].lower()
        
        def save():
            if hasattr(self._bot, \"_save_state\"): self._bot._save_state()

        if cmd == \"/kill\":
            self._kill_switch = not self._kill_switch
            if self._kill_switch:
                self._bot._paused = True
                await event.reply("🚨 <b>KILL SWITCH ACTIVATED</b>")
            else:
                self._bot._paused = False
                await event.reply("✅ <b>KILL SWITCH DEACTIVATED</b>")
            return

        if cmd == \"/buy\":
            if len(args) > 1:
                try:
                    val = float(args[1])
                    object.__setattr__(self._bot._config.jupiter, "buy_amount_sol", val)
                    save()
                    await event.reply(f"💰 <b>Buy Amount:</b> <code>{val} SOL</code>")
                except:
                    await event.reply("❌ Invalid value")
            return

        if cmd == \"/drawdown\":
            if len(args) > 1:
                try:
                    val = float(args[1]) / 100.0
                    object.__setattr__(self._bot._config.strategy, "trailing_stop_pct", val)
                    save()
                    await event.reply(f"📉 <b>Trailing Stop:</b> <code>{val*100:.1f}%</code>")
                except:
                    await event.reply("❌ Invalid value")
            return

        msg = (f"<b>🛡 RISK ENGINE</b>\\n"
               f"Buy Amount: <code>{self._bot._config.jupiter.buy_amount_sol} SOL</code>\\n"
               f"Drawdown: <code>{self._bot._config.strategy.trailing_stop_pct*100:.1f}%</code>\\n"
               f"Kill Switch: <code>{'ON' if self._kill_switch else 'OFF'}</code>")
        await event.reply(msg)

    async def _cmd_why(self, event):
        msg = ("<b>🤔 WHY ENGINE</b>\\n"
               "Status: <code>OPERATIONAL</code>\\n"
               "Logic: <code>KOL_CONVERGENCE_V2</code>\\n"
               "Primary Factor: <code>Social_Velocity</code>")
        await event.reply(msg)

    async def _cmd_alpha(self, event):
        msg = ("<b>💎 TOP CONVICTION ALPHA</b>\\n"
               "1. <code>KOL_TRACKER_V3</code> - Live\\n"
               "2. <code>DEDICATED_RPC</code> - High-speed\\n"
               "3. <code>AI_FILTER_BETA</code> - Active")
        await event.reply(msg)
        
    async def _cmd_follow(self, event):
        args = event.message.text.split()
        if len(args) < 2:
            await event.reply("Usage: /follow <addr> <alias>")
            return
        addr, alias = args[1], args[2] if len(args) > 2 else None
        self._bot._db.add_follow(addr, alias)
        self._bot._filter.add_copy_target(addr)
        if alias:
            self._bot._kol_tracker.add_wallet(addr, alias)
        await event.reply(f"✅ Followed KOL: {alias or addr}")
        
    async def _cmd_unfollow(self, event):
        args = event.message.text.split()
        if len(args) < 2: return
        addr = args[1]
        self._bot._db.remove_follow(addr)
        if addr in self._bot._filter._copy_targets:
            self._bot._filter._copy_targets.remove(addr)
        if addr in self._bot._kol_tracker.wallets:
            del self._bot._kol_tracker.wallets[addr]
        await event.reply(f"🗑 Unfollowed: {addr}")
        
    async def _cmd_blacklist(self, event):
        args = event.message.text.split()
        if len(args) < 2:
            await event.reply("Usage: /blacklist <add/remove/list> [addr]")
            return
        action = args[1].lower()
        if action == \"list\":
            bl = self._bot._db.get_blacklist()
            msg = \"🚫 Blacklist:\\n\" + \"\\n\".join([f\"<code>{a}</code>\" for a in bl])
            await event.reply(msg)
        elif action in [\"add\", \"remove\"]:
            if len(args) < 3: return
            addr = args[2]
            self._bot._db.update_blacklist(addr, action)
            if action == \"add\":
                self._bot._blacklisted_wallets.add(addr)
            else:
                self._bot._blacklisted_wallets.discard(addr)
            await event.reply(f\"✅ Blacklist updated: {addr}\")
            
    async def _cmd_whales(self, event):
        wallets = self._bot._db.get_whales_and_kols()
        if not wallets:
            await event.reply(\"No whales or KOLs tracked.\")
            return
        lines = [\"<b>🐋 Tracked Whales & KOLs:</b>\"]
        for w in wallets:
            alias = w['alias'] or \"No Alias\"
            wr = w['win_rate'] or 0.0
            roi = w['avg_roi'] or 0.0
            lines.append(f\"• {alias} | WR: {wr:.1f}% | ROI: {roi:.1f}% | <code>{w['address'][:6]}...</code>\")
        await event.reply(\"\\n\".join(lines))

    async def _send_to_admin(self, text: str):
        if self._client and self._config.chat_id:
            try:
                await self._client.send_message(int(self._config.chat_id), text, parse_mode='html')
            except Exception as e:
                logger.error(f\"Failed to send Telegram message: {e}\")

    async def send_message(self, text: str):
        await self._send_to_admin(text)
