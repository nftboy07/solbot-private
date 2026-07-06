import asyncio
import os
from pathlib import Path

from curl_cffi.requests import AsyncSession


def load_proxies() -> list[str]:
    proxies: list[str] = []
    single_proxy = os.getenv("PROXY_URL", "").strip()
    if single_proxy:
        proxies.append(single_proxy)

    proxy_file = Path(os.getenv("PROXY_LIST_PATH", "data/proxies.local.txt"))
    if proxy_file.exists():
        proxies.extend(
            line.strip()
            for line in proxy_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
    return proxies


async def test_proxy(url):
    try:
        async with AsyncSession(impersonate="chrome120") as session:
            resp = await session.get(
                "https://frontend-api.pump.fun/coins/latest",
                proxy=url,
                timeout=10,
            )
            return url, resp.status_code == 200, resp.status_code
    except Exception as exc:
        return url, False, str(exc)


async def main():
    proxies = load_proxies()
    if not proxies:
        print("No proxies configured. Set PROXY_URL or PROXY_LIST_PATH.")
        return

    results = await asyncio.gather(*(test_proxy(proxy) for proxy in proxies))
    working = [result[0] for result in results if result[1]]

    print("\n--- TEST RESULTS (using curl_cffi) ---")
    for url, status, detail in results:
        short_url = url.split("@", 1)[1] if "@" in url else url
        print(f"{short_url}: {'WORKING (200 OK)' if status else f'FAILED ({detail})'}")

    if working:
        print(f"\nfound a working one: {working[0].split('@', 1)[1] if '@' in working[0] else working[0]}")
    else:
        print("\nall configured proxies are blocked or failing even with chrome120 impersonation")


if __name__ == "__main__":
    asyncio.run(main())
