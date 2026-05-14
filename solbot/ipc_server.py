"""Async IPC server for inter-process command routing.

Exposes Solbot commands via a Unix domain socket so that external
processes (OpenClaw, other bots) can send commands and receive
formatted responses without direct process coupling.

Protocol:
    - JSON-based request/response over Unix socket
    - Each message is newline-delimited JSON
    - Request:  {"command": "status", "args": "", "auth_token": "...", "request_id": "..."}
    - Response: {"ok": true, "response": "...", "request_id": "..."}

Security:
    - Auth token required on every request
    - Only configured admin tokens are accepted
    - Socket file permissions restricted to owner

Architecture:
    - Runs as asyncio.start_unix_server inside Solbot's event loop
    - Routes commands to the existing CommandHandler logic
    - Returns formatted HTML responses (same as Telegram)
    - Supports future multi-bot routing via service_id field
"""

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Coroutine, Optional

from solbot.logger import get_logger

if TYPE_CHECKING:
    from solbot.bot import Solbot

logger = get_logger("ipc_server")

DEFAULT_SOCKET_PATH = "/tmp/solbot_ipc.sock"


class IPCServer:
    """Unix domain socket IPC server for Solbot command routing.

    Allows external processes (OpenClaw) to send commands and receive
    responses via a lightweight JSON protocol over Unix sockets.

    Features:
    - Zero-dependency (stdlib asyncio)
    - Auth token validation
    - Request/response correlation via request_id
    - Graceful connection handling
    - Multi-bot ready (service_id field)
    """

    def __init__(
        self,
        bot: "Solbot",
        socket_path: str = DEFAULT_SOCKET_PATH,
        auth_token: str = "",
        service_id: str = "solbot",
    ):
        self._bot = bot
        self._socket_path = socket_path
        self._auth_token = auth_token
        self._service_id = service_id
        self._server: Optional[asyncio.AbstractServer] = None
        self._running = False
        self._connections: int = 0

        # Import command handler methods (reuse existing logic)
        self._command_handlers: dict[str, Callable] = {}

    async def start(self):
        """Start the IPC server listening on the Unix socket."""
        if not self._auth_token:
            logger.warning("IPC server DISABLED (no IPC_AUTH_TOKEN configured)")
            return

        # Clean up stale socket file
        socket_path = Path(self._socket_path)
        if socket_path.exists():
            socket_path.unlink()

        # Ensure parent directory exists
        socket_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            self._server = await asyncio.start_unix_server(
                self._handle_connection,
                path=self._socket_path,
            )

            # Restrict socket permissions (owner only)
            os.chmod(self._socket_path, 0o600)

            self._running = True
            self._register_handlers()
            logger.info(f"IPC server started | socket={self._socket_path} | service={self._service_id}")

        except Exception as e:
            logger.error(f"Failed to start IPC server: {e}")

    async def stop(self):
        """Stop the IPC server and clean up."""
        self._running = False

        if self._server:
            self._server.close()
            await self._server.wait_closed()

        # Remove socket file
        socket_path = Path(self._socket_path)
        if socket_path.exists():
            socket_path.unlink()

        logger.info("IPC server stopped")

    def _register_handlers(self):
        """Register command handlers that mirror the Telegram command set."""
        self._command_handlers = {
            "status": self._cmd_status,
            "positions": self._cmd_positions,
            "pnl": self._cmd_pnl,
            "pause": self._cmd_pause,
            "resume": self._cmd_resume,
            "blacklist": self._cmd_blacklist,
            "kill": self._cmd_kill,
            "logs": self._cmd_logs,
            "help": self._cmd_help,
            "ping": self._cmd_ping,
        }

    # ── Connection Handling ─────────────────────────────────────────────

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        """Handle a single IPC client connection."""
        self._connections += 1
        peer = f"conn-{self._connections}"
        logger.debug(f"IPC connection opened: {peer}")

        try:
            while self._running:
                # Read a line (newline-delimited JSON)
                data = await asyncio.wait_for(reader.readline(), timeout=60.0)
                if not data:
                    break  # Connection closed

                # Parse request
                try:
                    request = json.loads(data.decode("utf-8").strip())
                except json.JSONDecodeError:
                    await self._send_error(writer, "Invalid JSON", "")
                    continue

                # Process request
                response = await self._process_request(request)

                # Send response
                response_bytes = json.dumps(response).encode("utf-8") + b"\n"
                writer.write(response_bytes)
                await writer.drain()

        except asyncio.TimeoutError:
            logger.debug(f"IPC connection timed out: {peer}")
        except asyncio.CancelledError:
            pass
        except ConnectionResetError:
            pass
        except Exception as e:
            logger.error(f"IPC connection error ({peer}): {e}")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            logger.debug(f"IPC connection closed: {peer}")

    async def _process_request(self, request: dict) -> dict:
        """Process a single IPC request and return a response."""
        request_id = request.get("request_id", str(uuid.uuid4())[:8])

        # Validate auth token
        token = request.get("auth_token", "")
        if token != self._auth_token:
            logger.warning(f"IPC auth failed | request_id={request_id}")
            return {
                "ok": False,
                "error": "Authentication failed",
                "request_id": request_id,
                "service_id": self._service_id,
            }

        # Extract command and args
        command = request.get("command", "").strip().lower()
        args = request.get("args", "").strip()

        if not command:
            return {
                "ok": False,
                "error": "No command specified",
                "request_id": request_id,
                "service_id": self._service_id,
            }

        # Dispatch to handler
        handler = self._command_handlers.get(command)
        if not handler:
            return {
                "ok": False,
                "error": f"Unknown command: {command}",
                "available_commands": list(self._command_handlers.keys()),
                "request_id": request_id,
                "service_id": self._service_id,
            }

        try:
            response_text = await handler(args)
            return {
                "ok": True,
                "response": response_text,
                "command": command,
                "request_id": request_id,
                "service_id": self._service_id,
            }
        except Exception as e:
            logger.error(f"IPC command error ({command}): {e}")
            return {
                "ok": False,
                "error": f"Command execution failed: {e}",
                "command": command,
                "request_id": request_id,
                "service_id": self._service_id,
            }

    async def _send_error(self, writer: asyncio.StreamWriter, error: str, request_id: str):
        """Send an error response."""
        response = {
            "ok": False,
            "error": error,
            "request_id": request_id,
            "service_id": self._service_id,
        }
        writer.write(json.dumps(response).encode("utf-8") + b"\n")
        await writer.drain()

    # ── Command Implementations ─────────────────────────────────────────
    # These return formatted HTML strings (same as Telegram commands)

    async def _cmd_ping(self, args: str) -> str:
        """Health check."""
        return "🏓 pong"

    async def _cmd_status(self, args: str) -> str:
        """Bot status overview."""
        bot = self._bot
        from time import time as _time

        mode = "PAPER" if bot._config.jupiter.paper_trade else "LIVE"

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

        return (
            f"📊 <b>SOLBOT STATUS</b>\n\n"
            f"<b>State:</b> {state}\n"
            f"<b>Mode:</b> {mode}\n\n"
            f"📈 <b>Activity:</b>\n"
            f"  Positions: {positions_count}/{max_positions}\n"
            f"  Blacklisted: {blacklist_count}\n"
            f"  Tokens seen: {tokens_seen}\n"
            f"  Realized P&L: {bot._total_realized_pnl_sol:+.4f} SOL\n"
            f"  Consecutive losses: {bot._consecutive_losses}\n\n"
            f"⚙️ <b>Config:</b>\n"
            f"  Buy amount: {bot._config.jupiter.buy_amount_sol} SOL\n"
            f"  Stop loss: {bot._config.trading.stop_loss_pct}%\n"
            f"  Kill switch: {'ON' if bot._config.trading.kill_switch_enabled else 'OFF'}"
        )

    async def _cmd_positions(self, args: str) -> str:
        """List open positions."""
        bot = self._bot
        if not bot._positions or bot._positions.open_count == 0:
            return "📭 No open positions."

        lines = ["📋 <b>OPEN POSITIONS</b>\n"]
        for i, (mint, pos) in enumerate(bot._positions.positions.items(), 1):
            pnl_emoji = "🟢" if pos.pnl_pct >= 0 else "🔴"
            lines.append(
                f"\n<b>{i}. {pos.symbol}</b>\n"
                f"  Entry: {pos.entry_price_sol:.4f} SOL\n"
                f"  {pnl_emoji} P&L: {pos.pnl_pct:+.1f}%\n"
                f"  Confidence: {pos.confidence}"
            )

        total_pnl = sum(p.pnl_sol for p in bot._positions.positions.values())
        lines.append(f"\n━━━━━━━━━━━━━━━━━━━━\n<b>Unrealized P&L:</b> {total_pnl:+.4f} SOL")
        return "\n".join(lines)

    async def _cmd_pnl(self, args: str) -> str:
        """P&L summary."""
        bot = self._bot
        unrealized = 0.0
        if bot._positions:
            unrealized = sum(p.pnl_sol for p in bot._positions.positions.values())

        realized = bot._total_realized_pnl_sol
        total = realized + unrealized
        emoji = "📈" if total > 0 else ("📉" if total < 0 else "➖")

        return (
            f"{emoji} <b>P&L REPORT</b>\n\n"
            f"<b>Realized:</b> {realized:+.4f} SOL\n"
            f"<b>Unrealized:</b> {unrealized:+.4f} SOL\n"
            f"<b>Total:</b> {total:+.4f} SOL"
        )

    async def _cmd_pause(self, args: str) -> str:
        """Pause buying."""
        bot = self._bot
        if bot._killed:
            return "🚨 Bot is KILLED. Use resume to reset."
        if bot._paused:
            return "⏸️ Already paused."

        bot._paused = True
        logger.info("Bot PAUSED via IPC command")
        return "⏸️ <b>BOT PAUSED</b>\n\nNew buys halted. Monitoring continues.\nUse resume to restart."

    async def _cmd_resume(self, args: str) -> str:
        """Resume buying."""
        bot = self._bot
        if bot._killed:
            bot._killed = False
            bot._paused = False
            bot._running = True
            logger.info("Bot RESUMED from KILL via IPC")
            return "🟢 <b>BOT RESUMED FROM KILL</b>\n\nTrading reactivated."

        if not bot._paused:
            return "🟢 Bot is already running."

        bot._paused = False
        logger.info("Bot RESUMED via IPC")
        return "🟢 <b>BOT RESUMED</b>\n\nBuy execution reactivated."

    async def _cmd_blacklist(self, args: str) -> str:
        """View or add to blacklist."""
        bot = self._bot
        if not bot._blacklist:
            return "❌ Blacklist not initialized."

        if args.strip():
            address = args.strip()
            if len(address) < 32 or len(address) > 44:
                return "❌ Invalid address (must be 32-44 chars)."

            newly_added = await bot._blacklist.add(
                creator_address=address,
                reason="manual",
                related_symbol="(IPC command)",
            )
            if newly_added:
                return f"🚫 <b>BLACKLISTED</b>\n\n<code>{address}</code>\nTotal: {bot._blacklist.count}"
            return f"ℹ️ Already blacklisted."

        entries = await bot._blacklist.get_all()
        if not entries:
            return "📭 Blacklist is empty."

        lines = [f"🚫 <b>BLACKLIST</b> ({len(entries)} creators)\n"]
        for i, entry in enumerate(entries[:15], 1):
            addr = entry["creator_address"]
            lines.append(f"{i}. <code>{addr[:16]}...</code> | {entry.get('reason', '?')}")
        if len(entries) > 15:
            lines.append(f"\n... and {len(entries) - 15} more")
        return "\n".join(lines)

    async def _cmd_kill(self, args: str) -> str:
        """Emergency kill switch."""
        bot = self._bot
        if bot._killed:
            return "🚨 Kill switch already active. Use resume to reset."

        if args.strip().lower() != "confirm":
            return "⚠️ Send 'kill confirm' to activate kill switch."

        bot._killed = True
        bot._running = False
        logger.critical("KILL SWITCH triggered via IPC")

        positions_closed = 0
        if bot._positions:
            closed = await bot._positions.emergency_close_all()
            positions_closed = len(closed)

        return (
            f"🚨🚨🚨 <b>KILL SWITCH ACTIVATED</b> 🚨🚨🚨\n\n"
            f"Positions closed: {positions_closed}\n"
            f"Trading: HALTED\n\nUse resume to restart."
        )

    async def _cmd_logs(self, args: str) -> str:
        """Recent logs."""
        from solbot.commands import log_capture

        count = 15
        if args.strip().isdigit():
            count = min(int(args.strip()), 30)

        entries = log_capture.get_recent(count)
        if not entries:
            return "📝 No recent logs."

        lines = [f"📝 <b>RECENT LOGS</b> ({len(entries)})\n", "<pre>"]
        for entry in entries:
            lines.append(entry[:80] + "..." if len(entry) > 80 else entry)
        lines.append("</pre>")
        result = "\n".join(lines)
        return result[:4000]  # Telegram limit

    async def _cmd_help(self, args: str) -> str:
        """Available commands."""
        return (
            "🤖 <b>SOLBOT IPC COMMANDS</b>\n\n"
            "  status    - Bot status overview\n"
            "  positions - Open positions with P&L\n"
            "  pnl       - Session P&L report\n"
            "  pause     - Pause new buys\n"
            "  resume    - Resume trading\n"
            "  blacklist [addr] - View/add blacklist\n"
            "  kill confirm - Emergency shutdown\n"
            "  logs [N]  - Recent log entries\n"
            "  ping      - Health check\n"
            "  help      - This message"
        )
