import aiohttp
import logging
import os
import time
from typing import Optional, Dict, Any

logger = logging.getLogger("bot.gecko")

class GeckoTerminalClient:
    """GeckoTerminal API Client for Solana Token Data."""

    BASE_URL = "https://api.geckoterminal.com/api/v2"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("COINGECKO_API_KEY")
        self._session: Optional[aiohttp.ClientSession] = None
        self._rate_limited_until = 0.0
        self.headers = {}
        if self.api_key:
            self.headers["x-cg-pro-api-key"] = self.api_key
            logger.info("GeckoTerminal initialized with Pro API Key.")

    async def start(self):
        if not self._session:
            self._session = aiohttp.ClientSession(headers=self.headers)

    async def stop(self):
        if self._session:
            await self._session.close()

    def _rate_limited(self) -> bool:
        return time.time() < self._rate_limited_until

    async def get_token_info(self, mint: str) -> Optional[Dict[str, Any]]:
        """Fetch token info for a Solana mint address."""
        if self._rate_limited():
            return None
        if not self._session:
            await self.start()

        url = f"{self.BASE_URL}/networks/solana/tokens/{mint}"
        try:
            async with self._session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("data", {}).get("attributes", {})
                if resp.status == 404:
                    return None
                if resp.status == 429:
                    self._rate_limited_until = time.time() + 60
                    logger.warning("GeckoTerminal rate limited; cooling down for 60s.")
                    return None
                logger.error("GeckoTerminal Error %s: %s", resp.status, await resp.text())
        except Exception as e:
            logger.error("GeckoTerminal request failed: %s", e)
        return None

    async def evaluate_safety(self, mint: str, min_liq_usd: float = 5000) -> bool:
        """Check if token has enough liquidity to be safe for trading."""
        info = await self.get_token_info(mint)
        if not info:
            return False

        liq = float(info.get("reserve_in_usd", 0))
        volume = float(info.get("volume_usd", {}).get("h24", 0))

        logger.info("Token %s Health: Liq $%s, 24h Vol $%s", mint, f"{liq:,.2f}", f"{volume:,.2f}")

        if liq < min_liq_usd:
            logger.warning("Safety check FAILED for %s: Low liquidity ($%s)", mint, liq)
            return False
        return True