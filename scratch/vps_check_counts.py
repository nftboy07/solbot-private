import json
import sqlite3

with open("/root/solbot-production/data/state.json", "r") as f:
    state = json.load(f)

print(f"Blacklisted: {len(state.get('blacklisted_wallets', []))}")
print(f"Smart Wallets: {len(state.get('copy_targets', []))}")
print(f"KOLs: {len(state.get('wallet_scores', {}))}")

db_path = "/root/solbot-production/solbot.db"
try:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Get total ticks scanned
    cursor.execute("SELECT COUNT(*) FROM ticks")
    ticks_count = cursor.fetchone()[0]

    # Get total unique creators
    cursor.execute("SELECT COUNT(*) FROM creators")
    creators_count = cursor.fetchone()[0]

    # Get closed positions
    cursor.execute("SELECT COUNT(*) FROM positions WHERE status='closed'")
    closed_positions = cursor.fetchone()[0]

    print(f"Ticks: {ticks_count}")
    print(f"Creators: {creators_count}")
    print(f"Closed Positions: {closed_positions}")
    conn.close()
except Exception as e:
    print(f"DB Error: {e}")
