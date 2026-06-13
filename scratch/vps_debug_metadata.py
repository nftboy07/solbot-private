import asyncio
import os
import sys
import aiohttp

async def test_apis(mint: str):
    urls = [
        f"https://frontend-api.pump.fun/coins/{mint}",
        f"https://frontend-api-v3.pump.fun/coins/{mint}"
    ]
    
    async with aiohttp.ClientSession() as session:
        for url in urls:
            print(f"\nQuerying: {url}")
            try:
                async with session.get(url) as resp:
                    print(f"Status: {resp.status}")
                    if resp.status == 200:
                        data = await resp.json()
                        print(f"Success! Symbol: {data.get('symbol')} | Name: {data.get('name')}")
                    else:
                        text = await resp.text()
                        print(f"Error body: {text[:200]}")
            except Exception as e:
                print(f"Exception: {e}")

async def main():
    mint = "4vfx6Ufwm6F3AbanbD3s9jVFF2H43e5KNapZY6vpump"
    if len(sys.argv) > 1:
        mint = sys.argv[1]
    await test_apis(mint)

if __name__ == "__main__":
    asyncio.run(main())
