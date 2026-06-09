import asyncio
import os
from curl_cffi.requests import AsyncSession

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
        async with AsyncSession(impersonate="chrome120") as s:
            # curl_cffi handles common browser headers automatically via impersonate
            resp = await s.get(
                "https://frontend-api.pump.fun/coins/latest", 
                proxy=url, 
                timeout=10
            )
            if resp.status_code == 200:
                return url, True, resp.status_code
            return url, False, resp.status_code
    except Exception as e:
        return url, False, str(e)

async def main():
    tasks = [test_proxy(p) for p in proxies]
    res = await asyncio.gather(*tasks)
    working = [r[0] for r in res if r[1]]
    
    print("\n--- TEST RESULTS (using curl_cffi) ---")
    for url, status, detail in res:
        # Mask the auth part for cleaner output
        short_url = url.split('@')[1] if '@' in url else url
        print(f"{short_url}: {'WORKING (200 OK)' if status else f'FAILED ({detail})'}")
        
    if working:
        print(f"\nfound a working one: {working[0].split('@')[1] if '@' in working[0] else working[0]}")
        lines = []
        if os.path.exists(".env"):
            with open(".env", "r") as f:
                lines = [l for l in f.readlines() if "PROXY_URL" not in l]
        lines.append(f"PROXY_URL={working[0]}\n")
        with open(".env", "w") as f:
            f.writelines(lines)
        print("cleaned .env and updated PROXY_URL")
    else:
        print("\nall 10 proxies are blocked or failing even with chrome120 impersonation")

if __name__ == '__main__':
    asyncio.run(main())
