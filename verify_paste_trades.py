import asyncio
import os
import sys

sys.path.append("/root/solbot-production")

# Load env variables manually from .env if present (when run standalone on VPS)
if os.path.exists(".env"):
    with open(".env", "r") as f:
        for line in f:
            if "=" in line and not line.strip().startswith("#"):
                parts = line.strip().split("=", 1)
                if len(parts) == 2:
                    k, v = parts
                    os.environ[k.strip()] = v.strip().strip('"').strip("'")

from solbot.paste_trade import PasteTradeClient


async def main():
    client = PasteTradeClient()

    print("--- paste.trade Verification Script ---")
    print(f"Base URL: {client.url}")
    print(f"Handle:   {client.handle}")
    masked_key = f"{client.key[:10]}...{client.key[-10:]}" if len(client.key) > 20 else "Not configured"
    print(f"API Key:  {masked_key}")

    print("\n1. Posting simulated BUY (long)...")
    buy_success = await client.post_trade(
        ticker="SOL",
        direction="long",
        author_price=162.50,
        thesis="Mock trade verification - opening position"
    )
    print(f"BUY Success: {buy_success}")

    print("\n2. Posting simulated SELL (short)...")
    sell_success = await client.post_trade(
        ticker="SOL",
        direction="short",
        author_price=168.00,
        thesis="Mock trade verification - closing position at profit"
    )
    print(f"SELL Success: {sell_success}")

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
