import asyncio
import httpx
import logging
import sys
from typing import Dict

try:
    from curl_cffi import requests
except ImportError:
    requests = None

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("proxy_diag")

TEST_TARGETS = [
    "https://www.google.com",
    "https://api.ipify.org?format=json",
    "https://httpbin.org/headers"
]

PROXIES = {
    "VPS": None,
    "Webshare": "http://username:password@p.webshare.io:80",
    "Residential": "http://username:password@geo.iproyal.com:12321"
}

async def test_httpx(name: str, proxy: str, url: str):
    logger.info(f"Testing {name} with httpx -> {url}")
    try:
        async with httpx.AsyncClient(proxy=proxy, timeout=10.0) as client:
            resp = await client.get(url)
            logger.info(f"[{name}][httpx] Status: {resp.status_code}")
            return resp.status_code
    except Exception as e:
        logger.error(f"[{name}][httpx] Failed: {e}")
        return None

async def test_curl_cffi(name: str, proxy: str, url: str):
    if not requests:
        logger.warning("curl_cffi not available")
        return None
    
    logger.info(f"Testing {name} with curl_cffi (chrome120) -> {url}")
    try:
        # Run in thread as curl_cffi requests.get is blocking
        resp = await asyncio.to_thread(
            requests.get, 
            url, 
            proxy=proxy, 
            impersonate="chrome120", 
            timeout=10
        )
        logger.info(f"[{name}][curl_cffi] Status: {resp.status_code}")
        return resp.status_code
    except Exception as e:
        logger.error(f"[{name}][curl_cffi] Failed: {e}")
        return None

async def run_matrix():
    print("Starting Proxy Diagnostics Matrix...")
    print("-" * 50)
    
    for provider_name, proxy_url in PROXIES.items():
        for target in TEST_TARGETS:
            await test_httpx(provider_name, proxy_url, target)
            await test_curl_cffi(provider_name, proxy_url, target)
            print("-" * 20)

if __name__ == "__main__":
    asyncio.run(run_matrix())
