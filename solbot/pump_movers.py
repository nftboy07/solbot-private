"""Pump.fun Movers Module for tracking and auto-buying trending tokens."""

import asyncio
import logging
from typing import List, Dict, Any, Optional
import aiohttp
from time import time
from dataclasses import dataclass

from solbot.models import TokenEvent

logger = logging.getLogger("bot.pump_movers")

@dataclass
class MoverToken:
    mint: str
    symbol: str
    name: str
    market_cap: float
    replies: int
    last_reply: int
    usd_market_cap: float

class PumpMovers:
    """Polls pump.fun trending API for high-momentum 'Movers'."""

    def __init__(self, bot: Any):
        self.bot = bot
        self._running = False
        self._session: Optional[aiohttp.ClientSession] = None
        self._trending_url = "https://frontend-api.pump.fun/coins/trending"
        self._seen_mints: set = set()
        self._poll_interval = 30  
        
        # Headers to bypass Cloudflare 530/403
        self._headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Referer": "https://pump.fun/",
            "Origin": "https://pump.fun"
        }
        
        # Thresholds for auto-buying movers
        self.min_market_cap_usd = 10000  
        self.max_market_cap_usd = 500000 
        self.min_replies = 5            

    async def start_monitoring(self):
        self._running = True
        logger.info("Pump.fun Movers Monitor started with updated headers.")
        if not self._session:
            self._session = aiohttp.ClientSession(headers=self._headers, timeout=aiohttp.ClientTimeout(total=10))
        
        while self._running:
            try:
                await self._poll_movers()
            except Exception as e:
                logger.error(f"PumpMovers error: {e}")
            await asyncio.sleep(self._poll_interval)

    async def stop(self):
        self._running = False
        if self._session:
            await self._session.close()

    async def _poll_movers(self):
        """Fetch trending coins from pump.fun and process them."""
        params = {
            "offset": 0,
            "limit": 20,
            "sort": "market_cap",
            "order": "DESC",
            "includeNsfw": "false"
        }
        
        async with self._session.get(self._trending_url, params=params) as resp:
            if resp.status != 200:
                logger.warning(f"Failed to fetch movers: HTTP {resp.status}")
                return
            
            movers = await resp.json()
            for m in movers:
                mint = m.get("mint")
                if not mint or mint in self._seen_mints:
                    continue
                
                mcap = float(m.get("usd_market_cap", 0))
                replies = int(m.get("reply_count", 0))
                
                if self.min_market_cap_usd <= mcap <= self.max_market_cap_usd and replies >= self.min_replies:
                    logger.info(f"Detected Pump Mover: {m.get('symbol')} ({mint}) | Mcap: ${mcap:,.0f} | Replies: {replies}")
                    
                    token = TokenEvent(
                        mint=mint,
                        name=m.get("name", "Unknown"),
                        symbol=m.get("symbol", "???"),
                        creator=m.get("creator", ""),
                        market_cap_usd=mcap,
                        liquidity_sol=0.0, 
                        timestamp=time()
                    )
                    
                    asyncio.create_task(self.bot._execute_snipe(token, self.bot._config.jupiter.buy_amount_sol, "Pump Mover"))
                    self._seen_mints.add(mint)
