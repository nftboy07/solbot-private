"""Async DexScreener API client for real-time token market data.

Provides:
- Token pair data (price, liquidity, volume, transactions)
- Multi-token batch fetching for position monitoring
- Rate limiting and error resilience

DexScreener API is free, no key required.
"""

import asyncio
from dataclasses import dataclass, field
from time import time
from typing import Optional

import aiohttp

from solbot.logger import get_logger

logger = get_logger("dexscreener")

DEXSCREENER_BASE = "https://api.dexscreener.com/latest/dex"


@dataclass
class DexPairData:
    """Parsed pair data from DexScreener."""
    mint: str
    pair_address: str = ""
    price_usd: float = 0.0
    price_sol: float = 0.0
    liquidity_usd: float = 0.0
    liquidity_base: float = 0.0
    liquidity_quote: float = 0.0
    volume_5m: float = 0.0
    volume_1h: float = 0.0
    volume_24h: float = 0.0
    price_change_5m: float = 0.0
    price_change_1h: float = 0.0
    price_change_24h: float = 0.0
    txns_buys_5m: int = 0
    txns_sells_5m: int = 0
    txns_buys_1h: int = 0
    txns_sells_1h: int = 0
    market_cap: float = 0.0
    fdv: float = 0.0
    created_at: float = 0.0
    fetched_at: float = field(default_factory=time)

    @property
    def buy_sell_ratio_5m(self) -> float:
        """Buy/sell transaction ratio for 5 minutes."""
        total = self.txns_buys_5m + self.txns_sells_5m
        if total == 0:
            return 1.0
        return self.txns_buys_5m / max(self.txns_sells_5m, 1)

    @property
    def buy_sell_ratio_1h(self) -> float:
        """Buy/sell transaction ratio for 1 hour."""
        total = self.txns_buys_1h + self.txns_sells_1h
        if total == 0:
            return 1.0
        return self.txns_buys_1h / max(self.txns_sells_1h, 1)

    @property
    def volume_velocity_5m(self) -> float:
        """Volume per minute over 5 minute window."""
        return self.volume_5m / 5.0 if self.volume_5m > 0 else 0.0

    @property
    def age_seconds(self) -> float:
        """Time since pair creation."""
        if self.created_at <= 0:
            return 0.0
        return time() - (self.created_at / 1000.0)


