"""Tungscreener early-signal sentiment scraper with Active Sniping."""
import asyncio
import aiohttp
import logging
from typing import List, Dict, Any
from solbot.models import TokenEvent

logger = logging.getLogger("bot.tungscreener")

class TungscreenerScraper:
    """Scraper for tungscreener.com to extract trending tokens and sentiment."""

    def __init__(self, bot):
        self.bot = bot
        self._running = False
        # Updated URL to correct API endpoint
        self._url = "https://tungscreener.com/api/trending" 
        self._headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept": "application/json"
        }

    async def start_monitoring(self):
        """Background task for periodic scraping."""
        self._running = True
        logger.info("Tungscreener scraper started.")
        while self._running:
            try:
                # Use a single session with updated headers
                async with aiohttp.ClientSession(headers=self._headers) as session:
                    async with session.get(self._url, timeout=10) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            await self._process_data(data)
                        elif resp.status == 404:
                            # Try fallback endpoint
                            fallback_url = "https://tungscreener.com/api/v1/trending"
                            async with session.get(fallback_url, timeout=10) as f_resp:
                                if f_resp.status == 200:
                                    data = await f_resp.json()
                                    await self._process_data(data)
                                else:
                                    logger.warning(f"Tungscreener fallback failed: {f_resp.status}")
                        else:
                            logger.warning(f"Tungscreener returned status {resp.status}")
            except Exception as e:
                logger.error(f"Tungscreener scraping error: {e}")
            await asyncio.sleep(300)

    async def stop(self):
        self._running = False

    async def _process_data(self, data: Any):
        """Process extracted trending tokens and trigger snipes for high sentiment."""
        tokens = data.get("tokens", []) if isinstance(data, dict) else []
        for token_info in tokens:
            symbol = token_info.get("symbol")
            mint = token_info.get("mint")
            sentiment = token_info.get("sentiment_score", 0)
            
            if sentiment > 85 and mint:
                logger.info(f"High sentiment detected on Tungscreener: {symbol} ({sentiment}). Checking token...")
                
                meta = await self.bot._pump_client.get_token_metadata(mint)
                mcap_usd = float(meta.get("market_cap_sol", 0)) * self.bot._telegram._sol_price
                
                token = TokenEvent(
                    mint=mint,
                    name=meta.get("name", symbol),
                    symbol=symbol,
                    creator=meta.get("creator", "unknown"),
                    market_cap_usd=mcap_usd,
                    liquidity_sol=float(meta.get("liquidity_sol", 0)),
                    timestamp=asyncio.get_event_loop().time()
                )
                
                qualified, size = self.bot._filter.is_qualified(token)
                if qualified:
                    asyncio.create_task(self.bot._execute_snipe(token, size, f"Tungscreener ({sentiment} sentiment)"))
