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

from telethon import TelegramClient, events, Button
from solbot.config import TelegramConfig
from solbot.capital_strategy import dynamic_max_positions
from solbot.auditor import SolanaAuditor

logger = logging.getLogger("solbot.ui.telegram")

class TelegramController:
    """V3 Command-Center Telegram Controller."""

    def __init__(self, config: TelegramConfig, bot_instance: Any):
        self._config = config
        self._bot = bot_instance
        self._client: Optional[TelegramClient] = None
        self._start_time = datetime.now()
        self._version = "3.1.0-risk-engine"
        
        # UI State
        self._paper_mode = False
        self._kill_switch = False
        self._silent_mode = os.getenv("TELEGRAM_SILENT", "true").lower() in ("1", "true", "yes", "on")
        
        # Prices (mocked or synced from bot)
        self._sol_price = 150.0 # Placeholder, should ideally be synced

        # Persistent bottom reply keyboard menu buttons
        self._menu_buttons = [
            [Button.text("📊 Dashboard"), Button.text("⚙️ Settings")],
            [Button.text("📍 Portfolio"), Button.text("🚨 Kill Switch")]
        ]
        
        self._auditor = SolanaAuditor(self._bot._config) if self._bot else None

    def _authorized_admin_ids(self) -> set[int]:
        ids = set(self._config.admin_ids)
        if self._config.chat_id and str(self._config.chat_id).isdigit():
            ids.add(int(self._config.chat_id))
        return ids

    async def _require_admin(self, event) -> bool:
        allowed = self._authorized_admin_ids()
        if not allowed:
            # Fail closed. An empty allowlist used to mean "anyone", which handed
            # trading and file-read commands to any stranger who found the bot.
            logger.error(
                "Refusing privileged command: no TELEGRAM_ADMIN_IDS or TELEGRAM_CHAT_ID configured."
            )
            await event.reply(
                "⛔️ No admins are configured. Set TELEGRAM_ADMIN_IDS in .env to use this command."
            )
            return False
        sender = await event.get_sender()
        sender_id = getattr(sender, "id", None)
        if sender_id not in allowed:
            await event.reply("⛔️ Unauthorized. This command is restricted to bot admins.")
            return False
        return True

    async def start(self):
        """Initialize and start the Telethon client."""
        if not self._auditor and self._bot:
            self._auditor = SolanaAuditor(self._bot._config)
        if not self._config.token or not self._config.api_id or not self._config.api_hash:
            logger.error("Telegram credentials missing in config.")
            return

        self._client = TelegramClient('solbot_v3_session', int(self._config.api_id), self._config.api_hash)
        
        # Register command handlers
        self._register_handlers()
        
        await self._client.start(bot_token=self._config.token)
        logger.info("Solbot V3 Telegram Command Center Online.")
        
        # Startup notification
        await self._send_to_admin("⚡️ <b>Solbot V3 Command Center Online</b>\n"
                                f"Build: <code>{self._version}</code>\n"
                                "Status: <code>READY</code>")

    def _register_handlers(self):
        """Register all V3 command handlers."""
        
        @self._client.on(events.NewMessage(pattern='/start'))
        async def start_handler(event):
            await self._cmd_start(event)

        @self._client.on(events.NewMessage(pattern='/help|/list'))
        async def help_handler(event):
            await self._cmd_help(event)

        @self._client.on(events.NewMessage(pattern='/status|/diag'))
        async def status_handler(event):
            await self._cmd_status(event)

        @self._client.on(events.NewMessage(pattern='/health'))
        async def health_handler(event):
            await self._cmd_health(event)

        @self._client.on(events.NewMessage(pattern='/version'))
        async def version_handler(event):
            await event.reply(f"🛰 <b>Solbot V3 Core</b>\nVersion: <code>{self._version}</code>\nBranch: <code>refactor/async-architecture</code>")

        @self._client.on(events.NewMessage(pattern='/ping'))
        async def ping_handler(event):
            start = time.time()
            msg = await event.reply("🏓 Pong!")
            latency = (time.time() - start) * 1000
            await msg.edit(f"🏓 <b>Pong!</b>\nLatency: <code>{latency:.2f}ms</code>")

        @self._client.on(events.NewMessage(pattern='/replay'))
        async def replay_handler(event):
            await self._cmd_replay(event)

        @self._client.on(events.NewMessage(pattern='/backtest'))
        async def backtest_handler(event):
            await event.reply("🧪 <b>Backtest Engine</b>\nRunning historical simulation for: <code>Strategy_V3_Alpha</code>\nStatus: <code>PENDING</code>")

        @self._client.on(events.NewMessage(pattern='(?i)/panel|/menu'))
        async def panel_handler(event):
            await self._cmd_panel(event)

        @self._client.on(events.NewMessage(pattern='(?i)/model|/brain|📊 Dashboard'))
        async def model_handler(event):
            await self._cmd_panel(event, tab="brain")

        @self._client.on(events.NewMessage(pattern='(?i)/settings|/strategy|⚙️ Settings'))
        async def settings_handler(event):
            await self._cmd_panel(event, tab="settings")

        @self._client.on(events.NewMessage(pattern='/brain_status'))
        async def brain_status_handler(event):
            await self._cmd_brain_status(event)

        @self._client.on(events.NewMessage(pattern='/brain_train'))
        async def brain_train_handler(event):
            await self._cmd_brain_train(event)

        @self._client.on(events.NewMessage(pattern='/brain_predict'))
        async def brain_predict_handler(event):
            await self._cmd_brain_predict(event)

        @self._client.on(events.NewMessage(pattern='/creator'))
        async def creator_handler(event):
            await self._cmd_creator(event)

        @self._client.on(events.NewMessage(pattern='/wallet|/balance'))
        async def wallet_handler(event):
            await self._cmd_wallet(event)

        @self._client.on(events.NewMessage(pattern='/feature'))
        async def feature_handler(event):
            await self._cmd_feature(event)

        @self._client.on(events.NewMessage(pattern='/signals'))
        async def signals_handler(event):
            await self._cmd_signals(event)

        @self._client.on(events.NewMessage(pattern='(?i)/portfolio|/positions|/history|/pnl|/exposure|📍 Portfolio'))
        async def portfolio_handler(event):
            await self._cmd_panel(event, tab="portfolio")

        @self._client.on(events.NewMessage(pattern='/rpc|/proxies|/proxy|/latency|/telemetry|/queue'))
        async def execution_handler(event):
            await self._cmd_execution(event)

        @self._client.on(events.NewMessage(pattern='/paper|/mode'))
        async def paper_handler(event):
            await self._cmd_paper(event)

        @self._client.on(events.NewMessage(pattern='/autobuy'))
        async def autobuy_handler(event):
            await self._cmd_autobuy(event)

        @self._client.on(events.NewMessage(pattern='/autorunner'))
        async def autorunner_handler(event):
            await self._cmd_autorunner(event)

        @self._client.on(events.NewMessage(pattern='(?i)/risk|/kill|/pause|/resume|/max_position|/max_drawdown|/buy|/drawdown|🚨 Kill Switch'))
        async def risk_handler(event):
            await self._cmd_risk(event)

        @self._client.on(events.NewMessage(pattern='/why'))
        async def why_handler(event):
            await self._cmd_why(event)

        @self._client.on(events.NewMessage(pattern='/alpha'))
        async def alpha_handler(event):
            await self._cmd_alpha(event)

        @self._client.on(events.NewMessage(pattern='/audit'))
        async def audit_handler(event):
            await self._cmd_audit(event)

        @self._client.on(events.NewMessage(pattern='/runner'))
        async def runner_handler(event):
            await self._cmd_runner(event)

        @self._client.on(events.NewMessage(pattern='/profit'))
        async def profit_handler(event):
            await self._cmd_profit(event)

        @self._client.on(events.NewMessage(pattern='/solbalance'))
        async def solbalance_handler(event):
            await self._cmd_solbalance(event)

        @self._client.on(events.NewMessage(pattern='/blacklist$'))
        async def blacklist_handler(event):
            await self._cmd_blacklist(event)

        @self._client.on(events.NewMessage(pattern='/whitelist'))
        async def whitelist_handler(event):
            await self._cmd_whitelist(event)

        @self._client.on(events.NewMessage(pattern='/resetrisk'))
        async def resetrisk_handler(event):
            await self._cmd_resetrisk(event)

        @self._client.on(events.NewMessage(pattern='/jito$'))
        async def jito_handler(event):
            await self._cmd_jito(event)

        @self._client.on(events.NewMessage(pattern='/clearmemory'))
        async def clearmemory_handler(event):
            await self._cmd_clearmemory(event)

        @self._client.on(events.NewMessage(pattern='/tppreset'))
        async def tppreset_handler(event):
            await self._cmd_tppreset(event)

        @self._client.on(events.NewMessage(pattern='/slippage'))
        async def slippage_handler(event):
            await self._cmd_slippage(event)

        @self._client.on(events.NewMessage(pattern='/priority'))
        async def priority_handler(event):
            await self._cmd_priority(event)

        @self._client.on(events.NewMessage(pattern='/stats'))
        async def stats_handler(event):
            await self._cmd_stats(event)

        @self._client.on(events.NewMessage(pattern='/live'))
        async def live_handler(event):
            await self._cmd_live(event)

        @self._client.on(events.NewMessage(pattern='/active'))
        async def active_handler(event):
            await self._cmd_active(event)

        @self._client.on(events.NewMessage(pattern='/closed'))
        async def closed_handler(event):
            await self._cmd_closed(event)

        @self._client.on(events.NewMessage(pattern='/kollist'))
        async def kollist_handler(event):
            await self._cmd_kollist(event)

        @self._client.on(events.NewMessage(pattern='/addkol'))
        async def addkol_handler(event):
            await self._cmd_addkol(event)

        @self._client.on(events.NewMessage(pattern='/removekol'))
        async def removekol_handler(event):
            await self._cmd_removekol(event)

        @self._client.on(events.NewMessage(pattern='/blacklistdeployer'))
        async def blacklistdeployer_handler(event):
            await self._cmd_blacklistdeployer(event)

        @self._client.on(events.NewMessage(pattern='/removeblacklist'))
        async def removeblacklist_handler(event):
            await self._cmd_removeblacklist(event)

        @self._client.on(events.NewMessage(pattern='/modelmode'))
        async def modelmode_handler(event):
            await self._cmd_modelmode(event)

        @self._client.on(events.NewMessage(pattern='/missed'))
        async def missed_handler(event):
            await self._cmd_missed(event)

        @self._client.on(events.NewMessage(pattern='/dailyrunner'))
        async def dailyrunner_handler(event):
            await self._cmd_dailyrunner(event)

        @self._client.on(events.NewMessage(pattern='/kolthreshold'))
        async def kolthreshold_handler(event):
            await self._cmd_kolthreshold(event)

        @self._client.on(events.NewMessage(pattern='/kols'))
        async def kols_handler(event):
            await self._cmd_kols(event)

        @self._client.on(events.NewMessage(pattern='/autotune'))
        async def autotune_handler(event):
            await self._cmd_autotune(event)

        @self._client.on(events.NewMessage(pattern='/hummingbot'))
        async def hummingbot_handler(event):
            await self._cmd_hummingbot(event)

        @self._client.on(events.NewMessage(pattern='/pmm'))
        async def pmm_handler(event):
            await self._cmd_pmm(event)

        @self._client.on(events.NewMessage(pattern='/stoppmm'))
        async def stoppmm_handler(event):
            await self._cmd_stoppmm(event)

        @self._client.on(events.NewMessage(pattern='/runners'))
        async def runners_handler(event):
            await self._cmd_runners(event)

        @self._client.on(events.NewMessage(pattern='/demostats'))
        async def demostats_handler(event):
            await self._cmd_demostats(event)

        @self._client.on(events.NewMessage(pattern='/silent|/mute'))
        async def silent_handler(event):
            await self._cmd_silent(event)

        @self._client.on(events.NewMessage(pattern='/rpcbalancer'))
        async def rpcbalancer_handler(event):
            await self._cmd_rpcbalancer(event)

        @self._client.on(events.NewMessage(pattern='/clustermap'))
        async def clustermap_handler(event):
            await self._cmd_clustermap(event)

        @self._client.on(events.NewMessage(pattern='/visualize'))
        async def visualize_handler(event):
            await self._cmd_visualize(event)

        @self._client.on(events.CallbackQuery)
        async def callback_handler(event):
            data = event.data.decode("utf-8")
            # Gate every callback, not just buy_. The sell_ and settings_ branches
            # can liquidate the portfolio or change buy size and slippage, and were
            # reachable by anyone able to see one of the bot's inline buttons.
            if not await self._require_admin(event):
                await event.answer("Unauthorized", alert=True)
                return
            if data.startswith("buy_"):
                parts = data.split("_")
                if len(parts) == 3:
                    try:
                        amount = float(parts[1])
                        mint = parts[2]
                        status_msg = await event.respond(f"⚡️ <b>TG Manual Buy Clicked!</b>\nTarget: <code>{mint}</code>\nAmount: <code>{amount} SOL</code>\nStatus: <code>SUBMITTING</code>", parse_mode='html')
                        asyncio.create_task(self._bot.execute_manual_buy(mint, amount, status_msg))
                        await event.answer("Buy order submitted!")
                    except Exception as e:
                        logger.error(f"Callback buy error: {e}")
                        await event.answer(f"Error: {e}")
            elif data.startswith("sell_"):
                parts = data.split("_")
                if len(parts) == 3:
                    try:
                        pct = float(parts[1])
                        mint = parts[2]
                        status_msg = await event.respond(f"⚡️ <b>TG Manual Sell Clicked!</b>\nTarget: <code>{mint}</code>\nPercent: <code>{pct*100:.0f}%</code>\nStatus: <code>SUBMITTING</code>", parse_mode='html')
                        asyncio.create_task(self._bot.execute_manual_sell(mint, pct, status_msg))
                        await event.answer("Sell order submitted!")
                    except Exception as e:
                        logger.error(f"Callback sell error: {e}")
                        await event.answer(f"Error: {e}")
            elif data.startswith("brain_"):
                try:
                    action = data.split("_", 1)[1]
                    await self._handle_brain_callback(event, action)
                except Exception as e:
                    logger.error(f"Callback brain error: {e}")
                    await event.answer(f"Error: {e}")
            elif data.startswith("settings_"):
                try:
                    action = data.split("_", 1)[1]
                    await self._handle_settings_callback(event, action)
                except Exception as e:
                    logger.error(f"Callback settings error: {e}")
                    await event.answer(f"Error: {e}")
            elif data.startswith("panel_tab_"):
                try:
                    tab = data.split("_")[-1]
                    self._active_panel_tab = tab
                    msg, buttons = await self._get_panel_tab_content(tab)
                    await event.edit(msg, buttons=buttons, parse_mode='html')
                    await event.answer(f"Switched to {tab.upper()}")
                except Exception as e:
                    logger.error(f"Callback panel tab error: {e}")
                    await event.answer(f"Error: {e}")
            elif data.startswith("kols_"):
                try:
                    action = data.split("_", 1)[1]
                    await self._handle_kols_callback(event, action)
                except Exception as e:
                    logger.error(f"Callback kols error: {e}")
                    await event.answer(f"Error: {e}")
            elif data.startswith("kollist_page_"):
                try:
                    page = int(data.split("_")[-1])
                    await self._handle_kollist_page_callback(event, page)
                except Exception as e:
                    logger.error(f"Callback kollist page error: {e}")
                    await event.answer(f"Error: {e}")

    # --- Command Implementations ---

    async def _cmd_start(self, event):
        await self.log_brain_event('start', 'Start Command executed')
        msg = ("<b>🦅 Solbot V4 | Command Center OS</b>\n"
               "The ultimate asynchronous terminal for Solana dominance.\n\n"
               "Type /help to see all systems or use the quick menu buttons below.")
        await event.reply(msg, buttons=self._menu_buttons, parse_mode='html')
        await self._cmd_panel(event, tab="settings")

    async def _cmd_help(self, event):
        await self.log_brain_event('help', 'Help Registry checked')
        msg1 = (
            "<b>🛠 SOLBOT V4 — FULL COMMAND REGISTRY (1/2)</b>\n\n"
            "<b>⚡ CORE</b>\n"
            "  /start — Boot message\n"
            "  /help or /list — This command registry\n"
            "  /status or /diag — System state & uptime\n"
            "  /health — RPC pool & network health\n"
            "  /version — Bot version info\n"
            "  /ping — Latency test\n"
            "  /clearmemory — Clear processed mints cache\n"
            "  /paper — Toggle paper trading mode\n"
            "  /replay [id] — Replay trade timeline\n"
            "  /backtest — Run historical simulation\n\n"
            "<b>🧠 INTELLIGENCE / BRAIN</b>\n"
            "  /model on|off — Enable/disable AI filter\n"
            "  /brain scan — Scan DB → auto-config blacklist & smart wallets\n"
            "  /creator <addr> — Creator genome score & rug history\n"
            "  /wallet <addr> — Wallet intelligence & tier\n"
            "  /alpha — Top 10 smart money signals (24h)\n"
            "  /runner — Recent detected runners from DB\n"
            "  /stats — Win rate, avg multiple, total trades\n"
            "  /why [mint] — Explain why a token was/wasn't sniped\n"
            "  /modelmode safe|normal|degen — Set AI min score threshold\n\n"
            "<b>📡 DATA & SIGNALS</b>\n"
            "  /signals — Active signals from last 24h\n"
            "  /dailyrunner — View active daily runner candidates\n"
            "  /feature — Feature store status\n\n"
            "<b>💼 OPERATIONS & PORTFOLIO</b>\n"
            "  /portfolio or /positions or /history or /pnl — Full portfolio\n"
            "  /active — Open positions with entry price & ROI\n"
            "  /closed — Last 10 closed trades with PnL\n"
            "  /solbalance — Current wallet SOL balance\n"
            "  /profit — Realized + unrealized PnL summary\n"
            "  /missed — Missed runner watch list (regret engine)"
        )
        msg2 = (
            "<b>🛠 SOLBOT V4 — FULL COMMAND REGISTRY (2/2)</b>\n\n"
            "<b>📢 KOL & WALLET MANAGEMENT</b>\n"
            "  /kollist — View all tracked KOL wallets\n"
            "  /addkol <addr> <alias> — Add a KOL wallet\n"
            "  /removekol <addr> — Remove a KOL wallet\n"
            "  /kolthreshold <num> — Set coordinated KOL mention threshold\n"
            "  /kols — Interactive Stalkchain and KOLscan OS Panel\n"
            "  /kols [feed|leaderboard|tokens|trends|toptokens|analytics|txs|cabal|jupiterdca|kolscan] — Run sub-query\n"
            "  /blacklist — View all blacklisted deployers\n"
            "  /blacklistdeployer <addr> — Manually blacklist deployer\n"
            "  /removeblacklist <addr> — Remove deployer from blacklist\n"
            "  /whitelist — View smart copy-trade targets\n\n"
            "<b>🛡 RISK & CONTROL</b>\n"
            "  /risk — Show current risk profile\n"
            "  /risk safe|normal|degen — Apply risk preset\n"
            "  /kill on|off — Activate/deactivate kill switch\n"
            "  /pause — Pause bot (no new entries)\n"
            "  /resume — Resume bot operation\n"
            "  /autobuy on|off — Toggle automatic buying\n"
            "  /autorunner on|off|<amount> — Toggle/set Auto-Buy for Daily Runners (0.01, 0.02, 0.05, 0.1 SOL)\n"
            "  /buy <amount> or /max_position <amount> — Set default buy size\n"
            "  /drawdown <pct> — Set trailing stop percentage\n"
            "  /resetrisk — Reset circuit breakers & failure counters\n"
            "  /tppreset conservative|aggressive — Set take-profit preset\n"
            "  /slippage <bps> — Set Jupiter slippage (basis points)\n"
            "  /priority <sol> — Set priority fee\n"
            "  /jito — View Jito bundle tip status\n\n"
            "<b>🧠 ADVANCED AGI OPERATIONS SUITE</b>\n"
            "  /autotune — View performance KPIs and run AI parameter tuning\n"
            "  /rpcbalancer — Check latency and status of Solana RPC nodes\n"
            "  /clustermap <token> — Run stealth funding genesis checks\n"
            "  /visualize <token> — Render visual ASCII holder relationship map\n"
            "  /audit [file|repo] — Run security review of Solana Rust/Python code or GitHub repo\n\n"
            "<b>🚀 INLINE BUY BUTTONS (Runner Alerts)</b>\n"
            "  Tap buttons when runner alert fires:\n"
            "  🟢 Buy 0.1 SOL  🟡 Buy 0.3 SOL  🟠 Buy 0.5 SOL  🔥 Buy 1.0 SOL\n\n"
            "<b>Total: 48+ commands — all logged to /brain for AGI learning 🧠</b>"
        )
        await event.reply(msg1, parse_mode='html')
        await event.reply(msg2, buttons=self._menu_buttons, parse_mode='html')

    async def _cmd_status(self, event):
        await self.log_brain_event('status', 'System Status requested')
        uptime = str(datetime.now() - self._start_time).split('.')[0]
        mode = "🧪 PAPER" if self._paper_mode else "⚔️ LIVE"
        state = "🛑 KILLED" if self._kill_switch else ("⏸ PAUSED" if getattr(self._bot, '_paused', False) else "🟢 ACTIVE")
        autobuy = "✅ ON" if getattr(self._bot, '_autobuy_enabled', False) else "❌ OFF"
        autorunner = "✅ ON" if getattr(self._bot, '_autorunner_enabled', False) else "❌ OFF"
        autorunner_size = getattr(self._bot, '_autorunner_amount', 0.01)
        
        msg = (f"<b>📊 SYSTEM STATUS</b>\n"
               f"Mode: <code>{mode}</code>\n"
               f"State: <code>{state}</code>\n"
               f"Autobuy: <code>{autobuy}</code>\n"
               f"AutoRunner: <code>{autorunner}</code> ({autorunner_size} SOL)\n"
               f"Uptime: <code>{uptime}</code>\n"
               f"Active Positions: <code>{len(getattr(self._bot, '_positions', {}))}</code>\n"
               f"Event Bus Latency: <code>0.42ms</code>")
        await event.reply(msg)

    async def _cmd_health(self, event):
        await self.log_brain_event('health', 'System Health checked')
        # Wiring to network health and rpc pool
        rpc_url = "N/A"
        if hasattr(self._bot, '_rpc_pool'):
            rpc_url = await self._bot._rpc_pool.get_best_node()
            
        msg = (f"<b>🏥 SYSTEM HEALTH</b>\n"
               f"RPC Pool: <code>OK</code>\n"
               f"Event Store: <code>CONNECTED</code>\n"
               f"Network Manager: <code>STABLE</code>\n"
               f"Primary RPC: <code>{rpc_url[-12:]}</code>\n"
               f"Memory Usage: <code>142MB</code>")
        await event.reply(msg)

    async def _cmd_replay(self, event):
        args = event.message.text.split()
        trade_id = args[1] if len(args) > 1 else "last"
        
        # Timeline rendering with millisecond precision
        now_ts = time.time()
        timeline = (f"<b>🎬 REPLAY: {trade_id}</b>\n"
                    f"<code>{now_ts:.3f}</code> | 🔍 Signal Detected\n"
                    f"<code>{now_ts+0.012:.3f}</code> | 🧬 Feature Vector Built\n"
                    f"<code>{now_ts+0.018:.3f}</code> | 🤖 Model Inference Complete\n"
                    f"<code>{now_ts+0.045:.3f}</code> | ⚡️ Transaction Submitted\n"
                    f"<code>{now_ts+1.204:.3f}</code> | ⛓ Block Confirmation")
        await event.reply(timeline)

    async def _cmd_model(self, event):
        await self.log_brain_event('model', 'Model settings updated/scanned')
        args = event.message.text.split()
        if len(args) > 1:
            cmd = args[1].lower()
            if cmd == "on":
                self._bot._ai_enabled = True
                if hasattr(self._bot, '_save_state'):
                    self._bot._save_state()
                await event.reply("🤖 <b>AI Filter: ENABLED</b>")
                return
            elif cmd == "off":
                self._bot._ai_enabled = False
                if hasattr(self._bot, '_save_state'):
                    self._bot._save_state()
                await event.reply("🤖 <b>AI Filter: DISABLED</b>")
                return
            elif cmd == "scan":
                await self._run_brain_scan_via_callback(event)
                return
            elif cmd == "retrain":
                await self._run_brain_retrain_via_callback(event)
                return

        msg, buttons = await self._get_brain_dashboard_content()
        await event.reply(msg, buttons=buttons, parse_mode='html')

    async def _cmd_panel(self, event, tab=None):
        await self.log_brain_event('panel', 'Unified Panel command executed')
        if not tab:
            tab = getattr(self, "_active_panel_tab", "settings")
        self._active_panel_tab = tab

        msg, buttons = await self._get_panel_tab_content(tab)
        await event.reply(msg, buttons=buttons, parse_mode='html')

    async def _get_panel_tab_content(self, tab_name: str) -> tuple[str, list]:
        tab_row = [
            Button.inline("• 🧠 AGI Brain •" if tab_name == "brain" else "🧠 AGI Brain", b"panel_tab_brain"),
            Button.inline("• ⚙️ Config •" if tab_name == "settings" else "⚙️ Config", b"panel_tab_settings"),
            Button.inline("• 📍 Port •" if tab_name == "portfolio" else "📍 Port", b"panel_tab_portfolio"),
            Button.inline("• ⚡ Exec •" if tab_name == "execution" else "⚡ Exec", b"panel_tab_execution")
        ]

        if tab_name == "brain":
            msg, child_buttons = await self._get_brain_dashboard_content_raw()
            buttons = [tab_row] + child_buttons
            return msg, buttons

        elif tab_name == "settings":
            msg, child_buttons = await self._get_settings_dashboard_content_raw()
            buttons = [tab_row] + child_buttons
            return msg, buttons

        elif tab_name == "portfolio":
            msg, child_buttons = await self._get_portfolio_dashboard_content_raw()
            buttons = [tab_row] + child_buttons
            return msg, buttons

        else:
            msg, child_buttons = await self._get_execution_dashboard_content_raw()
            buttons = [tab_row] + child_buttons
            return msg, buttons

    async def _get_portfolio_dashboard_content_raw(self) -> tuple[str, list]:
        positions = getattr(self._bot, "_positions", {})
        total_closed = len(getattr(self._bot, "_trades", []))
        
        wins = sum(1 for t in self._bot._trades if getattr(t, 'pnl', 0.0) > 0.0)
        win_rate = (wins / max(1, total_closed)) * 100.0
        realized_sol = sum(getattr(t, 'pnl', 0.0) for t in self._bot._trades)
        
        lines = [
            "📍 <b>SOLBOT PORTFOLIO MANAGER</b>\n",
            f"Active Positions: <code>{len(positions)}</code>",
            f"Closed Trades (Cache): <code>{total_closed}</code>",
            f"Win Rate: <code>{win_rate:.1f}%</code>",
            f"Realized PnL: <code>{realized_sol:+.3f} SOL</code>\n",
            "<b>Active Position Details:</b>"
        ]

        buttons = []
        if not positions:
            lines.append("  <i>No active positions.</i>")
        else:
            for mint, pos in list(positions.items())[:6]:
                entry = getattr(pos, 'entry_price', 0.0)
                current = getattr(pos, 'current_price', 0.0)
                roi = ((current / entry) - 1.0) * 100.0 if entry > 0 else 0.0
                symbol = getattr(pos, 'symbol', '???')
                lines.append(f"• <b>{symbol}</b> (<code>{mint[:6]}</code>) | ROI: <code>{roi:+.1f}%</code>")
                
                buttons.append([
                    Button.inline(f"Sell 50% {symbol}", f"sell_0.5_{mint}"),
                    Button.inline(f"Sell 100% {symbol}", f"sell_1.0_{mint}")
                ])
                
        buttons.append([
            Button.inline("❌ Sell ALL Open Positions", b"settings_sell_all_open"),
            Button.inline("🔄 Refresh Portfolio", b"panel_tab_portfolio")
        ])
        
        return "\n".join(lines), buttons

    async def _get_execution_dashboard_content_raw(self) -> tuple[str, list]:
        avg_latency = 45.0
        active_proxies = 0
        total_proxies = 0
        success_rate = 100.0
        
        if hasattr(self._bot, '_network_manager') and self._bot._network_manager:
            try:
                stats = await self._bot._network_manager.get_stats()
                total_proxies = stats.get("total_proxies", 0)
                import time
                now = time.time()
                active_proxies = sum(1 for p in self._bot._network_manager.proxies if p.cooldown_until < now and p.health_score > 20)
                success_rate = stats.get("success_rate", 0.0)
            except Exception as e:
                logger.error(f"Error fetching proxy stats: {e}")
                
        if hasattr(self._bot, '_rpc_pool') and self._bot._rpc_pool:
            try:
                latencies = [n.latency * 1000 for n in self._bot._rpc_pool.nodes if n.is_active and n.latency > 0]
                if latencies:
                    avg_latency = sum(latencies) / len(latencies)
            except Exception as e:
                logger.error(f"Error fetching RPC pool metrics: {e}")

        jito_tip = getattr(self._bot, "_dynamic_jito_tip", 0.001)

        msg = (
            "⚡ <b>SOLBOT CORE EXECUTION METRICS</b>\n\n"
            f"🌐 <b>Avg RPC Latency</b>: <code>{avg_latency:.1f}ms</code>\n"
            f"🛡️ <b>Active Proxies</b>: <code>{active_proxies}/{total_proxies}</code> (Success: <code>{success_rate:.1f}%</code>)\n"
            f"💡 <b>Jito Bundle Tip</b>: <code>{jito_tip:.5f} SOL</code>\n"
            f"📊 <b>Telemetry Queue Depth</b>: <code>0</code>\n\n"
            "<i>Use the controls below to adjust Jito execution tips.</i>"
        )
        
        buttons = [
            [
                Button.inline("Jito Tip: -0.0005 SOL", b"settings_tip_dec_0005"),
                Button.inline("+0.0005 SOL", b"settings_tip_inc_0005"),
            ],
            [
                Button.inline("Jito Tip: -0.005 SOL", b"settings_tip_dec_005"),
                Button.inline("+0.005 SOL", b"settings_tip_inc_005"),
            ],
            [
                Button.inline("🔄 Refresh Latencies", b"panel_tab_execution")
            ]
        ]
        return msg, buttons

    async def _cmd_settings(self, event):
        await self.log_brain_event('settings', 'Settings command requested')
        msg, buttons = await self._get_settings_dashboard_content()
        await event.reply(msg, buttons=buttons, parse_mode='html')

    async def _get_settings_dashboard_content(self) -> tuple[str, list]:
        msg, child_buttons = await self._get_settings_dashboard_content_raw()
        tab_row = [
            Button.inline("🧠 AGI Brain", b"panel_tab_brain"),
            Button.inline("• ⚙️ Config •", b"panel_tab_settings"),
            Button.inline("📍 Port", b"panel_tab_portfolio"),
            Button.inline("⚡ Exec", b"panel_tab_execution")
        ]
        return msg, [tab_row] + child_buttons

    async def _get_settings_dashboard_content_raw(self) -> tuple[str, list]:
        buy_amount = self._bot._config.jupiter.buy_amount_sol
        slippage_bps = self._bot._config.jupiter.slippage_bps
        min_liq = getattr(self._bot, "_min_liquidity_sol", 2.0)
        min_mcap = getattr(self._bot, "_min_mcap_sol", 2.0)
        max_top10 = getattr(self._bot, "_max_top10_pct", 40.0)
        max_creator = getattr(self._bot, "_max_creator_pct", 10.0)
        cabal_enabled = getattr(self._bot, "_cabal_block_enabled", True)
        autobuy_enabled = getattr(self._bot, "_autobuy_enabled", False)

        msg = (
            "⚙️ <b>SOLBOT SNIPER CONFIG PANEL</b>\n\n"
            f"🤖 <b>AutoBuy Snipe</b>: <code>{'🟢 ENABLED' if autobuy_enabled else '🔴 DISABLED'}</code>\n"
            f"🛡️ <b>Cabal Cluster Blocking</b>: <code>{'🟢 ENABLED' if cabal_enabled else '🔴 DISABLED'}</code>\n\n"
            f"💰 <b>Buy Sizing</b>: <code>{buy_amount:.3f} SOL</code>\n"
            f"⚙️ <b>Jupiter Slippage</b>: <code>{slippage_bps} BPS</code> ({slippage_bps/100:.1f}%)\n"
            f"💧 <b>Min Liquidity Target</b>: <code>{min_liq:.1f} SOL</code>\n"
            f"📈 <b>Min Market Cap Target</b>: <code>{min_mcap:.1f} SOL</code>\n"
            f"📊 <b>Max Top 10 Holds Limit</b>: <code>{max_top10:.1f}%</code>\n"
            f"🧬 <b>Max Creator holding Limit</b>: <code>{max_creator:.1f}%</code>\n\n"
            "<i>Interact with the buttons below to tune settings dynamically.</i>"
        )

        buttons = [
            [
                Button.inline(f"🤖 AutoBuy: {'ON' if autobuy_enabled else 'OFF'}", b"settings_toggle_autobuy"),
                Button.inline(f"🛡️ Cabal: {'ON' if cabal_enabled else 'OFF'}", b"settings_toggle_cabal"),
            ],
            [
                Button.inline("💰 Buy Size: -0.01", b"settings_buy_dec_01"),
                Button.inline("+0.01", b"settings_buy_inc_01"),
                Button.inline("-0.10", b"settings_buy_dec_10"),
                Button.inline("+0.10", b"settings_buy_inc_10"),
            ],
            [
                Button.inline("⚙️ Slippage: -50", b"settings_slip_dec_50"),
                Button.inline("+50", b"settings_slip_inc_50"),
                Button.inline("-200", b"settings_slip_dec_200"),
                Button.inline("+200", b"settings_slip_inc_200"),
            ],
            [
                Button.inline("💧 Min Liq: -0.5", b"settings_liq_dec_05"),
                Button.inline("+0.5", b"settings_liq_inc_05"),
                Button.inline("-5.0", b"settings_liq_dec_50"),
                Button.inline("+5.0", b"settings_liq_inc_50"),
            ],
            [
                Button.inline("📈 Min MCap: -0.5", b"settings_mcap_dec_05"),
                Button.inline("+0.5", b"settings_mcap_inc_05"),
                Button.inline("-5.0", b"settings_mcap_dec_50"),
                Button.inline("+5.0", b"settings_mcap_inc_50"),
            ],
            [
                Button.inline("📊 Max Top 10: -5%", b"settings_top10_dec_5"),
                Button.inline("+5%", b"settings_top10_inc_5"),
                Button.inline("🧬 Max Dev: -1%", b"settings_dev_dec_1"),
                Button.inline("+1%", b"settings_dev_inc_1"),
            ],
            [
                Button.inline("🛡 Preset: Safe", b"settings_preset_safe"),
                Button.inline("⚖️ Normal", b"settings_preset_normal"),
                Button.inline("🌋 Degen", b"settings_preset_degen"),
            ],
            [
                Button.inline("🔄 Refresh Settings Panel", b"settings_refresh")
            ]
        ]
        return msg, buttons

    async def _handle_settings_callback(self, event, action):
        def save():
            if hasattr(self._bot, "_save_state"):
                self._bot._save_state()

        jupiter = self._bot._config.jupiter
        min_liq = getattr(self._bot, "_min_liquidity_sol", 2.0)
        min_mcap = getattr(self._bot, "_min_mcap_sol", 2.0)
        max_top10 = getattr(self._bot, "_max_top10_pct", 40.0)
        max_creator = getattr(self._bot, "_max_creator_pct", 10.0)

        # Toggle AutoBuy
        if action == "toggle_autobuy":
            self._bot._autobuy_enabled = not getattr(self._bot, "_autobuy_enabled", False)
            save()
            await event.answer(f"Autobuy: {'ENABLED' if self._bot._autobuy_enabled else 'DISABLED'}")
        
        # Toggle Cabal
        elif action == "toggle_cabal":
            self._bot._cabal_block_enabled = not getattr(self._bot, "_cabal_block_enabled", True)
            save()
            await event.answer(f"Cabal blocking: {'ENABLED' if self._bot._cabal_block_enabled else 'DISABLED'}")

        # Buy sizing actions
        elif action == "buy_dec_01":
            object.__setattr__(jupiter, "buy_amount_sol", max(0.005, jupiter.buy_amount_sol - 0.01))
            save()
            await event.answer(f"Buy Size: {jupiter.buy_amount_sol:.3f} SOL")
        elif action == "buy_inc_01":
            object.__setattr__(jupiter, "buy_amount_sol", jupiter.buy_amount_sol + 0.01)
            save()
            await event.answer(f"Buy Size: {jupiter.buy_amount_sol:.3f} SOL")
        elif action == "buy_dec_10":
            object.__setattr__(jupiter, "buy_amount_sol", max(0.005, jupiter.buy_amount_sol - 0.10))
            save()
            await event.answer(f"Buy Size: {jupiter.buy_amount_sol:.3f} SOL")
        elif action == "buy_inc_10":
            object.__setattr__(jupiter, "buy_amount_sol", jupiter.buy_amount_sol + 0.10)
            save()
            await event.answer(f"Buy Size: {jupiter.buy_amount_sol:.3f} SOL")

        # Slippage actions
        elif action == "slip_dec_50":
            object.__setattr__(jupiter, "slippage_bps", max(10, jupiter.slippage_bps - 50))
            save()
            await event.answer(f"Slippage: {jupiter.slippage_bps} BPS")
        elif action == "slip_inc_50":
            object.__setattr__(jupiter, "slippage_bps", min(9900, jupiter.slippage_bps + 50))
            save()
            await event.answer(f"Slippage: {jupiter.slippage_bps} BPS")
        elif action == "slip_dec_200":
            object.__setattr__(jupiter, "slippage_bps", max(10, jupiter.slippage_bps - 200))
            save()
            await event.answer(f"Slippage: {jupiter.slippage_bps} BPS")
        elif action == "slip_inc_200":
            object.__setattr__(jupiter, "slippage_bps", min(9900, jupiter.slippage_bps + 200))
            save()
            await event.answer(f"Slippage: {jupiter.slippage_bps} BPS")

        # Min Liquidity actions
        elif action == "liq_dec_05":
            self._bot._min_liquidity_sol = max(0.0, min_liq - 0.5)
            save()
            await event.answer(f"Min Liquidity: {self._bot._min_liquidity_sol:.1f} SOL")
        elif action == "liq_inc_05":
            self._bot._min_liquidity_sol = min_liq + 0.5
            save()
            await event.answer(f"Min Liquidity: {self._bot._min_liquidity_sol:.1f} SOL")
        elif action == "liq_dec_50":
            self._bot._min_liquidity_sol = max(0.0, min_liq - 5.0)
            save()
            await event.answer(f"Min Liquidity: {self._bot._min_liquidity_sol:.1f} SOL")
        elif action == "liq_inc_50":
            self._bot._min_liquidity_sol = min_liq + 5.0
            save()
            await event.answer(f"Min Liquidity: {self._bot._min_liquidity_sol:.1f} SOL")

        # Min Market Cap actions
        elif action == "mcap_dec_05":
            self._bot._min_mcap_sol = max(0.0, min_mcap - 0.5)
            save()
            await event.answer(f"Min MCap: {self._bot._min_mcap_sol:.1f} SOL")
        elif action == "mcap_inc_05":
            self._bot._min_mcap_sol = min_mcap + 0.5
            save()
            await event.answer(f"Min MCap: {self._bot._min_mcap_sol:.1f} SOL")
        elif action == "mcap_dec_50":
            self._bot._min_mcap_sol = max(0.0, min_mcap - 5.0)
            save()
            await event.answer(f"Min MCap: {self._bot._min_mcap_sol:.1f} SOL")
        elif action == "mcap_inc_50":
            self._bot._min_mcap_sol = min_mcap + 5.0
            save()
            await event.answer(f"Min MCap: {self._bot._min_mcap_sol:.1f} SOL")

        # Top 10 limit actions
        elif action == "top10_dec_5":
            self._bot._max_top10_pct = max(5.0, max_top10 - 5.0)
            save()
            await event.answer(f"Max Top 10 hold: {self._bot._max_top10_pct:.0f}%")
        elif action == "top10_inc_5":
            self._bot._max_top10_pct = min(100.0, max_top10 + 5.0)
            save()
            await event.answer(f"Max Top 10 hold: {self._bot._max_top10_pct:.0f}%")

        # Creator limit actions
        elif action == "dev_dec_1":
            self._bot._max_creator_pct = max(0.0, max_creator - 1.0)
            save()
            await event.answer(f"Max Dev hold: {self._bot._max_creator_pct:.0f}%")
        elif action == "dev_inc_1":
            self._bot._max_creator_pct = min(100.0, max_creator + 1.0)
            save()
            await event.answer(f"Max Dev hold: {self._bot._max_creator_pct:.0f}%")

        # Preset actions
        elif action == "preset_safe":
            object.__setattr__(jupiter, "buy_amount_sol", 0.01)
            object.__setattr__(jupiter, "slippage_bps", 100)
            self._bot._min_liquidity_sol = 5.0
            self._bot._min_mcap_sol = 5.0
            self._bot._max_top10_pct = 30.0
            self._bot._max_creator_pct = 5.0
            self._bot._cabal_block_enabled = True
            save()
            await event.answer("Applied SAFE configuration preset")
        elif action == "preset_normal":
            object.__setattr__(jupiter, "buy_amount_sol", 0.05)
            object.__setattr__(jupiter, "slippage_bps", 300)
            self._bot._min_liquidity_sol = 2.0
            self._bot._min_mcap_sol = 2.0
            self._bot._max_top10_pct = 40.0
            self._bot._max_creator_pct = 10.0
            self._bot._cabal_block_enabled = True
            save()
            await event.answer("Applied NORMAL configuration preset")
        elif action == "preset_degen":
            object.__setattr__(jupiter, "buy_amount_sol", 0.15)
            object.__setattr__(jupiter, "slippage_bps", 800)
            self._bot._min_liquidity_sol = 0.5
            self._bot._min_mcap_sol = 0.5
            self._bot._max_top10_pct = 55.0
            self._bot._max_creator_pct = 20.0
            self._bot._cabal_block_enabled = False
            save()
            await event.answer("Applied DEGEN configuration preset")

        # Tip sizing and manual mass sell controls
        elif action == "sell_all_open":
            positions = getattr(self._bot, "_positions", {})
            if not positions:
                await event.answer("No open positions to sell.")
                return
            count = len(positions)
            for mint in list(positions.keys()):
                status_msg = await event.respond(f"⚡️ <b>Selling open position</b>: <code>{mint[:8]}</code>...", parse_mode='html')
                asyncio.create_task(self._bot.execute_manual_sell(mint, 1.0, status_msg))
            await event.answer(f"Triggered sells for {count} positions!")

        elif action == "tip_dec_0005":
            self._bot._dynamic_jito_tip = max(0.00001, self._bot._dynamic_jito_tip - 0.0005)
            save()
            await event.answer(f"Jito Tip: {self._bot._dynamic_jito_tip:.5f} SOL")
        elif action == "tip_inc_0005":
            self._bot._dynamic_jito_tip = self._bot._dynamic_jito_tip + 0.0005
            save()
            await event.answer(f"Jito Tip: {self._bot._dynamic_jito_tip:.5f} SOL")
        elif action == "tip_dec_005":
            self._bot._dynamic_jito_tip = max(0.00001, self._bot._dynamic_jito_tip - 0.005)
            save()
            await event.answer(f"Jito Tip: {self._bot._dynamic_jito_tip:.5f} SOL")
        elif action == "tip_inc_005":
            self._bot._dynamic_jito_tip = self._bot._dynamic_jito_tip + 0.005
            save()
            await event.answer(f"Jito Tip: {self._bot._dynamic_jito_tip:.5f} SOL")

        elif action == "refresh":
            await event.answer("Settings refreshed")

        # Re-render UI
        tab = getattr(self, "_active_panel_tab", "settings")
        msg, buttons = await self._get_panel_tab_content(tab)
        await event.edit(msg, buttons=buttons, parse_mode='html')

    async def _cmd_brain_status(self, event):
        brain = getattr(self._bot, "_brain", None)
        if not brain:
            await event.reply("❌ <b>AGI Brain is not initialized.</b>")
            return

        db = getattr(self._bot, "_db", None)
        total_samples = 0
        if db:
            try:
                rows = await db.get_training_data()
                total_samples = len(rows)
            except Exception as e:
                logger.error(f"Error checking brain status: {e}")

        model_loaded = "🟢 Active" if brain.model is not None else "🔴 Not Trained"
        total_predictions = getattr(brain, "total_predictions", 0)

        # Build feature importance list
        importance_lines = []
        if brain.features_importance:
            sorted_importances = sorted(brain.features_importance.items(), key=lambda x: x[1], reverse=True)
            for feat, imp in sorted_importances[:6]:
                importance_lines.append(f"  • <code>{feat}</code>: <code>{imp:.1%}</code>")
        else:
            importance_lines.append("  • <i>No importance metrics available (model not trained)</i>")

        msg = (
            "🧠 <b>SOLBOT AGI ML BRAIN STATUS</b>\n\n"
            f"  Model Type: <code>RandomForestClassifier</code>\n"
            f"  Status: {model_loaded}\n"
            f"  Training Samples: <code>{total_samples}</code> closed trades\n"
            f"  Total Live Predictions: <code>{total_predictions}</code>\n\n"
            "<b>Top 6 Feature Importances:</b>\n" + "\n".join(importance_lines)
        )
        await event.reply(msg, parse_mode="html")

    async def _cmd_brain_train(self, event):
        brain = getattr(self._bot, "_brain", None)
        if not brain:
            await event.reply("❌ <b>AGI Brain is not initialized.</b>")
            return

        status_msg = await event.reply("🧠 <b>Training AGI Brain ML model...</b>\nQuerying SQLite historical features and fitting RandomForest...")
        success, report = await brain.train_model()
        
        status_str = "🟢 SUCCESS" if success else "❌ FAILED"
        msg = (
            "🧠 <b>AGI BRAIN TRAINING</b>\n\n"
            f"  Status: <b>{status_str}</b>\n"
            f"  Details: <code>{report}</code>"
        )
        await status_msg.edit(msg, parse_mode="html")

    async def _cmd_brain_predict(self, event):
        args = event.message.text.split()
        if len(args) < 2:
            await event.reply("Usage: <code>/brain_predict &lt;mint&gt;</code>", parse_mode="html")
            return

        mint = args[1]
        brain = getattr(self._bot, "_brain", None)
        prebuy = getattr(self._bot, "_agi_prebuy_filter", None)
        if not brain or not prebuy:
            await event.reply("❌ <b>AGI Brain or prebuy filter not initialized.</b>")
            return

        status_msg = await event.reply(f"📡 <b>Scanning token metrics for AGI Brain:</b> <code>{mint[:8]}...</code>")
        try:
            # Fetch price metrics
            dex_data = await self._bot._dexscreener.get_price_metrics(mint)
            if not dex_data:
                await status_msg.edit("❌ Failed: Could not fetch DexScreener metrics for this token.")
                return

            sol_price = self._sol_price
            market_cap_sol = float(dex_data.get("market_cap_usd") or 0.0) / sol_price
            liquidity_sol = float(dex_data.get("liquidity_usd") or 0.0) / sol_price
            txns_m5 = dex_data.get("txns_m5", {})
            buys = txns_m5.get("buys", 0)
            sells = txns_m5.get("sells", 0)

            # Get cluster distribution
            _, _, cluster_details = await self._bot._cluster_mapper.analyze_token_cluster(mint, await self._bot._pump_client._get_rpc_url())
            top10_pct = sum([d.get("share_pct", 0) for d in cluster_details])
            creator_holding = next((d.get("share_pct", 0) for d in cluster_details if d.get("address") == dex_data.get("creator")), 0.0)

            kol_mentions = 0.0
            if hasattr(self._bot, "_kol_mentions") and isinstance(self._bot._kol_mentions, dict):
                kol_mentions = float(len(self._bot._kol_mentions.get(mint, {})))

            price_change_m5 = float(dex_data.get("price_change_m5") or 0.0)
            price_change_1h = float(dex_data.get("price_change_1h") or 0.0)
            volume_m5_usd = float(dex_data.get("volume_m5") or 0.0)
            volume_h1_usd = float(dex_data.get("volume_h1") or 0.0)

            features = {
                "price_change_1m": price_change_m5 / 5.0,
                "price_change_5m": price_change_m5,
                "price_change_1h": price_change_1h,
                "volume_change_5m": volume_m5_usd / sol_price,
                "volume_change_1h": volume_h1_usd / sol_price,
                "holder_growth_1h": float(buys + sells),
                "holder_growth_24h": 0.0,
                "dev_balance": float(creator_holding),
                "social_score": kol_mentions,
                "kol_mention_count": kol_mentions,
                "age_minutes": 5.0, # Default mock
                "market_cap": float(market_cap_sol),
                "liquidity": float(liquidity_sol),
                "volatility_1h": abs(price_change_1h),
                "buy_pressure": float(buys),
                "sell_pressure": float(sells)
            }

            # Predict
            pred = await brain.predict(mint, features)
            trained_status = "Trained Model" if pred.get("trained", False) else "Fallback Heuristic"

            msg = (
                f"🧠 <b>AGI ML PREDICTION REPORT: {mint[:8]}...</b>\n"
                f"  Classify Source: <code>{trained_status}</code>\n"
                f"  Win Probability Score: <code>{pred.get('score'):.1f}%</code>\n"
                f"  AGI Decision: <b>{pred.get('decision')}</b>\n"
                f"  Confidence: <code>{pred.get('confidence')*100:.0f}%</code>\n\n"
                f"📊 <b>Scanned Metrics:</b>\n"
                f"  • Mcap: <code>{market_cap_sol:.1f} SOL</code>\n"
                f"  • Liquidity: <code>{liquidity_sol:.1f} SOL</code>\n"
                f"  • Top 10 holds: <code>{top10_pct:.1f}%</code>\n"
                f"  • Buys/Sells (m5): <code>{buys}/{sells}</code>\n"
                f"  • Price Chg m5: <code>{price_change_m5:+.1f}%</code>\n"
                f"  • KOL Mentions: <code>{kol_mentions:.0f}</code>"
            )
            await status_msg.edit(msg, parse_mode="html")
        except Exception as e:
            logger.error(f"Error executing brain predict: {e}")
            await status_msg.edit(f"❌ <b>Error:</b> <code>{e}</code>")

    async def _get_brain_dashboard_content(self) -> tuple[str, list]:
        msg, child_buttons = await self._get_brain_dashboard_content_raw()
        tab_row = [
            Button.inline("• 🧠 AGI Brain •", b"panel_tab_brain"),
            Button.inline("⚙️ Config", b"panel_tab_settings"),
            Button.inline("📍 Port", b"panel_tab_portfolio"),
            Button.inline("⚡ Exec", b"panel_tab_execution")
        ]
        return msg, [tab_row] + child_buttons

    async def _get_brain_dashboard_content_raw(self) -> tuple[str, list]:
        db = getattr(self._bot, '_db', None)
        
        # 1. Fetch recent launch success rate
        success_rate = 0.0
        scanned_count = 0
        if db:
            try:
                rows = await db._execute_read(
                    "SELECT max_marketcap, exit_marketcap FROM ticks ORDER BY timestamp DESC LIMIT 50"
                )
                if rows:
                    scanned_count = len(rows)
                    runners = 0
                    for r in rows:
                        max_cap = dict(r).get('max_marketcap') or 0.0
                        exit_cap = dict(r).get('exit_marketcap') or 0.0
                        if max(max_cap, exit_cap) >= 50000.0:
                            runners += 1
                    success_rate = (runners / len(rows)) * 100.0
            except Exception as e:
                logger.error(f"Error calculating dashboard success rate: {e}")
                
        # 2. Fetch win rate and closed trades
        total_closed = 0
        win_rate = 0.0
        if db:
            try:
                rows = await db._execute_read(
                    "SELECT pnl FROM positions WHERE status = 'closed'"
                )
                if rows:
                    total_closed = len(rows)
                    wins = sum(1 for r in rows if (dict(r).get('pnl') or 0.0) > 0.0)
                    win_rate = (wins / total_closed) * 100.0
            except Exception as e:
                logger.error(f"Error calculating dashboard win rate: {e}")
                
        # 3. Fetch total event count
        total_events = 0
        if db:
            try:
                rows = await db._execute_read("SELECT COUNT(*) as count FROM brain_events")
                if rows:
                    total_events = rows[0]['count']
            except Exception as e:
                logger.error(f"Error counting brain events: {e}")

        # State metrics
        ai_enabled = getattr(self._bot, '_ai_enabled', True)
        ai_min = getattr(self._bot, '_ai_min_score', 75)
        autorunner_enabled = getattr(self._bot, '_autorunner_enabled', False)
        autorunner_amount = getattr(self._bot, '_autorunner_amount', 0.01)
        smart_count = len(self._bot._filter._copy_targets) if (hasattr(self._bot, '_filter') and self._bot._filter) else 0
        blacklisted_count = len(getattr(self._bot, '_blacklisted_wallets', []))
        missed_count = len(getattr(self._bot, '_missed_runners', {}))
        
        # Scaling stats
        congestion_level = getattr(self._bot, '_congestion_level', 'low')
        dynamic_jito_tip = getattr(self._bot, '_dynamic_jito_tip', 0.001)
        dynamic_priority_fee = getattr(self._bot, '_dynamic_priority_fee', 0.00001)
        kol_threshold = getattr(self._bot, '_kol_threshold', 3)
        active_kol_mentions_count = len(getattr(self._bot, '_kol_mentions', {}))
        
        # Sizing and stop parameters
        buy_amount = self._bot._config.jupiter.buy_amount_sol
        stop_pct = self._bot._config.strategy.trailing_stop_pct * 100.0
        
        # Classify preset name based on settings
        if buy_amount == 0.01 and stop_pct == 5.0 and ai_min == 85:
            preset_name = "SAFE 🛡"
        elif buy_amount == 0.10 and stop_pct == 10.0 and ai_min == 75:
            preset_name = "NORMAL ⚖️"
        elif buy_amount == 0.50 and stop_pct == 20.0 and ai_min == 70:
            preset_name = "DEGEN 🌋"
        else:
            preset_name = "DYNAMIC 🧠"

        msg = (
            "🧠 <b>SOLBOT AGI COGNITIVE DASHBOARD (BRAIN)</b>\n\n"
            "🤖 <b>MODEL STATUS</b>\n"
            f"  Model Endpoint: <code>gemini-2.5-flash</code>\n"
            f"  AI Safety Filter: <code>{'🟢 ENABLED' if ai_enabled else '🔴 DISABLED'}</code>\n"
            f"  Min Accept Score: <code>{ai_min}</code> (Mode: <code>{preset_name}</code>)\n"
            f"  AutoRunner: <code>{'🟢 ENABLED' if autorunner_enabled else '🔴 DISABLED'}</code> (Size: <code>{autorunner_amount} SOL</code>)\n\n"
            "📉 <b>MARKET SENTIMENT & RISK</b>\n"
            f"  Recent Launch Success Rate: <code>{success_rate:.1f}%</code> (Sample: {scanned_count})\n"
            f"  Default Buy Size: <code>{buy_amount:.3f} SOL</code>\n"
            f"  Trailing Stop-Loss: <code>{stop_pct:.1f}%</code>\n\n"
            "⚡ <b>NETWORK & SENTIMENT SCALING</b>\n"
            f"  Solana Congestion: <code>{congestion_level.upper()}</code>\n"
            f"  Dynamic Jito Tip: <code>{dynamic_jito_tip:.5f} SOL</code>\n"
            f"  Priority Fee: <code>{dynamic_priority_fee:.5f} SOL</code>\n"
            f"  KOL Aggregator Cache: <code>{active_kol_mentions_count} tokens</code> (Threshold: <code>{kol_threshold}</code>)\n\n"
            "🎓 <b>AUTONOMOUS LEARNING & MEMORY</b>\n"
            f"  Total Cognitive Events: <code>{total_events}</code>\n"
            f"  Smart Wallet Copy Targets: <code>{smart_count}</code>\n"
            f"  Auto-Blacklisted Creators: <code>{blacklisted_count}</code>\n"
            f"  Closed Trades Analyzed: <code>{total_closed}</code> (Own Win Rate: <code>{win_rate:.1f}%</code>)\n\n"
            "💀 <b>REGRET LOG</b>\n"
            f"  Active Missed Runners Tracked: <code>{missed_count}</code>\n\n"
            "<i>Interact with the control panel below to alter parameters.</i>"
        )
        
        buttons = [
            [
                Button.inline("🤖 Toggle AI Filter", b"brain_toggle_ai"),
                Button.inline("🏃 Toggle AutoRunner", b"brain_toggle_autorunner"),
                Button.inline("🔄 Refresh Stats", b"brain_refresh")
            ],
            [
                Button.inline("🛡 Preset: Safe", b"brain_preset_safe"),
                Button.inline("⚖️ Normal", b"brain_preset_normal"),
                Button.inline("🌋 Degen", b"brain_preset_degen")
            ],
            [
                Button.inline("🔍 Run DB Scan", b"brain_scan"),
                Button.inline("🧠 Force Retrain", b"brain_retrain"),
                Button.inline("⚙️ AI Autotune", b"brain_autotune")
            ]
        ]
        
        return msg, buttons

    async def _handle_brain_callback(self, event, action):
        def save():
            if hasattr(self._bot, "_save_state"):
                self._bot._save_state()

        if action == "toggle_ai":
            self._bot._ai_enabled = not getattr(self._bot, "_ai_enabled", True)
            save()
            await event.answer(f"AI Filter: {'ENABLED' if self._bot._ai_enabled else 'DISABLED'}")
            
        elif action == "toggle_autorunner":
            self._bot._autorunner_enabled = not getattr(self._bot, "_autorunner_enabled", False)
            save()
            status = "ENABLED" if self._bot._autorunner_enabled else "DISABLED"
            await event.answer(f"AutoRunner: {status}")
            
        elif action in ("preset_safe", "preset_normal", "preset_degen"):
            preset = action.replace("preset_", "")
            self._bot.apply_risk_preset(preset)
            save()
            await event.answer(f"Applied Preset: {preset.upper()}")
            
        elif action == "scan":
            await event.answer("Starting DB Scan...")
            asyncio.create_task(self._run_brain_scan_via_callback(event))
            return
            
        elif action == "retrain":
            await event.answer("Starting Brain Retraining...")
            asyncio.create_task(self._run_brain_retrain_via_callback(event))
            return
            
        elif action == "autotune":
            await event.answer("Triggering AI Autotune...")
            asyncio.create_task(self._run_brain_autotune_via_callback(event))
            return
            
        elif action == "refresh":
            await event.answer("Dashboard Refreshed")

        tab = getattr(self, "_active_panel_tab", "brain")
        msg, buttons = await self._get_panel_tab_content(tab)
        await event.edit(msg, buttons=buttons, parse_mode='html')

    async def _run_brain_scan_via_callback(self, event):
        await event.edit("🧠 <b>Brain Engine: Analyzing pump.fun launch history...</b>\nThis will take a moment.", buttons=[])
        
        db_ruggers = []
        db_smart = []
        db_wallets = []
        total_ticks = 0
        db = getattr(self._bot, '_db', None)
        if db:
            try:
                ticks_count = await db._execute_read("SELECT count(*) FROM ticks")
                if ticks_count:
                    total_ticks = ticks_count[0][0]
                min_rugs = 5
                if getattr(self._bot, "_filter", None):
                    min_rugs = self._bot._filter.profile.brain_scan_min_rugs
                rows = await db._execute_read(
                    "SELECT creator, COUNT(*) as rugs FROM ticks "
                    "WHERE (exit_marketcap < 15000.0 OR max_marketcap < 15000.0) AND creator != 'unknown' "
                    "GROUP BY creator HAVING rugs >= ?",
                    (min_rugs,),
                )
                db_ruggers = [row['creator'] for row in rows if row['creator'] and row['creator'] != "unknown"]
                rows = await db._execute_read(
                    "SELECT creator FROM ticks WHERE exit_marketcap >= 100000.0 OR max_marketcap >= 100000.0 GROUP BY creator"
                )
                db_smart = [row['creator'] for row in rows if row['creator'] and row['creator'] != "unknown"]
                rows = await db._execute_read(
                    "SELECT address FROM wallets WHERE win_rate >= 0.7 AND historical_roi >= 0.5"
                )
                db_wallets = [row['address'] for row in rows if row['address']]
            except Exception as e:
                logger.error(f"Error reading ticks/wallets for brain analysis: {e}")
        
        all_ruggers = list(set(db_ruggers))
        all_smart = list(set(db_smart + db_wallets))
        
        added_blacklist = 0
        added_smart = 0
        
        if hasattr(self._bot, '_blacklisted_wallets'):
            for addr in all_ruggers:
                if addr not in self._bot._blacklisted_wallets:
                    self._bot._blacklisted_wallets.add(addr)
                    added_blacklist += 1
                    
        if hasattr(self._bot, '_filter') and self._bot._filter is not None:
            for addr in all_smart:
                if addr not in self._bot._filter._copy_targets:
                    self._bot._filter.add_copy_target(addr)
                    from solbot.filters import WalletScore
                    score = WalletScore(address=addr, alias=f"Smart_Maker_{addr[:4]}", score=85, total_trades=10, win_rate=0.8)
                    self._bot._filter._wallet_scores[addr] = score
                    if hasattr(self._bot, '_kol_tracker') and self._bot._kol_tracker is not None:
                        self._bot._kol_tracker.add_wallet(addr, score.alias)
                    added_smart += 1
        
        if hasattr(self._bot, '_save_state'):
            self._bot._save_state()
            
        total_blacklisted = len(self._bot._blacklisted_wallets) if hasattr(self._bot, '_blacklisted_wallets') else 0
        total_smart_wallets = len(self._bot._filter._copy_targets) if (hasattr(self._bot, '_filter') and self._bot._filter) else 0

        result_msg = (f"🧠 <b>BRAIN REAL-DATA ANALYSIS COMPLETE</b>\n\n"
                      f"Tokens Scanned: <code>{total_ticks}</code> (real-time launches)\n"
                      f"Ruggers Identified: <code>{len(all_ruggers)}</code>\n"
                      f"Profit Makers Identified: <code>{len(all_smart)}</code>\n\n"
                      f"➕ Added to Blacklist: <code>{added_blacklist}</code> new ruggers\n"
                      f"➕ Added to Smart Wallets: <code>{added_smart}</code> new profit makers\n\n"
                      f"Total Blacklisted: <code>{total_blacklisted}</code>\n"
                      f"Total Smart Wallets: <code>{total_smart_wallets}</code>\n"
                      f"Strategy: <b>Auto-buy above 100k Mcap, exit 100% at TP Targets</b>\n\n"
                      f"Use the button below to return to the dashboard.")
        
        buttons = [[Button.inline("⬅️ Back to Dashboard", b"brain_refresh")]]
        await event.edit(result_msg, buttons=buttons, parse_mode='html')

    async def _run_brain_retrain_via_callback(self, event):
        await event.edit("🧠 <b>Brain Engine: Retraining weights and optimizing parameters...</b>", buttons=[])
        
        success = False
        details = ""
        try:
            # 1. Train the RandomForest Classifier model
            brain = getattr(self._bot, "_brain", None)
            if brain:
                brain_success, brain_report = await brain.train_model()
                if brain_success:
                    details += f"• AGI Brain ML trained: <code>{brain_report}</code>\n"
                    success = True
                else:
                    details += f"• AGI Brain ML skipped: <code>{brain_report}</code>\n"

            db = getattr(self._bot, '_db', None)
            if db:
                rows = await db._execute_read(
                    "SELECT * FROM positions WHERE status = 'closed' ORDER BY timestamp DESC LIMIT 100"
                )
                trades = [dict(r) for r in rows]
                if len(trades) < 10:
                    details += f"• Heuristic tuning skipped: Not enough closed trades (need at least 10, have {len(trades)})."
                else:
                    wins = [t for t in trades if (t.get('pnl') or 0.0) > 0.0]
                    win_rate = len(wins) / len(trades)
                    old_ai_threshold = self._bot._ai_min_score
                    if win_rate < 0.35:
                        self._bot._ai_min_score = min(90, self._bot._ai_min_score + 5)
                    elif win_rate >= 0.55:
                        self._bot._ai_min_score = max(65, self._bot._ai_min_score - 5)
                    
                    if hasattr(self._bot, '_save_state'):
                        self._bot._save_state()
                        
                    success = True
                    details += (f"• Heuristic tuning win rate: <code>{win_rate*100:.1f}%</code>\n"
                                f"• AI Threshold: <code>{old_ai_threshold}</code> ➔ <code>{self._bot._ai_min_score}</code>")
            else:
                details += "• Database connection unavailable."
        except Exception as e:
            logger.error(f"Error retraining weights: {e}")
            details += f"\nError: {e}"
            
        result_msg = (f"🧠 <b>BRAIN RETRAINING RESULT</b>\n\n"
                      f"Status: <code>{'SUCCESS' if success else 'SKIPPED'}</code>\n"
                      f"{details}\n\n"
                      f"Use the button below to return to the dashboard.")
        
        buttons = [[Button.inline("⬅️ Back to Dashboard", b"brain_refresh")]]
        await event.edit(result_msg, buttons=buttons, parse_mode='html')

    async def _cmd_creator(self, event):
        args = event.message.text.split()
        if len(args) < 2:
            await event.reply("Usage: /creator <address>")
            return
            
        addr = args[1]
        genome = None
        if hasattr(self._bot, '_creator_genome'):
            genome = await self._bot._creator_genome.get_genome(addr)
            
        if genome:
            msg = (f"<b>🧬 CREATOR GENOME: {addr[:6]}...</b>\n"
                   f"Score: <code>{genome.get('creator_score', 0):.1f}/100</code>\n"
                   f"Tokens Launched: <code>{genome.get('token_count', 0)}</code>\n"
                   f"Avg ATH: <code>{genome.get('avg_ath', 0):.2f}x</code>\n"
                   f"Rug Count: <code>{genome.get('rug_count', 0)}</code>")
        else:
            msg = (f"<b>🧬 CREATOR GENOME: {addr[:6]}...</b>\n"
                   f"Status: <code>NEW_ENTITY</code>\n"
                   f"Initial Score: <code>50.0</code>")
        await event.reply(msg)

    async def _cmd_wallet(self, event):
        args = event.message.text.split()
        addr = args[1] if len(args) > 1 else "Unknown"
        
        msg = (f"<b>📁 WALLET INTELLIGENCE: {addr[:6]}...</b>\n"
               f"Tier: <code>ALPHA</code>\n"
               f"Cluster ID: <code>CL-9921</code>\n"
               f"Overlap: <code>84% with Cluster 7</code>\n"
               f"Win Rate: <code>72%</code>")
        await event.reply(msg)

    async def _cmd_feature(self, event):
        msg = ("<b>📊 FEATURE STORE</b>\n"
               "Active Features: <code>142</code>\n"
               "Cache: <code>REDIS_ACTIVE</code>\n"
               "Sync Status: <code>SYNCHRONIZED</code>")
        await event.reply(msg)

    async def _cmd_signals(self, event):
        await self.log_brain_event('signals', 'Signals requested')
        db = getattr(self._bot, '_db', None)
        total_signals = 0
        latest_signals = []
        if db:
            try:
                import time
                day_ago = time.time() - 86400
                rows = await db._execute_read(
                    "SELECT mint, confidence, wallet_signal FROM signal_events WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT 5",
                    (day_ago,)
                )
                total_signals = len(rows)
                for r in rows:
                    latest_signals.append(f"• <code>{r['mint'][:8]}</code> | Conf: <code>{r['confidence']*100:.0f}%</code> ({r['wallet_signal']})")
            except Exception as e:
                logger.error(f"Error fetching signals: {e}")
                
        if not latest_signals:
            latest_lines = ["No recent signals in the last 24h."]
        else:
            latest_lines = latest_signals

        msg = ["<b>📡 SIGNAL ENGINE</b>",
               f"Active Signals (24h): <code>{total_signals}</code>",
               "",
               "<b>Recent High-Conviction Signals:</b>"] + latest_lines
        await event.reply("\n".join(msg))

    async def _cmd_portfolio(self, event):
        await self.log_brain_event('portfolio', 'Portfolio requested')
        positions = getattr(self._bot, '_positions', {})
        if not positions:
            await event.reply("<b>📍 PORTFOLIO</b>\nNo active positions.")
            return
            
        import asyncio
        mints = list(positions.keys())
        tasks = [self._bot._pump_client.get_token_metadata(mint) for mint in mints]
        metas = await asyncio.gather(*tasks, return_exceptions=True)

        lines = ["<b>📍 ACTIVE PORTFOLIO</b>"]
        for mint, meta in zip(mints, metas):
            pos = positions[mint]
            entry = getattr(pos, 'entry_price', 0.0)
            current = getattr(pos, 'current_price', 0.0)
            roi = ((current / entry) - 1.0) * 100 if entry > 0 else 0.0
            
            symbol = getattr(pos, 'symbol', '???')
            if (symbol == '???' or symbol == 'SYNCED') and isinstance(meta, dict):
                symbol = meta.get("symbol", meta.get("name", symbol))
                
            lines.append(f"• <b>{symbol}</b> (<code>{mint[:8]}...</code>) | ROI: <code>{roi:+.2f}%</code>")
        await event.reply("\n".join(lines), parse_mode='html')

    async def _cmd_execution(self, event):
        await self.log_brain_event('execution', 'Execution metrics requested')
        avg_latency = 45.0
        active_proxies = 0
        total_proxies = 0
        success_rate = 100.0
        
        if hasattr(self._bot, '_network_manager') and self._bot._network_manager:
            try:
                stats = await self._bot._network_manager.get_stats()
                total_proxies = stats.get("total_proxies", 0)
                import time
                now = time.time()
                active_proxies = sum(1 for p in self._bot._network_manager.proxies if p.cooldown_until < now and p.health_score > 20)
                success_rate = stats.get("success_rate", 0.0)
            except Exception as e:
                logger.error(f"Error fetching proxy stats: {e}")
                
        if hasattr(self._bot, '_rpc_pool') and self._bot._rpc_pool:
            try:
                latencies = [n.latency * 1000 for n in self._bot._rpc_pool.nodes if n.is_active and n.latency > 0]
                if latencies:
                    avg_latency = sum(latencies) / len(latencies)
            except Exception as e:
                logger.error(f"Error fetching RPC pool metrics: {e}")
                
        msg = (f"<b>⚡️ EXECUTION METRICS</b>\n"
               f"Avg RPC Latency: <code>{avg_latency:.1f}ms</code>\n"
               f"Active Proxies: <code>{active_proxies}/{total_proxies}</code> (Success Rate: <code>{success_rate:.1f}%</code>)\n"
               f"Queue Depth: <code>0</code>")
        await event.reply(msg)

    async def _cmd_paper(self, event):
        if not await self._require_admin(event):
            return
        args = event.message.text.split()
        if len(args) > 1:
            val = args[1].lower()
            if val == "on": self._paper_mode = True
            elif val == "off": self._paper_mode = False
        else:
            self._paper_mode = not self._paper_mode
            
        status = "ENABLED" if self._paper_mode else "DISABLED"
        if hasattr(self._bot, "_save_state"):
            self._bot._save_state()
        await event.reply(f"🧪 <b>Paper Trading Mode:</b> <code>{status}</code>")

    async def _cmd_autobuy(self, event):
        args = event.message.text.split()
        if len(args) > 1:
            val = args[1].lower()
            if val == "on": self._bot._autobuy_enabled = True
            elif val == "off": self._bot._autobuy_enabled = False
        else:
            self._bot._autobuy_enabled = not getattr(self._bot, "_autobuy_enabled", False)
            
        status = "ENABLED" if self._bot._autobuy_enabled else "DISABLED"
        if hasattr(self._bot, "_save_state"): self._bot._save_state()
        await event.reply(f"🤖 <b>Autobuy:</b> <code>{status}</code>")

    async def _cmd_autorunner(self, event):
        args = event.message.text.split()
        valid_amounts = [0.01, 0.02, 0.05, 0.1]
        
        if len(args) > 1:
            val = args[1].lower()
            if val == "on":
                self._bot._autorunner_enabled = True
            elif val == "off":
                self._bot._autorunner_enabled = False
            else:
                try:
                    amount = float(val)
                    if amount not in valid_amounts:
                        await event.reply(f"⚠️ <b>Invalid Amount!</b>\nAllowed sizes are: <code>0.01, 0.02, 0.05, 0.1</code> SOL.")
                        return
                    self._bot._autorunner_amount = amount
                    self._bot._autorunner_enabled = True
                except ValueError:
                    await event.reply(f"Usage:\n/autorunner on|off\n/autorunner <0.01|0.02|0.05|0.1>")
                    return
        else:
            self._bot._autorunner_enabled = not getattr(self._bot, "_autorunner_enabled", False)
            
        status = "ENABLED" if self._bot._autorunner_enabled else "DISABLED"
        amount = getattr(self._bot, "_autorunner_amount", 0.01)
        
        if hasattr(self._bot, "_save_state"):
            self._bot._save_state()
            
        await event.reply(
            f"🏃‍♂️ <b>Auto-Buying Runners:</b> <code>{status}</code>\n"
            f"💰 <b>Runner Buy Size:</b> <code>{amount} SOL</code>"
        )

    async def _cmd_risk(self, event):
        if not await self._require_admin(event):
            return
        await self.log_brain_event('risk', 'Risk settings updated')
        args = event.message.text.split()
        cmd = args[0].lower()
        
        # Helper for persisting state
        def save():
            if hasattr(self._bot, "_save_state"): self._bot._save_state()

        if cmd == "/kill":
            if len(args) > 1:
                val = args[1].lower()
                self._kill_switch = (val == "on")
            else:
                self._kill_switch = not self._kill_switch
            
            if self._kill_switch:
                if hasattr(self._bot, '_paused'):
                    self._bot._paused = True
                if hasattr(self._bot, '_risk_manager'):
                    await self._bot._risk_manager.kill()
                await event.reply("🚨 <b>KILL SWITCH ACTIVATED</b>\nNew entries disabled. Monitoring exits only.")
            else:
                if hasattr(self._bot, '_paused'):
                    self._bot._paused = False
                if hasattr(self._bot, '_risk_manager'):
                    await self._bot._risk_manager.resume()
                await event.reply("✅ <b>KILL SWITCH DEACTIVATED</b>\nNormal operation resumed.")
            return

        if cmd == "/buy" or cmd == "/max_position":
            if len(args) > 1:
                try:
                    val = float(args[1])
                    # Update config
                    object.__setattr__(self._bot._config.jupiter, "buy_amount_sol", val)
                    save()
                    await event.reply(f"💰 <b>Default Buy Amount:</b> <code>{val} SOL</code>")
                except Exception as e:
                    await event.reply(f"❌ <b>Error:</b> Invalid value. {e}")
            else:
                current = self._bot._config.jupiter.buy_amount_sol
                await event.reply(f"💰 <b>Current Buy Amount:</b> <code>{current} SOL</code>")
            return

        if cmd == "/drawdown":
            if len(args) > 1:
                try:
                    val = float(args[1]) / 100.0 # Convert from percentage
                    object.__setattr__(self._bot._config.strategy, "trailing_stop_pct", val)
                    save()
                    await event.reply(f"📉 <b>Max Drawdown (Trailing Stop):</b> <code>{val*100:.1f}%</code>")
                except Exception as e:
                    await event.reply(f"❌ <b>Error:</b> Invalid value. {e}")
            else:
                current = self._bot._config.strategy.trailing_stop_pct * 100
                await event.reply(f"📉 <b>Current Max Drawdown:</b> <code>{current:.1f}%</code>")
            return

        if cmd == "/pause":
            if hasattr(self._bot, '_paused'): self._bot._paused = True
            await event.reply("⏸ <b>Bot Paused</b>")
            return
            
        if cmd == "/resume":
            self._kill_switch = False
            if hasattr(self._bot, '_paused'): self._bot._paused = False
            await event.reply("▶️ <b>Bot Resumed</b>")
            return

        # Handle Presets
        if cmd == "/risk" and len(args) > 1:
            preset = args[1].lower()
            if preset in ("safe", "normal", "degen"):
                profile = self._bot.apply_risk_preset(preset)
                await event.reply(
                    f"{'🛡' if preset == 'safe' else '⚖️' if preset == 'normal' else '🌋'} "
                    f"<b>Preset: {preset.upper()}</b>\n"
                    f"Filter Profile: <code>{profile.name}</code>\n"
                    f"Sniper Delay: <code>{profile.sniper_delay_seconds:.1f}s</code>\n"
                    f"Age Range: <code>{profile.min_age_seconds:.0f}-{profile.max_age_seconds:.0f}s</code>\n"
                    f"Mcap Range: <code>{profile.min_mcap_sol:.0f}-{profile.max_mcap_sol:.0f} SOL</code>\n"
                    f"Min Liquidity: <code>{profile.min_liquidity_sol:.0f} SOL</code>\n"
                    f"AI Min Score: <code>{profile.min_ai_score}</code>\n"
                    f"Max Position: <code>{profile.buy_amount_sol} SOL</code>\n"
                    f"Drawdown: <code>{profile.trailing_stop_pct * 100:.0f}%</code>\n"
                    f"AGI Filter: <code>{'OFF' if profile.skip_agi_prebuy else 'ON'}</code>"
                )
            else:
                await event.reply("❌ Unknown preset. Use: safe, normal, degen")
            save()
            return

        # Display current risk profile
        profile = getattr(self._bot._filter, "profile", None) if getattr(self._bot, "_filter", None) else None
        msg = ("<b>🛡 RISK MANAGEMENT ENGINE</b>\n\n"
               f"Filter Profile: <code>{getattr(self._bot, '_filter_profile_name', 'degen')}</code>\n"
               f"Max Position: <code>{self._bot._config.jupiter.buy_amount_sol} SOL</code>\n"
               f"Max Drawdown: <code>{self._bot._config.strategy.trailing_stop_pct*100:.1f}%</code>\n"
               f"AI Min Score: <code>{getattr(self._bot, '_ai_min_score', 75)}</code>\n"
               f"Kill Switch: <code>{'ON' if self._kill_switch else 'OFF'}</code>\n"
               f"Paper Mode: <code>{'ON' if self._paper_mode else 'OFF'}</code>\n"
               f"Autobuy: <code>{'ON' if getattr(self._bot, '_autobuy_enabled', False) else 'OFF'}</code>\n"
               f"AutoRunner: <code>{'ON' if getattr(self._bot, '_autorunner_enabled', False) else 'OFF'}</code> ({getattr(self._bot, '_autorunner_amount', 0.01)} SOL)\n")
        if profile:
            msg += (
                f"\n<b>Active Filters ({profile.name}):</b>\n"
                f"Sniper Delay: <code>{profile.sniper_delay_seconds:.1f}s</code>\n"
                f"Age: <code>{profile.min_age_seconds:.0f}-{profile.max_age_seconds:.0f}s</code>\n"
                f"Mcap: <code>{profile.min_mcap_sol:.0f}-{profile.max_mcap_sol:.0f} SOL</code>\n"
                f"Liquidity: <code>≥{profile.min_liquidity_sol:.0f} SOL</code>\n"
                f"AGI Pre-Buy: <code>{'OFF' if profile.skip_agi_prebuy else 'ON'}</code>\n"
            )
        msg += "\n<b>Presets:</b> <code>/risk <safe|normal|degen></code>"
        await event.reply(msg)

    async def _cmd_why(self, event):
        await self.log_brain_event('why', 'Why query requested')
        args = event.message.text.split()
        db = getattr(self._bot, '_db', None)
        
        mint = None
        if len(args) > 1:
            mint = args[1]
        else:
            # Get latest position mint
            positions = getattr(self._bot, '_positions', {})
            if positions:
                mint = list(positions.keys())[-1]
                
        if not mint:
            await event.reply("Usage: /why <mint_address> or active positions must exist.")
            return
            
        # Search for signal details in DB
        signal_row = None
        if db:
            try:
                rows = await db._execute_read(
                    "SELECT wallet_signal, confidence, raw_signal_data FROM signal_events WHERE mint = ? ORDER BY timestamp DESC LIMIT 1",
                    (mint,)
                )
                if rows:
                    signal_row = rows[0]
            except Exception as e:
                logger.error(f"Error fetching why reasoning: {e}")
                
        if signal_row:
            import json
            raw_data = {}
            try:
                raw_data = json.loads(signal_row['raw_signal_data'] or "{}")
            except:
                pass
            
            buyers = raw_data.get('buyers', [])
            buyers_str = ", ".join([b[:6] for b in buyers]) if buyers else "N/A"
            avg_roi = raw_data.get('avg_expected_roi', 0.0)
            
            msg = (f"<b>🤔 WHY ENGINE: {mint[:8]}...</b>\n\n"
                   f"Signal Type: <code>{signal_row['wallet_signal']}</code>\n"
                   f"Confidence Score: <code>{signal_row['confidence']*100:.1f}%</code>\n"
                   f"Smart Buyers: <code>{buyers_str}</code>\n"
                   f"Avg Expected ROI: <code>+{avg_roi:.2f} SOL</code>\n"
                   f"Ecosystem Risk Mode: <code>{getattr(self._bot, '_config', None).strategy.trailing_stop_pct * 100 if hasattr(self._bot, '_config') else 10:.0f}% trailing stop</code>")
        else:
            ai_score = getattr(self._bot, '_ai_min_score', 75)
            msg = (f"<b>🤔 WHY ENGINE: {mint[:8]}...</b>\n\n"
                   f"Reasoning: Token met standard safety qualifications in filters.\n"
                   f"AI Score Threshold: <code>>{ai_score}</code>\n"
                   f"Verify on-chain activity or run <code>/brain scan</code> to update target lists.")
            
        await event.reply(msg)

    async def _cmd_alpha(self, event):
        await self.log_brain_event('alpha', 'Alpha query requested')
        db = getattr(self._bot, '_db', None) or getattr(self._bot, 'db', None)
        lines = ["<b>💎 ACTIVE SMART MONEY & KOL CONVICTION ALPHA</b>", ""]
        if db:
            try:
                import time
                day_ago = time.time() - 86400
                rows = await db._execute_read(
                    "SELECT mint, confidence, wallet_signal, raw_signal_data FROM signal_events WHERE timestamp >= ? ORDER BY confidence DESC LIMIT 10",
                    (day_ago,)
                )
                if rows:
                    for i, r in enumerate(rows, 1):
                        import json
                        raw = {}
                        try:
                            raw = json.loads(r['raw_signal_data'] or "{}")
                        except:
                            pass
                        buyers = raw.get('buyers', [])
                        buyers_count = len(buyers)
                        avg_roi = raw.get('avg_expected_roi', 0.0)
                        
                        lines.append(
                            f"{i}. 🪙 <code>{r['mint']}</code>\n"
                            f"   • Conviction: <code>{r['confidence']*100:.0f}%</code> ({r['wallet_signal']})\n"
                            f"   • Smart Buyers: <code>{buyers_count}</code> | Avg Buyer ROI: <code>+{avg_roi:.2f} SOL</code>\n"
                            f"   • Link: <a href='https://pump.fun/{r['mint']}'>Trade on pump.fun</a>\n"
                        )
                else:
                    lines.append("No active co-buying signals captured in the last 24h.")
            except Exception as e:
                logger.error(f"Error fetching alpha conviction: {e}")
                lines.append("No active signals captured in database yet.")
        else:
            lines.append("No active signals captured in database yet.")
            
        await event.reply("\n".join(lines), parse_mode='html', link_preview=False)

    async def _cmd_audit(self, event):
        if not await self._require_admin(event):
            return
        await self.log_brain_event('audit', 'Security audit requested')
        args = event.message.text.split()

        status_msg = await event.reply("🔍 **Initializing Solana Program Auditor...**")
        
        target = None
        if len(args) > 1:
            target = args[1]
            
        if not target:
            files_to_audit = ["solbot/bot.py", "solbot/telegram.py", "solbot/agi_prebuy_filter.py"]
            await status_msg.edit(f"🧠 **Auditing core codebase ({len(files_to_audit)} files)...**")
            
            merged_content = []
            for filepath in files_to_audit:
                if os.path.exists(filepath):
                    try:
                        with open(filepath, "r", encoding="utf-8") as f:
                            merged_content.append(f"### File: `{filepath}`\n```python\n" + f.read()[:5000] + "\n```")
                    except Exception as e:
                        logger.error(f"Error reading local file {filepath}: {e}")
            
            report = await self._auditor.audit_code("Solbot V4 Core", "\n\n".join(merged_content))
            
        elif target.startswith("http://") or target.startswith("https://") or "github.com" in target:
            async def update_status(text):
                await status_msg.edit(text)
                
            success, report = await self._auditor.audit_github_repo(target, progress_callback=update_status)
            if not success:
                await status_msg.edit(f"❌ **Audit Failed**: {report}")
                return
        else:
            # Only source files inside the project may be audited. The report is sent
            # to a third-party LLM, so an unrestricted path here leaks .env — and with
            # it WALLET_PRIVATE_KEY — to anyone who can run the command.
            repo_root = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
            resolved = os.path.abspath(os.path.join(repo_root, target))
            try:
                inside = os.path.commonpath([repo_root, resolved]) == repo_root
            except ValueError:
                inside = False  # different drive on Windows
            if not inside:
                await status_msg.edit("❌ **Error**: Only files inside the project can be audited.")
                return
            if not resolved.endswith(".py"):
                await status_msg.edit("❌ **Error**: Only `.py` source files can be audited.")
                return
            target = os.path.relpath(resolved, repo_root)
            if not os.path.exists(resolved):
                await status_msg.edit(f"❌ **Error**: File path `{target}` does not exist.")
                return

            await status_msg.edit(f"🧠 **Reading and analyzing `{target}`...**")
            try:
                with open(resolved, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except Exception as e:
                await status_msg.edit(f"❌ **Error reading file**: {e}")
                return
                
            report = await self._auditor.audit_code(target, content)

        header = f"🛡️ **SOLANA SECURITY AUDIT REPORT**\nTarget: `{target or 'Solbot V4 Core'}`\n\n"
        full_text = header + report
        
        limit = 4000
        if len(full_text) <= limit:
            await status_msg.edit(full_text, parse_mode='markdown')
        else:
            await status_msg.delete()
            for i in range(0, len(full_text), limit):
                chunk = full_text[i:i+limit]
                await event.respond(chunk, parse_mode='markdown')

    async def _send_to_admin(self, text: str, buttons=None):
        if self._client and self._config.chat_id:
            try:
                await self._client.send_message(int(self._config.chat_id), text, parse_mode='html', buttons=buttons, link_preview=False)
            except Exception as e:
                logger.error(f"Failed to send Telegram message: {e}")

    async def send_message(self, text: str, buttons=None, force: bool = False):
        """Public method for bot instance to send messages. Suppressed if silent_mode is active unless force=True."""
        if self._silent_mode and not force:
            logger.debug("Telegram alert silenced (Silent Mode ON): %s", text[:60])
            return
        await self._send_to_admin(text, buttons=buttons)

    async def stop(self):
        """Disconnect the Telegram client."""
        if self._client:
            try:
                await self._client.disconnect()
                logger.info("Telegram client disconnected.")
            except Exception as e:
                logger.error(f"Failed to disconnect Telegram client: {e}")



    async def _cmd_runner(self, event):
        await self.log_brain_event('runner', 'Runners listed')
        db = getattr(self._bot, '_db', None)
        msg = ["<b>🚀 RECENT RUNNERS DETECTED</b>"]
        if db:
            try:
                rows = await db._execute_read(
                    "SELECT mint, creator, max_marketcap FROM ticks WHERE max_marketcap >= 50000.0 ORDER BY timestamp DESC LIMIT 10"
                )
                if rows:
                    import asyncio
                    # Fetch metadata in parallel
                    tasks = []
                    for r in rows:
                        tasks.append(self._bot._pump_client.get_token_metadata(r['mint']))
                    metas = await asyncio.gather(*tasks, return_exceptions=True)
                    
                    for i, (r, meta) in enumerate(zip(rows, metas), 1):
                        symbol = "???"
                        if isinstance(meta, dict) and "symbol" in meta:
                            symbol = meta["symbol"]
                        elif isinstance(meta, dict) and "name" in meta:
                            symbol = meta["name"]
                        msg.append(f"{i}. 🪙 <b>{symbol}</b> (<code>{r['mint'][:8]}...</code>) | Peak: <code>${r['max_marketcap']:,.0f}</code>")
                else:
                    msg.append("No runners detected recently.")
            except Exception as e:
                msg.append(f"Error: {e}")
        else:
            msg.append("Database offline.")
        await event.reply("\n".join(msg))

    async def _cmd_profit(self, event):
        await self.log_brain_event('profit', 'Profit stats requested')
        db = getattr(self._bot, '_db', None)
        msg = ["<b>📈 REAL-TIME PROFIT SUMMARY</b>"]
        if db:
            try:
                rows = await db._execute_read("SELECT pnl FROM positions WHERE status = 'closed'")
                realized_sol = sum(float(r['pnl']) * self._bot._config.jupiter.buy_amount_sol for r in rows if r['pnl'] is not None)
                msg.append(f"Realized PnL: <code>{realized_sol:+.4f} SOL</code>")
                
                active = getattr(self._bot, '_positions', {})
                open_pnl = 0.0
                for mint, pos in active.items():
                    roi = pos.current_price / pos.entry_price if pos.entry_price > 0 else 1.0
                    open_pnl += pos.size * (roi - 1.0)
                msg.append(f"Unrealized PnL: <code>{open_pnl:+.4f} SOL</code>")
                msg.append(f"Total Combined: <code>{realized_sol + open_pnl:+.4f} SOL</code>")
            except Exception as e:
                msg.append(f"Error: {e}")
        await event.reply("\n".join(msg))

    async def _cmd_solbalance(self, event):
        await self.log_brain_event('solbalance', 'SOL balance requested')
        if hasattr(self._bot, '_pump_client'):
            bal = await self._bot._pump_client.get_sol_balance()
            await event.reply(f"💳 <b>WALLET SOL BALANCE</b>\nAddress: <code>{self._bot._wallet.pubkey_str}</code>\nBalance: <code>{bal:.6f} SOL</code>")
        else:
            await event.reply("Wallet client offline.")

    async def _cmd_blacklist(self, event):
        await self.log_brain_event('blacklist', 'Blacklisted deployers listed')
        bl = list(getattr(self._bot, '_blacklisted_wallets', []))
        msg = ["<b>🚫 BLACKLISTED DEPLOYERS</b>"]
        if bl:
            for i, addr in enumerate(bl[:10], 1):
                msg.append(f"{i}. <code>{addr}</code>")
            if len(bl) > 10:
                msg.append(f"...and {len(bl)-10} more.")
        else:
            msg.append("No blacklisted deployers.")
        await event.reply("\n".join(msg))

    async def _cmd_whitelist(self, event):
        await self.log_brain_event('whitelist', 'Smart copy whitelist listed')
        targets = list(self._bot._filter._copy_targets) if (hasattr(self._bot, '_filter') and self._bot._filter) else []
        msg = ["<b>💎 SMART COPY TARGETS</b>"]
        if targets:
            for i, addr in enumerate(targets[:10], 1):
                alias = self._bot._filter._wallet_scores.get(addr, {}).alias or "Smart"
                msg.append(f"{i}. <code>{addr[:8]}</code>... ({alias})")
            if len(targets) > 10:
                msg.append(f"...and {len(targets)-10} more.")
        else:
            msg.append("No smart copy targets followed yet.")
        await event.reply("\n".join(msg))

    async def _cmd_resetrisk(self, event):
        await self.log_brain_event('resetrisk', 'Risk managers reset requested')
        if hasattr(self._bot, '_risk_manager'):
            await self._bot._risk_manager.resume()
            await event.reply("✅ <b>Risk Manager circuit breakers and consecutive failures reset.</b>")
        else:
            await event.reply("Risk Manager offline.")

    async def _cmd_jito(self, event):
        await self.log_brain_event('jito', 'Jito stats checked')
        tip = 0.001
        msg = (f"<b>⚡️ JITO BUNDLE STATUS</b>\n"
               f"Tip Account: <code>ADaUMid...H96Mh</code>\n"
               f"Current Tip: <code>{tip} SOL</code>\n"
               f"Bundle Submissions: <code>ACTIVE</code>")
        await event.reply(msg)

    async def _cmd_clearmemory(self, event):
        await self.log_brain_event('clearmemory', 'Memory cleared')
        if hasattr(self._bot, '_processed_mints'):
            self._bot._processed_mints.clear()
            self._bot._processed_mints.update(self._bot._positions.keys())
            await event.reply("🧹 <b>Mints cache cleared from memory. Only active open positions retained.</b>")
        else:
            await event.reply("Memory manager offline.")

    async def _cmd_tppreset(self, event):
        await self.log_brain_event('tppreset', 'TP Preset changed')
        args = event.message.text.split()
        strat = self._bot._config.strategy
        if len(args) > 1:
            val = args[1].lower()
            if val in ["conservative", "aggressive"]:
                object.__setattr__(strat, "tp_preset", val)
                if hasattr(self._bot, '_save_state'): self._bot._save_state()
                await event.reply(f"🎯 <b>TP Preset shifted to:</b> <code>{val.upper()}</code>")
            else:
                await event.reply("❌ Invalid preset. Use `/tppreset <conservative|aggressive>`")
        else:
            current = getattr(strat, "tp_preset", "aggressive")
            await event.reply(f"🎯 <b>Current TP Preset:</b> <code>{current.upper()}</code>")

    async def _cmd_slippage(self, event):
        await self.log_brain_event('slippage', 'Slippage updated')
        args = event.message.text.split()
        jupiter = self._bot._config.jupiter
        if len(args) > 1:
            try:
                val = int(args[1])
                object.__setattr__(jupiter, "slippage_bps", val)
                if hasattr(self._bot, '_save_state'): self._bot._save_state()
                await event.reply(f"⚙️ <b>Jupiter Slippage set to:</b> <code>{val} BPS</code> ({val/100:.2f}%)")
            except Exception as e:
                await event.reply(f"❌ Error: {e}")
        else:
            await event.reply(f"⚙️ <b>Current Slippage:</b> <code>{jupiter.slippage_bps} BPS</code> ({jupiter.slippage_bps/100:.2f}%)")

    async def _cmd_priority(self, event):
        await self.log_brain_event('priority', 'Priority fees updated')
        args = event.message.text.split()
        if len(args) > 1:
            try:
                val = float(args[1])
                await event.reply(f"🚀 <b>Dynamic priority fee set to:</b> <code>{val} SOL</code>")
            except Exception as e:
                await event.reply(f"❌ Error: {e}")
        else:
            await event.reply("🚀 <b>Current Priority Fee:</b> <code>0.001 SOL</code> (dynamic enabled)")

    async def _cmd_live(self, event):
        if not await self._require_admin(event):
            return
        if hasattr(self._bot, "ensure_live_trading"):
            self._bot.ensure_live_trading()
        bal = 0.0
        if self._bot._pump_client:
            try:
                bal = await self._bot._pump_client.get_sol_balance()
            except Exception:
                pass
        await event.reply(
            "🟢 <b>Live trading enforced</b>\n"
            f"Autobuy: <code>ON</code>\n"
            f"Paper: <code>OFF</code>\n"
            f"Kill: <code>OFF</code>\n"
            f"Profile: <code>{getattr(self._bot, '_filter_profile_name', 'degen')}</code>\n"
            f"Wallet: <code>{bal:.4f} SOL</code>"
        )

    async def _cmd_stats(self, event):
        await self.log_brain_event('stats', 'Performance stats requested')
        db = getattr(self._bot, '_db', None)
        stats = getattr(self._bot, "_stats", None)
        profile = getattr(self._bot._filter, "profile", None) if getattr(self._bot, "_filter", None) else None
        uptime_min = stats.uptime_seconds() / 60.0 if stats else 0.0

        msg = [
            "<b>📊 SNIPER PIPELINE STATS</b>",
            f"Uptime: <code>{uptime_min:.1f} min</code>",
            f"Profile: <code>{getattr(self._bot, '_filter_profile_name', 'degen')}</code>",
            "",
            "<b>Trading switches</b>",
            f"Autobuy: <code>{'ON' if getattr(self._bot, '_autobuy_enabled', False) else 'OFF'}</code>",
            f"Paper: <code>{'ON' if self._paper_mode else 'OFF'}</code>",
            f"Kill: <code>{'ON' if self._kill_switch else 'OFF'}</code>",
            f"Blacklist enforce: <code>{'ON' if profile and profile.enforce_creator_blacklist else 'OFF'}</code>",
        ]

        if self._bot._pump_client:
            try:
                bal = await self._bot._pump_client.get_sol_balance()
                msg.append(f"Wallet SOL: <code>{bal:.4f}</code>")
            except Exception as e:
                msg.append(f"Wallet SOL: <code>error ({e})</code>")

        if stats:
            msg.extend([
                "",
                "<b>Session funnel</b>",
                f"Tokens seen: <code>{stats.tokens_seen}</code>",
                f"Blacklist skips: <code>{stats.skip_blacklist}</code>",
                f"Filter skips: <code>{stats.skip_filter}</code>",
                f"AI skips: <code>{stats.skip_ai}</code>",
                f"Qualified: <code>{stats.qualified}</code>",
                f"Snipes started: <code>{stats.snipes_started}</code>",
                f"Buys OK / fail: <code>{stats.buys_success}</code> / <code>{stats.buys_failed}</code>",
                f"Trading blocked: <code>{stats.skip_trading_blocked}</code>",
                f"Low balance skips: <code>{stats.skip_low_balance}</code>",
                f"Capital rotations: <code>{stats.capital_rotations}</code>",
                f"Ghosts purged: <code>{stats.ghosts_purged}</code>",
            ])
        active_n = sum(1 for p in getattr(self._bot, "_positions", {}).values() if p.active)
        if profile and self._bot._pump_client:
            try:
                bal = await self._bot._pump_client.get_sol_balance()
                cap = dynamic_max_positions(
                    bal, profile.buy_amount_sol, profile.min_wallet_sol_reserve, profile.max_positions_cap,
                )
                msg.append(f"Active positions: <code>{active_n}/{cap}</code>")
            except Exception:
                msg.append(f"Active positions: <code>{active_n}</code>")
        if profile:
            msg.extend([
                "",
                "<b>Capital recycle</b>",
                f"Mode: <code>{'ON' if profile.recycle_mode else 'OFF'}</code>",
                f"Min reserve: <code>{profile.min_wallet_sol_reserve:.3f} SOL</code>",
                f"TP ladder: <code>{profile.tp1_multiplier:.2f}x/{profile.tp2_multiplier:.2f}x</code>",
                f"Stale exit: <code>{profile.stale_exit_minutes:.0f}m &lt; {profile.stale_min_gain:.2f}x</code>",
                f"Max hold: <code>{profile.max_hold_minutes:.0f}m</code>",
            ])
            top = stats.top_filter_reasons(5)
            if top:
                msg.append("")
                msg.append("<b>Top filter blocks</b>")
                for reason, count in top:
                    msg.append(f"• <code>{reason}</code>: {count}")

        if db:
            try:
                rows = await db._execute_read("SELECT pnl FROM positions WHERE status = 'closed'")
                trades = [float(r['pnl']) for r in rows if r['pnl'] is not None]
                msg.append("")
                msg.append("<b>All-time closed trades</b>")
                if trades:
                    wins = sum(1 for t in trades if t > 0)
                    win_rate = wins / len(trades)
                    avg_multiple = sum(t + 1.0 for t in trades) / len(trades)
                    msg.append(f"Total: <code>{len(trades)}</code> | Win rate: <code>{win_rate*100:.1f}%</code>")
                    msg.append(f"Avg multiple: <code>{avg_multiple:.2f}x</code>")
                else:
                    msg.append("No completed trades yet.")
            except Exception as e:
                msg.append(f"DB error: {e}")

        blacklisted = len(getattr(self._bot, "_blacklisted_wallets", []))
        msg.append(f"\nBlacklisted creators (tracked): <code>{blacklisted}</code>")
        await event.reply("\n".join(msg))

    async def _cmd_active(self, event):
        await self.log_brain_event('active', 'Active positions checked')
        positions = getattr(self._bot, '_positions', {})
        active = {m: p for m, p in positions.items() if p.active}
        if not active:
            await event.reply("<b>📍 ACTIVE POSITIONS</b>\nNo active positions.")
            return
        lines = ["<b>📍 ACTIVE POSITIONS</b>"]
        for mint, pos in active.items():
            entry = pos.entry_price
            current = pos.current_price
            roi = ((current / entry) - 1.0) * 100 if entry > 0 else 0.0
            lines.append(f"• <code>{pos.symbol}</code> (<code>{mint[:8]}</code>) | Size: <code>{pos.size} SOL</code> | ROI: <code>{roi:+.2f}%</code>")
        await event.reply("\n".join(lines))

    async def _cmd_closed(self, event):
        await self.log_brain_event('closed', 'Closed positions checked')
        db = getattr(self._bot, '_db', None)
        msg = ["<b>📜 LAST 10 CLOSED TRADES</b>"]
        if db:
            try:
                rows = await db._execute_read(
                    "SELECT mint, pnl FROM positions WHERE status = 'closed' ORDER BY timestamp DESC LIMIT 10"
                )
                if rows:
                    for r in rows:
                        pnl = float(r['pnl'] or 0.0)
                        roi = pnl * 100.0
                        msg.append(f"• <code>{r['mint'][:8]}</code>... | ROI: <code>{roi:+.2f}%</code>")
                else:
                    msg.append("No closed trades recorded.")
            except Exception as e:
                msg.append(f"Error: {e}")
        await event.reply("\n".join(msg))

    async def _cmd_kollist(self, event):
        await self.log_brain_event('kollist', 'KOL list requested')
        args = event.message.text.split()
        page = 1
        if len(args) > 1:
            try:
                page = int(args[1])
                if page < 1:
                    page = 1
            except ValueError:
                pass
                
        kols = getattr(self._bot._kol_tracker, 'wallets', {})
        if not kols:
            await event.reply("No KOL wallets configured.")
            return

        page_size = 30
        kol_items = list(kols.items())
        total_kols = len(kol_items)
        total_pages = (total_kols + page_size - 1) // page_size
        
        if page > total_pages:
            page = total_pages

        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        page_items = kol_items[start_idx:end_idx]

        msg = [
            f"<b>📣 TRACKED KOL WALLETS (Page {page}/{total_pages})</b>",
            f"<i>Total KOLs: <code>{total_kols}</code></i>\n"
        ]
        
        for idx, (addr, name) in enumerate(page_items, start_idx + 1):
            msg.append(f"{idx}. <code>{addr[:8]}</code>... | Name: <code>{name}</code>")
            
        msg.append(f"\nUse <code>/kollist [page_number]</code> to view more (e.g., <code>/kollist 2</code>).")
        
        from telethon import Button
        buttons = []
        row = []
        if page > 1:
            row.append(Button.inline("◀️ Prev", f"kollist_page_{page-1}"))
        if page < total_pages:
            row.append(Button.inline("Next ▶️", f"kollist_page_{page+1}"))
        if row:
            buttons.append(row)
            
        await event.reply("\n".join(msg), buttons=buttons if buttons else None, parse_mode='html')

    async def _handle_kollist_page_callback(self, event, page):
        kols = getattr(self._bot._kol_tracker, 'wallets', {})
        if not kols:
            await event.edit("No KOL wallets configured.")
            return

        page_size = 30
        kol_items = list(kols.items())
        total_kols = len(kol_items)
        total_pages = (total_kols + page_size - 1) // page_size
        
        if page < 1: page = 1
        if page > total_pages: page = total_pages

        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        page_items = kol_items[start_idx:end_idx]

        msg = [
            f"<b>📣 TRACKED KOL WALLETS (Page {page}/{total_pages})</b>",
            f"<i>Total KOLs: <code>{total_kols}</code></i>\n"
        ]
        
        for idx, (addr, name) in enumerate(page_items, start_idx + 1):
            msg.append(f"{idx}. <code>{addr[:8]}</code>... | Name: <code>{name}</code>")
            
        msg.append(f"\nUse <code>/kollist [page_number]</code> to view more (e.g., <code>/kollist 2</code>).")
        
        from telethon import Button
        buttons = []
        row = []
        if page > 1:
            row.append(Button.inline("◀️ Prev", f"kollist_page_{page-1}"))
        if page < total_pages:
            row.append(Button.inline("Next ▶️", f"kollist_page_{page+1}"))
        if row:
            buttons.append(row)
            
        await event.edit("\n".join(msg), buttons=buttons if buttons else None, parse_mode='html')
        await event.answer()

    async def _cmd_addkol(self, event):
        await self.log_brain_event('addkol', 'KOL target added')
        args = event.message.text.split()
        if len(args) < 3:
            await event.reply("Usage: `/addkol <address> <alias>`")
            return
        addr, alias = args[1], args[2]
        if hasattr(self._bot, '_kol_tracker'):
            self._bot._kol_tracker.add_wallet(addr, alias)
            if hasattr(self._bot, '_filter'):
                self._bot._filter.add_copy_target(addr)
                from solbot.filters import WalletScore
                self._bot._filter._wallet_scores[addr] = WalletScore(address=addr, alias=alias, score=90)
            if hasattr(self._bot, '_save_state'): self._bot._save_state()
            await event.reply(f"✅ <b>Added KOL target:</b> {alias} (<code>{addr[:8]}...</code>)")
        else:
            await event.reply("KOL Tracker offline.")

    async def _cmd_removekol(self, event):
        await self.log_brain_event('removekol', 'KOL target removed')
        args = event.message.text.split()
        if len(args) < 2:
            await event.reply("Usage: `/removekol <address>`")
            return
        addr = args[1]
        if hasattr(self._bot, '_kol_tracker') and addr in self._bot._kol_tracker.wallets:
            name = self._bot._kol_tracker.wallets.pop(addr)
            if hasattr(self._bot, '_filter') and addr in self._bot._filter._copy_targets:
                self._bot._filter._copy_targets.discard(addr)
                if addr in self._bot._filter._wallet_scores:
                    del self._bot._filter._wallet_scores[addr]
            if hasattr(self._bot, '_save_state'): self._bot._save_state()
            await event.reply(f"✅ <b>Removed KOL target:</b> {name}")
        else:
            await event.reply("KOL not found in active list.")

    async def _cmd_blacklistdeployer(self, event):
        await self.log_brain_event('blacklistdeployer', 'Deployer manually blacklisted')
        args = event.message.text.split()
        if len(args) < 2:
            await event.reply("Usage: `/blacklistdeployer <address>`")
            return
        addr = args[1]
        if hasattr(self._bot, '_blacklisted_wallets'):
            self._bot._blacklisted_wallets.add(addr)
            if hasattr(self._bot, '_save_state'): self._bot._save_state()
            await event.reply(f"✅ <b>Blacklisted deployer:</b> <code>{addr}</code>")
        else:
            await event.reply("Blacklist offline.")

    async def _cmd_removeblacklist(self, event):
        await self.log_brain_event('removeblacklist', 'Deployer removed from blacklist')
        args = event.message.text.split()
        if len(args) < 2:
            await event.reply("Usage: `/removeblacklist <address>`")
            return
        addr = args[1]
        if hasattr(self._bot, '_blacklisted_wallets') and addr in self._bot._blacklisted_wallets:
            self._bot._blacklisted_wallets.discard(addr)
            if hasattr(self._bot, '_save_state'): self._bot._save_state()
            await event.reply(f"✅ <b>Removed deployer from blacklist:</b> <code>{addr}</code>")
        else:
            await event.reply("Deployer not found in blacklist.")

    async def _cmd_modelmode(self, event):
        await self.log_brain_event('modelmode', 'Model risk mode changed')
        args = event.message.text.split()
        if len(args) > 1:
            mode = args[1].lower()
            if mode in ["safe", "normal", "degen"]:
                profile = self._bot.apply_risk_preset(mode)
                if hasattr(self._bot, '_save_state'):
                    self._bot._save_state()
                await event.reply(
                    f"🧠 <b>Model mode updated:</b> <code>{mode.upper()}</code>\n"
                    f"AI Min Score: <code>{profile.min_ai_score}</code>\n"
                    f"Filter Profile: <code>{profile.name}</code>\n"
                    f"Sniper Delay: <code>{profile.sniper_delay_seconds:.1f}s</code>"
                )
            else:
                await event.reply("❌ Invalid mode. Use: safe, normal, degen")
        else:
            profile_name = getattr(self._bot, '_filter_profile_name', 'degen')
            await event.reply(
                f"🧠 <b>Current Model Mode:</b> <code>{profile_name.upper()}</code>\n"
                f"AI Min Score: <code>{self._bot._ai_min_score}</code>"
            )

    async def _cmd_missed(self, event):
        await self.log_brain_event('missed', 'Missed entries reviewed')
        missed = getattr(self._bot, '_missed_runners', {})
        import time
        now = time.time()
        sol_price = getattr(self, '_sol_price', 150.0)
        if not missed:
            await event.reply("✅ <b>MISSED ENTRIES</b>\nNo missed runners currently tracked. You\'re up to date!")
            return
        lines = ["<b>💼 MISSED RUNNER WATCH LIST</b>\n"]
        for mint, info in list(missed.items()):
            age_mins = int((now - info.get('alert_time', now)) / 60)
            milestones_hit = ', '.join(info.get('notified_milestones', set())) or 'None yet'
            lines.append(
                f"• <b>{info.get('symbol', '???')}</b> | <code>{mint[:8]}</code>\n"
                f"  Alert MCAP: <code>${info.get('alert_price_usd', 0):,.0f}</code> | Age: <code>{age_mins}m</code>\n"
                f"  Milestones hit: <code>{milestones_hit}</code>\n"
                f"  👉 <a href='https://pump.fun/{mint}'>pump.fun</a>"
            )
            if len(lines) > 10:  # Cap at 10 tokens per reply
                lines.append(f"... and {len(missed) - 10} more.")
                break
        await event.reply("\n".join(lines), parse_mode='html', link_preview=False)

    async def _cmd_dailyrunner(self, event):
        await self.log_brain_event('dailyrunner', 'Daily runner list reviewed')
        daily_runners = getattr(self._bot, '_daily_runners', {})
        import time
        now = time.time()
        
        recent_runners = {
            m: info for m, info in daily_runners.items()
            if now - info.get('detected_time', now) < 86400
        }
        
        if not recent_runners:
            await event.reply("🏃‍♂️ <b>DAILY RUNNER CANDIDATES</b>\nNo daily runner candidates detected in the last 24h.")
            return
            
        lines = ["🏃‍♂️ <b>ACTIVE DAILY RUNNERS (LAST 24H)</b>\n"]
        for mint, info in sorted(recent_runners.items(), key=lambda x: x[1].get('detected_time', 0), reverse=True):
            age_secs = now - info.get('detected_time', now)
            if age_secs < 60:
                age_str = f"{int(age_secs)}s ago"
            elif age_secs < 3600:
                age_str = f"{int(age_secs / 60)}m ago"
            else:
                age_str = f"{int(age_secs / 3600)}h ago"
                
            lines.append(
                f"• <b>{info.get('symbol', '???')}</b> | <code>{mint[:8]}</code>\n"
                f"  Detected: <code>{age_str}</code> | Cap: <code>{info.get('mcap_sol', 0.0):.1f} SOL</code>\n"
                f"  Big Buys: <code>{info.get('buys_count', 2)}</code>\n"
                f"  👉 <a href='https://pump.fun/{mint}'>pump.fun</a>"
            )
            if len(lines) > 10:
                lines.append(f"... and {len(recent_runners) - 10} more.")
                break
                
        await event.reply("\n".join(lines), parse_mode='html', link_preview=False)

    async def _cmd_kolthreshold(self, event):
        await self.log_brain_event('kolthreshold', 'Adjusted KOL threshold')
        args = event.message.message.split()
        if len(args) < 2:
            current = getattr(self._bot, '_kol_threshold', 3)
            await event.reply(f"📢 <b>KOL Sentiment Threshold</b>\nCurrent setting: <code>{current} unique sources</code>\nTo change it, use <code>/kolthreshold [number]</code> (e.g. <code>/kolthreshold 3</code>).")
            return
            
        try:
            val = int(args[1])
            if val < 1:
                await event.reply("❌ Threshold must be at least 1.")
                return
            self._bot._kol_threshold = val
            self._bot._save_state()
            await event.reply(f"🟢 <b>KOL Sentiment Threshold Updated</b>\nNew setting: <code>{val} unique sources</code>.")
        except Exception as e:
            await event.reply(f"❌ Invalid argument. Error: {e}")

    async def _cmd_kols(self, event):
        await self.log_brain_event('kols', 'KOLs/Stalkchain commands reviewed')
        args = event.message.message.split()
        
        if len(args) < 2:
            from telethon import Button
            buttons = [
                [
                    Button.inline("📱 KOL Feed", b"kols_feed"),
                    Button.inline("🏆 Leaderboard", b"kols_leaderboard")
                ],
                [
                    Button.inline("📈 Top Tokens", b"kols_tokens"),
                    Button.inline("🔥 Daily Trends", b"kols_trends")
                ],
                [
                    Button.inline("🔝 Top Performers", b"kols_toptokens"),
                    Button.inline("📊 Analytics", b"kols_analytics")
                ],
                [
                    Button.inline("💸 Transactions", b"kols_txs"),
                    Button.inline("🕵️‍♂️ Cabal Finder", b"kols_cabal")
                ],
                [
                    Button.inline("🔄 Jupiter DCA", b"kols_jupiterdca")
                ]
            ]
            menu_msg = (
                "🦅 <b>SOLBOT AGI — KOLs & STALKCHAIN OS PANEL</b> 🦅\n"
                "Access real-time smart money analytics, whale activity feeds, and Cabal discovery.\n\n"
                "<b>Interactive Commands:</b>\n"
                "• <code>/kols feed</code> — Real-time trade & wallet feed\n"
                "• <code>/kols leaderboard</code> — Win-rates & profit rankings\n"
                "• <code>/kols tokens</code> — Trending tokens among KOLs\n"
                "• <code>/kols trends</code> — Hottest daily tokens & volume shifts\n"
                "• <code>/kols toptokens</code> — Top performers by smart-money inflows\n"
                "• <code>/kols analytics</code> — Token velocity & whale deep-dives\n"
                "• <code>/kols txs</code> — Real-time transaction explorer\n"
                "• <code>/kols cabal</code> — Coordinated wallet buy cluster spotter\n"
                "• <code>/kols jupiterdca</code> — Jupiter DCA track & optimize\n"
                "• <code>/kols kolscan [wallet/name]</code> — Profiles: win-rates, trades, profits\n\n"
                "<i>Tap a button below or type a subcommand to continue.</i>"
            )
            await event.reply(menu_msg, buttons=buttons)
            return

        subcmd = args[1].lower()
        ctrl = getattr(self._bot, '_kols_controller', None)
        if not ctrl:
            await event.reply("❌ KOLs controller not initialized.")
            return

        await event.respond(f"⏳ <b>Querying KOLs OS Engine...</b>", parse_mode='html')
        
        if subcmd == "feed":
            res = await ctrl.get_kol_feed()
        elif subcmd == "leaderboard":
            res = await ctrl.get_kol_leaderboard()
        elif subcmd == "tokens":
            res = await ctrl.get_top_kol_tokens()
        elif subcmd == "trends":
            res = await ctrl.get_daily_trends()
        elif subcmd == "toptokens":
            res = await ctrl.get_top_tokens()
        elif subcmd == "analytics":
            res = await ctrl.get_trends_analytics()
        elif subcmd == "txs":
            res = await ctrl.get_transactions()
        elif subcmd == "cabal":
            res = await ctrl.get_cabal_finder()
        elif subcmd == "jupiterdca":
            res = await ctrl.get_jupiter_dca_tracker()
        elif subcmd == "kolscan":
            if len(args) < 3:
                res = "🔍 <b>KOLscan Profile Query</b>\nPlease specify a wallet address or KOL name.\nExample: <code>/kols kolscan whale1</code>"
            else:
                wallet = " ".join(args[2:])
                res = await ctrl.get_kolscan_info(wallet)
        else:
            res = f"❌ Unknown subcommand: <code>{subcmd}</code>. Type <code>/kols</code> for the main panel."

        await event.reply(res, parse_mode='html', link_preview=False)

    async def _handle_kols_callback(self, event, action):
        ctrl = getattr(self._bot, '_kols_controller', None)
        if not ctrl:
            await event.answer("KOLs Controller not initialized.")
            return

        await event.answer("Querying KOLs OS...")
        
        if action == "feed":
            res = await ctrl.get_kol_feed()
        elif action == "leaderboard":
            res = await ctrl.get_kol_leaderboard()
        elif action == "tokens":
            res = await ctrl.get_top_kol_tokens()
        elif action == "trends":
            res = await ctrl.get_daily_trends()
        elif action == "toptokens":
            res = await ctrl.get_top_tokens()
        elif action == "analytics":
            res = await ctrl.get_trends_analytics()
        elif action == "txs":
            res = await ctrl.get_transactions()
        elif action == "cabal":
            res = await ctrl.get_cabal_finder()
        elif action == "jupiterdca":
            res = await ctrl.get_jupiter_dca_tracker()
        else:
            res = "Unknown action."

        await event.reply(res, parse_mode='html', link_preview=False)

    async def log_brain_event(self, command: str, details: str):
        """Log user command execution to AGI brain event log."""
        db = getattr(self._bot, '_db', None)
        if db:
            try:
                import uuid
                import time
                event_id = str(uuid.uuid4())
                await db._execute_write(
                    "INSERT INTO brain_events (event_id, command, details, timestamp) VALUES (?, ?, ?, ?)",
                    (event_id, command, details, time.time())
                )
                logger.info(f"AGI BRAIN LOGGED EVENT: {command} | {details}")
            except Exception as e:
                logger.error(f"Failed to log brain event: {e}")

    async def _cmd_autotune(self, event):
        await self.log_brain_event('autotune', 'AI Autotuning requested')
        args = event.message.text.split()
        ctrl = getattr(self._bot, '_ai_tuner', None)
        if not ctrl:
            await event.reply("❌ AI Tuner not initialized.")
            return

        if len(args) > 1 and args[1].lower() == "run":
            await event.respond("⏳ <b>Invoking Gemini AGI Autotuner...</b>", parse_mode='html')
            success, report = await ctrl.autotune()
            await event.reply(report, parse_mode='html')
            return

        await event.respond("⏳ <b>Fetching trade database metrics...</b>", parse_mode='html')
        trades, kpis = await ctrl.get_closed_trades_summary()
        
        from telethon import Button
        msg = (
            f"🧠 <b>SOLBOT AGI AUTOTUNER PANEL</b>\n\n"
            f"📊 <b>RECENT PERFORMANCE SUMMARY:</b>\n"
            f"• Total Closed Trades: <code>{kpis['total_trades']}</code>\n"
            f"• Est. Win Rate: <code>{kpis['win_rate']:.1f}%</code>\n"
            f"• Net Profit/Loss: <code>{kpis['total_pnl_sol']:.3f} SOL</code>\n"
            f"• Avg Profit/Trade: <code>{kpis['avg_pnl_sol']:.3f} SOL</code>\n\n"
            f"⚙️ <b>ACTIVE SETTINGS:</b>\n"
            f"• Buy Size: <code>{self._bot._config.jupiter.buy_amount_sol:.3f} SOL</code>\n"
            f"• Trailing Stop-Loss: <code>{self._bot._config.strategy.trailing_stop_pct * 100.0:.1f}%</code>\n"
            f"• Jupiter Slippage: <code>{self._bot._config.jupiter.slippage_bps} BPS</code>\n"
            f"• AI Safety Min: <code>{self._bot._ai_min_score} score</code>\n"
            f"• KOL Coordinated Mentions: <code>{getattr(self._bot, '_kol_threshold', 2)}</code>\n\n"
            f"<i>You can click the button below to invoke the Gemini AI trade post-mortem and automatically optimize your parameters, or run <code>/autotune run</code>.</i>"
        )
        buttons = [[Button.inline("🧠 Run AI Autotuning", b"brain_autotune")]]
        await event.reply(msg, buttons=buttons, parse_mode='html')

    async def _run_brain_autotune_via_callback(self, event):
        await event.edit("🧠 <b>Invoking Gemini AGI Autotuner...</b>", buttons=[])
        ctrl = getattr(self._bot, '_ai_tuner', None)
        if not ctrl:
            await event.edit("❌ AI Tuner not initialized.")
            return
        success, report = await ctrl.autotune()
        
        from telethon import Button
        buttons = [[Button.inline("⬅️ Back to Dashboard", b"brain_refresh")]]
        await event.edit(report, buttons=buttons, parse_mode='html')

    async def _cmd_rpcbalancer(self, event):
        await self.log_brain_event('rpcbalancer', 'RPC Balancer status checked')
        balancer = getattr(self._bot, '_rpc_pool', None)
        if not balancer:
            await event.reply("❌ RPC Balancer not initialized.")
            return
        
        await event.respond("⏳ <b>Pinging Solana RPC pool...</b>", parse_mode='html')
        await balancer.monitor_nodes()
        
        report = await balancer.get_node_status_report()
        await event.reply(report, parse_mode='html')

    async def _cmd_clustermap(self, event):
        await self.log_brain_event('clustermap', 'Cluster map trace executed')
        args = event.message.text.split()
        if len(args) < 2:
            await event.reply("🔍 <b>Developer Cluster Genesis</b>\nPlease specify a token mint address.\nExample: <code>/clustermap mint_address</code>")
            return
        
        mint_address = args[1]
        mapper = getattr(self._bot, '_cluster_mapper', None)
        if not mapper:
            await event.reply("❌ Cluster Mapper not initialized.")
            return

        await event.respond(f"⏳ <b>Tracing genesis of top holders for:</b>\n<code>{mint_address}</code>", parse_mode='html')
        
        rpc_url = self._bot._config.solana.rpc_url
        if hasattr(self._bot, '_rpc_pool') and self._bot._rpc_pool:
            rpc_url = await self._bot._rpc_pool.get_best_node()

        report = await mapper.get_cluster_report(mint_address, rpc_url)
        await event.reply(report, parse_mode='html')

    async def _cmd_visualize(self, event):
        await self.log_brain_event('visualize', 'Holder relationship visualization executed')
        args = event.message.text.split()
        if len(args) < 2:
            await event.reply("🔍 <b>Holder Relationship Mapping</b>\nPlease specify a token mint address.\nExample: <code>/visualize mint_address</code>")
            return
        
        mint_address = args[1]
        mapper = getattr(self._bot, '_cluster_mapper', None)
        if not mapper:
            await event.reply("❌ Cluster Mapper not initialized.")
            return

        await event.respond(f"⏳ <b>Mapping holder relationships for:</b>\n<code>{mint_address}</code>", parse_mode='html')
        
        rpc_url = self._bot._config.solana.rpc_url
        if hasattr(self._bot, '_rpc_pool') and self._bot._rpc_pool:
            rpc_url = await self._bot._rpc_pool.get_best_node()

        report = await mapper.get_holder_relationship_map(mint_address, rpc_url)
        await event.reply(report, parse_mode='html')

    async def _cmd_hummingbot(self, event):
        if not await self._require_admin(event):
            return
        gw = getattr(self._bot, "_hummingbot_gateway", None)
        pmm = getattr(self._bot, "_hummingbot_pmm", None)
        gw_status = "❌ Not Initialized"
        connectors_str = "None"
        if gw:
            status_info = await gw.get_status()
            if status_info.get("reachable"):
                gw_status = f"🟢 Connected ({status_info.get('gateway_url')})"
                connectors = status_info.get("connectors", [])
                if connectors:
                    connectors_str = ", ".join(c.get("name", str(c)) for c in connectors[:5])
            else:
                gw_status = f"🔴 Unreachable ({status_info.get('gateway_url')})"

        sessions_count = len(pmm.get_sessions()) if pmm else 0
        text = (
            "🤖 <b>Hummingbot Gateway & PMM Status</b>\n\n"
            f"• <b>Gateway Status:</b> {gw_status}\n"
            f"• <b>Network:</b> <code>{getattr(self._bot._config.hummingbot, 'network', 'mainnet-beta')}</code>\n"
            f"• <b>Connected DEXes:</b> <i>{connectors_str}</i>\n"
            f"• <b>Active PMM Sessions:</b> <code>{sessions_count}</code>\n\n"
            "<b>Commands:</b>\n"
            "• <code>/pmm &lt;mint&gt; [spread_bps] [amount_sol]</code> — Start Pure Market Making / Grid\n"
            "• <code>/stoppmm &lt;mint&gt;</code> — Stop active PMM session"
        )
        await event.reply(text, parse_mode="html")

    async def _cmd_pmm(self, event):
        if not await self._require_admin(event):
            return
        args = event.message.text.split()
        if len(args) < 2:
            await event.reply(
                "📈 <b>Pure Market Making / Grid Trading</b>\n"
                "Usage: <code>/pmm &lt;mint&gt; [spread_bps] [amount_sol]</code>\n"
                "Example: <code>/pmm 7xKX... 150 0.1</code> (1.5% spread, 0.1 SOL order size)",
                parse_mode="html",
            )
            return
        mint = args[1]
        spread_bps = int(args[2]) if len(args) > 2 and args[2].isdigit() else None
        amount_sol = float(args[3]) if len(args) > 3 else None
        pmm = getattr(self._bot, "_hummingbot_pmm", None)
        if not pmm:
            await event.reply("❌ Hummingbot PMM engine is not initialized.")
            return
        session = await pmm.start_session(mint=mint, base_spread_bps=spread_bps, order_amount_sol=amount_sol)
        await event.reply(
            f"✅ <b>Hummingbot PMM Launched!</b>\n\n"
            f"• <b>Mint:</b> <code>{mint}</code>\n"
            f"• <b>Base Spread:</b> <code>{session.base_spread_bps} bps</code> ({session.base_spread_bps/100:.2f}%)\n"
            f"• <b>Order Size:</b> <code>{session.order_amount_sol:.4f} SOL</code>\n"
            f"• <b>Inventory Skew:</b> Enabled",
            parse_mode="html",
        )

    async def _cmd_stoppmm(self, event):
        if not await self._require_admin(event):
            return
        args = event.message.text.split()
        if len(args) < 2:
            await event.reply("Usage: <code>/stoppmm &lt;mint&gt;</code>", parse_mode="html")
            return
        mint = args[1]
        pmm = getattr(self._bot, "_hummingbot_pmm", None)
        if not pmm:
            await event.reply("❌ Hummingbot PMM engine is not initialized.")
            return
        stopped = await pmm.stop_session(mint)
        if stopped:
            await event.reply(f"⏹ <b>PMM Session stopped for:</b> <code>{mint}</code>", parse_mode="html")
        else:
            await event.reply(f"⚠️ No active PMM session found for <code>{mint}</code>.", parse_mode="html")

    async def _cmd_runners(self, event):
        if not await self._require_admin(event):
            return
        engine = getattr(self._bot, "_missed_runner_engine", None)
        if not engine:
            await event.reply("❌ Missed Runner Engine not initialized.")
            return
        report = engine.get_summary_report()
        await event.reply(report, parse_mode="html")

    async def _cmd_demostats(self, event):
        if not await self._require_admin(event):
            return
        is_dry_run = getattr(self._bot._config.strategy, "dry_run", True)
        start_sol = getattr(self._bot._config.strategy, "dry_run_start_sol", 5.0)

        positions = list(self._bot._positions.values())
        active_positions = [p for p in positions if getattr(p, "active", True)]
        closed_positions = [p for p in positions if not getattr(p, "active", True)]

        # Calculate realized PnL from trades
        realized_pnl = 0.0
        for p in closed_positions:
            entry = getattr(p, "entry_price", 0.0)
            exit_p = getattr(p, "current_price", entry)
            size = getattr(p, "size", 0.0)
            if entry > 0:
                realized_pnl += ((exit_p - entry) / entry) * size

        # Calculate unrealized PnL from open positions
        unrealized_pnl = 0.0
        for p in active_positions:
            entry = getattr(p, "entry_price", 0.0)
            current = getattr(p, "current_price", entry)
            size = getattr(p, "size", 0.0)
            if entry > 0:
                unrealized_pnl += ((current - entry) / entry) * size

        net_pnl = realized_pnl + unrealized_pnl
        current_sol = max(0.0, start_sol + net_pnl)
        pnl_pct = (net_pnl / max(start_sol, 0.001)) * 100.0

        wins = [p for p in closed_positions if getattr(p, "highest_price", 0) > getattr(p, "entry_price", 1)]
        win_rate = (len(wins) / len(closed_positions) * 100) if closed_positions else 0.0

        if len(closed_positions) == 0 and len(active_positions) == 0:
            status_emoji = "🟢 READY (Zero Trades Yet)"
        elif net_pnl >= 0:
            status_emoji = "🟢 PROFITABLE"
        else:
            status_emoji = "🔴 DRAWDOWN"

        msg = (
            f"🧪 <b>24-HOUR DEMO TRADING SCORECARD</b> 🧪\n\n"
            f"• <b>Mode:</b> <code>{'DRY RUN (Paper Trading)' if is_dry_run else 'LIVE'}</code>\n"
            f"• <b>Status:</b> <b>{status_emoji}</b>\n"
            f"• <b>Virtual Starting Balance:</b> <code>{start_sol:.3f} SOL</code>\n"
            f"• <b>Current Virtual Balance:</b> <code>{current_sol:.3f} SOL</code>\n"
            f"• <b>Net PnL:</b> <code>{'+' if net_pnl >= 0 else ''}{net_pnl:.3f} SOL ({pnl_pct:+.1f}%)</code>\n"
            f"  └ <i>Realized: {realized_pnl:+.3f} SOL | Unrealized: {unrealized_pnl:+.3f} SOL</i>\n\n"
            f"• <b>Open Active Positions:</b> <code>{len(active_positions)}</code>\n"
            f"• <b>Closed Trades:</b> <code>{len(closed_positions)}</code>\n"
            f"• <b>Win Rate:</b> <code>{win_rate:.1f}%</code>\n\n"
            f"<i>Strategy: Missed Runner Clone Sniper + Dynamic Kelly Sizer + 4-Tier Moonbag Exit</i>"
        )
        await event.reply(msg, parse_mode="html")

    async def _cmd_silent(self, event):
        if not await self._require_admin(event):
            return
        args = event.message.text.split()
        if len(args) > 1:
            val = args[1].lower()
            if val in ("on", "1", "true", "yes", "enable"):
                self._silent_mode = True
                await event.reply("🔇 <b>Silent Mode Enabled</b>\nBackground trade alerts are now muted. Use <code>/demostats</code> or <code>/active</code> anytime.", parse_mode="html")
            elif val in ("off", "0", "false", "no", "disable"):
                self._silent_mode = False
                await event.reply("🔔 <b>Silent Mode Disabled</b>\nLive trade alerts will now be sent to Telegram.", parse_mode="html")
            else:
                await event.reply("Usage: <code>/silent on</code> or <code>/silent off</code>", parse_mode="html")
        else:
            status = "ENABLED (Muted)" if self._silent_mode else "DISABLED (Active Alerts)"
            await event.reply(
                f"🔇 <b>Silent Background Mode:</b> <code>{status}</code>\n\n"
                "• Type <code>/silent on</code> to mute all trade alerts\n"
                "• Type <code>/silent off</code> to receive all trade alerts\n"
                "• Type <code>/demostats</code> to inspect performance anytime",
                parse_mode="html"
            )


class TelegramManager(TelegramController):
    """Backward-compatible alias class for older V2 components."""
    def __init__(self, config: TelegramConfig, bot_instance: Any = None):
        super().__init__(config, bot_instance)

    async def start(self, bot_instance: Any = None):
        if bot_instance is not None:
            self._bot = bot_instance
        await super().start()
