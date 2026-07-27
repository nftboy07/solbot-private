import sqlite3
import json
import os

def main():
    conn = sqlite3.connect('/root/solbot-production/solbot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT status, COUNT(*) FROM positions GROUP BY status")
    rows = cursor.fetchall()
    print("=== Database Positions ===")
    for status, count in rows:
        print(f"Status: {status} | Count: {count}")

    state_path = '/root/solbot-production/data/state.json'
    if os.path.exists(state_path):
        with open(state_path, 'r') as f:
            state = json.load(f)
        print("\n=== State JSON Positions ===")
        print(f"Total positions in state.json: {len(state.get('positions', {}))}")
        active_in_state = sum(1 for p in state.get('positions', {}).values() if p.get('active', True))
        print(f"Active positions in state.json: {active_in_state}")
    else:
        print("state.json not found")

if __name__ == '__main__':
    main()
