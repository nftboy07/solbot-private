import asyncio
import os
import aiohttp
from solders.pubkey import Pubkey

async def main():
    mint = "4vfx6Ufwm6F3AbanbD3s9jVFF2H43e5KNapZY6vpump"
    rpc_url = "https://cosmopolitan-ancient-valley.solana-mainnet.quiknode.pro/377822aee2302f1af5a277a3032c3743d8d91385/"
    
    pids = [
        "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s",
        "metaqbxxUerdq28cj1RbAWkJaW3yiowqX154ej5CgxK"
    ]
    
    mint_pubkey = Pubkey.from_string(mint)
    
    async with aiohttp.ClientSession() as session:
        for pid_str in pids:
            pid = Pubkey.from_string(pid_str)
            seeds = [b"metadata", bytes(pid), bytes(mint_pubkey)]
            pda, _ = Pubkey.find_program_address(seeds, pid)
            print(f"Program ID: {pid_str} -> PDA: {pda}")
            
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [str(pda), {"encoding": "base64"}]
            }
            
            async with session.post(rpc_url, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    val = data.get("result", {}).get("value")
                    print(f"Exists: {val is not None}")
                else:
                    print(f"RPC HTTP Status: {resp.status}")

if __name__ == "__main__":
    asyncio.run(main())
