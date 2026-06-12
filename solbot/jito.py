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

    async def send_bundle(self, transactions: list, *args, **kwargs):
        """Submit a bundle of transactions with a Jito tip."""
        import base58
        serialized_txs = []
        for tx in transactions:
            if hasattr(tx, '__bytes__') or not isinstance(tx, str):
                serialized_txs.append(base58.b58encode(bytes(tx)).decode("utf-8"))
            else:
                serialized_txs.append(tx)

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendBundle",
            "params": [serialized_txs]
        }
        
        bundle_id = None
        async with aiohttp.ClientSession() as session:
            tasks = [session.post(url, json=payload) for url in self._endpoints]
            responses = await asyncio.gather(*tasks, return_exceptions=True)
            for resp in responses:
                if isinstance(resp, aiohttp.ClientResponse):
                    logger.debug(f"Jito bundle response: {resp.status}")
                    if resp.status == 200:
                        try:
                            data = await resp.json()
                            if "result" in data:
                                bundle_id = data["result"]
                        except Exception as e:
                            logger.error(f"Error parsing Jito response: {e}")
        return bundle_id

