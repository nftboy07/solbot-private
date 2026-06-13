import asyncio
from curl_cffi.requests import AsyncSession

async def main():
    url = "https://frontend-api-v3.pump.fun/coins/trending"
    params = {
        "offset": "0",
        "limit": "20",
        "sort": "market_cap",
        "order": "DESC",
        "includeNsfw": "false"
    }
    
    print(f"Querying with curl_cffi: {url}")
    try:
        async with AsyncSession(impersonate="chrome120") as session:
            resp = await session.get(url, params=params, timeout=10)
            print(f"Status Code: {resp.status_code}")
            print(f"Headers: {resp.headers}")
            text = resp.text
            print(f"Response (length {len(text)}): {text[:500]}")
    except Exception as e:
        print(f"Exception: {e}")

if __name__ == "__main__":
    asyncio.run(main())
