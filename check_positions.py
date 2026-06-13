import sqlite3
import os

db_path = '/root/solbot-production/solbot.db'
if not os.path.exists(db_path):
    print("Error: DB not found")
    exit(1)

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

try:
    positions = [dict(r) for r in cursor.execute("SELECT * FROM positions").fetchall()]
    print("Positions Count:", len(positions))
    for pos in positions:
        print("Position:", pos)
        
    ticks = cursor.execute("SELECT count(*) FROM ticks").fetchone()[0]
    print("Ticks Count:", ticks)
    
except Exception as e:
    print("Error:", e)
finally:
    conn.close()
