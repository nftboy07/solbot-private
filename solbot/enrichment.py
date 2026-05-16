"""Delayed liquidity enrichment pipeline for Solbot.

Solves: PumpPortal sends newToken events BEFORE liquidity is initialized.
Tokens arrive with liq=0.00 and get instantly rejected.

Solution: Queue zero-liquidity tokens, retry DexScreener lookups on a
schedule (2s, 5s, 10s...), and release tokens back into the scoring
pipeline once liquidity appears.
"""

import asyncio
from dataclasses import dataclass, field
from time import time
from typing import TYPE_CHECKING, Optional

from solbot.dexscreener import DexScreenerClient
from solbot.logger import get_logger
from solbot.models import TokenEvent

if TYPE_CHECKING:
    pass

logger = get_logger("enrichment")

# Default retry delays in seconds
DEFAULT_RETRY_DELAYS = [2, 5, 10, 15, 20, 25, 30]


@dataclass
class PendingToken:
    """A token waiting for liquidity data."""
    token: TokenEvent
    queued_at: float = field(default_factory=time)
    retry_count: int = 0
    last_liq: float = 0.0


class LiquidityEnrichmentQueue:
    """Async queue that retries liquidity lookups for zero-liq tokens.

    Flow:
        1. Token arrives with liq <= threshold → enqueue()
        2. Background worker retries DexScreener every N seconds
        3. If liquidity found → callback releases token to scoring pipeline
        4. If max retries exhausted → token expires with log

    Features:
        - Dedup by mint (no duplicate queue entries)
        - Configurable retry delays
        - Async-safe with locks
        - Telemetry counters
    """

    def __init__(
        self,
        dex_client: DexScreenerClient,
        retry_delays: list[float] = None,
        min_liquidity_sol: float = 0.5,
    ):
        self._dex = dex_client
        self._retry_delays = retry_delays or DEFAULT_RETRY_DELAYS
        self._max_retries = len(self._retry_delays)
        self._min_liq = min_liquidity_sol
        self._pending: dict[str, PendingToken] = {}  # mint -> PendingToken
        self._lock = asyncio.Lock()
        self._running = False
        self._worker_task: Optional[asyncio.Task] = None
        self._release_callback = None

        # Telemetry
        self.total_queued: int = 0
        self.total_released: int = 0
        self.total_expired: int = 0
        self.avg_discovery_time: float = 0.0
        self._discovery_times: list[float] = []

    @property
    def queue_size(self) -> int:
        return len(self._pending)

    def set_release_callback(self, callback):
        """Set the callback to release enriched tokens back to pipeline.

        Signature: async def callback(token: TokenEvent)
        """
        self._release_callback = callback

    def set_min_liquidity(self, val: float):
        """Update minimum liquidity threshold (for /minliq runtime changes)."""
        self._min_liq = val

    async def start(self):
        """Start the background enrichment worker."""
        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop())
        logger.info(
            f"Liquidity enrichment queue started | "
            f"retries={self._max_retries} delays={self._retry_delays}"
        )

    async def stop(self):
        """Stop the enrichment worker."""
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        logger.info(
            f"Enrichment queue stopped | "
            f"queued={self.total_queued} released={self.total_released} expired={self.total_expired}"
        )

    async def enqueue(self, token: TokenEvent) -> bool:
        """Add a token to the pending liquidity queue.

        Returns:
            True if newly queued, False if already pending (dedup).
        """
        async with self._lock:
            if token.mint in self._pending:
                return False  # Already pending, dedup

            self._pending[token.mint] = PendingToken(token=token)
            self.total_queued += 1

        logger.info(
            f"TOKEN_PENDING_LIQUIDITY: {token.symbol} | "
            f"mint={token.mint[:16]}... | liq={token.liquidity_sol:.4f} | "
            f"queue_size={self.queue_size}"
        )
        return True

    async def _worker_loop(self):
        """Background loop that processes pending tokens."""
        while self._running:
            try:
                await self._process_pending()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Enrichment worker error: {e}")

            await asyncio.sleep(1.0)  # Check every second

    async def _process_pending(self):
        """Check all pending tokens and retry enrichment where due."""
        now = time()
        to_remove: list[str] = []
        to_release: list[PendingToken] = []

        async with self._lock:
            items = list(self._pending.items())

        for mint, pt in items:
            # Determine if it's time for next retry
            if pt.retry_count >= self._max_retries:
                # Expired
                to_remove.append(mint)
                self.total_expired += 1
                logger.info(
                    f"TOKEN_EXPIRED_NO_LIQUIDITY: {pt.token.symbol} | "
                    f"mint={mint[:16]}... | retries={pt.retry_count} | "
                    f"last_liq={pt.last_liq:.4f}"
                )
                continue

            # Calculate when next retry is due
            delay = self._retry_delays[min(pt.retry_count, len(self._retry_delays) - 1)]
            next_retry_at = pt.queued_at + sum(self._retry_delays[:pt.retry_count + 1])

            if now < next_retry_at:
                continue  # Not time yet

            # Do the enrichment lookup
            pt.retry_count += 1
            pair_data = await self._dex.get_token_data(mint)

            if pair_data and pair_data.liquidity_usd > 0:
                # Convert USD liquidity to approximate SOL (rough: $150/SOL)
                liq_sol = pair_data.liquidity_usd / 150.0
                pt.last_liq = liq_sol
                pt.token.liquidity_sol = liq_sol
                pt.token.market_cap_usd = pair_data.market_cap

                logger.info(
                    f"LIQUIDITY_FOUND: {pt.token.symbol} | "
                    f"liq={liq_sol:.2f} SOL (${pair_data.liquidity_usd:.0f}) | "
                    f"mcap=${pair_data.market_cap:.0f} | "
                    f"retry={pt.retry_count}/{self._max_retries}"
                )

                if liq_sol >= self._min_liq:
                    to_release.append(pt)
                    to_remove.append(mint)
                    discovery_time = now - pt.queued_at
                    self._discovery_times.append(discovery_time)
                    if len(self._discovery_times) > 100:
                        self._discovery_times = self._discovery_times[-100:]
                    self.avg_discovery_time = sum(self._discovery_times) / len(self._discovery_times)
                else:
                    logger.info(
                        f"LIQUIDITY_RETRY: {pt.token.symbol} | "
                        f"liq={liq_sol:.2f} < required={self._min_liq:.2f} | "
                        f"retry={pt.retry_count}/{self._max_retries}"
                    )
            else:
                pt.last_liq = 0.0
                logger.info(
                    f"LIQUIDITY_RETRY: {pt.token.symbol} | "
                    f"liq=0.00 (no data) | "
                    f"retry={pt.retry_count}/{self._max_retries}"
                )

        # Remove processed tokens
        if to_remove:
            async with self._lock:
                for mint in to_remove:
                    self._pending.pop(mint, None)

        # Release enriched tokens back to pipeline
        for pt in to_release:
            self.total_released += 1
            logger.info(
                f"TOKEN_RELEASED_FOR_SCORING: {pt.token.symbol} | "
                f"liq={pt.token.liquidity_sol:.2f} SOL | "
                f"discovery_time={time() - pt.queued_at:.1f}s"
            )
            if self._release_callback:
                asyncio.create_task(self._release_callback(pt.token))

    def get_telemetry(self) -> dict:
        """Get enrichment queue telemetry for /filters command."""
        return {
            "queue_size": self.queue_size,
            "total_queued": self.total_queued,
            "total_released": self.total_released,
            "total_expired": self.total_expired,
            "avg_discovery_time_s": self.avg_discovery_time,
        }
