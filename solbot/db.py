import asyncio
import sqlite3
import json
import logging
from typing import Any, Dict, List, Optional, Union
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

class Database:
    def __init__(self, db_path: str = "solbot.db"):
        self.db_path = db_path
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._loop = asyncio.get_event_loop()

    async def connect(self):
        """Initialize the database and run migrations."""
        await self._execute_write("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=NORMAL;
        """)
        await self._create_tables()

    async def _execute_read(self, query: str, params: tuple = ()) -> List[sqlite3.Row]:
        return await self._loop.run_in_executor(
            self._executor, self.__execute_read_sync, query, params
        )

    def __execute_read_sync(self, query: str, params: tuple) -> List[sqlite3.Row]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return cursor.fetchall()
        finally:
            conn.close()

    async def _execute_write(self, query: str, params: tuple = ()) -> int:
        return await self._loop.run_in_executor(
            self._executor, self.__execute_write_sync, query, params
        )

    def __execute_write_sync(self, query: str, params: tuple) -> int:
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            if ";" in query and not params:
                cursor.executescript(query)
            else:
                cursor.execute(query, params)
            conn.commit()
            return cursor.rowcount
        finally:
            conn.close()

    async def _create_tables(self):
        schemas = """
        CREATE TABLE IF NOT EXISTS ticks (
            mint TEXT PRIMARY KEY,
            creator TEXT,
            initial_liquidity REAL,
            max_marketcap REAL,
            exit_marketcap REAL,
            roi REAL,
            timestamp INTEGER,
            holder_data TEXT
        );

        CREATE TABLE IF NOT EXISTS creators (
            address TEXT PRIMARY KEY,
            token_count INTEGER DEFAULT 0,
            avg_ath REAL DEFAULT 0.0,
            rug_count INTEGER DEFAULT 0,
            blacklist_score REAL DEFAULT 0.0
        );

        CREATE TABLE IF NOT EXISTS positions (
            mint TEXT PRIMARY KEY,
            entry_price REAL,
            size REAL,
            status TEXT,
            pnl REAL,
            timestamp INTEGER
        );

        CREATE TABLE IF NOT EXISTS wallets (
            address TEXT PRIMARY KEY,
            label TEXT,
            tier TEXT,
            win_rate REAL DEFAULT 0.0,
            historical_roi REAL DEFAULT 0.0
        );
        """
        await self._execute_write(schemas)

    # Ticks Methods
    async def add_tick(self, data: Dict[str, Any]):
        query = """
            INSERT OR REPLACE INTO ticks 
            (mint, creator, initial_liquidity, max_marketcap, exit_marketcap, roi, timestamp, holder_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            data['mint'], data['creator'], data['initial_liquidity'], 
            data['max_marketcap'], data['exit_marketcap'], data['roi'], 
            data['timestamp'], json.dumps(data.get('holder_data', {}))
        )
        await self._execute_write(query, params)

    async def get_tick(self, mint: str) -> Optional[Dict]:
        rows = await self._execute_read("SELECT * FROM ticks WHERE mint = ?", (mint,))
        return dict(rows[0]) if rows else None

    # Creators Methods
    async def update_creator(self, address: str, **kwargs):
        fields = ", ".join([f"{k} = ?" for k in kwargs.keys()])
        params = list(kwargs.values()) + [address]
        query = f"UPDATE creators SET {fields} WHERE address = ?"
        
        # Try update, if 0 rows, insert
        count = await self._execute_write(query, tuple(params))
        if count == 0:
            cols = ", ".join(kwargs.keys())
            placeholders = ", ".join(["?" for _ in kwargs])
            insert_query = f"INSERT INTO creators (address, {cols}) VALUES (?, {placeholders})"
            await self._execute_write(insert_query, (address, *kwargs.values()))

    async def get_creator(self, address: str) -> Optional[Dict]:
        rows = await self._execute_read("SELECT * FROM creators WHERE address = ?", (address,))
        return dict(rows[0]) if rows else None

    # Positions Methods
    async def save_position(self, mint: str, entry_price: float, size: float, status: str = "open"):
        query = """
            INSERT OR REPLACE INTO positions (mint, entry_price, size, status, pnl, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """
        import time
        params = (mint, entry_price, size, status, 0.0, int(time.time()))
        await self._execute_write(query, params)

    async def update_position_pnl(self, mint: str, pnl: float, status: str = None):
        if status:
            query = "UPDATE positions SET pnl = ?, status = ? WHERE mint = ?"
            params = (pnl, status, mint)
        else:
            query = "UPDATE positions SET pnl = ? WHERE mint = ?"
            params = (pnl, mint)
        await self._execute_write(query, params)

    async def get_active_positions(self) -> List[Dict]:
        rows = await self._execute_read("SELECT * FROM positions WHERE status = 'open'")
        return [dict(r) for r in rows]

    # Wallets Methods
    async def upsert_wallet(self, address: str, **kwargs):
        cols = ["address"] + list(kwargs.keys())
        placeholders = ", ".join(["?" for _ in cols])
        query = f"INSERT OR REPLACE INTO wallets ({', '.join(cols)}) VALUES ({placeholders})"
        params = [address] + list(kwargs.values())
        await self._execute_write(query, tuple(params))

    async def get_wallet(self, address: str) -> Optional[Dict]:
        rows = await self._execute_read("SELECT * FROM wallets WHERE address = ?", (address,))
        return dict(rows[0]) if rows else None
