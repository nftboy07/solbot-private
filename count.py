import sqlite3
import os

db_path = '/root/solbot-production/solbot.db'
if not os.path.exists(db_path):
    print("Error: db not found")
    exit(1)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()
open_count = cursor.execute("SELECT count(1) FROM positions WHERE status='open'").fetchone()[0]
closed_count = cursor.execute("SELECT count(1) FROM positions WHERE status='closed'").fetchone()[0]
print(f"Open positions: {open_count}")
print(f"Closed positions: {closed_count}")
conn.close()
