"""Async-safe creator blacklist with persistent storage.

Features:
- In-memory set for O(1) lookup during hot path
- SQLite persistence via Database layer
- Auto-blacklist on rug conditions (liquidity pull, etc.)
- Manual add/remove support
- Telegram notification on blacklist events
"""

import asyncio
from typing import Optional

from solbot.database import Database
from solbot.logger import get_logger
from solbot.models import TokenEvent

logger = get_logger("blacklist")


class BlacklistReason:
    """Standard blacklist reason codes."""
    MANUAL = "manual"
    RUG_LIQUIDITY_PULL = "rug_liquidity_pull"
    RUG_MINT_AUTHORITY = "rug_mint_authority"
    RUG_RAPID_DUMP = "rug_rapid_dump"
    RUG_STOP_LOSS_HIT = "rug_stop_loss_hit"
    REPEATED_RUGS = "repeated_rugs"
    SUSPICIOUS_PATTERN = "suspicious_pattern"


class CreatorBlacklist:
    """Async-safe creator blacklist with disk persistence and auto-blacklisting.

    Maintains an in-memory set for fast O(1) checks during the hot path
    (token filtering), backed by SQLite for persistence across restarts.
    """

    def __init__(self, db: Database, auto_blacklist_enabled: bool = True):
        self._db = db
        self._auto_blacklist_enabled = auto_blacklist_enabled
        self._cache: set[str] = set()
        self._lock = asyncio.Lock()
        # Track rug counts per creator for escalation
        self._rug_counts: dict[str, int] = {}

    async def initialize(self):
        """Load blacklist from database into memory cache."""
        entries = await self._db.get_blacklist()
        async with self._lock:
            self._cache = {entry["creator_address"] for entry in entries}
        count = len(self._cache)
        logger.info(f"Blacklist loaded: {count} creators")

    def is_blacklisted(self, creator_address: str) -> bool:
        """Fast synchronous check if a creator is blacklisted.

        This is safe to call from the hot path without awaiting
        because it only reads from the in-memory set.
        """
        if not creator_address:
            return False
        return creator_address in self._cache

    async def add(
        self,
        creator_address: str,
        reason: str = BlacklistReason.MANUAL,
        related_mint: str = "",
        related_symbol: str = "",
    ) -> bool:
        """Add a creator to the blacklist.

        Args:
            creator_address: The creator's public key.
            reason: Why this creator was blacklisted.
            related_mint: Token mint that triggered the blacklist.
            related_symbol: Token symbol for display.

        Returns:
            True if newly added, False if already blacklisted.
        """
        if not creator_address:
            return False

        async with self._lock:
            if creator_address in self._cache:
                return False
            self._cache.add(creator_address)

        # Persist to database
        await self._db.add_to_blacklist(
            creator_address=creator_address,
            reason=reason,
            related_mint=related_mint,
            related_symbol=related_symbol,
        )

        logger.warning(
            f"BLACKLISTED: {creator_address[:16]}... | "
            f"reason={reason} | token={related_symbol or related_mint[:12]}"
        )
        return True

    async def remove(self, creator_address: str) -> bool:
        """Remove a creator from the blacklist.

        Returns:
            True if removed, False if not found.
        """
        if not creator_address:
            return False

        async with self._lock:
            if creator_address not in self._cache:
                return False
            self._cache.discard(creator_address)

        removed = await self._db.remove_from_blacklist(creator_address)
        if removed:
            logger.info(f"UNBLACKLISTED: {creator_address[:16]}...")
        return removed

    async def auto_blacklist_on_rug(
        self,
        creator_address: str,
        reason: str,
        mint: str = "",
        symbol: str = "",
    ) -> bool:
        """Auto-blacklist a creator when a rug condition is detected.

        Only acts if auto_blacklist is enabled in config.

        Args:
            creator_address: Creator to blacklist.
            reason: Rug reason code from BlacklistReason.
            mint: Related token mint.
            symbol: Related token symbol.

        Returns:
            True if newly blacklisted.
        """
        if not self._auto_blacklist_enabled:
            logger.debug(f"Auto-blacklist disabled, skipping: {creator_address[:12]}...")
            return False

        if not creator_address:
            return False

        # Track rug count
        self._rug_counts[creator_address] = self._rug_counts.get(creator_address, 0) + 1

        # Escalate reason if repeated
        if self._rug_counts[creator_address] >= 2:
            reason = BlacklistReason.REPEATED_RUGS

        return await self.add(
            creator_address=creator_address,
            reason=reason,
            related_mint=mint,
            related_symbol=symbol,
        )

    async def check_and_reject(self, token: TokenEvent) -> bool:
        """Check if a token's creator is blacklisted.

        Args:
            token: The incoming token event.

        Returns:
            True if token should be REJECTED (creator is blacklisted).
        """
        if not token.creator:
            return False

        if self.is_blacklisted(token.creator):
            logger.info(
                f"BLOCKED by blacklist: {token.symbol} | "
                f"creator={token.creator[:16]}..."
            )
            return True

        return False

    @property
    def count(self) -> int:
        """Number of blacklisted creators."""
        return len(self._cache)

    async def get_all(self) -> list[dict]:
        """Get all blacklist entries with metadata."""
        return await self._db.get_blacklist()
