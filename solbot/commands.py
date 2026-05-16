"""Telegram command handler for Solbot with centralized command registry.

Features:
- Centralized command registry with categories, descriptions, aliases
- Runtime mode switching (safe/normal/degen)
- Trade statistics commands (/stats, /wins, /losses, /recent)
- PM2-safe graceful restart
- /solbot IPC routing for OpenClaw integration
- Command aliases (/on, /off, /stopbuy, /startbuy)
"""

import asyncio
import collections
import os
import signal
from dataclasses import dataclass, field
from time import time
from typing import TYPE_CHECKING, Callable, Coroutine, Optional

import aiohttp

from solbot.config import TelegramConfig
from solbot.logger import get_logger

if TYPE_CHECKING:
    from solbot.bot import Solbot

logger = get_logger("commands")

# Ring buffer for recent log capture
LOG_BUFFER_SIZE = 50


class LogCapture:
    """Captures recent log messages for /logs command."""

    def __init__(self, max_size: int = LOG_BUFFER_SIZE):
        self._buffer: collections.deque[str] = collections.deque(maxlen=max_size)

    def add(self, message: str):
        self._buffer.append(message)

    def get_recent(self, count: int = 20) -> list[str]:
        return list(self._buffer)[-count:]

    def clear(self):
        self._buffer.clear()


# Global log capture instance
log_capture = LogCapture()


# ── Command Registry ────────────────────────────────────────────────────

@dataclass
class CommandEntry:
    """A registered command with metadata."""
    name: str                   # Primary command name (e.g., "/status")
    handler: str                # Method name on CommandHandler
    description: str            # Short description for /list
    category: str               # Category for grouping
    aliases: list[str] = field(default_factory=list)  # Alias commands
    usage: str = ""             # Usage example


# Trading mode presets
TRADING_MODES = {
    "safe": {
        "label": "🛡️ SAFE",
        "min_liquidity_sol": 10.0,
        "max_concurrent_positions": 3,
        "buy_cooldown_seconds": 20.0,
        "stop_loss_pct": 20.0,
        "min_trade_confidence": "HIGH",
    },
    "normal": {
        "label": "⚖️ NORMAL",
        "min_liquidity_sol": 5.0,
        "max_concurrent_positions": 5,
        "buy_cooldown_seconds": 10.0,
        "stop_loss_pct": 30.0,
        "min_trade_confidence": "HIGH",
    },
    "degen": {
        "label": "🔥 DEGEN",
        "min_liquidity_sol": 2.0,
        "max_concurrent_positions": 8,
        "buy_cooldown_seconds": 5.0,
        "stop_loss_pct": 40.0,
        "min_trade_confidence": "MEDIUM",
    },
}


# Registry definition
COMMAND_REGISTRY: list[CommandEntry] = [
    # ── Core Control ────────────────────────────────────────────────────
    CommandEntry("/status", "_cmd_status", "Bot status overview", "Core Control"),
    CommandEntry("/pause", "_cmd_pause", "Pause new buys", "Core Control", aliases=["/off", "/stopbuy", "/hold"]),
    CommandEntry("/resume", "_cmd_resume", "Resume trading", "Core Control", aliases=["/on", "/startbuy", "/go"]),
    CommandEntry("/restart", "_cmd_restart", "Graceful PM2-safe restart", "Core Control"),
    # ── Emergency ───────────────────────────────────────────────────────
    CommandEntry("/kill", "_cmd_kill", "Kill switch (requires confirm)", "Emergency", usage="/kill confirm"),
    CommandEntry("/emergency", "_cmd_emergency", "Instant emergency shutdown", "Emergency"),
    CommandEntry("/killall", "_cmd_emergency", "Alias for /emergency", "Emergency"),
    # ── Analytics ───────────────────────────────────────────────────────
    CommandEntry("/positions", "_cmd_positions", "Open positions with P&L", "Analytics"),
    CommandEntry("/pnl", "_cmd_pnl", "Session P&L report", "Analytics"),
    CommandEntry("/stats", "_cmd_stats", "Trade statistics summary", "Analytics"),
    CommandEntry("/wins", "_cmd_wins", "Recent winning trades", "Analytics"),
    CommandEntry("/losses", "_cmd_losses", "Recent losing trades", "Analytics"),
    CommandEntry("/recent", "_cmd_recent", "Last N trades", "Analytics", usage="/recent [N]"),
    CommandEntry("/top", "_cmd_top", "Top P&L trades all time", "Analytics"),
    CommandEntry("/logs", "_cmd_logs", "Recent log entries", "Analytics", usage="/logs [N]"),
    # ── Runtime Config ──────────────────────────────────────────────────
    CommandEntry("/mode", "_cmd_mode", "Switch trading mode", "Runtime Config", usage="/mode [safe|normal|degen]"),
    CommandEntry("/maxbuy", "_cmd_maxbuy", "Set buy amount (SOL)", "Runtime Config", usage="/maxbuy 0.05"),
    CommandEntry("/slippage", "_cmd_slippage", "Set slippage (bps)", "Runtime Config", usage="/slippage 400"),
    CommandEntry("/maxpositions", "_cmd_maxpositions", "Set max positions", "Runtime Config", usage="/maxpositions 5"),
    CommandEntry("/cooldown", "_cmd_cooldown", "Set buy cooldown (sec)", "Runtime Config", usage="/cooldown 15"),
    CommandEntry("/minliq", "_cmd_minliq", "Set min liquidity (SOL)", "Runtime Config", usage="/minliq 8"),
    CommandEntry("/minmcap", "_cmd_minmcap", "Set min mcap (USD)", "Runtime Config", usage="/minmcap 15000"),
    CommandEntry("/stoploss", "_cmd_stoploss", "Set stop loss (%)", "Runtime Config", usage="/stoploss 25"),
    # ── Risk Management ─────────────────────────────────────────────────
    CommandEntry("/blacklist", "_cmd_blacklist", "View/add blacklisted creators", "Risk Management", usage="/blacklist [addr]"),
    CommandEntry("/rugs", "_cmd_rugs", "Recent detected rugs", "Risk Management"),
    # ── Wallet Intelligence ─────────────────────────────────────────────
    CommandEntry("/creator", "_cmd_creator", "Look up creator stats", "Wallet Intelligence", usage="/creator <addr>"),
    CommandEntry("/wallet", "_cmd_wallet", "Look up wallet stats", "Wallet Intelligence", usage="/wallet <addr>"),
    CommandEntry("/smartmoney", "_cmd_smartmoney", "Top smart money wallets", "Wallet Intelligence"),
    CommandEntry("/copywallet", "_cmd_copywallet", "Manage copy-wallet list", "Wallet Intelligence", usage="/copywallet [add|remove|list] [addr]"),
    # ── Debug ───────────────────────────────────────────────────────────
    CommandEntry("/debug", "_cmd_debug", "Toggle debug mode on/off", "Debug", usage="/debug [on|off]"),
    CommandEntry("/filters", "_cmd_filters", "Show rejection analytics", "Debug"),
    # ── Info ────────────────────────────────────────────────────────────
    CommandEntry("/list", "_cmd_list", "Full command registry", "Info"),
    CommandEntry("/help", "_cmd_help", "Quick help reference", "Info"),
]




