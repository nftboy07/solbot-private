"""Telegram command handler for Solbot management via OpenClaw.

Polls for incoming Telegram updates and dispatches commands to the
running bot instance. All command handlers are async-safe and interact
with the bot's internal state through its public interface.

Supported commands:
    /status    - Bot status overview (mode, state, positions, uptime)
    /positions - List all open positions with P&L
    /pnl       - Session P&L summary (realized + unrealized)
    /pause     - Pause new buy execution (monitoring continues)
    /resume    - Resume buy execution
    /blacklist - Show blacklisted creators (or add with /blacklist <addr>)
    /kill      - Emergency kill switch activation
    /logs      - Show recent log entries
"""

import asyncio
import collections
from time import time
from typing import TYPE_CHECKING, Optional

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
        """Add a log entry."""
        self._buffer.append(message)

    def get_recent(self, count: int = 20) -> list[str]:
        """Get the most recent N log entries."""
        entries = list(self._buffer)
        return entries[-count:]

    def clear(self):
        self._buffer.clear()


# Global log capture instance
log_capture = LogCapture()


class CommandHandler:
    """Async Telegram command handler using long-polling.

    Integrates with the running Solbot instance to provide real-time
    management via Telegram commands. Only responds to authorized
    admin chat IDs.

    Also routes /solbot-prefixed commands through IPC to maintain
    PM2-separated process architecture with OpenClaw.
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

        # Admin authorization
        self._admin_ids: set[str] = set()
        admin_str = config.chat_id  # Primary chat is always admin
        if admin_str:
            self._admin_ids.add(admin_str)

        # IPC router for /solbot commands (OpenClaw integration)
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

    async def start(self):
        """Start the command polling loop."""
        if not self._config.enabled or not self._config.bot_token:
            logger.info("Telegram commands DISABLED (bot not configured)")
            return

        timeout = aiohttp.ClientTimeout(total=35, connect=10)
        self._session = aiohttp.ClientSession(timeout=timeout)
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("Telegram command handler started")

        # Log IPC router status
        if self._solbot_router and self._solbot_router.is_configured:
            logger.info("OpenClaw /solbot IPC router: REGISTERED (routing /solbot commands via IPC)")
        else:
            logger.info("OpenClaw /solbot IPC router: DISABLED (direct command handling only)")

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
        """Add an additional admin chat ID."""
        self._admin_ids.add(chat_id)

    # ── Polling Loop ────────────────────────────────────────────────────

    async def _poll_loop(self):
        """Long-poll Telegram for updates and dispatch commands."""
        while self._running:
            try:
                updates = await self._get_updates()
                for update in updates:
                    await self._handle_update(update)
            except asyncio.CancelledError:
                break
            except asyncio.TimeoutError:
                continue  # Normal for long-polling
            except Exception as e:
                logger.error(f"Command poll error: {e}")
                await asyncio.sleep(2.0)

    async def _get_updates(self) -> list[dict]:
        """Fetch new updates from Telegram via long polling."""
        if not self._session:
            return []

        params = {
            "offset": self._last_update_id + 1,
            "timeout": 30,
            "allowed_updates": '["message"]',
        }

        try:
            async with self._session.get(
                f"{self._base_url}/getUpdates", params=params
            ) as resp:
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
        """Parse and dispatch a single update."""
        message = update.get("message", {})
        text = message.get("text", "").strip()
        chat_id = str(message.get("chat", {}).get("id", ""))

        if not text or not chat_id:
            return

        # Authorization check
        if chat_id not in self._admin_ids:
            logger.warning(f"Unauthorized command from chat_id={chat_id}: {text}")
            await self._reply(chat_id, "⛔ Unauthorized. Access denied.")
            return

        # Parse command
        if not text.startswith("/"):
            return

        # ── /solbot prefix: route through IPC to Solbot ─────────────────
        # Intercepts: /solbot status, /solbot positions, /solbot kill confirm, etc.
        lower_text = text.lower()
        if lower_text.startswith("/solbot"):
            await self._route_solbot_command(chat_id, text)
            return

        parts = text.split(maxsplit=1)
        command = parts[0].lower().split("@")[0]  # Handle /command@botname
        args = parts[1] if len(parts) > 1 else ""

        # Dispatch
        handler = self._get_handler(command)
        if handler:
            try:
                await handler(chat_id, args)
            except Exception as e:
                logger.error(f"Command handler error ({command}): {e}")
                await self._reply(chat_id, f"❌ Error executing {command}: {e}")
        else:
            await self._reply(
                chat_id,
                "❓ Unknown command. Available:\n"
                "/status /positions /pnl /pause /resume /blacklist /kill /logs\n"
                "/solbot <cmd> - Route to Solbot via IPC"
            )

    def _get_handler(self, command: str):
        """Map command string to handler method."""
        handlers = {
            "/status": self._cmd_status,
            "/positions": self._cmd_positions,
            "/pnl": self._cmd_pnl,
            "/pause": self._cmd_pause,
            "/resume": self._cmd_resume,
            "/blacklist": self._cmd_blacklist,
            "/kill": self._cmd_kill,
            "/logs": self._cmd_logs,
            "/start": self._cmd_status,  # Default for Telegram /start
            "/help": self._cmd_help,
        }
        return handlers.get(command)

    # ── /solbot IPC Routing ─────────────────────────────────────────────

    async def _route_solbot_command(self, chat_id: str, text: str):
        """Route /solbot-prefixed commands through the IPC bridge.

        If IPC router is configured, sends the command to Solbot via
        Unix socket and returns the response. If not configured, falls
        back to direct command execution (same process).

        Args:
            chat_id: Telegram chat ID.
            text: Full message text (e.g., "/solbot status")
        """
        # If IPC router is available (separate process mode)
        if self._solbot_router and self._solbot_router.is_configured:
            logger.debug(f"IPC routing: {text}")

            response = await self._solbot_router.handle_command(chat_id, text)
            await self._reply(chat_id, response)
            logger.debug(f"IPC response delivered for: {text}")
            return

        # Fallback: handle directly in same process (no IPC needed)
        # Parse "/solbot <command> [args]" -> route to local handler
        parts = text.strip().split(maxsplit=2)
        if len(parts) < 2:
            await self._reply(
                chat_id,
                "🤖 <b>SOLBOT COMMANDS</b>\n\n"
                "<b>Usage:</b> /solbot &lt;command&gt; [args]\n\n"
                "<b>Available:</b>\n"
                "  /solbot status\n"
                "  /solbot positions\n"
                "  /solbot pnl\n"
                "  /solbot pause\n"
                "  /solbot resume\n"
                "  /solbot blacklist [addr]\n"
                "  /solbot kill confirm\n"
                "  /solbot logs [N]"
            )
            return

        subcommand = parts[1].lower().strip()
        args = parts[2] if len(parts) > 2 else ""

        # Map subcommand to existing handler
        handler_map = {
            "status": self._cmd_status,
            "positions": self._cmd_positions,
            "pnl": self._cmd_pnl,
            "pause": self._cmd_pause,
            "resume": self._cmd_resume,
            "blacklist": self._cmd_blacklist,
            "kill": self._cmd_kill,
            "logs": self._cmd_logs,
            "help": self._cmd_help,
        }

        handler = handler_map.get(subcommand)
        if handler:
            logger.debug(f"Direct routing /solbot {subcommand} (no IPC)")
            try:
                await handler(chat_id, args)
            except Exception as e:
                logger.error(f"/solbot {subcommand} error: {e}")
                await self._reply(chat_id, f"❌ Error: {e}")
        else:
            await self._reply(
                chat_id,
                f"❓ Unknown subcommand: {subcommand}\n\n"
                "Available: status, positions, pnl, pause, resume, blacklist, kill, logs"
            )

    # ── Command Handlers ────────────────────────────────────────────────

    async def _cmd_status(self, chat_id: str, args: str):
        """Handle /status - Bot status overview."""
        bot = self._bot
        mode = "PAPER" if bot._config.jupiter.paper_trade else "LIVE"
        uptime = time() - self._start_time
        uptime_str = self._format_duration(uptime)

        # Determine state
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

        message = (
            f"📊 <b>SOLBOT STATUS</b>\n\n"
            f"<b>State:</b> {state}\n"
            f"<b>Mode:</b> {mode}\n"
            f"<b>Uptime:</b> {uptime_str}\n\n"
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
        await self._reply(chat_id, message)

    async def _cmd_positions(self, chat_id: str, args: str):
        """Handle /positions - List all open positions."""
        bot = self._bot
        if not bot._positions or bot._positions.open_count == 0:
            await self._reply(chat_id, "📭 No open positions.")
            return

        lines = ["📋 <b>OPEN POSITIONS</b>\n"]

        for i, (mint, pos) in enumerate(bot._positions.positions.items(), 1):
            pnl_emoji = "🟢" if pos.pnl_pct >= 0 else "🔴"
            age_str = self._format_duration(pos.age_seconds)

            lines.append(
                f"\n<b>{i}. {pos.symbol}</b> ({pos.name})\n"
                f"  Mint: <code>{mint[:20]}...</code>\n"
                f"  Entry: {pos.entry_price_sol:.4f} SOL\n"
                f"  Current: {pos.current_price_sol:.4f} SOL\n"
                f"  Peak: {pos.highest_price_sol:.4f} SOL\n"
                f"  {pnl_emoji} P&L: {pos.pnl_pct:+.1f}% ({pos.pnl_sol:+.4f} SOL)\n"
                f"  Tokens left: {pos.remaining_tokens:.0f}\n"
                f"  Held: {age_str}\n"
                f"  Confidence: {pos.confidence}"
            )

        # Summary line
        total_invested = sum(p.entry_price_sol for p in bot._positions.positions.values())
        total_pnl = sum(p.pnl_sol for p in bot._positions.positions.values())
        lines.append(
            f"\n━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Total invested:</b> {total_invested:.4f} SOL\n"
            f"<b>Unrealized P&L:</b> {total_pnl:+.4f} SOL"
        )

        await self._reply(chat_id, "\n".join(lines))

    async def _cmd_pnl(self, chat_id: str, args: str):
        """Handle /pnl - Session P&L summary."""
        bot = self._bot
        uptime = time() - self._start_time

        # Unrealized P&L from open positions
        unrealized = 0.0
        if bot._positions:
            unrealized = sum(p.pnl_sol for p in bot._positions.positions.values())

        realized = bot._total_realized_pnl_sol
        total = realized + unrealized

        # Emoji
        if total > 0:
            emoji = "📈"
        elif total < 0:
            emoji = "📉"
        else:
            emoji = "➖"

        # Get trade stats from DB
        stats = {}
        if bot._db:
            stats = await bot._db.get_session_stats()

        buys = stats.get("buys", 0) or 0
        sells = stats.get("sells", 0) or 0
        total_bought = stats.get("total_bought_sol", 0) or 0
        total_sold = stats.get("total_sold_sol", 0) or 0

        message = (
            f"{emoji} <b>P&L REPORT</b>\n\n"
            f"<b>Realized:</b> {realized:+.4f} SOL\n"
            f"<b>Unrealized:</b> {unrealized:+.4f} SOL\n"
            f"<b>Total:</b> {total:+.4f} SOL\n\n"
            f"📊 <b>Trade Stats:</b>\n"
            f"  Buys: {buys} ({total_bought:.4f} SOL)\n"
            f"  Sells: {sells} ({total_sold:.4f} SOL)\n"
            f"  Open positions: {bot._positions.open_count if bot._positions else 0}\n"
            f"  Consecutive losses: {bot._consecutive_losses}\n\n"
            f"⏱️ Session duration: {self._format_duration(uptime)}"
        )
        await self._reply(chat_id, message)

    async def _cmd_pause(self, chat_id: str, args: str):
        """Handle /pause - Pause new buy execution."""
        bot = self._bot

        if bot._killed:
            await self._reply(chat_id, "🚨 Bot is KILLED. Use /resume after clearing kill state.")
            return

        if bot._paused:
            await self._reply(chat_id, "⏸️ Already paused.")
            return

        bot._paused = True
        logger.info("Bot PAUSED via Telegram command")
        await self._reply(
            chat_id,
            "⏸️ <b>BOT PAUSED</b>\n\n"
            "New buys are halted.\n"
            "Position monitoring and auto-sells continue.\n"
            "Use /resume to restart buying."
        )

    async def _cmd_resume(self, chat_id: str, args: str):
        """Handle /resume - Resume buy execution."""
        bot = self._bot

        if bot._killed:
            # Allow resuming from kill state
            bot._killed = False
            bot._paused = False
            bot._running = True
            logger.info("Bot RESUMED from KILL state via Telegram command")
            await self._reply(
                chat_id,
                "🟢 <b>BOT RESUMED FROM KILL</b>\n\n"
                "Kill switch reset. Trading reactivated.\n"
                "⚠️ Monitor carefully."
            )
            return

        if not bot._paused:
            await self._reply(chat_id, "🟢 Bot is already running.")
            return

        bot._paused = False
        logger.info("Bot RESUMED via Telegram command")
        await self._reply(
            chat_id,
            "🟢 <b>BOT RESUMED</b>\n\n"
            "Buy execution reactivated.\n"
            "All systems operational."
        )

    async def _cmd_blacklist(self, chat_id: str, args: str):
        """Handle /blacklist - Show or add to blacklist.

        Usage:
            /blacklist          - Show all blacklisted creators
            /blacklist <addr>   - Manually blacklist a creator address
        """
        bot = self._bot

        if not bot._blacklist:
            await self._reply(chat_id, "❌ Blacklist not initialized.")
            return

        # If args provided, add to blacklist
        if args.strip():
            address = args.strip()
            if len(address) < 32 or len(address) > 44:
                await self._reply(chat_id, "❌ Invalid address. Must be a Solana public key (32-44 chars).")
                return

            newly_added = await bot._blacklist.add(
                creator_address=address,
                reason="manual",
                related_mint="",
                related_symbol="(manual via Telegram)",
            )

            if newly_added:
                await self._reply(
                    chat_id,
                    f"🚫 <b>BLACKLISTED</b>\n\n"
                    f"<code>{address}</code>\n\n"
                    f"Reason: Manual (Telegram command)\n"
                    f"Total blacklisted: {bot._blacklist.count}"
                )
            else:
                await self._reply(chat_id, f"ℹ️ Address already blacklisted: <code>{address[:20]}...</code>")
            return

        # Show blacklist
        entries = await bot._blacklist.get_all()
        if not entries:
            await self._reply(chat_id, "📭 Blacklist is empty.")
            return

        lines = [f"🚫 <b>BLACKLIST</b> ({len(entries)} creators)\n"]

        for i, entry in enumerate(entries[:20], 1):  # Cap at 20
            addr = entry["creator_address"]
            reason = entry.get("reason", "unknown")
            symbol = entry.get("related_symbol", "")

            reason_display = {
                "manual": "👤 Manual",
                "rug_liquidity_pull": "💀 Liquidity pull",
                "rug_stop_loss_hit": "🛑 Stop loss",
                "repeated_rugs": "🔁 Repeated rugs",
                "suspicious_pattern": "⚠️ Suspicious",
            }.get(reason, reason)

            token_str = f" [{symbol}]" if symbol else ""
            lines.append(f"{i}. <code>{addr[:16]}...</code>{token_str}\n   {reason_display}")

        if len(entries) > 20:
            lines.append(f"\n... and {len(entries) - 20} more")

        lines.append(f"\nℹ️ Add: /blacklist <address>")
        await self._reply(chat_id, "\n".join(lines))

    async def _cmd_kill(self, chat_id: str, args: str):
        """Handle /kill - Emergency kill switch activation."""
        bot = self._bot

        if bot._killed:
            await self._reply(chat_id, "🚨 Kill switch already active. Use /resume to reset.")
            return

        # Require confirmation
        if args.strip().lower() != "confirm":
            await self._reply(
                chat_id,
                "⚠️ <b>KILL SWITCH</b>\n\n"
                "This will:\n"
                "• Close ALL open positions immediately\n"
                "• Halt all trading\n"
                "• Require /resume to restart\n\n"
                "To confirm, send: /kill confirm"
            )
            return

        # Execute kill
        bot._killed = True
        bot._running = False
        logger.critical("KILL SWITCH triggered via Telegram command")

        positions_closed = 0
        if bot._positions:
            closed = await bot._positions.emergency_close_all()
            positions_closed = len(closed)

        await self._reply(
            chat_id,
            f"🚨🚨🚨 <b>KILL SWITCH ACTIVATED</b> 🚨🚨🚨\n\n"
            f"<b>Positions closed:</b> {positions_closed}\n"
            f"<b>Trading:</b> HALTED\n\n"
            f"Use /resume to restart trading."
        )

    async def _cmd_logs(self, chat_id: str, args: str):
        """Handle /logs - Show recent log entries."""
        # Parse optional count
        count = 15
        if args.strip().isdigit():
            count = min(int(args.strip()), 40)  # Cap at 40

        entries = log_capture.get_recent(count)

        if not entries:
            await self._reply(chat_id, "📝 No recent log entries captured.")
            return

        lines = [f"📝 <b>RECENT LOGS</b> (last {len(entries)})\n"]
        lines.append("<pre>")
        for entry in entries:
            # Truncate long lines
            truncated = entry[:80] + "..." if len(entry) > 80 else entry
            lines.append(truncated)
        lines.append("</pre>")

        message = "\n".join(lines)
        # Telegram message limit is 4096 chars
        if len(message) > 4000:
            message = message[:4000] + "\n...</pre>"

        await self._reply(chat_id, message)

    async def _cmd_help(self, chat_id: str, args: str):
        """Handle /help - Show available commands."""
        message = (
            "🤖 <b>SOLBOT COMMANDS</b>\n\n"
            "<b>Monitoring:</b>\n"
            "  /status - Bot status overview\n"
            "  /positions - Open positions with P&L\n"
            "  /pnl - Session P&L report\n"
            "  /logs [N] - Recent log entries\n\n"
            "<b>Control:</b>\n"
            "  /pause - Pause new buys\n"
            "  /resume - Resume trading\n"
            "  /kill confirm - Emergency shutdown\n\n"
            "<b>Management:</b>\n"
            "  /blacklist - View blacklisted creators\n"
            "  /blacklist <addr> - Add to blacklist\n"
        )
        await self._reply(chat_id, message)

    # ── Internal Helpers ────────────────────────────────────────────────

    async def _reply(self, chat_id: str, text: str):
        """Send a reply message to a chat."""
        if not self._session:
            return

        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        try:
            async with self._session.post(
                f"{self._base_url}/sendMessage", json=payload
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"Command reply failed ({resp.status}): {body[:200]}")
        except Exception as e:
            logger.error(f"Command reply error: {e}")

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """Format seconds into human-readable duration."""
        if seconds < 60:
            return f"{seconds:.0f}s"
        elif seconds < 3600:
            m = int(seconds // 60)
            s = int(seconds % 60)
            return f"{m}m {s}s"
        else:
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            return f"{h}h {m}m"
