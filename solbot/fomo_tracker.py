import aiohttp
import asyncio
import logging
import re
from typing import List, Any

logger = logging.getLogger("bot.fomo")

class FomoTracker:
    """Scraper/Tracker for fomo.fund top traders and activity."""

    def __init__(self, bot: Any):
        self.bot = bot
        self._running = False
        self._base_url = "https://fomo.fund"
        self._api_url = "https://api.fomo.fund/v1" # Hypothetical API endpoint
        self._headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

    async def start_monitoring(self):
        self._running = True
        logger.info("Fomo Tracker started.")
        while self._running:
            try:
                await self._fetch_leaderboard()
            except Exception as e:
                logger.error(f"Fomo Tracker error: {e}")
            await asyncio.sleep(300) # Poll every 5 minutes

    async def stop(self):
        self._running = False

    async def _fetch_leaderboard(self):
        """Fetch top traders from fomo.fund leaderboard and add to copy targets."""
        url = f"{self._api_url}/leaderboard"
        async with aiohttp.ClientSession(headers=self._headers) as session:
            try:
                async with session.get(url, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        traders = data.get("traders", [])
                        for trader in traders:
                            address = trader.get("address")
                            if address and address not in self.bot._filter._copy_targets:
                                self.bot._filter.add_copy_target(address)
                                logger.info(f"Auto-added Fomo top trader: {address}")
                        self.bot._save_state()
                    else:
                        # Soft-fail fallback: try scraping the main page for JSON scripts
                        await self._scrape_main_page(session)
            except Exception as e:
                logger.debug(f"Fomo API failed, attempting scrape: {e}")
                await self._scrape_main_page(session)

    async def _scrape_main_page(self, session: aiohttp.ClientSession):
        """Fallback: Scrape the main page for embedded state or trader addresses."""
        async with session.get(self._base_url, timeout=10) as resp:
            if resp.status == 200:
                html = await resp.text()
                # Use regex to find Solana addresses (basic heuristic)
                addresses = re.findall(r'[1-9A-HJ-NP-Za-km-z]{32,44}', html)
                added_count = 0
                for addr in set(addresses):
                    if addr not in self.bot._filter._copy_targets:
                        # Basic check: is it a valid-looking Solana address?
                        # In a real scenario, we'd check against a known whitelist or PnL data
                        # For now, we skip auto-adding from pure regex to avoid junk
                        pass
                if added_count > 0:
                    self.bot._save_state()
            else:
                logger.warning(f"Fomo scrape failed (HTTP {resp.status})")