class CommandHandler:
    """Async Telegram command handler with centralized registry.

    Features:
    - Command registry with categories, descriptions, aliases
    - Runtime mode switching (safe/normal/degen)
    - /solbot IPC routing for OpenClaw
    - Trade stats commands
    - PM2-safe restart
    """

    def __init__(self, config: TelegramConfig, bot: "Solbot"):
        self._config = config
        self._bot = bot
        self._session: Optional[aiohttp.ClientSession] = None
        self._base_url = f"https://api.telegram.org/bot{config.bot_token}"
        self._running = False
        self._poll_task: Optional[asyncio.Task] = None
        self._last_update_id: int = 0
        self._start_time: float = time()
        self._current_mode: str = "normal"

        # Admin authorization
        self._admin_ids: set[str] = set()
        if config.chat_id:
            self._admin_ids.add(config.chat_id)

        # Build handler dispatch table from registry
        self._handlers: dict[str, Callable] = {}
        self._alias_map: dict[str, str] = {}
        self._build_dispatch_table()

        # IPC router for /solbot commands
        from solbot.ipc_client import OpenClawSolbotRouter
        ipc_config = bot._config.ipc if hasattr(bot._config, 'ipc') else None
        if ipc_config and ipc_config.auth_token:
            self._solbot_router = OpenClawSolbotRouter(
                socket_path=ipc_config.socket_path,
                auth_token=ipc_config.auth_token,
                admin_chat_ids=self._admin_ids,
            )
        else:
            self._solbot_router = None

    def _build_dispatch_table(self):
        """Build handler lookup from the command registry."""
        for entry in COMMAND_REGISTRY:
            handler = getattr(self, entry.handler, None)
            if handler:
                self._handlers[entry.name] = handler
                for alias in entry.aliases:
                    self._handlers[alias] = handler
                    self._alias_map[alias] = entry.name
        # Special: /start -> /status
        self._handlers["/start"] = getattr(self, "_cmd_status")

    async def start(self):
        """Start the command polling loop."""
        if not self._config.enabled or not self._config.bot_token:
            logger.info("Telegram commands DISABLED")
            return

        timeout = aiohttp.ClientTimeout(total=35, connect=10)
        self._session = aiohttp.ClientSession(timeout=timeout)
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info(f"Telegram command handler started | {len(self._handlers)} commands registered")

        if self._solbot_router and self._solbot_router.is_configured:
            logger.info("OpenClaw /solbot IPC router: REGISTERED")
        else:
            logger.info("OpenClaw /solbot IPC router: DISABLED (direct mode)")

    async def stop(self):
        """Stop the command polling loop."""
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("Telegram command handler stopped")

    def add_admin(self, chat_id: str):
        self._admin_ids.add(chat_id)

    # ── Polling Loop ────────────────────────────────────────────────────

    async def _poll_loop(self):
        while self._running:
            try:
                updates = await self._get_updates()
                for update in updates:
                    await self._handle_update(update)
            except asyncio.CancelledError:
                break
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Command poll error: {e}")
                await asyncio.sleep(2.0)

    async def _get_updates(self) -> list[dict]:
        if not self._session:
            return []
        params = {"offset": self._last_update_id + 1, "timeout": 30, "allowed_updates": '["message"]'}
        try:
            async with self._session.get(f"{self._base_url}/getUpdates", params=params) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                if not data.get("ok"):
                    return []
                results = data.get("result", [])
                if results:
                    self._last_update_id = results[-1]["update_id"]
                return results
        except asyncio.TimeoutError:
            return []
        except Exception as e:
            logger.debug(f"getUpdates error: {e}")
            return []

    async def _handle_update(self, update: dict):
        message = update.get("message", {})
        text = message.get("text", "").strip()
        chat_id = str(message.get("chat", {}).get("id", ""))
        if not text or not chat_id:
            return
        if chat_id not in self._admin_ids:
            await self._reply(chat_id, "⛔ Unauthorized.")
            return
        if not text.startswith("/"):
            return

        # /solbot prefix -> IPC routing
        if text.lower().startswith("/solbot"):
            await self._route_solbot_command(chat_id, text)
            return

        parts = text.split(maxsplit=1)
        command = parts[0].lower().split("@")[0]
        args = parts[1] if len(parts) > 1 else ""

        handler = self._handlers.get(command)
        if handler:
            try:
                await handler(chat_id, args)
            except Exception as e:
                logger.error(f"Command error ({command}): {e}")
                await self._reply(chat_id, f"❌ Error: {e}")
        else:
            await self._reply(chat_id, "❓ Unknown command. Use /list to see all commands.")

    # ── /solbot IPC Routing ─────────────────────────────────────────────

    async def _route_solbot_command(self, chat_id: str, text: str):
        if self._solbot_router and self._solbot_router.is_configured:
            logger.debug(f"IPC routing: {text}")
            response = await self._solbot_router.handle_command(chat_id, text)
            await self._reply(chat_id, response)
            return

        # Direct fallback
        parts = text.strip().split(maxsplit=2)
        if len(parts) < 2:
            await self._cmd_list(chat_id, "")
            return
        subcommand = f"/{parts[1].lower().strip()}"
        args = parts[2] if len(parts) > 2 else ""
        handler = self._handlers.get(subcommand)
        if handler:
            await handler(chat_id, args)
        else:
            await self._reply(chat_id, f"❓ Unknown: {parts[1]}. Use /list for commands.")



    # ── /list - Command Registry Display ────────────────────────────────

    async def _cmd_list(self, chat_id: str, args: str):
        """Show all available commands grouped by category."""
        categories: dict[str, list[CommandEntry]] = {}
        for entry in COMMAND_REGISTRY:
            categories.setdefault(entry.category, []).append(entry)

        lines = ["🤖 <b>SOLBOT COMMAND REGISTRY</b>\n"]
        for cat, entries in categories.items():
            lines.append(f"\n<b>{'─' * 3} {cat} {'─' * 3}</b>")
            for entry in entries:
                alias_str = ""
                if entry.aliases:
                    alias_str = f"  <i>(alias: {', '.join(entry.aliases)})</i>"
                usage = entry.usage or entry.name
                lines.append(f"  <code>{usage}</code> - {entry.description}{alias_str}")

        lines.append(f"\n<b>Current mode:</b> {TRADING_MODES[self._current_mode]['label']}")
        lines.append(f"<b>Total commands:</b> {len(COMMAND_REGISTRY)}")
        await self._reply(chat_id, "\n".join(lines))

    # ── /help ───────────────────────────────────────────────────────────

    async def _cmd_help(self, chat_id: str, args: str):
        await self._reply(
            chat_id,
            "🤖 <b>QUICK HELP</b>\n\n"
            "/list - Full command registry\n"
            "/status - Bot state\n"
            "/pnl - P&L report\n"
            "/positions - Open positions\n"
            "/on or /resume - Start buying\n"
            "/off or /pause - Stop buying\n"
            "/mode [safe|normal|degen] - Switch mode\n"
            "/stats - Trade statistics\n"
            "/kill confirm - Emergency stop\n"
            "/restart - PM2-safe restart"
        )

    # ── /mode - Runtime Mode Switching ──────────────────────────────────

    async def _cmd_mode(self, chat_id: str, args: str):
        """Switch trading mode dynamically."""
        mode_name = args.strip().lower()

        # Show current mode if no arg
        if not mode_name:
            current = TRADING_MODES[self._current_mode]
            await self._reply(
                chat_id,
                f"⚙️ <b>CURRENT MODE: {current['label']}</b>\n\n"
                f"  Min liquidity: {current['min_liquidity_sol']} SOL\n"
                f"  Max positions: {current['max_concurrent_positions']}\n"
                f"  Buy cooldown: {current['buy_cooldown_seconds']}s\n"
                f"  Stop loss: {current['stop_loss_pct']}%\n"
                f"  Confidence: {current['min_trade_confidence']}\n\n"
                f"<b>Available:</b> /mode safe | /mode normal | /mode degen"
            )
            return

        if mode_name not in TRADING_MODES:
            await self._reply(chat_id, f"❌ Unknown mode: {mode_name}\nAvailable: safe, normal, degen")
            return

        preset = TRADING_MODES[mode_name]
        bot = self._bot

        # Apply runtime config changes
        # Note: these modify the mutable TradingConfig-derived objects on positions manager
        if bot._positions:
            bot._positions._config.max_concurrent_positions = preset["max_concurrent_positions"]
            bot._positions._config.buy_cooldown_seconds = preset["buy_cooldown_seconds"]
            bot._positions._config.stop_loss_pct = preset["stop_loss_pct"]

        # Update filter min_liquidity (mutable override)
        if bot._filter:
            bot._filter._config_min_liquidity_sol = preset["min_liquidity_sol"]

        self._current_mode = mode_name
        logger.info(f"Trading mode switched to: {mode_name}")

        await self._reply(
            chat_id,
            f"✅ <b>MODE SWITCHED: {preset['label']}</b>\n\n"
            f"  Min liquidity: {preset['min_liquidity_sol']} SOL\n"
            f"  Max positions: {preset['max_concurrent_positions']}\n"
            f"  Buy cooldown: {preset['buy_cooldown_seconds']}s\n"
            f"  Stop loss: {preset['stop_loss_pct']}%\n"
            f"  Confidence: {preset['min_trade_confidence']}\n\n"
            f"⚠️ Changes applied immediately."
        )

    # ── /stats - Trade Statistics ───────────────────────────────────────

    async def _cmd_stats(self, chat_id: str, args: str):
        """Show comprehensive trade statistics."""
        bot = self._bot
        stats = {}
        if bot._db:
            stats = await bot._db.get_session_stats()

        buys = stats.get("buys", 0) or 0
        sells = stats.get("sells", 0) or 0
        total_bought = stats.get("total_bought_sol", 0) or 0
        total_sold = stats.get("total_sold_sol", 0) or 0
        net = total_sold - total_bought

        uptime = time() - self._start_time
        positions = bot._positions.open_count if bot._positions else 0
        tokens_seen = bot._filter.seen_count if bot._filter else 0

        win_rate = "N/A"
        if sells > 0:
            # Approximate: positive realized = win
            wins_approx = sells - bot._consecutive_losses if bot._consecutive_losses < sells else sells // 2
            win_rate = f"{(wins_approx / sells) * 100:.0f}%"

        await self._reply(
            chat_id,
            f"📊 <b>TRADE STATISTICS</b>\n\n"
            f"<b>Trades:</b>\n"
            f"  Buys: {buys}\n"
            f"  Sells: {sells}\n"
            f"  Open: {positions}\n\n"
            f"<b>Volume:</b>\n"
            f"  SOL bought: {total_bought:.4f}\n"
            f"  SOL sold: {total_sold:.4f}\n"
            f"  Net: {net:+.4f} SOL\n\n"
            f"<b>Performance:</b>\n"
            f"  Realized P&L: {bot._total_realized_pnl_sol:+.4f} SOL\n"
            f"  Consecutive losses: {bot._consecutive_losses}\n"
            f"  Approx win rate: {win_rate}\n\n"
            f"<b>Session:</b>\n"
            f"  Uptime: {self._format_duration(uptime)}\n"
            f"  Tokens seen: {tokens_seen}\n"
            f"  Mode: {TRADING_MODES[self._current_mode]['label']}"
        )

    # ── /wins - Recent Winning Trades ───────────────────────────────────

    async def _cmd_wins(self, chat_id: str, args: str):
        """Show recent winning trades from DB."""
        bot = self._bot
        if not bot._db:
            await self._reply(chat_id, "❌ Database not available.")
            return

        # Query closed positions with positive P&L
        rows = await bot._db._fetch_all_async(
            "SELECT symbol, pnl_pct, pnl_sol, sell_reason, closed_at "
            "FROM positions WHERE status='closed' AND pnl_sol > 0 "
            "ORDER BY closed_at DESC LIMIT 10"
        )

        if not rows:
            await self._reply(chat_id, "📭 No winning trades recorded yet.")
            return

        lines = ["🏆 <b>RECENT WINS</b>\n"]
        for i, row in enumerate(rows, 1):
            lines.append(
                f"  {i}. <b>{row['symbol']}</b> | "
                f"+{row['pnl_pct']:.1f}% | "
                f"+{row['pnl_sol']:.4f} SOL | "
                f"{row['sell_reason']}"
            )

        await self._reply(chat_id, "\n".join(lines))

    # ── /losses - Recent Losing Trades ──────────────────────────────────

    async def _cmd_losses(self, chat_id: str, args: str):
        """Show recent losing trades from DB."""
        bot = self._bot
        if not bot._db:
            await self._reply(chat_id, "❌ Database not available.")
            return

        rows = await bot._db._fetch_all_async(
            "SELECT symbol, pnl_pct, pnl_sol, sell_reason, closed_at "
            "FROM positions WHERE status='closed' AND pnl_sol < 0 "
            "ORDER BY closed_at DESC LIMIT 10"
        )

        if not rows:
            await self._reply(chat_id, "📭 No losing trades recorded yet.")
            return

        lines = ["💀 <b>RECENT LOSSES</b>\n"]
        for i, row in enumerate(rows, 1):
            lines.append(
                f"  {i}. <b>{row['symbol']}</b> | "
                f"{row['pnl_pct']:.1f}% | "
                f"{row['pnl_sol']:.4f} SOL | "
                f"{row['sell_reason']}"
            )

        await self._reply(chat_id, "\n".join(lines))

    # ── /recent - Recent Trades ─────────────────────────────────────────

    async def _cmd_recent(self, chat_id: str, args: str):
        """Show last N trades (buys and sells)."""
        count = 10
        if args.strip().isdigit():
            count = min(int(args.strip()), 20)

        bot = self._bot
        if not bot._db:
            await self._reply(chat_id, "❌ Database not available.")
            return

        rows = await bot._db._fetch_all_async(
            "SELECT symbol, side, amount_sol, tx_signature, executed_at "
            "FROM trade_history ORDER BY executed_at DESC LIMIT ?",
            (count,),
        )

        if not rows:
            await self._reply(chat_id, "📭 No trades recorded yet.")
            return

        lines = [f"📋 <b>LAST {len(rows)} TRADES</b>\n"]
        for row in rows:
            side_emoji = "🟢 BUY" if row["side"] == "buy" else "🔴 SELL"
            tx_short = row["tx_signature"][:12] + "..." if row["tx_signature"] else "—"
            lines.append(
                f"  {side_emoji} <b>{row['symbol']}</b> | "
                f"{row['amount_sol']:.4f} SOL | "
                f"<code>{tx_short}</code>"
            )

        await self._reply(chat_id, "\n".join(lines))



    # ── /restart - PM2-safe Graceful Restart ────────────────────────────

    async def _cmd_restart(self, chat_id: str, args: str):
        """Gracefully restart the bot process (PM2-safe)."""
        bot = self._bot

        await self._reply(
            chat_id,
            "🔄 <b>RESTARTING...</b>\n\n"
            "Graceful shutdown initiated.\n"
            "PM2 will auto-restart the process.\n"
            "Please wait 5-10 seconds."
        )

        logger.info("RESTART requested via Telegram command")

        # Schedule shutdown after reply is sent
        async def _do_restart():
            await asyncio.sleep(1.0)  # Let the reply send
            logger.info("Initiating PM2-safe shutdown for restart...")
            # Stop the bot gracefully
            await bot.stop()
            # Send SIGTERM to self (PM2 will restart)
            os.kill(os.getpid(), signal.SIGTERM)

        asyncio.create_task(_do_restart())

    # ── Core Commands (status, positions, pnl, pause, resume, etc.) ────

    async def _cmd_status(self, chat_id: str, args: str):
        bot = self._bot
        mode_label = TRADING_MODES[self._current_mode]["label"]
        trade_mode = "PAPER" if bot._config.jupiter.paper_trade else "LIVE"
        uptime = time() - self._start_time

        if bot._killed:
            state = "🚨 KILLED"
        elif bot._paused:
            state = "⏸️ PAUSED"
        elif bot._running:
            state = "🟢 RUNNING"
        else:
            state = "⚪ STOPPED"

        positions_count = bot._positions.open_count if bot._positions else 0
        max_positions = bot._config.trading.max_concurrent_positions
        blacklist_count = bot._blacklist.count if bot._blacklist else 0
        tokens_seen = bot._filter.seen_count if bot._filter else 0

        await self._reply(
            chat_id,
            f"📊 <b>SOLBOT STATUS</b>\n\n"
            f"<b>State:</b> {state}\n"
            f"<b>Trade mode:</b> {trade_mode}\n"
            f"<b>Strategy:</b> {mode_label}\n"
            f"<b>Uptime:</b> {self._format_duration(uptime)}\n\n"
            f"📈 <b>Activity:</b>\n"
            f"  Positions: {positions_count}/{max_positions}\n"
            f"  Blacklisted: {blacklist_count}\n"
            f"  Tokens seen: {tokens_seen}\n"
            f"  Realized P&L: {bot._total_realized_pnl_sol:+.4f} SOL\n"
            f"  Consecutive losses: {bot._consecutive_losses}\n\n"
            f"⚙️ <b>Config:</b>\n"
            f"  Buy amount: {bot._config.jupiter.buy_amount_sol} SOL\n"
            f"  Stop loss: {bot._config.trading.stop_loss_pct}%\n"
            f"  Cooldown: {bot._config.trading.buy_cooldown_seconds}s\n"
            f"  Kill switch: {'ON' if bot._config.trading.kill_switch_enabled else 'OFF'}"
        )

    async def _cmd_positions(self, chat_id: str, args: str):
        bot = self._bot
        if not bot._positions or bot._positions.open_count == 0:
            await self._reply(chat_id, "📭 No open positions.")
            return
        lines = ["📋 <b>OPEN POSITIONS</b>\n"]
        for i, (mint, pos) in enumerate(bot._positions.positions.items(), 1):
            pnl_emoji = "🟢" if pos.pnl_pct >= 0 else "🔴"
            lines.append(
                f"\n<b>{i}. {pos.symbol}</b> ({pos.name})\n"
                f"  Mint: <code>{mint[:20]}...</code>\n"
                f"  Entry: {pos.entry_price_sol:.4f} SOL\n"
                f"  {pnl_emoji} P&L: {pos.pnl_pct:+.1f}% ({pos.pnl_sol:+.4f} SOL)\n"
                f"  Held: {self._format_duration(pos.age_seconds)} | {pos.confidence}"
            )
        total_pnl = sum(p.pnl_sol for p in bot._positions.positions.values())
        lines.append(f"\n━━━━━━━━━━━━━━━━━━━━\n<b>Unrealized P&L:</b> {total_pnl:+.4f} SOL")
        await self._reply(chat_id, "\n".join(lines))

    async def _cmd_pnl(self, chat_id: str, args: str):
        bot = self._bot
        unrealized = sum(p.pnl_sol for p in bot._positions.positions.values()) if bot._positions else 0.0
        realized = bot._total_realized_pnl_sol
        total = realized + unrealized
        emoji = "📈" if total > 0 else ("📉" if total < 0 else "➖")

        stats = await bot._db.get_session_stats() if bot._db else {}
        buys = stats.get("buys", 0) or 0
        sells = stats.get("sells", 0) or 0
        total_bought = stats.get("total_bought_sol", 0) or 0
        total_sold = stats.get("total_sold_sol", 0) or 0

        await self._reply(
            chat_id,
            f"{emoji} <b>P&L REPORT</b>\n\n"
            f"<b>Realized:</b> {realized:+.4f} SOL\n"
            f"<b>Unrealized:</b> {unrealized:+.4f} SOL\n"
            f"<b>Total:</b> {total:+.4f} SOL\n\n"
            f"📊 <b>Trades:</b> {buys} buys / {sells} sells\n"
            f"💰 <b>Volume:</b> {total_bought:.4f} in / {total_sold:.4f} out\n"
            f"⏱️ Session: {self._format_duration(time() - self._start_time)}"
        )

    async def _cmd_pause(self, chat_id: str, args: str):
        bot = self._bot
        if bot._killed:
            await self._reply(chat_id, "🚨 Bot is KILLED. Use /resume to reset.")
            return
        if bot._paused:
            await self._reply(chat_id, "⏸️ Already paused.")
            return
        bot._paused = True
        logger.info("Bot PAUSED via command")
        await self._reply(chat_id, "⏸️ <b>PAUSED</b> - New buys halted. Auto-sells continue.\nUse /on or /resume to restart.")

    async def _cmd_resume(self, chat_id: str, args: str):
        bot = self._bot
        if bot._killed:
            bot._killed = False
            bot._paused = False
            bot._running = True
            logger.info("Bot RESUMED from KILL via command")
            await self._reply(chat_id, "🟢 <b>RESUMED FROM KILL</b> - Trading reactivated.")
            return
        if not bot._paused:
            await self._reply(chat_id, "🟢 Already running.")
            return
        bot._paused = False
        logger.info("Bot RESUMED via command")
        await self._reply(chat_id, "🟢 <b>RESUMED</b> - Buy execution reactivated.")

    async def _cmd_blacklist(self, chat_id: str, args: str):
        bot = self._bot
        if not bot._blacklist:
            await self._reply(chat_id, "❌ Blacklist not initialized.")
            return
        if args.strip():
            address = args.strip()
            if len(address) < 32 or len(address) > 44:
                await self._reply(chat_id, "❌ Invalid address (32-44 chars).")
                return
            added = await bot._blacklist.add(creator_address=address, reason="manual", related_symbol="(Telegram)")
            if added:
                await self._reply(chat_id, f"🚫 <b>BLACKLISTED:</b> <code>{address}</code>\nTotal: {bot._blacklist.count}")
            else:
                await self._reply(chat_id, "ℹ️ Already blacklisted.")
            return

        entries = await bot._blacklist.get_all()
        if not entries:
            await self._reply(chat_id, "📭 Blacklist empty.")
            return
        lines = [f"🚫 <b>BLACKLIST</b> ({len(entries)})\n"]
        for i, e in enumerate(entries[:15], 1):
            lines.append(f"  {i}. <code>{e['creator_address'][:16]}...</code> | {e.get('reason', '?')}")
        if len(entries) > 15:
            lines.append(f"  ... +{len(entries)-15} more")
        await self._reply(chat_id, "\n".join(lines))

    async def _cmd_kill(self, chat_id: str, args: str):
        bot = self._bot
        if bot._killed:
            await self._reply(chat_id, "🚨 Already killed. /resume to reset.")
            return
        if args.strip().lower() != "confirm":
            await self._reply(chat_id, "⚠️ <b>KILL SWITCH</b>\n\nSend /kill confirm to activate.")
            return
        bot._killed = True
        bot._running = False
        logger.critical("KILL SWITCH via Telegram")
        closed = await bot._positions.emergency_close_all() if bot._positions else []
        await self._reply(chat_id, f"🚨🚨🚨 <b>KILLED</b>\nPositions closed: {len(closed)}\n/resume to restart.")

    async def _cmd_logs(self, chat_id: str, args: str):
        count = min(int(args.strip()), 40) if args.strip().isdigit() else 15
        entries = log_capture.get_recent(count)
        if not entries:
            await self._reply(chat_id, "📝 No logs.")
            return
        lines = [f"📝 <b>LOGS</b> ({len(entries)})\n<pre>"]
        for e in entries:
            lines.append(e[:80] + "..." if len(e) > 80 else e)
        lines.append("</pre>")
        msg = "\n".join(lines)
        await self._reply(chat_id, msg[:4000])

    # ── Internal Helpers ────────────────────────────────────────────────

    async def _reply(self, chat_id: str, text: str):
        if not self._session:
            return
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        try:
            async with self._session.post(f"{self._base_url}/sendMessage", json=payload) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"Reply failed ({resp.status}): {body[:200]}")
        except Exception as e:
            logger.error(f"Reply error: {e}")

    @staticmethod
    def _format_duration(seconds: float) -> str:
        if seconds < 60:
            return f"{seconds:.0f}s"
        elif seconds < 3600:
            return f"{int(seconds // 60)}m {int(seconds % 60)}s"
        else:
            return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m"



    # ── /emergency & /killall - Emergency Shutdown ──────────────────────

    async def _cmd_emergency(self, chat_id: str, args: str):
        """Emergency: close all + kill + disable buying immediately."""
        bot = self._bot
        bot._killed = True
        bot._paused = True
        bot._running = False
        logger.critical("EMERGENCY triggered via Telegram")

        closed = await bot._positions.emergency_close_all() if bot._positions else []

        await self._reply(
            chat_id,
            f"🚨🚨🚨 <b>EMERGENCY ACTIVATED</b> 🚨🚨🚨\n\n"
            f"• Positions closed: {len(closed)}\n"
            f"• Buying: DISABLED\n"
            f"• Kill switch: ACTIVE\n\n"
            f"Use /resume to restore."
        )

    # ── Live Config Commands ────────────────────────────────────────────

    async def _cmd_maxbuy(self, chat_id: str, args: str):
        """Set buy amount per trade (SOL)."""
        if not args.strip():
            await self._reply(chat_id, f"💰 Current buy amount: {self._bot._config.jupiter.buy_amount_sol} SOL\nUsage: /maxbuy 0.05")
            return
        try:
            val = float(args.strip())
            if val <= 0 or val > 10:
                await self._reply(chat_id, "❌ Must be between 0 and 10 SOL.")
                return
            # Runtime override via mutable object hack
            object.__setattr__(self._bot._config.jupiter, 'buy_amount_sol', val)
            logger.info(f"Buy amount set to {val} SOL via command")
            await self._reply(chat_id, f"✅ Buy amount → <b>{val} SOL</b>")
        except ValueError:
            await self._reply(chat_id, "❌ Invalid number.")

    async def _cmd_slippage(self, chat_id: str, args: str):
        """Set slippage in basis points."""
        if not args.strip():
            await self._reply(chat_id, f"📊 Current slippage: {self._bot._config.jupiter.slippage_bps} bps\nUsage: /slippage 400")
            return
        try:
            val = int(args.strip())
            if val < 50 or val > 5000:
                await self._reply(chat_id, "❌ Must be 50-5000 bps.")
                return
            object.__setattr__(self._bot._config.jupiter, 'slippage_bps', val)
            logger.info(f"Slippage set to {val} bps via command")
            await self._reply(chat_id, f"✅ Slippage → <b>{val} bps</b>")
        except ValueError:
            await self._reply(chat_id, "❌ Invalid number.")

    async def _cmd_maxpositions(self, chat_id: str, args: str):
        """Set max concurrent positions."""
        if not args.strip():
            cur = self._bot._positions._config.max_concurrent_positions if self._bot._positions else "?"
            await self._reply(chat_id, f"📊 Max positions: {cur}\nUsage: /maxpositions 5")
            return
        try:
            val = int(args.strip())
            if val < 1 or val > 20:
                await self._reply(chat_id, "❌ Must be 1-20.")
                return
            if self._bot._positions:
                self._bot._positions._config.max_concurrent_positions = val
            logger.info(f"Max positions set to {val} via command")
            await self._reply(chat_id, f"✅ Max positions → <b>{val}</b>")
        except ValueError:
            await self._reply(chat_id, "❌ Invalid number.")

    async def _cmd_cooldown(self, chat_id: str, args: str):
        """Set buy cooldown in seconds."""
        if not args.strip():
            cur = self._bot._positions._config.buy_cooldown_seconds if self._bot._positions else "?"
            await self._reply(chat_id, f"⏱️ Cooldown: {cur}s\nUsage: /cooldown 15")
            return
        try:
            val = float(args.strip())
            if val < 0 or val > 300:
                await self._reply(chat_id, "❌ Must be 0-300s.")
                return
            if self._bot._positions:
                self._bot._positions._config.buy_cooldown_seconds = val
            logger.info(f"Cooldown set to {val}s via command")
            await self._reply(chat_id, f"✅ Cooldown → <b>{val}s</b>")
        except ValueError:
            await self._reply(chat_id, "❌ Invalid number.")

    async def _cmd_minliq(self, chat_id: str, args: str):
        """Set minimum liquidity filter (SOL)."""
        if not args.strip():
            cur = self._bot._config.pumpfun.min_liquidity_sol
            await self._reply(chat_id, f"💧 Min liquidity: {cur} SOL\nUsage: /minliq 8")
            return
        try:
            val = float(args.strip())
            if val < 0 or val > 1000:
                await self._reply(chat_id, "❌ Must be 0-1000.")
                return
            if self._bot._filter:
                self._bot._filter._config_min_liquidity_sol = val
            logger.info(f"Min liquidity set to {val} SOL via command")
            await self._reply(chat_id, f"✅ Min liquidity → <b>{val} SOL</b>")
        except ValueError:
            await self._reply(chat_id, "❌ Invalid number.")

    async def _cmd_minmcap(self, chat_id: str, args: str):
        """Set minimum market cap filter (USD)."""
        if not args.strip():
            cur = self._bot._config.pumpfun.min_market_cap_usd
            await self._reply(chat_id, f"💰 Min mcap: ${cur:,.0f}\nUsage: /minmcap 15000")
            return
        try:
            val = float(args.strip())
            if val < 0:
                await self._reply(chat_id, "❌ Must be positive.")
                return
            if self._bot._filter:
                self._bot._filter._config_min_market_cap_usd = val
            logger.info(f"Min mcap set to ${val:.0f} via command")
            await self._reply(chat_id, f"✅ Min mcap → <b>${val:,.0f}</b>")
        except ValueError:
            await self._reply(chat_id, "❌ Invalid number.")

    async def _cmd_stoploss(self, chat_id: str, args: str):
        """Set stop loss percentage."""
        if not args.strip():
            cur = self._bot._positions._config.stop_loss_pct if self._bot._positions else "?"
            await self._reply(chat_id, f"🛑 Stop loss: {cur}%\nUsage: /stoploss 25")
            return
        try:
            val = float(args.strip())
            if val <= 0 or val > 100:
                await self._reply(chat_id, "❌ Must be 1-100%.")
                return
            if self._bot._positions:
                self._bot._positions._config.stop_loss_pct = val
            logger.info(f"Stop loss set to {val}% via command")
            await self._reply(chat_id, f"✅ Stop loss → <b>{val}%</b>")
        except ValueError:
            await self._reply(chat_id, "❌ Invalid number.")

    # ── /top - Top PnL Trades ───────────────────────────────────────────

    async def _cmd_top(self, chat_id: str, args: str):
        """Show top P&L trades of all time."""
        bot = self._bot
        if not bot._db:
            await self._reply(chat_id, "❌ Database not available.")
            return
        rows = await bot._db._fetch_all_async(
            "SELECT symbol, pnl_pct, pnl_sol, sell_reason "
            "FROM positions WHERE status='closed' "
            "ORDER BY pnl_sol DESC LIMIT 10"
        )
        if not rows:
            await self._reply(chat_id, "📭 No closed trades yet.")
            return
        lines = ["🏅 <b>TOP TRADES (by SOL P&L)</b>\n"]
        for i, row in enumerate(rows, 1):
            emoji = "🟢" if row["pnl_sol"] >= 0 else "🔴"
            lines.append(f"  {i}. {emoji} <b>{row['symbol']}</b> | {row['pnl_pct']:+.1f}% | {row['pnl_sol']:+.4f} SOL")
        await self._reply(chat_id, "\n".join(lines))

    # ── Wallet Intelligence Stubs ───────────────────────────────────────

    async def _cmd_creator(self, chat_id: str, args: str):
        """Look up creator/deployer stats from Smart Money engine."""
        if not args.strip():
            await self._reply(chat_id, "Usage: /creator <wallet_address>")
            return

        address = args.strip()[:44]
        bot = self._bot
        sm = getattr(bot, '_smart_money', None)

        if not sm:
            await self._reply(chat_id, "⚠️ Smart Money engine not active.")
            return

        stats = await sm.get_creator_stats(address)
        if not stats:
            await self._reply(
                chat_id,
                f"🔍 <b>Creator Intel</b>\n\n"
                f"<code>{address}</code>\n\n"
                f"📭 No data found for this creator.\n"
                f"They may not have launched any tracked tokens yet."
            )
            return

        # Format output
        rep_emoji = "🟢" if stats.reputation_score >= 60 else ("🟡" if stats.reputation_score >= 40 else "🔴")
        await self._reply(
            chat_id,
            f"🔍 <b>CREATOR INTELLIGENCE</b>\n\n"
            f"<b>Address:</b> <code>{address[:20]}...</code>\n\n"
            f"📊 <b>Launch History:</b>\n"
            f"  Total launches: {stats.total_launches}\n"
            f"  Successful: {stats.successful_launches}\n"
            f"  Rugged: {stats.rugged_launches}\n"
            f"  Success rate: {stats.success_rate:.0f}%\n"
            f"  Rug rate: {stats.rug_rate:.0f}%\n\n"
            f"📈 <b>Performance:</b>\n"
            f"  Avg ATH multiplier: {stats.avg_ath_multiplier:.1f}x\n"
            f"  Avg launch liquidity: {stats.avg_liquidity_at_launch:.2f} SOL\n\n"
            f"{rep_emoji} <b>Reputation Score:</b> {stats.reputation_score:.0f}/100"
        )

    async def _cmd_wallet(self, chat_id: str, args: str):
        """Look up wallet stats from Smart Money engine."""
        if not args.strip():
            await self._reply(chat_id, "Usage: /wallet <address>")
            return

        address = args.strip()[:44]
        bot = self._bot
        sm = getattr(bot, '_smart_money', None)

        if not sm:
            await self._reply(chat_id, "⚠️ Smart Money engine not active.")
            return

        stats = await sm.get_wallet_stats(address)
        if not stats:
            await self._reply(
                chat_id,
                f"🔍 <b>Wallet Intel</b>\n\n"
                f"<code>{address}</code>\n\n"
                f"📭 No data found. Wallet not yet tracked."
            )
            return

        # Tags
        tag_str = ""
        if stats.is_smart_money:
            tag_str = "🏆 SMART MONEY"
        elif stats.is_toxic:
            tag_str = "☠️ TOXIC WALLET"
        else:
            tag_str = "⚪ NEUTRAL"

        conv_emoji = "🟢" if stats.conviction_score >= 70 else ("🟡" if stats.conviction_score >= 40 else "🔴")

        await self._reply(
            chat_id,
            f"🔍 <b>WALLET INTELLIGENCE</b>\n\n"
            f"<b>Address:</b> <code>{address[:20]}...</code>\n"
            f"<b>Classification:</b> {tag_str}\n\n"
            f"📊 <b>Trading Stats:</b>\n"
            f"  Total buys: {stats.total_buys}\n"
            f"  Total sells: {stats.total_sells}\n"
            f"  Wins: {stats.wins} | Losses: {stats.losses}\n"
            f"  Winrate: {stats.winrate:.0f}%\n\n"
            f"💰 <b>Performance:</b>\n"
            f"  Realized P&L: {stats.total_realized_pnl:+.4f} SOL\n"
            f"  Avg ROI: {stats.avg_roi_pct:+.1f}%\n"
            f"  Avg hold: {stats.avg_hold_seconds:.0f}s\n\n"
            f"⚠️ <b>Risk:</b>\n"
            f"  Rug participations: {stats.rug_participations}\n"
            f"  Rug rate: {stats.rug_rate:.0f}%\n\n"
            f"{conv_emoji} <b>Conviction Score:</b> {stats.conviction_score:.0f}/100"
        )

    async def _cmd_smartmoney(self, chat_id: str, args: str):
        """Show top smart money wallets."""
        bot = self._bot
        sm = getattr(bot, '_smart_money', None)

        if not sm:
            await self._reply(chat_id, "⚠️ Smart Money engine not active.")
            return

        top_wallets = await sm.get_top_wallets(limit=10)
        if not top_wallets:
            await self._reply(chat_id, "📭 No smart money data yet.\nThe engine needs to observe wallet activity first.")
            return

        lines = ["🏆 <b>TOP SMART MONEY WALLETS</b>\n"]
        for i, w in enumerate(top_wallets, 1):
            total = w.get("wins", 0) + w.get("losses", 0)
            wr = (w.get("wins", 0) / total * 100) if total > 0 else 0
            pnl = w.get("total_realized_pnl", 0)
            emoji = "🟢" if pnl > 0 else "🔴"
            lines.append(
                f"  {i}. <code>{w['address'][:12]}...</code>\n"
                f"     {emoji} {pnl:+.4f} SOL | WR: {wr:.0f}% | "
                f"Buys: {w.get('total_buys', 0)} | Rugs: {w.get('rug_participations', 0)}"
            )

        copy_count = len(sm.get_copy_wallets())
        lines.append(f"\n<b>Copy wallets tracked:</b> {copy_count}")
        lines.append("ℹ️ Use /copywallet add <addr> to track a wallet")
        await self._reply(chat_id, "\n".join(lines))

    async def _cmd_copywallet(self, chat_id: str, args: str):
        """Manage copy-wallet list (add/remove/list)."""
        bot = self._bot
        sm = getattr(bot, '_smart_money', None)

        if not sm:
            await self._reply(chat_id, "⚠️ Smart Money engine not active.")
            return

        parts = args.strip().split(maxsplit=1)
        action = parts[0].lower() if parts else ""

        # /copywallet list (or no args)
        if not action or action == "list":
            wallets = sm.get_copy_wallets()
            if not wallets:
                await self._reply(chat_id, "📭 No copy wallets configured.\nUse /copywallet add <address>")
                return
            lines = [f"👁️ <b>COPY WALLETS</b> ({len(wallets)})\n"]
            for i, addr in enumerate(wallets, 1):
                lines.append(f"  {i}. <code>{addr[:20]}...</code>")
            lines.append("\nℹ️ /copywallet remove <addr> to untrack")
            await self._reply(chat_id, "\n".join(lines))
            return

        # /copywallet add <addr>
        if action == "add":
            if len(parts) < 2:
                await self._reply(chat_id, "Usage: /copywallet add <wallet_address>")
                return
            address = parts[1].strip()[:44]
            if len(address) < 32:
                await self._reply(chat_id, "❌ Invalid address (must be 32-44 chars).")
                return
            added = await sm.add_copy_wallet(address)
            if added:
                await self._reply(chat_id, f"✅ <b>Copy wallet added:</b>\n<code>{address}</code>\n\nTokens bought by this wallet will boost confidence.")
            else:
                await self._reply(chat_id, "ℹ️ Already tracking this wallet.")
            return

        # /copywallet remove <addr>
        if action == "remove":
            if len(parts) < 2:
                await self._reply(chat_id, "Usage: /copywallet remove <wallet_address>")
                return
            address = parts[1].strip()[:44]
            removed = await sm.remove_copy_wallet(address)
            if removed:
                await self._reply(chat_id, f"✅ Copy wallet removed:\n<code>{address}</code>")
            else:
                await self._reply(chat_id, "❌ Wallet not in copy list.")
            return

        await self._reply(chat_id, "Usage: /copywallet [add|remove|list] [address]")

    async def _cmd_rugs(self, chat_id: str, args: str):
        """Show recent detected rugs."""
        bot = self._bot

        # Try smart money engine first for creator-level rug data
        sm = getattr(bot, '_smart_money', None)
        if sm:
            top_creators = await self._db._fetch_all_async(
                "SELECT address, total_launches, rugged_launches FROM creator_stats "
                "WHERE rugged_launches > 0 ORDER BY rugged_launches DESC LIMIT 10"
            ) if bot._db else []

            if top_creators:
                lines = ["💀 <b>TOP RUG CREATORS</b>\n"]
                for i, c in enumerate(top_creators, 1):
                    rug_rate = (c["rugged_launches"] / c["total_launches"] * 100) if c["total_launches"] > 0 else 0
                    lines.append(
                        f"  {i}. <code>{c['address'][:16]}...</code> | "
                        f"Rugs: {c['rugged_launches']}/{c['total_launches']} ({rug_rate:.0f}%)"
                    )
                await self._reply(chat_id, "\n".join(lines))
                return

        # Fallback to blacklist data
        if not bot._blacklist:
            await self._reply(chat_id, "📭 No rug data.")
            return
        entries = await bot._blacklist.get_all()
        rug_entries = [e for e in entries if "rug" in e.get("reason", "")][:10]
        if not rug_entries:
            await self._reply(chat_id, "📭 No rugs detected yet.")
            return
        lines = ["💀 <b>RECENT RUGS</b>\n"]
        for i, e in enumerate(rug_entries, 1):
            lines.append(f"  {i}. <code>{e['creator_address'][:16]}...</code> | {e.get('related_symbol', '?')} | {e['reason']}")
        await self._reply(chat_id, "\n".join(lines))



    # ── /debug - Toggle Debug Mode ──────────────────────────────────────

    async def _cmd_debug(self, chat_id: str, args: str):
        """Toggle debug mode on/off."""
        import solbot.filters as _filters

        arg = args.strip().lower()
        if arg == "on":
            _filters.DEBUG_MODE = True
            logger.info("DEBUG MODE: ON via Telegram")
            await self._reply(chat_id, "🐛 <b>DEBUG MODE: ON</b>\n\nVerbose lifecycle logs enabled.")
        elif arg == "off":
            _filters.DEBUG_MODE = False
            logger.info("DEBUG MODE: OFF via Telegram")
            await self._reply(chat_id, "🔇 <b>DEBUG MODE: OFF</b>\n\nOnly production logs active.")
        else:
            current = "ON" if _filters.DEBUG_MODE else "OFF"
            await self._reply(
                chat_id,
                f"🐛 <b>Debug Mode:</b> {current}\n\n"
                f"Usage:\n"
                f"  /debug on - Enable verbose logs\n"
                f"  /debug off - Production logs only"
            )

    # ── /filters - Rejection Analytics ──────────────────────────────────

    async def _cmd_filters(self, chat_id: str, args: str):
        """Show rejection statistics and filter analytics."""
        from solbot.filters import rejection_counters, DEBUG_MODE

        s = rejection_counters.summary()
        debug_status = "ON" if DEBUG_MODE else "OFF"

        await self._reply(
            chat_id,
            f"📊 <b>FILTER & REJECTION ANALYTICS</b>\n\n"
            f"<b>Pipeline:</b>\n"
            f"  Tokens detected: {s['total_detected']}\n"
            f"  Qualified (passed filters): {s['qualified_tokens']}\n"
            f"  Execution attempts: {s['execution_attempts']}\n"
            f"  Successful buys: {s['successful_buys']}\n"
            f"  Buy success rate: {s['buy_success_rate']:.0f}%\n\n"
            f"<b>Rejections:</b>\n"
            f"  Total rejected: {s['total_rejected']}\n"
            f"  ├ Low liquidity: {s['rejected_low_liquidity']}\n"
            f"  ├ Low mcap: {s['rejected_market_cap']}\n"
            f"  ├ Too old: {s['rejected_age']}\n"
            f"  ├ Duplicate: {s['rejected_duplicate']}\n"
            f"  ├ Blacklisted: {s['rejected_blacklist']}\n"
            f"  ├ Low confidence: {s['rejected_low_confidence']}\n"
            f"  ├ Cooldown: {s['rejected_cooldown']}\n"
            f"  ├ Max positions: {s['rejected_max_positions']}\n"
            f"  ├ Paused: {s['rejected_paused']}\n"
            f"  ├ No route: {s['rejected_no_route']}\n"
            f"  └ Execution failed: {s['rejected_execution_failed']}\n\n"
            f"<b>Debug mode:</b> {debug_status}\n"
            f"ℹ️ /debug on | /debug off"
        )