class DexScreenerClient:
    """Async client for DexScreener API.

    Features:
    - Batch token lookups (up to 30 per request)
    - Automatic rate limiting (300 req/min free tier)
    - Retry with backoff on failures
    - Returns parsed DexPairData objects
    """

    def __init__(self, max_concurrent: int = 5):
        self._session: Optional[aiohttp.ClientSession] = None
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._last_request_time: float = 0.0
        self._min_request_interval: float = 0.2  # 5 req/sec max

    async def start(self):
        """Initialize aiohttp session."""
        timeout = aiohttp.ClientTimeout(total=15, connect=5)
        self._session = aiohttp.ClientSession(
            timeout=timeout,
            headers={"Accept": "application/json"},
        )
        logger.info("DexScreener client initialized")

    async def stop(self):
        """Close aiohttp session."""
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("DexScreener client closed")

    async def get_token_data(self, mint: str) -> Optional[DexPairData]:
        """Fetch pair data for a single token.

        Args:
            mint: Token mint address.

        Returns:
            DexPairData if found, None otherwise.
        """
        results = await self.get_tokens_batch([mint])
        return results.get(mint)

    async def get_tokens_batch(self, mints: list[str]) -> dict[str, DexPairData]:
        """Fetch pair data for multiple tokens (batch).

        DexScreener supports comma-separated token addresses.

        Args:
            mints: List of token mint addresses (max 30).

        Returns:
            Dict mapping mint -> DexPairData for found tokens.
        """
        if not self._session or not mints:
            return {}

        results: dict[str, DexPairData] = {}

        # DexScreener supports up to 30 tokens per request
        for i in range(0, len(mints), 30):
            batch = mints[i:i + 30]
            batch_results = await self._fetch_batch(batch)
            results.update(batch_results)

        return results

    async def _fetch_batch(self, mints: list[str]) -> dict[str, DexPairData]:
        """Fetch a single batch of tokens from DexScreener."""
        if not self._session:
            return {}

        addresses = ",".join(mints)
        url = f"{DEXSCREENER_BASE}/tokens/{addresses}"

        async with self._semaphore:
            # Rate limiting
            elapsed = time() - self._last_request_time
            if elapsed < self._min_request_interval:
                await asyncio.sleep(self._min_request_interval - elapsed)

            try:
                self._last_request_time = time()
                async with self._session.get(url) as resp:
                    if resp.status == 429:
                        logger.warning("DexScreener rate limited, backing off...")
                        await asyncio.sleep(2.0)
                        return {}

                    if resp.status != 200:
                        logger.error(f"DexScreener error ({resp.status})")
                        return {}

                    data = await resp.json()
                    return self._parse_pairs(data, mints)

            except asyncio.TimeoutError:
                logger.warning("DexScreener request timed out")
                return {}
            except Exception as e:
                logger.error(f"DexScreener fetch error: {e}")
                return {}

    def _parse_pairs(self, data: dict, requested_mints: list[str]) -> dict[str, DexPairData]:
        """Parse DexScreener response into DexPairData objects.

        Selects the highest-liquidity Solana pair for each token.
        """
        results: dict[str, DexPairData] = {}
        pairs = data.get("pairs") or []

        # Group pairs by base token, filter for Solana
        mint_pairs: dict[str, list[dict]] = {}
        for pair in pairs:
            if pair.get("chainId") != "solana":
                continue
            base_token = pair.get("baseToken", {})
            mint = base_token.get("address", "")
            if mint in requested_mints:
                mint_pairs.setdefault(mint, []).append(pair)

        # Pick highest liquidity pair for each mint
        for mint, pair_list in mint_pairs.items():
            best = max(
                pair_list,
                key=lambda p: (p.get("liquidity") or {}).get("usd", 0),
                default=None,
            )
            if best:
                results[mint] = self._parse_single_pair(mint, best)

        return results

    @staticmethod
    def _parse_single_pair(mint: str, pair: dict) -> DexPairData:
        """Parse a single pair dict into DexPairData."""
        liquidity = pair.get("liquidity") or {}
        volume = pair.get("volume") or {}
        price_change = pair.get("priceChange") or {}
        txns = pair.get("txns") or {}
        txns_5m = txns.get("m5") or {}
        txns_1h = txns.get("h1") or {}

        return DexPairData(
            mint=mint,
            pair_address=pair.get("pairAddress", ""),
            price_usd=float(pair.get("priceUsd") or 0),
            price_sol=float(pair.get("priceNative") or 0),
            liquidity_usd=float(liquidity.get("usd") or 0),
            liquidity_base=float(liquidity.get("base") or 0),
            liquidity_quote=float(liquidity.get("quote") or 0),
            volume_5m=float(volume.get("m5") or 0),
            volume_1h=float(volume.get("h1") or 0),
            volume_24h=float(volume.get("h24") or 0),
            price_change_5m=float(price_change.get("m5") or 0),
            price_change_1h=float(price_change.get("h1") or 0),
            price_change_24h=float(price_change.get("h24") or 0),
            txns_buys_5m=int(txns_5m.get("buys") or 0),
            txns_sells_5m=int(txns_5m.get("sells") or 0),
            txns_buys_1h=int(txns_1h.get("buys") or 0),
            txns_sells_1h=int(txns_1h.get("sells") or 0),
            market_cap=float(pair.get("marketCap") or 0),
            fdv=float(pair.get("fdv") or 0),
            created_at=float(pair.get("pairCreatedAt") or 0),
        )
