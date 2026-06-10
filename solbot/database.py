import sqlite3
import os
import json
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

logger = logging.getLogger("solbot.database")

class DatabaseManager:
    """SQLite Database Manager for Solbot tracking and reputation."""

    def __init__(self, db_path: str = "data/solbot.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Initialize database tables."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # Wallets table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS wallets (
                    address TEXT PRIMARY KEY,
                    alias TEXT,
                    win_rate REAL DEFAULT 0.0,
                    avg_roi REAL DEFAULT 0.0,
                    total_trades INTEGER DEFAULT 0,
                    tags TEXT,
                    is_blacklisted INTEGER DEFAULT 0,
                    is_whitelisted INTEGER DEFAULT 0,
                    last_active TIMESTAMP
                )
            """)
            # KOL Activity table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS kol_activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    wallet_address TEXT,
                    token_address TEXT,
                    timestamp TIMESTAMP,
                    buy_amount REAL,
                    detected_latency REAL,
                    FOREIGN KEY (wallet_address) REFERENCES wallets (address)
                )
            """)
            conn.commit()

    def migrate_from_json(self, state_path: str = "data/state.json"):
        """Migrate existing tracking config from state.json to SQLite."""
        if not os.path.exists(state_path):
            return

        try:
            with open(state_path, "r") as f:
                state = json.load(f)

            copy_targets = set(state.get("copy_targets", []))
            wallet_scores = state.get("wallet_scores", {})
            blacklisted = set(state.get("blacklisted_wallets", []))

            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Migrate copy targets and scores
                for addr, score in wallet_scores.items():
                    alias = score.get("alias")
                    win_rate = score.get("winrate", 0.0)
                    # Deduce tags from alias or copy_targets
                    tags_list = []
                    if "KOL" in (alias or "").upper(): tags_list.append("KOL")
                    if "WHALE" in (alias or "").upper(): tags_list.append("whale")
                    if "SMART" in (alias or "").upper(): tags_list.append("smart")
                    if addr in copy_targets and not tags_list: tags_list.append("whale")
                    
                    is_whitelisted = 1 if addr in copy_targets else 0
                    is_blacklisted = 1 if addr in blacklisted else 0
                    
                    cursor.execute("""
                        INSERT OR REPLACE INTO wallets 
                        (address, alias, win_rate, tags, is_whitelisted, is_blacklisted, last_active)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (addr, alias, win_rate, ",".join(tags_list), is_whitelisted, is_blacklisted, datetime.now()))

                # Migrate remaining blacklisted wallets not in scores
                for addr in blacklisted:
                    if addr not in wallet_scores:
                        cursor.execute("""
                            INSERT OR IGNORE INTO wallets (address, is_blacklisted, last_active)
                            VALUES (?, ?, ?)
                        """, (addr, 1, datetime.now()))
                
                # Migrate remaining copy targets not in scores
                for addr in copy_targets:
                    if addr not in wallet_scores:
                        cursor.execute("""
                            INSERT OR IGNORE INTO wallets (address, is_whitelisted, tags, last_active)
                            VALUES (?, ?, ?, ?)
                        """, (addr, 1, "whale", datetime.now()))

                conn.commit()
            logger.info("Successfully migrated state.json to SQLite database.")
        except Exception as e:
            logger.error(f"Migration failed: {e}")

    def add_follow(self, address: str, alias: Optional[str] = None):
        """Add or update a wallet as followed (KOL)."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO wallets (address, alias, tags, is_whitelisted, last_active)
                VALUES (?, ?, 'KOL', 1, ?)
                ON CONFLICT(address) DO UPDATE SET
                    alias = COALESE(?, alias),
                    tags = CASE WHEN tags LIKE '%KOL%' THEN tags ELSE tags || ',KOL' END,
                    is_whitelisted = 1,
                    last_active = ?
            """, (address, alias, datetime.now(), alias, datetime.now()))
            conn.commit()

    def remove_follow(self, address: str):
        """Remove KOL/whale tag or delete tracking state."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # Fetch current tags
            cursor.execute("SELECT tags FROM wallets WHERE address = ?", (address,))
            row = cursor.fetchone()
            if row:
                tags = row[0].split(",") if row[0] else []
                new_tags = [t for t in tags if t not in ["KOL", "whale"]]
                if not new_tags and row[0]: # If it was only KOL/whale, we might want to just un-whitelist
                    cursor.execute("UPDATE wallets SET is_whitelisted = 0, tags = '' WHERE address = ?", (address,))
                else:
                    cursor.execute("UPDATE wallets SET tags = ?, is_whitelisted = 0 WHERE address = ?", (",".join(new_tags), address))
            conn.commit()

    def update_blacklist(self, address: str, action: str):
        """Add or remove a wallet from blacklist."""
        is_bl = 1 if action == "add" else 0
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO wallets (address, is_blacklisted, last_active)
                VALUES (?, ?, ?)
                ON CONFLICT(address) DO UPDATE SET is_blacklisted = ?, last_active = ?
            """, (address, is_bl, datetime.now(), is_bl, datetime.now()))
            conn.commit()

    def get_blacklist(self) -> List[str]:
        """Get all blacklisted addresses."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT address FROM wallets WHERE is_blacklisted = 1")
            return [row[0] for row in cursor.fetchall()]

    def get_whales_and_kols(self) -> List[Dict[str, Any]]:
        """Query wallets with KOL or whale tags."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT address, alias, win_rate, avg_roi, tags 
                FROM wallets 
                WHERE tags LIKE '%KOL%' OR tags LIKE '%whale%'
            """)
            return [dict(row) for row in cursor.fetchall()]

    def log_kol_activity(self, wallet: str, token: str, amount: float, latency: float = 0.0):
        """Track a buy from a followed wallet."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO kol_activity (wallet_address, token_address, timestamp, buy_amount, detected_latency)
                VALUES (?, ?, ?, ?, ?)
            """, (wallet, token, datetime.now(), amount, latency))
            conn.commit()
