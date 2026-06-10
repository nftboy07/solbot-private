import asyncio
import time
import random
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

try:
    from curl_cffi import requests
except ImportError:
    requests = None

try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("solbot.network")

@dataclass
class ProxyNode:
    url: str
    pool: str
    headers: Dict[str, str] = field(default_factory=dict)
    user_agent: str = ""
    browser_fingerprint: str = "chrome120"
    latency: List[float] = field(default_factory=list)
    success_count: int = 0
    fail_count: int = 0
    status_403: int = 0
    status_429: int = 0
    status_530: int = 0
    last_used: float = 0
    cool_down_until: float = 0

    @property
    def health_score(self) -> float:
        total = self.success_count + self.fail_count
        if total == 0:
            return 100.0
        
        success_rate = (self.success_count / total) * 100
        # Penalize heavily for blocks
        penalty = (self.status_403 * 5) + (self.status_429 * 10) + (self.status_530 * 15)
        score = success_rate - (penalty / total)
        return max(0.0, min(100.0, score))

    def is_available(self) -> bool:
        return time.time() > self.cool_down_until and self.health_score >= 70

class NetworkManager:
    def __init__(self):
        self.pools: Dict[str, List[ProxyNode]] = {
            "webshare": [],
            "iproyal": [],
            "smartproxy": []
        }
        self.session: Optional[Any] = None

    async def add_proxy(self, pool: str, url: str, user_agent: str, headers: Dict[str, str] = None):
        if pool not in self.pools:
            self.pools[pool] = []
        node = ProxyNode(url=url, pool=pool, user_agent=user_agent, headers=headers or {})
        self.pools[pool].append(node)

    def _get_best_proxy(self) -> Optional[ProxyNode]:
        candidates = [p for pool in self.pools.values() for p in pool if p.is_available()]
        if not candidates:
            return None
        return random.choice(candidates)

    async def fetch(self, url: str, **kwargs) -> Any:
        proxy_node = self._get_best_proxy()
        proxy_dict = {"http": proxy_node.url, "https": proxy_node.url} if proxy_node else None
        
        headers = kwargs.pop("headers", {})
        if proxy_node:
            headers.update(proxy_node.headers)
            if proxy_node.user_agent:
                headers["User-Agent"] = proxy_node.user_agent

        start_time = time.time()
        try:
            if requests:
                # Use curl_cffi for advanced fingerprinting
                impersonate = proxy_node.browser_fingerprint if proxy_node else "chrome120"
                response = await asyncio.to_thread(
                    requests.get, 
                    url, 
                    proxy=proxy_node.url if proxy_node else None,
                    headers=headers,
                    impersonate=impersonate,
                    timeout=30,
                    **kwargs
                )
                
                latency = time.time() - start_time
                if proxy_node:
                    proxy_node.latency.append(latency)
                    if response.status_code == 200:
                        proxy_node.success_count += 1
                    else:
                        proxy_node.fail_count += 1
                        if response.status_code == 403: proxy_node.status_403 += 1
                        elif response.status_code == 429: proxy_node.status_429 += 1
                        elif response.status_code == 530:
                            proxy_node.status_530 += 1
                            return await self._fallback_playwright(url, proxy_node)
                
                return response.text
            else:
                raise ImportError("curl_cffi not installed")
        except Exception as e:
            logger.error(f"Fetch failed: {e}")
            if proxy_node:
                proxy_node.fail_count += 1
                proxy_node.cool_down_until = time.time() + 60
            raise

    async def _fallback_playwright(self, url: str, proxy_node: ProxyNode) -> str:
        if not async_playwright:
            logger.warning("Playwright not available for fallback")
            proxy_node.cool_down_until = time.time() + 300
            return ""

        logger.info(f"Initiating Playwright fallback for {url}")
        async with async_playwright() as p:
            browser = await p.chromium.launch(proxy={"server": proxy_node.url})
            context = await browser.new_context(user_agent=proxy_node.user_agent)
            page = await context.new_page()
            await page.goto(url, wait_until="networkidle")
            content = await page.content()
            await browser.close()
            return content
