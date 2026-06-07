"""Token filtering and scoring logic for Solbot."""

from dataclasses import dataclass, field
from time import time
from typing import Dict, Set, Optional, Tuple

from solbot.config import BotConfig, BotMode
from solbot.logger import get_logger
from solbot.models import TokenEvent

logger = get_logger("filters")

@dataclass
class WalletMetrics:
    score: float = 0.0
    buys: int = 0
    sells: int = 0

class TokenFilter:
    """Applies V28 advanced filters and confidence scoring."""

    def __init__(self, config: BotConfig):
        self._config = config
        self._mode = config.strategy.mode  # Internal state for dynamic switching
        self._seen_mints: Set[str] = set()
        self.wallet_metrics: Dict[str, WalletMetrics] = {}
        self.smart_wallets: Set[str] = set()
        self.token_stats: Dict[str, dict] = {} # mint -> {buys: int, start_time: float}

    def update_wallet_score(self, address: str, is_buy: bool):
        metrics = self.wallet_metrics.setdefault(address, WalletMetrics())
        if is_buy:
            metrics.score += 0.01
            metrics.buys += 1
        else:
            metrics.score -= 0.02
            metrics.sells += 1
        
        if metrics.score > 0.1:
            self.smart_wallets.add(address)
        elif metrics.score < -0.2:
            self.smart_wallets.discard(address)

    def calculate_confidence_score(self, token: TokenEvent) -> int:
        score = 0
        # Liquidity +20
        if token.liquidity_sol > 50: score += 20
        # Smart Dev +20
        if token.creator in self.smart_wallets: score += 20
        # Whale Buy (Placeholder) +25
        # Volume Spike (Placeholder) +20
        # Holder Dist (Placeholder) +15
        
        # Ensure we return a baseline if DEGEN mode is checking score
        return score

    def get_dynamic_fee(self, mint: str) -> int:
        stats = self.token_stats.get(mint, {"buys": 0})
        buys = stats["buys"]
        base = self._config.fee.base_fee_lamports
        mult = self._config.fee.multiplier_per_buy
        return int(base * (1 + buys * mult))

    def is_qualified(self, token: TokenEvent) -> Tuple[bool, float]:
        """Check if a token passes V28 filters and return size."""
        if self._mode == BotMode.DEGEN:
            # Degen mode bypasses filters, zero thresholds, fast execution
            return True, 0.05

        # Dedup
        if token.mint in self._seen_mints:
            return False, 0
        self._seen_mints.add(token.mint)

        # Volume acceleration
        stats = self.token_stats.setdefault(token.mint, {"buys": 0, "start_time": time()})
        stats["buys"] += 1
        
        elapsed = time() - stats["start_time"]
        acceleration = stats["buys"] / elapsed if elapsed > 0 else 0
        
        if stats["buys"] < 3 or acceleration < 0.02:
            logger.debug(f"SKIP low momentum: {token.symbol}")
            return False, 0
            
        score = self.calculate_confidence_score(token)
        if score < self._config.strategy.min_confidence_score:
            logger.debug(f"SKIP low confidence ({score}): {token.symbol}")
            return False, 0
            
        # Confidence-based sizing
        if score >= 90: size = 0.20
        elif score >= 80: size = 0.10
        else: size = 0.05
        
        logger.info(f"PASS {token.symbol} | Score: {score} | Size: {size} SOL")
        return True, size

    def reset(self):
        self._seen_mints.clear()

    @property
    def seen_count(self) -> int:
        return len(self._seen_mints)
