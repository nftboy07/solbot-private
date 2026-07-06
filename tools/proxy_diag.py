import asyncio
import logging
import os
from pathlib import Path

import httpx

try:
    from curl_cffi import requests
except ImportError:
    requests = None

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("proxy_diag")

TEST_TARGETS = [
    "https://www.google.com",
    "https://api.ipify.org?format=json",
    "https://httpbin.org/headers",
]


def load_proxies() -> dict[str, str | None]:
    proxies: dict[str, str | None] = {"Direct": None}
    single_proxy = os.getenv("PROXY_URL", "").strip()
    if single_proxy:
        proxies["PROXY_URL"] = single_proxy

    proxy_file = Path(os.getenv("PROXY_LIST_PATH", "data/proxies.local.txt"))
    if proxy_file.exists():
        for index, line in enumerate(proxy_file.read_text(encoding="utf-8").splitlines(), start=1):
            value = line.strip()
            if value and not value.startswith("#"):
                proxies[f"file_proxy_{index}"] = value
    return proxies


async def test_httpx(name: str, proxy: str | None, url: str):
    logger.info("Testing %s with httpx -> %s", name, url)
    try:
        async with httpx.AsyncClient(proxy=proxy, timeout=10.0) as client:
            resp = await client.get(url)
            logger.info("[%s][httpx] Status: %s", name, resp.status_code)
            return resp.status_code
    except Exception as exc:
        logger.error("[%s][httpx] Failed: %s", name, exc)
        return None


async def test_curl_cffi(name: str, proxy: str | None, url: str):
    if not requests:
        logger.warning("curl_cffi not available")
        return None

    logger.info("Testing %s with curl_cffi (chrome120) -> %s", name, url)
    try:
        resp = await asyncio.to_thread(
            requests.get,
            url,
            proxy=proxy,
            impersonate="chrome120",
            timeout=10,
        )
        logger.info("[%s][curl_cffi] Status: %s", name, resp.status_code)
        return resp.status_code
    except Exception as exc:
        logger.error("[%s][curl_cffi] Failed: %s", name, exc)
        return None


async def run_matrix():
    print("Starting Proxy Diagnostics Matrix...")
    print("-" * 50)

    for provider_name, proxy_url in load_proxies().items():
        for target in TEST_TARGETS:
            await test_httpx(provider_name, proxy_url, target)
            await test_curl_cffi(provider_name, proxy_url, target)
            print("-" * 20)


if __name__ == "__main__":
    asyncio.run(run_matrix())
