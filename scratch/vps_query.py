import sqlite3
import asyncio
import os
import sys

# Add solbot-production to path so we can import things
sys.path.append("/root/solbot-production")

from solbot.config import BotConfig
from solbot.wallet import Wallet
from solbot.pumpfun_client import PumpFunClient

async def main():
    db_path = "/root/solbot-production/solbot.db"
    if not os.path.exists(db_path):
        print(f"Database file not found at {db_path}")
        return

    print("--- SQLite Database Info ---")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cursor.fetchall()]
    print(f"Tables: {tables}")

    for table in tables:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
        print(f"Table '{table}': {count} rows")

    # Let's inspect some ticks
    if 'ticks' in tables:
        cursor.execute("SELECT mint, max_marketcap FROM ticks ORDER BY timestamp DESC LIMIT 5")
        rows = cursor.fetchall()
        print("\n--- Recent Ticks ---")
        for r in rows:
            print(f"Mint: {r[0]} | Max MCAP: {r[1]}")

    conn.close()

    # Let's test the PumpFunClient metadata retrieval on VPS
    print("\n--- Testing PumpFunClient on VPS ---")
    # Initialize a mock config and wallet
    config = BotConfig()
    wallet = Wallet(config.solana)
    client = PumpFunClient(config, wallet)
    await client.start()

    # Get some mints
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT mint FROM ticks ORDER BY timestamp DESC LIMIT 3")
    rows = cursor.fetchall()
    conn.close()

    if rows:
        print("Resolving metadata for recent ticks:")
        for r in rows:
            mint = r[0]
            try:
                meta = await client.get_token_metadata(mint)
                print(f"Mint: {mint} -> Symbol: {meta.get('symbol')} | Name: {meta.get('name')}")
            except Exception as e:
                print(f"Mint: {mint} -> Failed to resolve: {e}")
    else:
        print("No ticks found in database.")

    await client.stop()

if __name__ == "__main__":
    asyncio.run(main())
