import asyncio
import os
from pathlib import Path

from curl_cffi.requests import AsyncSession


def load_proxies() -> list[str]:
    path = os.getenv("PROXY_LIST_PATH", "data/proxies.txt")
    if not Path(path).exists():
        print(f"No proxy list found at {path}")
        return []
    proxies = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line and not line.startswith("#"):
                proxies.append(line)
    single = os.getenv("PROXY_URL", "").strip()
    if single:
        proxies.insert(0, single)
    return proxies


async def test_proxy(url: str):
    try:
        async with AsyncSession(impersonate="chrome120") as session:
            resp = await session.get(
                "https://frontend-api.pump.fun/coins/latest",
                proxy=url,
                timeout=10,
            )
            if resp.status_code == 200:
                return url, True, resp.status_code
            return url, False, resp.status_code
    except Exception as exc:
        return url, False, str(exc)


async def main():
    proxies = load_proxies()
    if not proxies:
        print("Add proxies to data/proxies.txt or set PROXY_URL in .env")
        return

    results = await asyncio.gather(*(test_proxy(proxy) for proxy in proxies))
    working = [result[0] for result in results if result[1]]

    print("\n--- TEST RESULTS (using curl_cffi) ---")
    for url, status, detail in results:
        short_url = url.split("@")[1] if "@" in url else url
        print(f"{short_url}: {'WORKING (200 OK)' if status else f'FAILED ({detail})'}")

    if working:
        best = working[0]
        short = best.split("@")[1] if "@" in best else best
        print(f"\nfound a working one: {short}")
        if Path(".env").exists():
            lines = []
            with open(".env", "r", encoding="utf-8") as handle:
                lines = [line for line in handle.readlines() if not line.startswith("PROXY_URL=")]
            lines.append(f"PROXY_URL={best}\n")
            with open(".env", "w", encoding="utf-8") as handle:
                handle.writelines(lines)
            print("updated PROXY_URL in .env")
    else:
        print("\nall proxies failed")


if __name__ == "__main__":
    asyncio.run(main())