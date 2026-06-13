import aiohttp
from solbot.logger import get_logger

logger = get_logger("dexscreener")

class DexScreenerClient:
    """DexScreener API Integration."""
    def __init__(self):
        self._base_url = "https://api.dexscreener.com/latest/dex"

    async def get_token_pairs(self, mint: str):
        async with aiohttp.ClientSession() as session:
            url = f"{self._base_url}/tokens/{mint}"
            async with session.get(url) as resp:
                if resp.status == 200:
                    return await resp.json()
                return None

    async def get_price_metrics(self, mint: str):
        data = await self.get_token_pairs(mint)
        if not data or not data.get("pairs"):
            return None
        
        # Return primary pair data (usually highest liquidity)
        pair = data["pairs"][0]
        return {
            "price_usd": pair.get("priceUsd"),
            "volume_24h": pair.get("volume", {}).get("h24"),
            "volume_m5": pair.get("volume", {}).get("m5"),
            "volume_h1": pair.get("volume", {}).get("h1"),
            "txns_m5": pair.get("txns", {}).get("m5", {}),
            "txns_h1": pair.get("txns", {}).get("h1", {}),
            "liquidity_usd": pair.get("liquidity", {}).get("usd"),
            "price_change_m5": pair.get("priceChange", {}).get("m5"),
            "price_change_1h": pair.get("priceChange", {}).get("h1"),
            "market_cap_usd": pair.get("fdv") or pair.get("marketCap")
        }