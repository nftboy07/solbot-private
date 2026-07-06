#!/usr/bin/env python3
"""Print wallet SOL balance (for ops checks)."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os

from solbot.config import BotConfig
from solbot.pumpfun_client import PumpFunClient
from solbot.rpc_pool import RPCPool
from solbot.wallet import Wallet


async def main():
    config = BotConfig()
    wallet = Wallet(config.solana)
    client = PumpFunClient(config, wallet)
    pool_urls = os.getenv("SOLANA_RPC_POOL", "")
    nodes = [{"url": u.strip(), "name": f"node_{i}"} for i, u in enumerate(pool_urls.split(","), 1) if u.strip()]
    if nodes:
        client._rpc_pool = RPCPool(nodes)
    await client.start()
    try:
        bal = await client.get_sol_balance()
        print(f"WALLET_SOL={bal:.6f}")
    finally:
        await client.stop()


if __name__ == "__main__":
    asyncio.run(main())