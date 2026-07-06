#!/usr/bin/env python3
"""Print wallet SOL balance (for ops checks)."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from solbot.config import BotConfig
from solbot.pumpfun_client import PumpFunClient
from solbot.wallet import Wallet


async def main():
    config = BotConfig()
    wallet = Wallet(config.solana)
    client = PumpFunClient(config, wallet)
    await client.start()
    try:
        bal = await client.get_sol_balance()
        print(f"WALLET_SOL={bal:.6f}")
    finally:
        await client.stop()


if __name__ == "__main__":
    asyncio.run(main())