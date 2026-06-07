"""Streamlined Token Filter for Solbot (Clean DEGEN mode)."""

from typing import Set, Tuple
from solbot.config import BotConfig
from solbot.logger import get_logger
from solbot.models import TokenEvent

logger = get_logger("filters")

class TokenFilter:
    """Ultra-fast sniper filter. Minimal checks, maximum speed."""

    def __init__(self, config: BotConfig):
        self._config = config
        self._seen_mints: Set[str] = set()

    def is_qualified(self, token: TokenEvent) -> Tuple[bool, float]:
        """Bypasses all safety checks for maximum speed."""
        # Only check if we've already bought this mint to avoid duplicates
        if token.mint in self._seen_mints:
            return False, 0
            
        self._seen_mints.add(token.mint)
        
        # Use a fixed sniper size for speed
        size = self._config.jupiter.buy_amount_sol
        
        logger.info(f"SNIPING {token.symbol} | size={size} SOL")
        return True, size

    def get_dynamic_fee(self, mint: str) -> int:
        """Simple fixed priority fee for speed."""
        return self._config.fee.base_fee_lamports

    def reset(self):
        self._seen_mints.clear()

    @property
    def seen_count(self) -> int:
        return len(self._seen_mints)
