import sqlite3
import os

db_path = '/root/solbot-production/solbot.db'
if not os.path.exists(db_path):
    print(f"Error: {db_path} does not exist")
    exit(1)

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

try:
    tables = [t[0] for t in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    print("Tables:", tables)
    for table in tables:
        count = cursor.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        print(f"Table {table}: {count} records")
        if count > 0:
            latest = cursor.execute(f"SELECT * FROM {table} LIMIT 1").fetchone()
            print(f"  Sample row from {table}:", dict(latest))
except Exception as e:
    print("Error querying database:", e)
finally:
    conn.close()
