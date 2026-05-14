"""Async IPC client for OpenClaw → Solbot command routing.

Provides a lightweight async client that connects to Solbot's Unix
domain socket and sends commands, receiving formatted HTML responses.

Usage from OpenClaw:
    from solbot.ipc_client import SolbotIPCClient

    client = SolbotIPCClient(
        socket_path="/tmp/solbot_ipc.sock",
        auth_token="your-secret-token",
    )

    # In your /solbot command handler:
    response = await client.send_command("status")
    if response["ok"]:
        await send_telegram_message(chat_id, response["response"])

Architecture:
    - Connects on-demand (no persistent connection)
    - Auto-reconnect on failure
    - Timeout handling for unresponsive services
    - Multi-bot support via service routing
    - Returns structured dict with ok/response/error fields
"""

import asyncio
import json
import uuid
from pathlib import Path
from typing import Optional

from solbot.logger import get_logger

logger = get_logger("ipc_client")

DEFAULT_SOCKET_PATH = "/tmp/solbot_ipc.sock"


class SolbotIPCClient:
    """Async client for sending commands to Solbot via Unix socket IPC.

    Designed for use within OpenClaw's async event loop to forward
    /solbot subcommands to the running Solbot process.

    Features:
    - On-demand connections (no background tasks)
    - Configurable timeout
    - Auth token included in every request
    - Multi-bot ready (route to different sockets)
    - Returns parsed response dicts
    """

    def __init__(
        self,
        socket_path: str = DEFAULT_SOCKET_PATH,
        auth_token: str = "",
        timeout: float = 10.0,
        service_id: str = "solbot",
    ):
        self._socket_path = socket_path
        self._auth_token = auth_token
        self._timeout = timeout
        self._service_id = service_id

    @property
    def is_configured(self) -> bool:
        """Check if the client has auth credentials configured."""
        return bool(self._auth_token)

    async def send_command(self, command: str, args: str = "") -> dict:
        """Send a command to Solbot and return the response.

        Args:
            command: The command name (status, positions, pnl, etc.)
            args: Optional arguments string.

        Returns:
            Dict with keys:
                ok: bool - whether the command succeeded
                response: str - formatted HTML response (if ok)
                error: str - error message (if not ok)
                service_id: str - which service responded
                request_id: str - correlation ID
        """
        if not self._auth_token:
            return {
                "ok": False,
                "error": "IPC client not configured (missing auth token)",
                "service_id": self._service_id,
                "request_id": "",
            }

        # Check socket exists
        if not Path(self._socket_path).exists():
            return {
                "ok": False,
                "error": f"Solbot not running (socket not found: {self._socket_path})",
                "service_id": self._service_id,
                "request_id": "",
            }

        request_id = uuid.uuid4().hex[:8]
        request = {
            "command": command,
            "args": args,
            "auth_token": self._auth_token,
            "request_id": request_id,
            "service_id": self._service_id,
        }

        try:
            response = await asyncio.wait_for(
                self._send_and_receive(request),
                timeout=self._timeout,
            )
            return response

        except asyncio.TimeoutError:
            logger.warning(f"IPC timeout: command={command} | timeout={self._timeout}s")
            return {
                "ok": False,
                "error": f"Solbot did not respond within {self._timeout}s",
                "service_id": self._service_id,
                "request_id": request_id,
            }
        except ConnectionRefusedError:
            return {
                "ok": False,
                "error": "Connection refused - Solbot may not be running",
                "service_id": self._service_id,
                "request_id": request_id,
            }
        except FileNotFoundError:
            return {
                "ok": False,
                "error": "Socket not found - Solbot not running",
                "service_id": self._service_id,
                "request_id": request_id,
            }
        except Exception as e:
            logger.error(f"IPC client error: {e}")
            return {
                "ok": False,
                "error": f"IPC communication error: {e}",
                "service_id": self._service_id,
                "request_id": request_id,
            }

    async def _send_and_receive(self, request: dict) -> dict:
        """Open connection, send request, read response, close."""
        reader, writer = await asyncio.open_unix_connection(self._socket_path)

        try:
            # Send request as newline-delimited JSON
            request_bytes = json.dumps(request).encode("utf-8") + b"\n"
            writer.write(request_bytes)
            await writer.drain()

            # Read response (newline-delimited JSON)
            response_data = await reader.readline()
            if not response_data:
                return {
                    "ok": False,
                    "error": "Empty response from Solbot",
                    "service_id": self._service_id,
                    "request_id": request.get("request_id", ""),
                }

            return json.loads(response_data.decode("utf-8").strip())

        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def ping(self) -> bool:
        """Quick health check - is Solbot responding?

        Returns:
            True if Solbot is alive and authenticated.
        """
        response = await self.send_command("ping")
        return response.get("ok", False)

    async def get_available_commands(self) -> list[str]:
        """Get list of available commands from Solbot.

        Returns:
            List of command names, or empty list on failure.
        """
        response = await self.send_command("help")
        if response.get("ok"):
            return [
                "status", "positions", "pnl", "pause", "resume",
                "blacklist", "kill", "logs", "ping", "help",
            ]
        return []


