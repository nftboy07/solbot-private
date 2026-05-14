"""SQLite persistence layer for Solbot.

Provides async-safe database operations for:
- Position tracking (open, update, close)
- Creator blacklist (add, check, list)
- Trade history

Uses aiosqlite-style patterns with threading for async safety,
but implements with standard sqlite3 + asyncio.to_thread for zero
extra dependencies.
"""

import asyncio
import sqlite3
from pathlib import Path
from time import time
from typing import Optional

from solbot.logger import get_logger

logger = get_logger("database")

DEFAULT_DB_PATH = "solbot_data.db"


class Database:
    """Async-safe SQLite database for positions and blacklist persistence."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = asyncio.Lock()

    async def initialize(self):
        """Create database and tables if they don't exist."""
        self._conn = await asyncio.to_thread(self._connect)
        await asyncio.to_thread(self._create_tables)
        logger.info(f"Database initialized: {self._db_path}")

    async def close(self):
        """Close the database connection."""
        if self._conn:
            await asyncio.to_thread(self._conn.close)
            self._conn = None
            logger.info("Database connection closed")

    def _connect(self) -> sqlite3.Connection:
        """Create SQLite connection with WAL mode for concurrency."""
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _create_tables(self):
        """Create all required tables."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mint TEXT NOT NULL UNIQUE,
                symbol TEXT NOT NULL,
                name TEXT DEFAULT '',
                creator TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open',
                entry_price_sol REAL NOT NULL,
                entry_amount_tokens REAL NOT NULL,
                entry_tx TEXT DEFAULT '',
                exit_price_sol REAL DEFAULT 0.0,
                exit_amount_sol REAL DEFAULT 0.0,
                exit_tx TEXT DEFAULT '',
                highest_price_sol REAL DEFAULT 0.0,
                current_price_sol REAL DEFAULT 0.0,
                pnl_sol REAL DEFAULT 0.0,
                pnl_pct REAL DEFAULT 0.0,
                sell_reason TEXT DEFAULT '',
                confidence TEXT DEFAULT '',
                composite_score REAL DEFAULT 0.0,
                opened_at REAL NOT NULL,
                closed_at REAL DEFAULT 0.0,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS blacklist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_address TEXT NOT NULL UNIQUE,
                reason TEXT NOT NULL DEFAULT 'manual',
                related_mint TEXT DEFAULT '',
                related_symbol TEXT DEFAULT '',
                added_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS trade_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mint TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                amount_sol REAL NOT NULL,
                amount_tokens REAL NOT NULL,
                tx_signature TEXT DEFAULT '',
                is_paper INTEGER NOT NULL DEFAULT 1,
                confidence TEXT DEFAULT '',
                composite_score REAL DEFAULT 0.0,
                latency_ms REAL DEFAULT 0.0,
                executed_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
            CREATE INDEX IF NOT EXISTS idx_positions_mint ON positions(mint);
            CREATE INDEX IF NOT EXISTS idx_blacklist_creator ON blacklist(creator_address);
            CREATE INDEX IF NOT EXISTS idx_trade_history_mint ON trade_history(mint);
        """)
        self._conn.commit()

    # ── Position Operations ─────────────────────────────────────────────

    async def insert_position(
        self,
        mint: str,
        symbol: str,
        name: str,
        creator: str,
        entry_price_sol: float,
        entry_amount_tokens: float,
        entry_tx: str,
        confidence: str,
        composite_score: float,
    ) -> int:
        """Insert a new open position. Returns the row ID."""
        now = time()
        async with self._lock:
            row_id = await asyncio.to_thread(
                self._exec_insert,
                """INSERT OR REPLACE INTO positions
                   (mint, symbol, name, creator, status, entry_price_sol,
                    entry_amount_tokens, entry_tx, highest_price_sol,
                    current_price_sol, confidence, composite_score, opened_at, updated_at)
                   VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (mint, symbol, name, creator, entry_price_sol,
                 entry_amount_tokens, entry_tx, entry_price_sol,
                 entry_price_sol, confidence, composite_score, now, now),
            )
        logger.debug(f"Position inserted: {symbol} ({mint[:12]}...) id={row_id}")
        return row_id

    async def update_position_price(
        self, mint: str, current_price_sol: float, highest_price_sol: float
    ):
        """Update the current and highest price for a position."""
        async with self._lock:
            await asyncio.to_thread(
                self._exec,
                """UPDATE positions SET current_price_sol=?, highest_price_sol=?,
                   updated_at=? WHERE mint=? AND status='open'""",
                (current_price_sol, highest_price_sol, time(), mint),
            )

    async def close_position(
        self,
        mint: str,
        exit_price_sol: float,
        exit_amount_sol: float,
        exit_tx: str,
        pnl_sol: float,
        pnl_pct: float,
        sell_reason: str,
    ):
        """Close a position with exit details."""
        now = time()
        async with self._lock:
            await asyncio.to_thread(
                self._exec,
                """UPDATE positions SET status='closed', exit_price_sol=?,
                   exit_amount_sol=?, exit_tx=?, pnl_sol=?, pnl_pct=?,
                   sell_reason=?, closed_at=?, updated_at=?
                   WHERE mint=? AND status='open'""",
                (exit_price_sol, exit_amount_sol, exit_tx, pnl_sol, pnl_pct,
                 sell_reason, now, now, mint),
            )
        logger.info(f"Position closed: {mint[:12]}... | reason={sell_reason} | pnl={pnl_pct:+.1f}%")

    async def get_open_positions(self) -> list[dict]:
        """Get all open positions as dicts."""
        async with self._lock:
            rows = await asyncio.to_thread(
                self._fetch_all,
                "SELECT * FROM positions WHERE status='open' ORDER BY opened_at DESC",
            )
        return [dict(r) for r in rows]

    async def get_position_by_mint(self, mint: str) -> Optional[dict]:
        """Get an open position by mint address."""
        async with self._lock:
            row = await asyncio.to_thread(
                self._fetch_one,
                "SELECT * FROM positions WHERE mint=? AND status='open'",
                (mint,),
            )
        return dict(row) if row else None

    async def get_open_position_count(self) -> int:
        """Get the count of currently open positions."""
        async with self._lock:
            row = await asyncio.to_thread(
                self._fetch_one,
                "SELECT COUNT(*) as cnt FROM positions WHERE status='open'",
            )
        return row["cnt"] if row else 0

    # ── Blacklist Operations ────────────────────────────────────────────

    async def add_to_blacklist(
        self, creator_address: str, reason: str, related_mint: str = "", related_symbol: str = ""
    ):
        """Add a creator to the blacklist."""
        async with self._lock:
            await asyncio.to_thread(
                self._exec,
                """INSERT OR IGNORE INTO blacklist
                   (creator_address, reason, related_mint, related_symbol, added_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (creator_address, reason, related_mint, related_symbol, time()),
            )
        logger.warning(f"BLACKLISTED: {creator_address[:12]}... | reason={reason}")

    async def is_blacklisted(self, creator_address: str) -> bool:
        """Check if a creator is blacklisted."""
        if not creator_address:
            return False
        async with self._lock:
            row = await asyncio.to_thread(
                self._fetch_one,
                "SELECT 1 FROM blacklist WHERE creator_address=?",
                (creator_address,),
            )
        return row is not None

    async def get_blacklist(self) -> list[dict]:
        """Get all blacklisted creators."""
        async with self._lock:
            rows = await asyncio.to_thread(
                self._fetch_all,
                "SELECT * FROM blacklist ORDER BY added_at DESC",
            )
        return [dict(r) for r in rows]

    async def get_blacklist_count(self) -> int:
        """Get the number of blacklisted creators."""
        async with self._lock:
            row = await asyncio.to_thread(
                self._fetch_one,
                "SELECT COUNT(*) as cnt FROM blacklist",
            )
        return row["cnt"] if row else 0

    async def remove_from_blacklist(self, creator_address: str) -> bool:
        """Remove a creator from the blacklist. Returns True if removed."""
        async with self._lock:
            cursor = await asyncio.to_thread(
                self._exec,
                "DELETE FROM blacklist WHERE creator_address=?",
                (creator_address,),
            )
        return cursor.rowcount > 0

    # ── Trade History ───────────────────────────────────────────────────

    async def record_trade(
        self,
        mint: str,
        symbol: str,
        side: str,
        amount_sol: float,
        amount_tokens: float,
        tx_signature: str,
        is_paper: bool,
        confidence: str = "",
        composite_score: float = 0.0,
        latency_ms: float = 0.0,
    ):
        """Record a trade in history."""
        async with self._lock:
            await asyncio.to_thread(
                self._exec,
                """INSERT INTO trade_history
                   (mint, symbol, side, amount_sol, amount_tokens, tx_signature,
                    is_paper, confidence, composite_score, latency_ms, executed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (mint, symbol, side, amount_sol, amount_tokens, tx_signature,
                 1 if is_paper else 0, confidence, composite_score, latency_ms, time()),
            )

    async def get_session_stats(self) -> dict:
        """Get aggregate stats for the current session."""
        async with self._lock:
            row = await asyncio.to_thread(
                self._fetch_one,
                """SELECT
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN side='buy' THEN 1 ELSE 0 END) as buys,
                    SUM(CASE WHEN side='sell' THEN 1 ELSE 0 END) as sells,
                    SUM(CASE WHEN side='buy' THEN amount_sol ELSE 0 END) as total_bought_sol,
                    SUM(CASE WHEN side='sell' THEN amount_sol ELSE 0 END) as total_sold_sol
                   FROM trade_history""",
            )
        return dict(row) if row else {}

    # ── Internal Helpers ────────────────────────────────────────────────

    def _exec(self, sql: str, params: tuple = ()):
        """Execute a SQL statement and commit."""
        cursor = self._conn.execute(sql, params)
        self._conn.commit()
        return cursor

    def _exec_insert(self, sql: str, params: tuple = ()) -> int:
        """Execute an INSERT and return lastrowid."""
        cursor = self._conn.execute(sql, params)
        self._conn.commit()
        return cursor.lastrowid

    def _fetch_all(self, sql: str, params: tuple = ()):
        """Fetch all rows."""
        return self._conn.execute(sql, params).fetchall()

    def _fetch_one(self, sql: str, params: tuple = ()):
        """Fetch one row."""
        return self._conn.execute(sql, params).fetchone()
