import aiohttp
import logging
from typing import List, Dict, Any

logger = logging.getLogger("bot.gmgn_client")

class GMGNClient:
    """Functional GMGN Client for Solana discovery and security."""
    def __init__(self):
        self._session = None
        self._base_url = "https://gmgn.ai/api/v1" # Example base URL

    async def get_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json"
            })
        return self._session

    async def get_new_tokens(self) -> List[Dict[str, Any]]:
        """Fetch trending new tokens from GMGN."""
        try:
            session = await self.get_session()
            async with session.get(f"{self._base_url}/token/trending") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("data", {}).get("list", [])
        except Exception as e:
            logger.error(f"GMGN get_new_tokens error: {e}")
        return []

    async def get_smart_money_inflow(self) -> List[Dict[str, Any]]:
        """Fetch tokens with high smart money inflow."""
        try:
            session = await self.get_session()
            async with session.get(f"{self._base_url}/token/smart_money_inflow") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("data", {}).get("list", [])
        except Exception as e:
            logger.error(f"GMGN smart_money_inflow error: {e}")
        return []

    async def get_token_security(self, mint: str) -> Dict[str, Any]:
        """Fetch security/audit data for a specific token."""
        try:
            session = await self.get_session()
            async with session.get(f"{self._base_url}/token/security/{mint}") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("data", {})
        except Exception as e:
            logger.error(f"GMGN security scan error: {e}")
        return {}

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
