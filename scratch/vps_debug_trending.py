import asyncio
import aiohttp

async def main():
    urls = [
        "https://frontend-api.pump.fun/coins/trending",
        "https://frontend-api-v3.pump.fun/coins/trending"
    ]
    async with aiohttp.ClientSession() as session:
        for url in urls:
            print(f"Querying: {url}")
            try:
                async with session.get(url) as resp:
                    print(f"Status: {resp.status}")
                    if resp.status == 200:
                        text = await resp.text()
                        print(f"Success! Response: {text[:300]}")
                    else:
                        text = await resp.text()
                        print(f"Error body: {text[:200]}")
            except Exception as e:
                print(f"Exception: {e}")

if __name__ == "__main__":
    asyncio.run(main())
