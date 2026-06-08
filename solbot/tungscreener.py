"""Tungscreener early-signal sentiment scraper."""
import asyncio
import aiohttp
import logging
from typing import List, Dict, Any

logger = logging.getLogger("bot.tungscreener")

class TungscreenerScraper:
    """Scraper for tungscreener.com to extract trending tokens and sentiment."""

    def __init__(self, bot):
        self.bot = bot
        self._running = False
        # Updated to the correct public trending endpoint
        self._url = "https://tungscreener.com/api/v1/trending"
        self._headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Referer": "https://tungscreener.com/"
        }

    async def start_monitoring(self):
        """Background task for periodic scraping."""
        self._running = True
        logger.info("Tungscreener scraper started.")
        while self._running:
            try:
                # Use a single session per poll to handle connection resets
                async with aiohttp.ClientSession(headers=self._headers) as session:
                    async with session.get(self._url, timeout=15) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            await self._process_data(data)
                        elif resp.status == 404:
                            logger.error(f"Tungscreener API 404: The endpoint {self._url} may have changed.")
                        else:
                            logger.warning(f"Tungscreener returned status {resp.status}: {await resp.text()}")
            except Exception as e:
                logger.error(f"Tungscreener scraping error: {type(e).__name__}: {e}")
            await asyncio.sleep(300)

    async def stop(self):
        self._running = False

    async def _process_data(self, data: Any):
        """Process extracted trending tokens."""
        # Tungscreener often nests data under 'data' or 'results'
        tokens = data.get("data", {}).get("tokens", []) if isinstance(data.get("data"), dict) else data.get("tokens", [])
        for token in tokens:
            symbol = token.get("symbol")
            sentiment = token.get("sentiment_score", 0)
            if sentiment > 80:
                logger.info(f"High sentiment detected on Tungscreener: {symbol} ({sentiment})")
