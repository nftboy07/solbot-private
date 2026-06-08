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
        self._url = "https://tungscreener.com/api/trending"
        self._headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

    async def start_monitoring(self):
        """Background task for periodic scraping."""
        self._running = True
        logger.info("Tungscreener scraper started.")
        while self._running:
            try:
                async with aiohttp.ClientSession(headers=self._headers) as session:
                    async with session.get(self._url, timeout=10) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            await self._process_data(data)
                        else:
                            logger.warning(f"Tungscreener returned status {resp.status}")
            except Exception as e:
                logger.error(f"Tungscreener scraping error: {e}")
            await asyncio.sleep(300)

    async def stop(self):
        self._running = False

    async def _process_data(self, data: Any):
        """Process extracted trending tokens."""
        tokens = data.get("tokens", [])
        for token in tokens:
            symbol = token.get("symbol")
            sentiment = token.get("sentiment_score", 0)
            if sentiment > 80:
                logger.info(f"High sentiment detected on Tungscreener: {symbol} ({sentiment})")
