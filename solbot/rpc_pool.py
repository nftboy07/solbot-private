import asyncio
import time
import logging
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field
import aiohttp

logger = logging.getLogger(__name__)

@dataclass
class RPCNode:
    url: str
    name: str
    latency: float = 0.0
    last_slot: int = 0
    total_requests: int = 0
    failed_requests: int = 0
    status_codes: Dict[int, int] = field(default_factory=dict)
    is_active: bool = True
    error_count: int = 0
    inactive_since: float = 0.0

class RPCPool:
    """
    Asynchronous RPCPool that tracks multiple Solana RPC endpoints.
    Monitors metrics (latency, slot difference, 429s, 5xx) and routes to the best client.
    """

    def __init__(self, nodes: List[Dict[str, str]]):
        self.nodes = [RPCNode(url=n["url"], name=n["name"]) for n in nodes]
        self._lock = asyncio.Lock()
        self._last_error_log_time = 0.0

    async def get_best_node(self) -> str:
        """Returns the best available RPC node based on slot and latency."""
        urls = await self.get_retry_urls()
        return urls[0]

    async def get_retry_urls(self) -> List[str]:
        """Ordered RPC URLs for failover (active first, then cooldown nodes)."""
        async with self._lock:
            await self._reactivate_stale_nodes(cooldown_seconds=60)
            active_nodes = [n for n in self.nodes if n.is_active]
            if active_nodes:
                sorted_nodes = sorted(active_nodes, key=lambda x: (-x.last_slot, x.latency))
                return [n.url for n in sorted_nodes]
            if self.nodes:
                now = time.time()
                if now - self._last_error_log_time >= 30.0:
                    logger.error("No active RPC nodes available! Falling back to all configured nodes.")
                    self._last_error_log_time = now
                return [n.url for n in self.nodes]
            return []

    async def report_metrics(self, url: str, success: bool, latency: float = 0.0, slot: int = 0, status_code: Optional[int] = None):
        """Updates node metrics after a request."""
        for node in self.nodes:
            if node.url == url:
                node.total_requests += 1
                if slot > 0:
                    node.last_slot = slot

                if status_code:
                    node.status_codes[status_code] = node.status_codes.get(status_code, 0) + 1

                if success:
                    node.latency = (node.latency * 0.8) + (latency * 0.2)
                    node.error_count = max(0, node.error_count - 1)
                    node.is_active = True
                    node.inactive_since = 0.0
                else:
                    node.failed_requests += 1
                    node.error_count += 1

                if status_code in [429] or (status_code and status_code >= 500) or node.error_count > 25:
                    if node.is_active:
                        node.inactive_since = time.time()
                    node.is_active = False
                    logger.warning(
                        "RPC Node %s marked inactive (Status: %s, Errors: %s)",
                        node.name, status_code, node.error_count,
                    )
                break

    async def _reactivate_stale_nodes(self, cooldown_seconds: int = 120):
        now = time.time()
        for node in self.nodes:
            if not node.is_active and node.inactive_since and (now - node.inactive_since) >= cooldown_seconds:
                node.is_active = True
                node.error_count = max(0, node.error_count - 5)
                node.inactive_since = 0.0
                logger.info("RPC Node %s reactivated after cooldown.", node.name)

    async def monitor_nodes(self):
        await self._reactivate_stale_nodes()
        tasks = [self.check_node_health(node) for node in self.nodes]
        await asyncio.gather(*tasks)

    async def check_node_health(self, node: RPCNode):
        """Internal check for a single node's health."""
        start_time = time.time()
        payload = {"jsonrpc": "2.0", "id": 1, "method": "getSlot"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(node.url, json=payload, timeout=5) as resp:
                    latency = time.time() - start_time
                    if resp.status == 200:
                        data = await resp.json()
                        if "error" in data:
                            await self.report_metrics(node.url, False, latency, status_code=500)
                        else:
                            slot = data.get("result", 0)
                            await self.report_metrics(node.url, True, latency, slot, resp.status)
                    else:
                        await self.report_metrics(node.url, False, latency, status_code=resp.status)
        except Exception as e:
            logger.debug("RPC monitor failed for %s: %s", node.name, e)
            await self.report_metrics(node.url, False, status_code=500)

    async def run_monitor(self, interval: int = 30):
        """Background loop for node monitoring."""
        while True:
            await self.monitor_nodes()
            await asyncio.sleep(interval)