import asyncio
import aiohttp
import time
import random
import logging
import os
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any

logger = logging.getLogger("bot.network")

@dataclass
class ProxyNode:
    url: str
    total_requests: int = 0
    success_requests: int = 0
    error_counts: Dict[int, int] = field(default_factory=lambda: {402: 0, 403: 0, 407: 0, 429: 0, 530: 0})
    latencies: List[float] = field(default_factory=list)
    cooldown_until: float = 0
    health_score: float = 100.0

    @property
    def success_rate(self) -> float:
        if self.total_requests == 0: return 0.0
        return (self.success_requests / self.total_requests) * 100

    @property
    def avg_latency(self) -> float:
        if not self.latencies: return 0.0
        return sum(self.latencies[-10:]) / len(self.latencies[-10:])

class NetworkManager:
    """Manages residential proxy rotation and health telemetry."""
    
    def __init__(self, proxy_list_path: Optional[str] = None):
        self.proxies: List[ProxyNode] = []
        self.proxy_list_path = proxy_list_path or os.getenv("PROXY_LIST_PATH")
        self.residential_session_id = random.randint(10000, 99999)
        self._session: Optional[aiohttp.ClientSession] = None
        
        if self.proxy_list_path:
            self.load_from_file(self.proxy_list_path)

    def load_from_file(self, filepath: str):
        """Load proxies from file in http://user:pass@host:port or host:port:user:pass format."""
        if not os.path.exists(filepath):
            logger.warning(f"Proxy file not found: {filepath}")
            return
            
        try:
            with open(filepath, "r") as f:
                lines = [l.strip() for l in f if l.strip()]
                
            count = 0
            for line in lines:
                proxy_url = line
                if not line.startswith("http"):
                    # Handle host:port:user:pass
                    parts = line.split(":")
                    if len(parts) == 4:
                        host, port, user, password = parts
                        proxy_url = f"http://{user}:{password}@{host}:{port}"
                
                self.proxies.append(ProxyNode(url=proxy_url))
                count += 1
            
            logger.info(f"Loaded {count} proxies from {filepath}")
        except Exception as e:
            logger.error(f"Failed to load proxies: {e}")

    def get_proxy(self) -> Optional[str]:
        """Get a healthy proxy from the list, or rotating residential if configured."""
        now = time.time()
        available = [p for p in self.proxies if p.cooldown_until < now and p.health_score > 20]
        
        if not available:
            # Fallback to random if all on cooldown
            if not self.proxies: return None
            return random.choice(self.proxies).url
            
        # Select best health + low latency
        selected = sorted(available, key=lambda x: (-x.health_score, x.avg_latency))[0]
        return selected.url

    def report_result(self, proxy_url: str, success: bool, status: int = 200, latency: float = 0):
        """Update telemetry for a proxy."""
        node = next((p for p in self.proxies if p.url == proxy_url), None)
        if not node: return
        
        node.total_requests += 1
        if success:
            node.success_requests += 1
            node.health_score = min(100.0, node.health_score + 2.0)
            if latency > 0:
                node.latencies.append(latency)
        else:
            if status in node.error_counts:
                node.error_counts[status] += 1
            
            # Penalize based on status
            penalty = 10.0
            if status in [403, 429, 530]:
                penalty = 25.0
                node.cooldown_until = time.time() + 30 # 30s cooldown
            elif status in (402, 407):
                penalty = 50.0
                node.cooldown_until = time.time() + 120
                
            node.health_score = max(0.0, node.health_score - penalty)

    async def get_stats(self) -> Dict[str, Any]:
        """Aggregate stats for /proxy command."""
        total_reqs = sum(p.total_requests for p in self.proxies)
        total_success = sum(p.success_requests for p in self.proxies)
        
        errors = {402: 0, 403: 0, 407: 0, 429: 0, 530: 0}
        for p in self.proxies:
            for code in errors:
                errors[code] += p.error_counts.get(code, 0)
        
        avg_lat = 0
        nodes_with_lat = [p.avg_latency for p in self.proxies if p.avg_latency > 0]
        if nodes_with_lat:
            avg_lat = sum(nodes_with_lat) / len(nodes_with_lat)
            
        health = 0
        if self.proxies:
            health = sum(p.health_score for p in self.proxies) / len(self.proxies)

        return {
            "total_proxies": len(self.proxies),
            "total_requests": total_reqs,
            "success_rate": (total_success / total_reqs * 100) if total_reqs > 0 else 0,
            "errors": errors,
            "avg_latency": avg_lat,
            "health_score": health
        }
