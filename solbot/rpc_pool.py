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

class RPCPool:
    """
    Asynchronous RPCPool that tracks multiple Solana RPC endpoints.
    Monitors metrics (latency, slot difference, 429s, 5xx) and routes to the best client.
    """

    def __init__(self, nodes: List[Dict[str, str]]):
        self.nodes = [RPCNode(url=n["url"], name=n["name"]) for n in nodes]
        self._lock = asyncio.Lock()

    async def get_best_node(self) -> str:
        """Returns the best available RPC node based on slot and latency."""
        async with self._lock:
            # Filter active nodes
            active_nodes = [n for n in self.nodes if n.is_active]
            if not active_nodes:
                logger.error("No active RPC nodes available! Falling back to first configured node.")
                return self.nodes[0].url

            # Sort by highest slot (most recent) then lowest latency
            sorted_nodes = sorted(
                active_nodes,
                key=lambda x: (-x.last_slot, x.latency)
            )
            return sorted_nodes[0].url

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
                else:
                    node.failed_requests += 1
                    node.error_count += 1
                
                # Node health logic
                if status_code in [429] or (status_code and status_code >= 500) or node.error_count > 10:
                    node.is_active = False
                    logger.warning(f"RPC Node {node.name} marked inactive (Status: {status_code}, Errors: {node.error_count})")
                elif success:
                    node.is_active = True
                break

    async def monitor_nodes(self):
        """Checks nodes for slot height and latency periodically."""
        tasks = [self.check_node_health(node) for node in self.nodes]
        await asyncio.gather(*tasks)

    async def check_node_health(self, node: RPCNode):
        """Internal check for a single node's health."""
        start_time = time.time()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSlot"
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(node.url, json=payload, timeout=5) as resp:
                    latency = time.time() - start_time
                    if resp.status == 200:
                        data = await resp.json()
                        slot = data.get("result", 0)
                        await self.report_metrics(node.url, True, latency, slot, resp.status)
                    else:
                        await self.report_metrics(node.url, False, latency, status_code=resp.status)
        except Exception as e:
            logger.debug(f"RPC monitor failed for {node.name}: {e}")
            await self.report_metrics(node.url, False, status_code=500)

    async def run_monitor(self, interval: int = 30):
        """Background loop for node monitoring."""
        while True:
            await self.monitor_nodes()
            await asyncio.sleep(interval)
