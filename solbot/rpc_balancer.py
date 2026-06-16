import logging
from typing import List, Dict, Optional
from solbot.rpc_pool import RPCPool, RPCNode

logger = logging.getLogger("bot.rpc_balancer")

class RPCBalancer(RPCPool):
    """
    Enhanced RPC Balancer that extends RPCPool with load-balancing queries,
    latency tracking, and formatted status reports.
    """
    def __init__(self, nodes: List[Dict[str, str]]):
        super().__init__(nodes)
        logger.info(f"Initialized RPC Balancer with {len(self.nodes)} endpoints")

    async def get_node_status_report(self) -> str:
        """Returns a string summary of the status of all configured RPC nodes."""
        lines = ["⚡ <b>SOLBOT RPC LOAD BALANCER STATUS</b>\n"]
        for node in self.nodes:
            status = "🟢 ACTIVE" if node.is_active else "🔴 INACTIVE"
            latency_ms = node.latency * 1000.0 if node.latency > 0 else 0.0
            lines.append(
                f"• <b>{node.name}</b>\n"
                f"  URL: <code>{node.url[:40]}...</code>\n"
                f"  Status: <code>{status}</code> | Latency: <code>{latency_ms:.1f}ms</code>\n"
                f"  Slot: <code>{node.last_slot}</code> | Failed: <code>{node.failed_requests}/{node.total_requests}</code>"
            )
        return "\n".join(lines)
