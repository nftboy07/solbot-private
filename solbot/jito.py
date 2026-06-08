import asyncio
import aiohttp
from solbot.logger import get_logger

logger = get_logger("jito")

class JitoClient:
    """Asynchronous Jito Block-Engine client for Block-0 sniping."""
    def __init__(self, config):
        self._config = config
        self._endpoints = [
            "https://mainnet.block-engine.jito.wtf/api/v1/bundles",
            "https://amsterdam.mainnet.block-engine.jito.wtf/api/v1/bundles",
            "https://frankfurt.mainnet.block-engine.jito.wtf/api/v1/bundles"
        ]
        self._tip_accounts = [
            "Cw8CFyMv96H6vS5MktW3NfSAmXF7YmF2W4P5XGvjM4Lp",
            "ADaUMid9yfUytqMBB6f7JSt39zG9u4L9J6vCjW2H96Mh"
        ]

    async def send_bundle(self, transactions: list, tip_lamports: int):
        """Submit a bundle of transactions with a Jito tip."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendBundle",
            "params": [transactions]
        }
        
        async with aiohttp.ClientSession() as session:
            tasks = [session.post(url, json=payload) for url in self._endpoints]
            responses = await asyncio.gather(*tasks, return_exceptions=True)
            for resp in responses:
                if isinstance(resp, aiohttp.ClientResponse):
                    logger.debug(f"Jito bundle response: {resp.status}")
