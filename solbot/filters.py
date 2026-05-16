"""Token filtering logic for Pump.fun events with rejection analytics."""

from solbot.config import PumpFunConfig
from solbot.logger import get_logger
from solbot.models import TokenEvent

logger = get_logger("filters")


# Global rejection counters (shared across bot lifecycle)
class RejectionCounters:
    """Tracks rejection reasons for /filters analytics."""

    def __init__(self):
        self.rejected_duplicate: int = 0
        self.rejected_age: int = 0
        self.rejected_low_liquidity: int = 0
        self.rejected_market_cap: int = 0
        self.rejected_blacklist: int = 0
        self.rejected_low_confidence: int = 0
        self.rejected_cooldown: int = 0
        self.rejected_max_positions: int = 0
        self.rejected_paused: int = 0
        self.rejected_no_route: int = 0
        self.rejected_execution_failed: int = 0
        self.qualified_tokens: int = 0
        self.execution_attempts: int = 0
        self.successful_buys: int = 0
        self.total_detected: int = 0

    def summary(self) -> dict:
        total_rejected = (
            self.rejected_duplicate + self.rejected_age +
            self.rejected_low_liquidity + self.rejected_market_cap +
            self.rejected_blacklist + self.rejected_low_confidence +
            self.rejected_cooldown + self.rejected_max_positions +
            self.rejected_paused + self.rejected_no_route +
            self.rejected_execution_failed
        )
        buy_rate = (self.successful_buys / self.execution_attempts * 100) if self.execution_attempts > 0 else 0.0
        return {
            "total_detected": self.total_detected,
            "total_rejected": total_rejected,
            "qualified_tokens": self.qualified_tokens,
            "execution_attempts": self.execution_attempts,
            "successful_buys": self.successful_buys,
            "buy_success_rate": buy_rate,
            "rejected_duplicate": self.rejected_duplicate,
            "rejected_age": self.rejected_age,
            "rejected_low_liquidity": self.rejected_low_liquidity,
            "rejected_market_cap": self.rejected_market_cap,
            "rejected_blacklist": self.rejected_blacklist,
            "rejected_low_confidence": self.rejected_low_confidence,
            "rejected_cooldown": self.rejected_cooldown,
            "rejected_max_positions": self.rejected_max_positions,
            "rejected_paused": self.rejected_paused,
            "rejected_no_route": self.rejected_no_route,
            "rejected_execution_failed": self.rejected_execution_failed,
        }

    def reset(self):
        self.__init__()


# Global singleton
rejection_counters = RejectionCounters()

# Global debug mode flag
DEBUG_MODE: bool = True  # Start with debug ON so we can see why no buys


class TokenFilter:
    """Applies configurable filters to incoming token events.

    Supports runtime overrides via _config_min_liquidity_sol and
    _config_min_market_cap_usd attributes (set by /minliq, /minmcap commands).
    """

    def __init__(self, config: PumpFunConfig):
        self._config = config
        self._seen_mints: set[str] = set()
        self._config_min_liquidity_sol: float = config.min_liquidity_sol
        self._config_min_market_cap_usd: float = config.min_market_cap_usd

    def is_qualified(self, token: TokenEvent) -> tuple[bool, str]:
        """Check if a token passes all filters.

        Returns:
            (passed: bool, rejection_reason: str or "")
        """
        rejection_counters.total_detected += 1

        # Dedup
        if token.mint in self._seen_mints:
            rejection_counters.rejected_duplicate += 1
            return False, "DUPLICATE"
        self._seen_mints.add(token.mint)

        # Age filter
        if token.age_seconds > self._config.max_token_age_seconds:
            rejection_counters.rejected_age += 1
            logger.info(
                f"REJECTED reason=AGE token={token.symbol} "
                f"age={token.age_seconds:.1f}s max={self._config.max_token_age_seconds}s"
            )
            return False, "AGE"

        # Liquidity filter
        if token.liquidity_sol < self._config_min_liquidity_sol:
            rejection_counters.rejected_low_liquidity += 1
            logger.info(
                f"REJECTED reason=LOW_LIQUIDITY token={token.symbol} "
                f"liq={token.liquidity_sol:.2f} required={self._config_min_liquidity_sol}"
            )
            return False, "LOW_LIQUIDITY"

        # Market cap filter
        if token.market_cap_usd < self._config_min_market_cap_usd:
            rejection_counters.rejected_market_cap += 1
            logger.info(
                f"REJECTED reason=LOW_MCAP token={token.symbol} "
                f"mcap=${token.market_cap_usd:.0f} required=${self._config_min_market_cap_usd:.0f}"
            )
            return False, "LOW_MCAP"

        # Passed all filters
        rejection_counters.qualified_tokens += 1
        logger.info(
            f"TOKEN PASSED FILTERS: {token.symbol} | "
            f"liq={token.liquidity_sol:.2f} SOL | mcap=${token.market_cap_usd:.0f} | "
            f"age={token.age_seconds:.1f}s"
        )
        return True, ""

    def reset(self):
        """Clear seen tokens cache."""
        self._seen_mints.clear()

    @property
    def seen_count(self) -> int:
        return len(self._seen_mints)
