"""Async Birdeye API client for holder data and market intelligence.

Provides:
- Holder count and growth tracking
- Token overview data (volume, trades, price)
- Security/risk metadata

Requires BIRDEYE_API_KEY for authenticated access.
"""

import asyncio
from dataclasses import dataclass, field
from time import time
from typing import Optional

import aiohttp

from solbot.logger import get_logger

logger = get_logger("birdeye")

BIRDEYE_BASE = "https://public-api.birdeye.so"


@dataclass
class BirdeyeTokenData:
    """Parsed token data from Birdeye."""
    mint: str
    holder_count: int = 0
    price_usd: float = 0.0
    volume_24h: float = 0.0
    volume_change_pct: float = 0.0
    trade_count_24h: int = 0
    buy_count_24h: int = 0
    sell_count_24h: int = 0
    liquidity_usd: float = 0.0
    market_cap: float = 0.0
    supply: float = 0.0
    fetched_at: float = field(default_factory=time)

    @property
    def buy_sell_ratio(self) -> float:
        """24h buy/sell ratio."""
        if self.sell_count_24h == 0:
            return float(self.buy_count_24h) if self.buy_count_24h > 0 else 1.0
        return self.buy_count_24h / self.sell_count_24h


@dataclass
class BirdeyeSecurityData:
    """Security/risk metadata from Birdeye."""
    mint: str
    is_token_2022: bool = False
    freeze_authority: Optional[str] = None
    mint_authority: Optional[str] = None
    is_mutable: bool = True
    top_10_holder_pct: float = 0.0
    fetched_at: float = field(default_factory=time)

    @property
    def has_freeze_authority(self) -> bool:
        return self.freeze_authority is not None and self.freeze_authority != ""

    @property
    def has_mint_authority(self) -> bool:
        return self.mint_authority is not None and self.mint_authority != ""


class BirdeyeClient:
    """Async client for Birdeye API.

    Features:
    - Token overview (price, volume, trades)
    - Holder count tracking
    - Security metadata for risk assessment
    - Rate-limited (free tier: 100 req/min)
    """

    def __init__(self, api_key: str, max_concurrent: int = 3):
        self._api_key = api_key
        self._session: Optional[aiohttp.ClientSession] = None
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._enabled = bool(api_key)
        self._last_request_time: float = 0.0
        self._min_request_interval: float = 0.6  # ~100 req/min

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def start(self):
        """Initialize aiohttp session."""
        if not self._enabled:
            logger.info("Birdeye client DISABLED (no API key)")
            return

        timeout = aiohttp.ClientTimeout(total=15, connect=5)
        self._session = aiohttp.ClientSession(
            timeout=timeout,
            headers={
                "Accept": "application/json",
                "X-API-KEY": self._api_key,
                "x-chain": "solana",
            },
        )
        logger.info("Birdeye client initialized")

    async def stop(self):
        """Close aiohttp session."""
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("Birdeye client closed")

    async def get_token_overview(self, mint: str) -> Optional[BirdeyeTokenData]:
        """Fetch token overview including price, volume, and trade counts.

        Args:
            mint: Token mint address.

        Returns:
            BirdeyeTokenData if successful.
        """
        if not self._enabled or not self._session:
            return None

        url = f"{BIRDEYE_BASE}/defi/token_overview"
        params = {"address": mint}

        data = await self._request(url, params)
        if not data:
            return None

        d = data.get("data") or {}
        return BirdeyeTokenData(
            mint=mint,
            holder_count=int(d.get("holder") or 0),
            price_usd=float(d.get("price") or 0),
            volume_24h=float(d.get("v24hUSD") or 0),
            volume_change_pct=float(d.get("v24hChangePercent") or 0),
            trade_count_24h=int(d.get("trade24h") or 0),
            buy_count_24h=int(d.get("buy24h") or 0),
            sell_count_24h=int(d.get("sell24h") or 0),
            liquidity_usd=float(d.get("liquidity") or 0),
            market_cap=float(d.get("mc") or 0),
            supply=float(d.get("supply") or 0),
        )

    async def get_token_security(self, mint: str) -> Optional[BirdeyeSecurityData]:
        """Fetch token security metadata.

        Args:
            mint: Token mint address.

        Returns:
            BirdeyeSecurityData if successful.
        """
        if not self._enabled or not self._session:
            return None

        url = f"{BIRDEYE_BASE}/defi/token_security"
        params = {"address": mint}

        data = await self._request(url, params)
        if not data:
            return None

        d = data.get("data") or {}
        return BirdeyeSecurityData(
            mint=mint,
            is_token_2022=bool(d.get("isToken2022")),
            freeze_authority=d.get("freezeAuthority"),
            mint_authority=d.get("mintAuthority"),
            is_mutable=bool(d.get("isMutable", True)),
            top_10_holder_pct=float(d.get("top10HolderPercent") or 0),
        )

    async def get_holder_count(self, mint: str) -> int:
        """Quick fetch of just the holder count.

        Args:
            mint: Token mint address.

        Returns:
            Holder count, 0 if unavailable.
        """
        overview = await self.get_token_overview(mint)
        return overview.holder_count if overview else 0

    async def _request(self, url: str, params: dict) -> Optional[dict]:
        """Execute a rate-limited request to Birdeye API."""
        if not self._session:
            return None

        async with self._semaphore:
            # Rate limiting
            elapsed = time() - self._last_request_time
            if elapsed < self._min_request_interval:
                await asyncio.sleep(self._min_request_interval - elapsed)

            try:
                self._last_request_time = time()
                async with self._session.get(url, params=params) as resp:
                    if resp.status == 401:
                        logger.error("Birdeye API key invalid")
                        self._enabled = False
                        return None

                    if resp.status == 429:
                        logger.warning("Birdeye rate limited")
                        await asyncio.sleep(5.0)
                        return None

                    if resp.status != 200:
                        logger.error(f"Birdeye error ({resp.status})")
                        return None

                    data = await resp.json()
                    if not data.get("success"):
                        return None
                    return data

            except asyncio.TimeoutError:
                logger.warning("Birdeye request timed out")
                return None
            except Exception as e:
                logger.error(f"Birdeye request error: {e}")
                return None
