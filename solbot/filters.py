"""Token filtering logic for Pump.fun events."""

from solbot.config import PumpFunConfig
from solbot.logger import get_logger
from solbot.models import TokenEvent

logger = get_logger("filters")


class TokenFilter:
    """Applies configurable filters to incoming token events."""

    def __init__(self, config: PumpFunConfig):
        self._config = config
        self._seen_mints: set[str] = set()

    def is_qualified(self, token: TokenEvent) -> bool:
        """Check if a token passes all filters."""
        # Dedup
        if token.mint in self._seen_mints:
            logger.debug(f"SKIP duplicate: {token.symbol} ({token.mint[:8]}...)")
            return False
        self._seen_mints.add(token.mint)

        # Age filter
        if token.age_seconds > self._config.max_token_age_seconds:
            logger.debug(f"SKIP too old ({token.age_seconds:.1f}s): {token.symbol}")
            return False

        # Liquidity filter
        if token.liquidity_sol < self._config.min_liquidity_sol:
            logger.debug(f"SKIP low liquidity ({token.liquidity_sol:.2f} SOL): {token.symbol}")
            return False

        # Market cap filter
        if token.market_cap_usd < self._config.min_market_cap_usd:
            logger.debug(f"SKIP low mcap (${token.market_cap_usd:.0f}): {token.symbol}")
            return False

        logger.info(
            f"PASS {token.symbol} | liq={token.liquidity_sol:.2f} SOL | "
            f"mcap=${token.market_cap_usd:.0f} | age={token.age_seconds:.1f}s"
        )
        return True

    def reset(self):
        """Clear seen tokens cache."""
        self._seen_mints.clear()

    @property
    def seen_count(self) -> int:
        return len(self._seen_mints)
