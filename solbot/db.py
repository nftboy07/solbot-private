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
        # Run column migrations for creators
        for col, col_type in [
            ("median_roi", "REAL DEFAULT 0.0"),
            ("survival_time_avg", "REAL DEFAULT 0.0"),
            ("whale_participation", "REAL DEFAULT 0.0"),
            ("liquidity_quality", "REAL DEFAULT 0.0"),
            ("creator_score", "REAL DEFAULT 50.0")
        ]:
            try:
                await self._execute_write(f"ALTER TABLE creators ADD COLUMN {col} {col_type}")
                logger.info(f"Added column {col} to creators table successfully.")
            except Exception as e:
                # Column likely already exists
                logger.debug(f"Column {col} already exists or failed to add: {e}")

        # Run column migrations for wallets
        for col, col_type in [
            ("avg_hold_time", "REAL DEFAULT 0.0"),
            ("avg_multiple", "REAL DEFAULT 0.0"),
            ("rug_pct", "REAL DEFAULT 0.0"),
            ("avg_entry_mcap", "REAL DEFAULT 0.0"),
            ("avg_exit_mcap", "REAL DEFAULT 0.0"),
            ("wallet_score", "REAL DEFAULT 0.0"),
            ("weight", "REAL DEFAULT 0.0")
        ]:
            try:
                await self._execute_write(f"ALTER TABLE wallets ADD COLUMN {col} {col_type}")
                logger.info(f"Added column {col} to wallets table successfully.")
            except Exception as e:
                # Column likely already exists
                logger.debug(f"Column {col} already exists or failed to add: {e}")

        # Run column migrations for positions
        for col, col_type in [
            ("reason", "TEXT")
        ]:
            try:
                await self._execute_write(f"ALTER TABLE positions ADD COLUMN {col} {col_type}")
                logger.info(f"Added column {col} to positions table successfully.")
            except Exception as e:
                logger.debug(f"Column {col} already exists or failed to add: {e}")

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
            blacklist_score REAL DEFAULT 0.0,
            median_roi REAL DEFAULT 0.0,
            survival_time_avg REAL DEFAULT 0.0,
            whale_participation REAL DEFAULT 0.0,
            liquidity_quality REAL DEFAULT 0.0,
            creator_score REAL DEFAULT 50.0
        );

        CREATE TABLE IF NOT EXISTS positions (
            mint TEXT PRIMARY KEY,
            entry_price REAL,
            size REAL,
            status TEXT,
            pnl REAL,
            timestamp INTEGER,
            reason TEXT
        );

        CREATE TABLE IF NOT EXISTS wallets (
            address TEXT PRIMARY KEY,
            label TEXT,
            tier TEXT,
            win_rate REAL DEFAULT 0.0,
            historical_roi REAL DEFAULT 0.0,
            avg_hold_time REAL DEFAULT 0.0,
            avg_multiple REAL DEFAULT 0.0,
            rug_pct REAL DEFAULT 0.0,
            avg_entry_mcap REAL DEFAULT 0.0,
            avg_exit_mcap REAL DEFAULT 0.0,
            wallet_score REAL DEFAULT 0.0,
            weight REAL DEFAULT 0.0
        );

        CREATE TABLE IF NOT EXISTS trade_events (
            trade_id TEXT PRIMARY KEY,
            signal_id TEXT,
            detect_ts REAL,
            feature_complete_ts REAL,
            model_complete_ts REAL,
            tx_build_start_ts REAL,
            tx_build_end_ts REAL,
            tx_submit_ts REAL,
            rpc_ack_ts REAL,
            block_confirm_ts REAL,
            exit_submit_ts REAL,
            exit_confirm_ts REAL,
            entry_price REAL,
            exit_price REAL,
            pnl REAL,
            strategy_version TEXT,
            git_commit_hash TEXT,
            metadata TEXT
        );

        CREATE TABLE IF NOT EXISTS signal_events (
            event_id TEXT PRIMARY KEY,
            signal_id TEXT,
            mint TEXT,
            creator TEXT,
            wallet_signal TEXT,
            confidence REAL,
            raw_signal_data TEXT,
            timestamp REAL
        );

        CREATE TABLE IF NOT EXISTS rpc_events (
            request_id TEXT PRIMARY KEY,
            provider TEXT,
            endpoint TEXT,
            method TEXT,
            latency_ms REAL,
            slot INTEGER,
            success INTEGER,
            timestamp REAL
        );

        CREATE TABLE IF NOT EXISTS proxy_events (
            proxy_id TEXT PRIMARY KEY,
            proxy_url TEXT,
            endpoint TEXT,
            latency_ms REAL,
            status_code INTEGER,
            error_type TEXT,
            timestamp REAL
        );

        CREATE TABLE IF NOT EXISTS feature_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id TEXT,
            signal_id TEXT,
            serialized_features TEXT,
            timestamp REAL
        );

        CREATE TABLE IF NOT EXISTS brain_events (
            event_id TEXT PRIMARY KEY,
            command TEXT,
            details TEXT,
            timestamp REAL
        );

        CREATE TABLE IF NOT EXISTS agi_features (
            token_mint TEXT PRIMARY KEY,
            price_change_1m REAL DEFAULT 0.0,
            price_change_5m REAL DEFAULT 0.0,
            price_change_1h REAL DEFAULT 0.0,
            volume_change_5m REAL DEFAULT 0.0,
            volume_change_1h REAL DEFAULT 0.0,
            holder_growth_1h REAL DEFAULT 0.0,
            holder_growth_24h REAL DEFAULT 0.0,
            dev_balance REAL DEFAULT 0.0,
            social_score REAL DEFAULT 0.0,
            kol_mention_count INTEGER DEFAULT 0,
            age_minutes INTEGER DEFAULT 0,
            market_cap REAL DEFAULT 0.0,
            liquidity REAL DEFAULT 0.0,
            volatility_1h REAL DEFAULT 0.0,
            buy_pressure REAL DEFAULT 0.0,
            sell_pressure REAL DEFAULT 0.0,
            timestamp INTEGER
        );

        CREATE TABLE IF NOT EXISTS agi_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_mint TEXT,
            decision TEXT,
            score REAL,
            features TEXT,
            model_version TEXT,
            timestamp INTEGER
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
    async def save_position(self, mint: str, entry_price: float, size: float, status: str = "open", reason: str = None):
        query = """
            INSERT OR REPLACE INTO positions (mint, entry_price, size, status, pnl, timestamp, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        import time
        params = (mint, entry_price, size, status, 0.0, int(time.time()), reason)
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

    # Telemetry Methods
    async def log_trade_event(self, data: Dict[str, Any]):
        cols = list(data.keys())
        placeholders = ", ".join(["?" for _ in cols])
        query = f"INSERT OR REPLACE INTO trade_events ({', '.join(cols)}) VALUES ({placeholders})"
        await self._execute_write(query, tuple(data.values()))

    async def log_signal_event(self, data: Dict[str, Any]):
        cols = list(data.keys())
        placeholders = ", ".join(["?" for _ in cols])
        query = f"INSERT OR REPLACE INTO signal_events ({', '.join(cols)}) VALUES ({placeholders})"
        await self._execute_write(query, tuple(data.values()))

    async def log_rpc_event(self, data: Dict[str, Any]):
        cols = list(data.keys())
        placeholders = ", ".join(["?" for _ in cols])
        query = f"INSERT OR REPLACE INTO rpc_events ({', '.join(cols)}) VALUES ({placeholders})"
        await self._execute_write(query, tuple(data.values()))

    async def log_proxy_event(self, data: Dict[str, Any]):
        cols = list(data.keys())
        placeholders = ", ".join(["?" for _ in cols])
        query = f"INSERT OR REPLACE INTO proxy_events ({', '.join(cols)}) VALUES ({placeholders})"
        await self._execute_write(query, tuple(data.values()))

    async def log_feature_snapshot(self, data: Dict[str, Any]):
        cols = list(data.keys())
        placeholders = ", ".join(["?" for _ in cols])
        query = f"INSERT INTO feature_snapshots ({', '.join(cols)}) VALUES ({placeholders})"
        await self._execute_write(query, tuple(data.values()))

    async def save_agi_features(self, token_mint: str, features: Dict[str, float]):
        query = """
            INSERT OR REPLACE INTO agi_features (
                token_mint, price_change_1m, price_change_5m, price_change_1h,
                volume_change_5m, volume_change_1h, holder_growth_1h, holder_growth_24h,
                dev_balance, social_score, kol_mention_count, age_minutes,
                market_cap, liquidity, volatility_1h, buy_pressure, sell_pressure,
                timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        import time
        params = (
            token_mint,
            features.get('price_change_1m', 0.0),
            features.get('price_change_5m', 0.0),
            features.get('price_change_1h', 0.0),
            features.get('volume_change_5m', 0.0),
            features.get('volume_change_1h', 0.0),
            features.get('holder_growth_1h', 0.0),
            features.get('holder_growth_24h', 0.0),
            features.get('dev_balance', 0.0),
            features.get('social_score', 0.0),
            int(features.get('kol_mention_count', 0)),
            int(features.get('age_minutes', 0)),
            features.get('market_cap', 0.0),
            features.get('liquidity', 0.0),
            features.get('volatility_1h', 0.0),
            features.get('buy_pressure', 0.0),
            features.get('sell_pressure', 0.0),
            int(time.time())
        )
        await self._execute_write(query, params)

    async def save_agi_decision(self, token_mint: str, decision: str, score: float, features: Dict[str, float], model_version: str):
        query = """
            INSERT INTO agi_decisions (token_mint, decision, score, features, model_version, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """
        import time
        params = (
            token_mint,
            decision,
            score,
            json.dumps(features),
            model_version,
            int(time.time())
        )
        await self._execute_write(query, params)

    async def get_training_data(self) -> List[Dict[str, Any]]:
        query = """
            SELECT 
                p.pnl > 0 AS win,
                f.price_change_1m,
                f.price_change_5m,
                f.price_change_1h,
                f.volume_change_5m,
                f.volume_change_1h,
                f.holder_growth_1h,
                f.holder_growth_24h,
                f.dev_balance,
                f.social_score,
                f.kol_mention_count,
                f.age_minutes,
                f.market_cap,
                f.liquidity,
                f.volatility_1h,
                f.buy_pressure,
                f.sell_pressure
            FROM positions p
            JOIN agi_features f ON p.mint = f.token_mint
            WHERE p.status = 'closed' OR p.status = 'sold'
            ORDER BY p.timestamp DESC
        """
        rows = await self._execute_read(query)
        return [dict(r) for r in rows]

