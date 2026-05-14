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
    """WebSocket-based monitor for new Pump.fun token launches.

    Runs websocket-client in a background thread and bridges events
    into the asyncio event loop via an asyncio.Queue.
    """

    def __init__(self, config: PumpFunConfig, loop: asyncio.AbstractEventLoop):
        self._config = config
        self._loop = loop
        self._queue: asyncio.Queue[TokenEvent] = asyncio.Queue(maxsize=500)
        self._ws: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._reconnect_delay = 1.0
        self._max_reconnect_delay = 30.0

    @property
    def queue(self) -> asyncio.Queue[TokenEvent]:
        """Access the async queue of incoming token events."""
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
        """Subscribe to new token creation events."""
        logger.info("WebSocket connected, subscribing to newTokens...")
        subscribe_msg = {
            "method": "subscribeNewToken",
        }
        ws.send(json.dumps(subscribe_msg))
        # Reset backoff on successful connection
        self._reconnect_delay = 1.0

    def _on_message(self, ws, message: str):
        """Parse incoming message and push to async queue."""
        try:
            data = json.loads(message)
            token = self._parse_token_event(data)
            if token:
                # Thread-safe put into asyncio queue
                asyncio.run_coroutine_threadsafe(
                    self._safe_put(token), self._loop
                )
        except json.JSONDecodeError:
            logger.debug(f"Non-JSON message: {message[:100]}")
        except Exception as e:
            logger.error(f"Error processing message: {e}")

    async def _safe_put(self, token: TokenEvent):
        """Put token into queue, dropping oldest if full."""
        if self._queue.full():
            try:
                self._queue.get_nowait()  # Drop oldest
            except asyncio.QueueEmpty:
                pass
        await self._queue.put(token)

    def _on_error(self, ws, error):
        """Handle WebSocket errors."""
        logger.error(f"WebSocket error: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        """Handle WebSocket disconnection."""
        logger.warning(
            f"WebSocket closed | code={close_status_code} msg={close_msg}"
        )

    @staticmethod
    def _parse_token_event(data: dict) -> Optional[TokenEvent]:
        """Parse raw WebSocket data into a TokenEvent model."""
        # Pump.fun sends different event types; filter for token creation
        if not isinstance(data, dict):
            return None

        mint = data.get("mint")
        if not mint:
            return None

        return TokenEvent(
            mint=mint,
            name=data.get("name", "Unknown"),
            symbol=data.get("symbol", "???"),
            uri=data.get("uri"),
            creator=data.get("traderPublicKey"),
            initial_buy_sol=float(data.get("initialBuy", 0)) / 1e9,
            market_cap_usd=float(data.get("marketCapSol", 0)) * 150,  # Approx SOL price
            liquidity_sol=float(data.get("vSolInBondingCurve", 0)) / 1e9,
            timestamp=time(),
        )
