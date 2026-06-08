import aiohttp
import asyncio
import re
import logging
from bs4 import BeautifulSoup
from typing import List

class MonitorScraper:
    def __init__(self, bot=None):
        self.url = "https://985monitor.xyz/smartmoney/"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        # Solana addresses are base58 and 32-44 characters long
        self.wallet_pattern = re.compile(r'[1-9A-HJ-NP-Za-km-z]{32,44}')
        self.logger = logging.getLogger("MonitorScraper")
        self.bot = bot
        self._running = False

    async def fetch_addresses(self) -> List[str]:
        """Scrapes Solana addresses from 985monitor.xyz/smartmoney/"""
        self.logger.info(f"Scraping {self.url} for Solana addresses...")
        try:
            async with aiohttp.ClientSession(headers=self.headers) as session:
                async with session.get(self.url, timeout=15) as response:
                    if response.status != 200:
                        self.logger.error(f"Failed to fetch {self.url}: {response.status}")
                        return []
                    
                    html = await response.text()
                    # Find all potential Solana addresses using regex
                    addresses = self.wallet_pattern.findall(html)
                    # Filter unique addresses and perform basic validation
                    unique_addresses = sorted(list(set(addresses)))
                    self.logger.info(f"Found {len(unique_addresses)} unique addresses.")
                    return unique_addresses
        except Exception as e:
            self.logger.error(f"Error during scraping: {e}")
            return []

    async def background_task(self, interval: int = 300):
        """Periodically scrapes addresses and updates the bot's filter."""
        self._running = True
        self.logger.info(f"Scraper background task started (interval: {interval}s)")
        while self._running:
            addresses = await self.fetch_addresses()
            if addresses and self.bot and hasattr(self.bot, '_filter'):
                # Add found addresses to copy trading targets
                for addr in addresses:
                    if not self.bot._filter.is_copy_target(addr):
                        self.bot._filter.add_copy_target(addr)
                        self.logger.debug(f"Added {addr} to tracking from scraper.")
            
            await asyncio.sleep(interval)

    async def stop(self):
        self._running = False
        self.logger.info("Scraper background task stopped")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    scraper = MonitorScraper()
    asyncio.run(scraper.fetch_addresses())
