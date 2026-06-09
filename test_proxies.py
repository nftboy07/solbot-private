import asyncio
import aiohttp
import os

proxies = [
    "http://REDACTED_PROXY_USER:REDACTED_PROXY_PASS@38.154.203.95:5863",
    "http://REDACTED_PROXY_USER:REDACTED_PROXY_PASS@198.105.121.200:6462",
    "http://REDACTED_PROXY_USER:REDACTED_PROXY_PASS@64.137.96.74:6641",
    "http://REDACTED_PROXY_USER:REDACTED_PROXY_PASS@209.127.138.10:5784",
    "http://REDACTED_PROXY_USER:REDACTED_PROXY_PASS@38.154.185.97:6370",
    "http://REDACTED_PROXY_USER:REDACTED_PROXY_PASS@84.247.60.125:6095",
    "http://REDACTED_PROXY_USER:REDACTED_PROXY_PASS@142.111.67.146:5611",
    "http://REDACTED_PROXY_USER:REDACTED_PROXY_PASS@191.96.254.138:6185",
    "http://REDACTED_PROXY_USER:REDACTED_PROXY_PASS@31.58.9.4:6077",
    "http://REDACTED_PROXY_USER:REDACTED_PROXY_PASS@104.239.107.47:5699"
]

async def test_proxy(url):
    try:
        async with aiohttp.ClientSession() as s:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            async with s.get("https://frontend-api.pump.fun/coins/latest", proxy=url, headers=headers, timeout=5) as r:
                if r.status == 200:
                    return url, True, r.status
                return url, False, r.status
    except Exception as e:
        return url, False, str(e)

async def main():
    tasks = [test_proxy(p) for p in proxies]
    res = await asyncio.gather(*tasks)
    working = [r[0] for r in res if r[1]]
    
    print("\n--- TEST RESULTS ---")
    for url, status, detail in res:
        short_url = url.split('@')[1]
        print(f"{short_url}: {'WORKING (200 OK)' if status else f'FAILED ({detail})'}")
        
    if working:
        print(f"\nfound a working one: {working[0].split('@')[1]}")
        lines = []
        if os.path.exists(".env"):
            with open(".env", "r") as f:
                lines = [l for l in f.readlines() if "PROXY_URL" not in l]
        lines.append(f"PROXY_URL={working[0]}\n")
        with open(".env", "w") as f:
            f.writelines(lines)
        print("cleaned .env and updated PROXY_URL")
    else:
        print("\nall 10 proxies are blocked by cloudflare fr")

if __name__ == '__main__':
    asyncio.run(main())
