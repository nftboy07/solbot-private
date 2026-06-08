import logging
from dataclasses import dataclass, field
from typing import Dict, Set, Tuple, Optional
from solbot.models import TokenEvent
from solbot.config import BotConfig

logger = logging.getLogger("bot.filters")

@dataclass
class WalletScore:
    address: str
    alias: Optional[str] = None
    score: int = 0
    total_trades: int = 0
    win_rate: float = 0.0

class TokenFilter:
    """Filters tokens based on safety, volume, and copy-trade targets."""
    
    def __init__(self, config: BotConfig):
        self._config = config
        self._copy_targets: Set[str] = set()
        self._wallet_scores: Dict[str, WalletScore] = {}
        self._blacklisted_tokens: Set[str] = set()

    def is_qualified(self, token: TokenEvent) -> Tuple[bool, float]:
        """Check if a token meets all snipe criteria."""
        # Check blacklist
        if token.mint in self._blacklisted_tokens:
            return False, 0.0

        # Check market cap limits
        if token.market_cap_usd < self._config.strategy.min_mcap:
            return False, 0.0
        
        # Check liquidity
        if token.liquidity_sol < self._config.strategy.min_liquidity:
            return False, 0.0

        # Default trade size
        return True, self._config.jupiter.buy_amount_sol

    def add_copy_target(self, address: str):
        self._copy_targets.add(address)

    def is_copy_target(self, address: str) -> bool:
        return address in self._copy_targets

    def get_dynamic_fee(self, mint: str) -> int:
        """Returns priority fee in lamports."""
        return int(0.001 * 1e9)
