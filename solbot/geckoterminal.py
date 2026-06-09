import aiohttp
import logging
import os
from typing import Optional, Dict, Any

logger = logging.getLogger("bot.gecko")

class GeckoTerminalClient:
    """GeckoTerminal API Client for Solana Token Data."""
    
    BASE_URL = "https://api.geckoterminal.com/api/v2"
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("COINGECKO_API_KEY")
        self._session: Optional[aiohttp.ClientSession] = None
        self.headers = {}
        if self.api_key:
            # Pro API uses x-cg-pro-api-key
            self.headers["x-cg-pro-api-key"] = self.api_key
            logger.info("GeckoTerminal initialized with Pro API Key.")

    async def start(self):
        if not self._session:
            self._session = aiohttp.ClientSession(headers=self.headers)

    async def stop(self):
        if self._session:
            await self._session.close()

    async def get_token_info(self, mint: str) -> Optional[Dict[str, Any]]:
        """Fetch token info for a Solana mint address."""
        if not self._session: await self.start()
        
        # Endpoint for specific token on Solana
        url = f"{self.BASE_URL}/networks/solana/tokens/{mint}"
        try:
            async with self._session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("data", {}).get("attributes", {})
                elif resp.status == 404:
                    # Token might be too new for GeckoTerminal
                    return None
                else:
                    logger.error(f"GeckoTerminal Error {resp.status}: {await resp.text()}")
        except Exception as e:
            logger.error(f"GeckoTerminal request failed: {e}")
        return None

    async def evaluate_safety(self, mint: str, min_liq_usd: float = 5000) -> bool:
        """Check if token has enough liquidity to be safe for trading."""
        info = await self.get_token_info(mint)
        if not info:
            # If not found on GeckoTerminal yet, it's either brand new or a scam
            # For pump.fun we might fallback to our own scraper, but for safety we return False here
            return False
            
        liq = float(info.get("reserve_in_usd", 0))
        volume = float(info.get("volume_usd", {}).get("h24", 0))
        
        logger.info(f"Token {mint} Health: Liq ${liq:,.2f}, 24h Vol ${volume:,.2f}")
        
        if liq < min_liq_usd:
            logger.warning(f"Safety check FAILED for {mint}: Low liquidity (${liq})")
            return False
        return True
