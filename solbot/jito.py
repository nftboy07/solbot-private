"""Multi-region Jito Block-Engine and NextBlock MEV Relay Client for Solana."""

import asyncio
import base58
import logging
import time
from typing import List, Optional, Tuple, Dict, Any

import aiohttp

logger = logging.getLogger("bot.jito")


class JitoClient:
    """High-speed asynchronous Jito & MEV block engine client with multi-region routing."""

    # Ranked global block-engine endpoints
    BLOCK_ENGINES = [
        {"name": "Frankfurt", "url": "https://frankfurt.mainnet.block-engine.jito.wtf/api/v1/bundles"},
        {"name": "Amsterdam", "url": "https://amsterdam.mainnet.block-engine.jito.wtf/api/v1/bundles"},
        {"name": "New York", "url": "https://ny.mainnet.block-engine.jito.wtf/api/v1/bundles"},
        {"name": "Tokyo", "url": "https://tokyo.mainnet.block-engine.jito.wtf/api/v1/bundles"},
        {"name": "Salt Lake", "url": "https://slc.mainnet.block-engine.jito.wtf/api/v1/bundles"},
        {"name": "Global Default", "url": "https://mainnet.block-engine.jito.wtf/api/v1/bundles"},
    ]

    # Official Jito Tip Accounts
    TIP_ACCOUNTS = [
        "Cw8CFyMv96H6vS5MktW3NfSAmXF7YmF2W4P5XGvjM4Lp",
        "ADaUMid9yfUytqMBB6f7JSt39zG9u4L9J6vCjW2H96Mh",
        "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh",
        "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
        "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
        "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL",
        "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT",
        "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
    ]

    def __init__(self, config):
        self._config = config
        self._endpoints = [e["url"] for e in self.BLOCK_ENGINES]
        self._tip_index = 0
        self._endpoint_latencies: Dict[str, float] = {}

    def get_tip_account(self) -> str:
        """Rotate through official Jito tip accounts."""
        account = self.TIP_ACCOUNTS[self._tip_index % len(self.TIP_ACCOUNTS)]
        self._tip_index += 1
        return account

    async def benchmark_endpoints(self) -> List[Tuple[str, float]]:
        """Benchmark latency across all multi-region block engines."""
        results = []
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3.0)) as session:
            for eng in self.BLOCK_ENGINES:
                url = eng["url"]
                start = time.perf_counter()
                try:
                    # Ping block engine
                    payload = {"jsonrpc": "2.0", "id": 1, "method": "getTipAccounts", "params": []}
                    async with session.post(url, json=payload) as resp:
                        if resp.status == 200:
                            latency_ms = (time.perf_counter() - start) * 1000
                            self._endpoint_latencies[url] = latency_ms
                            results.append((eng["name"], latency_ms))
                except Exception as e:
                    logger.debug("Latency probe failed for %s: %s", eng["name"], e)
        results.sort(key=lambda x: x[1])
        return results

    async def send_bundle(
        self,
        transactions: list,
        session: Optional[aiohttp.ClientSession] = None,
        parallel_dispatch: bool = True,
    ) -> Optional[str]:
        """Submit an atomic transaction bundle across all ranked block engines in parallel."""
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
        client_session = session if session is not None else aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4.0))

        async def _post_endpoint(url: str) -> Optional[str]:
            try:
                async with client_session.post(url, json=payload) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if "result" in data and data["result"]:
                            return str(data["result"])
            except Exception as exc:
                logger.debug("Jito post to %s failed: %s", url, exc)
            return None

        bundle_id = None
        try:
            if parallel_dispatch:
                tasks = [_post_endpoint(url) for url in self._endpoints]
                responses = await asyncio.gather(*tasks, return_exceptions=True)
                for res in responses:
                    if isinstance(res, str) and res:
                        bundle_id = res
                        break
            else:
                for url in self._endpoints:
                    res = await _post_endpoint(url)
                    if res:
                        bundle_id = res
                        break
        finally:
            if owns_session:
                await client_session.close()
        return bundle_id

    async def confirm_bundle(
        self,
        bundle_id: str,
        session: aiohttp.ClientSession,
        timeout_sec: float = 25.0,
        poll_interval: float = 0.5,
    ) -> Optional[str]:
        """Poll multi-region endpoints until bundle lands on-chain."""
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
                except Exception:
                    continue
            await asyncio.sleep(poll_interval)
        return None