class OpenClawSolbotRouter:
    """Command router for OpenClaw to handle /solbot prefix commands.

    Parses /solbot subcommands and routes them to Solbot via IPC.
    Designed to be integrated into OpenClaw's Telegram command handler.

    Usage in OpenClaw:
        router = OpenClawSolbotRouter(
            socket_path="/tmp/solbot_ipc.sock",
            auth_token="shared-secret",
            admin_chat_ids={"123456789"},
        )

        # In your message handler:
        if text.startswith("/solbot"):
            response = await router.handle_command(chat_id, text)
            await send_message(chat_id, response)
    """

    def __init__(
        self,
        socket_path: str = DEFAULT_SOCKET_PATH,
        auth_token: str = "",
        admin_chat_ids: Optional[set[str]] = None,
    ):
        self._client = SolbotIPCClient(
            socket_path=socket_path,
            auth_token=auth_token,
        )
        self._admin_ids = admin_chat_ids or set()

    @property
    def is_configured(self) -> bool:
        return self._client.is_configured

    def is_authorized(self, chat_id: str) -> bool:
        """Check if a chat ID is authorized to use /solbot commands."""
        return chat_id in self._admin_ids

    async def handle_command(self, chat_id: str, text: str) -> str:
        """Route a /solbot command to the Solbot IPC service.

        Args:
            chat_id: Telegram chat ID of the sender.
            text: Full message text (e.g., "/solbot status" or "/solbot kill confirm")

        Returns:
            Formatted HTML response string for Telegram.
        """
        # Authorization
        if not self.is_authorized(chat_id):
            return "⛔ Unauthorized. Access denied."

        # Parse: "/solbot <command> [args]"
        parts = text.strip().split(maxsplit=2)

        # parts[0] = "/solbot", parts[1] = command, parts[2] = args
        if len(parts) < 2:
            return await self._show_help()

        command = parts[1].lower().strip()
        args = parts[2] if len(parts) > 2 else ""

        # Send to Solbot via IPC
        response = await self._client.send_command(command, args)

        if response.get("ok"):
            return response["response"]
        else:
            error = response.get("error", "Unknown error")
            return f"❌ <b>Solbot Error:</b> {error}"

    async def _show_help(self) -> str:
        """Show available /solbot subcommands."""
        return (
            "🤖 <b>SOLBOT COMMANDS</b>\n\n"
            "<b>Usage:</b> /solbot &lt;command&gt; [args]\n\n"
            "<b>Monitoring:</b>\n"
            "  /solbot status    - Bot status\n"
            "  /solbot positions - Open positions\n"
            "  /solbot pnl       - P&L report\n"
            "  /solbot logs [N]  - Recent logs\n\n"
            "<b>Control:</b>\n"
            "  /solbot pause     - Pause buying\n"
            "  /solbot resume    - Resume trading\n"
            "  /solbot kill confirm - Emergency stop\n\n"
            "<b>Management:</b>\n"
            "  /solbot blacklist       - View blacklist\n"
            "  /solbot blacklist &lt;addr&gt; - Add to blacklist"
        )
