import asyncio
import os
from solbot.config import BotConfig
from solbot.wallet import Wallet
from solbot.pumpfun_client import PumpFunClient

async def check():
    config = BotConfig()
    wallet = Wallet(config.solana)
    client = PumpFunClient(config, wallet)
    await client.start()
    
    sol = await client.get_sol_balance()
    print(f"Wallet: {wallet.pubkey_str}")
    print(f"SOL Balance: {sol:.4f} SOL")
    
    tokens = await client.get_all_token_balances()
    print(f"Tokens with balance: {len(tokens)}")
    for mint, data in tokens.items():
        print(f"Mint: {mint} | Balance: {data['balance']}")
        
    await client.stop()

asyncio.run(check())
