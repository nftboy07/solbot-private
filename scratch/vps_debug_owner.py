import asyncio
import os
import aiohttp
from solders.pubkey import Pubkey

async def main():
    mint = "CLkwqGufjMkmDMHmBxT3QMEQttjZXPNU5VkxFmU2pump"
    rpc_url = "https://cosmopolitan-ancient-valley.solana-mainnet.quiknode.pro/377822aee2302f1af5a277a3032c3743d8d91385/"
    
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getAccountInfo",
        "params": [mint, {"encoding": "base64"}]
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(rpc_url, json=payload) as resp:
            if resp.status == 200:
                data = await resp.json()
                value = data.get("result", {}).get("value")
                if value:
                    print(f"Owner program of mint: {value['owner']}")
                    print(f"Space/Size: {value['space']} bytes")
                else:
                    print("Account does not exist!")

if __name__ == "__main__":
    asyncio.run(main())
