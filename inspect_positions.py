import sqlite3

def main():
    conn = sqlite3.connect('/root/solbot-production/solbot.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    print("--- POSITIONS PN&L BREAKDOWN ---")
    c.execute("SELECT status, pnl, count(*) FROM positions GROUP BY status, pnl")
    for row in c.fetchall():
        print(dict(row))
        
    print("\n--- LATEST 10 CLOSED POSITIONS ---")
    c.execute("SELECT * FROM positions WHERE status='closed' ORDER BY timestamp DESC LIMIT 10")
    for row in c.fetchall():
        print(dict(row))
        
    print("\n--- LATEST 10 TRADE EVENTS ---")
    c.execute("SELECT * FROM trade_events ORDER BY detect_ts DESC LIMIT 10")
    for row in c.fetchall():
        print(dict(row))
        
    conn.close()

if __name__ == "__main__":
    main()
