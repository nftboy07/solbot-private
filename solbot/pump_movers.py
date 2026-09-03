"""Pump.fun Movers Module using curl_cffi for tracking trending tokens."""

import asyncio
import logging
import os
import re
from typing import List, Dict, Any, Optional
from time import time
from dataclasses import dataclass
from curl_cffi.requests import AsyncSession

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
    """Polls pump.fun trending API for high-momentum 'Movers' using impersonated browser requests."""

    def __init__(self, bot: Any):
        self.bot = bot
        self._running = False
        self._session: Optional[AsyncSession] = None
        self._trending_url = "https://frontend-api-v3.pump.fun/coins"
        self._seen_mints: set = set()
        self._poll_interval = float(os.getenv("SNIPER_MOVERS_INTERVAL_SECONDS", "30"))
        
        # Thresholds for auto-buying movers
        self.min_market_cap_usd = 10000  
        self.max_market_cap_usd = 500000 
        self.min_replies = 5            

    def _sanitize_proxy(self, proxy: str) -> str:
        """Hide password in proxy URL for safe logging."""
        if not proxy:
            return "None"
        return re.sub(r"://.*@", "://***:***@", proxy)

    async def start_monitoring(self):
        self._running = True
        logger.info("Pump.fun Movers Monitor started with curl_cffi impersonation.")
        
        async with AsyncSession(impersonate="chrome120") as session:
            self._session = session
            while self._running:
                try:
                    await self._poll_movers()
                except Exception as e:
                    logger.error(f"PumpMovers error: {e}")
                await asyncio.sleep(self._poll_interval)

    async def _poll_movers(self):
        """Fetch trending coins from pump.fun and process them."""
        params = {
            "offset": "0",
            "limit": "20",
            "sort": "market_cap",
            "order": "DESC",
            "includeNsfw": "false"
        }
        
        proxy = self.bot._config.proxy_url if self.bot._config.proxy_url else None
        logger.debug(f"Polling Movers. Proxy: {self._sanitize_proxy(proxy)}")
        
        try:
            resp = await self._session.get(
                self._trending_url, 
                params=params, 
                proxy=proxy, 
                timeout=10
            )
            
            if resp.status_code != 200:
                logger.warning(f"Failed to fetch movers: HTTP {resp.status_code}")
                return
            
            movers = resp.json()
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
                    
                    # Same filter/buy path as WS + 1s scanner — do not skip junk checks.
                    asyncio.create_task(self.bot._schedule_token_evaluation(token, m))
                    self._seen_mints.add(mint)
        except Exception as e:
            logger.error(f"Movers polling exception: {e}")

    async def stop(self):
        self._running = False
        logger.info("Pump.fun Movers Monitor stopped.")
