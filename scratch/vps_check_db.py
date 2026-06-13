import asyncio
import struct
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey

async def get_metadata_from_rpc(mint: str, rpc_client) -> dict:
    try:
        mint_pubkey = Pubkey.from_string(mint)
        metadata_program_id = Pubkey.from_string("metaqbxxUerdq28cj1RbAWkJaW3yiowqX154ej5CgxK")
        
        # Derive Metaplex metadata PDA
        seeds = [b"metadata", bytes(metadata_program_id), bytes(mint_pubkey)]
        metadata_pda, _ = Pubkey.find_program_address(seeds, metadata_program_id)
        
        resp = await rpc_client.get_account_info(metadata_pda)
        if resp.value is None or not resp.value.data:
            return None
            
        data = resp.value.data
        
        # Metaplex metadata layout:
        # key (1 byte)
        # update_authority (32 bytes)
        # mint (32 bytes)
        # name (4 bytes length + string)
        # symbol (4 bytes length + string)
        
        offset = 65
        name_len = struct.unpack("<I", data[offset:offset+4])[0]
        offset += 4
        name_len = min(name_len, 32) 
        name = data[offset:offset+name_len].decode("utf-8", errors="ignore").strip("\x00 \t\n\r")
        offset += 32  # Metaplex name field is allocated 32 bytes
        
        symbol_len = struct.unpack("<I", data[offset:offset+4])[0]
        offset += 4
        symbol_len = min(symbol_len, 10)
        symbol = data[offset:offset+symbol_len].decode("utf-8", errors="ignore").strip("\x00 \t\n\r")
        
        return {"symbol": symbol, "name": name}
    except Exception as e:
        print(f"Error parsing Metaplex metadata from RPC: {e}")
        return None

async def test():
    # Use RPC URL from environment or fallback
    rpc_url = "https://cosmopolitan-ancient-valley.solana-mainnet.quiknode.pro/377822aee2302f1af5a277a3032c3743d8d91385/"
    client = AsyncClient(rpc_url)
    mints = [
        'GYeTP9KZFdf1Tv7Vi8voz5nGdJMAu9hYBQJ5pFiCjray',
        'Aq3o4txccugh5tZNPL11Bn3tWBBVo6KKNxyigz4Fpump',
        '7XiJwUqFcdR7WHTaGDtZo956HJmQkHSS9EEPKsDKhWTF'
    ]
    print("=== METAPLEX RPC METADATA DECODER ===")
    for m in mints:
        meta = await get_metadata_from_rpc(m, client)
        if meta:
            print(f"Mint: {m[:8]}... -> Symbol: {meta.get('symbol')} | Name: {meta.get('name')}")
        else:
            print(f"Mint: {m[:8]}... -> Failed to resolve via RPC")
    await client.close()

asyncio.run(test())
