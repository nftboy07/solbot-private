"""Pump.fun WebSocket client for real-time token monitoring.

Uses websocket-client in a dedicated thread with an asyncio bridge
for non-blocking integration with the main event loop.
"""

import asyncio
import json
import threading
from time import time
from typing import Callable, Optional

import websocket

from solbot.config import PumpFunConfig
from solbot.logger import get_logger
from solbot.models import TokenEvent

logger = get_logger("pumpfun")


class PumpFunMonitor:
    """WebSocket-based monitor for Pump.fun events.

    Runs websocket-client in a background thread and bridges events
    into the asyncio event loop via an asyncio.Queue.
    """

    def __init__(self, config: PumpFunConfig, loop: asyncio.AbstractEventLoop):
        self._config = config
        self._loop = loop
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=1000)
        self._ws: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._reconnect_delay = 1.0
        self._max_reconnect_delay = 30.0

    @property
    def queue(self) -> asyncio.Queue[dict]:
        """Access the async queue of incoming events."""
        return self._queue

    def start(self):
        """Start the WebSocket listener in a background thread."""
        if self._running:
            logger.warning("PumpFun monitor already running")
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._run_ws_loop,
            name="pumpfun-ws",
            daemon=True,
        )
        self._thread.start()
        logger.info(f"PumpFun monitor started | url={self._config.ws_url}")

    def stop(self):
        """Gracefully stop the WebSocket listener."""
        self._running = False
        if self._ws:
            self._ws.close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        logger.info("PumpFun monitor stopped")

    def _run_ws_loop(self):
        """Reconnection loop running in the background thread."""
        while self._running:
            try:
                self._connect()
            except Exception as e:
                logger.error(f"WebSocket connection error: {e}")

            if not self._running:
                break

            # Exponential backoff reconnect
            logger.info(f"Reconnecting in {self._reconnect_delay:.1f}s...")
            threading.Event().wait(self._reconnect_delay)
            self._reconnect_delay = min(
                self._reconnect_delay * 2, self._max_reconnect_delay
            )

    def _connect(self):
        """Create and run the WebSocket connection."""
        self._ws = websocket.WebSocketApp(
            self._config.ws_url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._ws.run_forever(ping_interval=20, ping_timeout=10)

    def _on_open(self, ws):
        """Subscribe to token creation and trade events."""
        logger.info("WebSocket connected, subscribing to newTokens and trades...")
        
        # Subscribe to new token launches
        ws.send(json.dumps({"method": "subscribeNewToken"}))
        
        # Correct subscription for all trades on PumpPortal
        ws.send(json.dumps({"method": "subscribeTrade", "keys": []}))
        
        # Reset backoff on successful connection
        self._reconnect_delay = 1.0

    def _on_message(self, ws, message: str):
        """Parse incoming message and push to async queue."""
        try:
            data = json.loads(message)
            if not isinstance(data, dict):
                return

            # Bridge the raw message to the bot's event loop
            asyncio.run_coroutine_threadsafe(
                self._safe_put(data), self._loop
            )
        except json.JSONDecodeError:
            pass
        except Exception as e:
            logger.error(f"Error processing message: {e}")

    async def _safe_put(self, data: dict):
        """Put data into queue, dropping oldest if full."""
        if self._queue.full():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        await self._queue.put(data)

    def _on_error(self, ws, error):
        logger.error(f"WebSocket error: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        logger.warning(f"WebSocket closed | code={close_status_code} msg={close_msg}")
