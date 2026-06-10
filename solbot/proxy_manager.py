import asyncio
import time
import random
import logging
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field
from curl_cffi.requests import AsyncSession

logger = logging.getLogger(__name__)

@dataclass
class ProxyStats:
    proxy_url: str
    latency: float = 0.0
    success_rate: float = 1.0
    total_requests: int = 0
    failed_requests: int = 0
    last_failure: float = 0.0
    status_codes: Dict[int, int] = field(default_factory=dict)
    is_healthy: bool = True

class ProxyManager:
    """
    Asynchronous ProxyManager for managing rotating residential proxies.
    Tracks health (latency, success rate, errors) and auto-rotates.
    """

    def __init__(self, proxies: List[str], check_url: str = "https://httpbin.org/ip", timeout: int = 10):
        self.proxies = [ProxyStats(proxy_url=p) for p in proxies]
        self.check_url = check_url
        self.timeout = timeout
        self._current_index = 0
        self._lock = asyncio.Lock()

    async def get_proxy(self) -> str:
        """Returns the next healthy proxy in a round-robin fashion."""
        async with self._lock:
            for _ in range(len(self.proxies)):
                proxy = self.proxies[self._current_index]
                self._current_index = (self._current_index + 1) % len(self.proxies)
                if proxy.is_healthy:
                    return proxy.proxy_url
            
            # If all are unhealthy, return a random one as fallback
            return random.choice(self.proxies).proxy_url

    async def report_result(self, proxy_url: str, success: bool, latency: float = 0.0, status_code: Optional[int] = None):
        """Reports the result of a request to update proxy stats."""
        for proxy in self.proxies:
            if proxy.proxy_url == proxy_url:
                proxy.total_requests += 1
                if status_code:
                    proxy.status_codes[status_code] = proxy.status_codes.get(status_code, 0) + 1
                
                if success:
                    # Rolling average for latency
                    proxy.latency = (proxy.latency * 0.9) + (latency * 0.1)
                else:
                    proxy.failed_requests += 1
                    proxy.last_failure = time.time()

                proxy.success_rate = (proxy.total_requests - proxy.failed_requests) / proxy.total_requests
                
                # Health logic: Mark unhealthy on 403, 429, 530 or low success rate
                critical_errors = [403, 429, 530]
                if (status_code in critical_errors) or (proxy.success_rate < 0.5 and proxy.total_requests > 5):
                    proxy.is_healthy = False
                    logger.warning(f"Proxy {proxy_url} marked unhealthy (Status: {status_code}, SR: {proxy.success_rate:.2f})")
                else:
                    proxy.is_healthy = True
                break

    async def check_all_proxies(self):
        """Periodically checks the health of all proxies."""
        tasks = [self.check_proxy_health(proxy) for proxy in self.proxies]
        await asyncio.gather(*tasks)

    async def check_proxy_health(self, proxy: ProxyStats):
        """Checks the health of a single proxy using curl_cffi."""
        start_time = time.time()
        try:
            async with AsyncSession(proxies={"http": proxy.proxy_url, "https": proxy.proxy_url}) as s:
                response = await s.get(self.check_url, timeout=self.timeout)
                latency = time.time() - start_time
                await self.report_result(proxy.proxy_url, response.status_code == 200, latency, response.status_code)
        except Exception as e:
            logger.debug(f"Health check failed for {proxy.proxy_url}: {e}")
            await self.report_result(proxy.proxy_url, False, status_code=getattr(e, 'status_code', 500))

    async def run_health_checker(self, interval: int = 60):
        """Background task to monitor proxy health."""
        while True:
            await self.check_all_proxies()
            await asyncio.sleep(interval)
