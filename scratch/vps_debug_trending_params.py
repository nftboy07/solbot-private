import asyncio
import aiohttp

async def main():
    url = "https://frontend-api-v3.pump.fun/coins"
    params = {
        "offset": "0",
        "limit": "5",
        "sort": "market_cap",
        "order": "DESC",
        "includeNsfw": "false"
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data:
                        first = data[0]
                        print("Keys in response item:")
                        print(list(first.keys()))
                        print("\nExample data:")
                        for k, v in first.items():
                            print(f"{k}: {v}")
                else:
                    print(f"Status: {resp.status}")
        except Exception as e:
            print(f"Exception: {e}")

if __name__ == "__main__":
    asyncio.run(main())
