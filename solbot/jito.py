import asyncio
import aiohttp
from typing import Optional, List
from solbot.logger import get_logger

logger = get_logger("jito")

class JitoClient:
    """Asynchronous Jito Block-Engine client for Block-0 sniping."""

    def __init__(self, config):
        self._config = config
        self._endpoints = [
            "https://mainnet.block-engine.jito.wtf/api/v1/bundles",
            "https://amsterdam.mainnet.block-engine.jito.wtf/api/v1/bundles",
            "https://frankfurt.mainnet.block-engine.jito.wtf/api/v1/bundles",
        ]
        self._tip_accounts = [
            "Cw8CFyMv96H6vS5MktW3NfSAmXF7YmF2W4P5XGvjM4Lp",
            "ADaUMid9yfUytqMBB6f7JSt39zG9u4L9J6vCjW2H96Mh",
        ]

    async def send_bundle(self, transactions: list, session: Optional[aiohttp.ClientSession] = None) -> Optional[str]:
        """Submit a bundle of transactions with a Jito tip."""
        import base58

        serialized_txs = []
        for tx in transactions:
            if hasattr(tx, "__bytes__") or not isinstance(tx, str):
                serialized_txs.append(base58.b58encode(bytes(tx)).decode("utf-8"))
            else:
                serialized_txs.append(tx)

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendBundle",
            "params": [serialized_txs],
        }

        owns_session = session is None
        if owns_session:
            session = aiohttp.ClientSession()

        bundle_id = None
        try:
            tasks = [session.post(url, json=payload) for url in self._endpoints]
            responses = await asyncio.gather(*tasks, return_exceptions=True)
            for resp in responses:
                if isinstance(resp, aiohttp.ClientResponse):
                    try:
                        if resp.status == 200:
                            data = await resp.json()
                            if "result" in data:
                                bundle_id = data["result"]
                                break
                    finally:
                        resp.close()
        finally:
            if owns_session:
                await session.close()
        return bundle_id

    async def confirm_bundle(
        self,
        bundle_id: str,
        session: aiohttp.ClientSession,
        timeout_sec: float = 25.0,
        poll_interval: float = 0.5,
    ) -> Optional[str]:
        """Poll Jito until bundle lands; return first on-chain transaction signature."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBundleStatuses",
            "params": [[bundle_id], {"searchTransactionHistory": True}],
        }
        deadline = asyncio.get_running_loop().time() + timeout_sec
        while asyncio.get_running_loop().time() < deadline:
            for url in self._endpoints:
                try:
                    async with session.post(url, json=payload) as resp:
                        if resp.status != 200:
                            continue
                        data = await resp.json()
                        values = data.get("result", {}).get("value", []) or []
                        for entry in values:
                            txs = entry.get("transactions") or []
                            status = entry.get("confirmation_status") or entry.get("status")
                            if txs and status in ("confirmed", "finalized", "landed", None):
                                return txs[0]
                except Exception as exc:
                    logger.debug("Bundle status poll error: %s", exc)
            await asyncio.sleep(poll_interval)
        logger.warning("Bundle %s not confirmed within %ss", bundle_id, timeout_sec)
        return None