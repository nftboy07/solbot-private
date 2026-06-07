"""Advanced Token Filter with Smart Wallet Scoring for Solbot."""

from typing import Set, Tuple, Dict
from dataclasses import dataclass
from solbot.config import BotConfig
from solbot.logger import get_logger
from solbot.models import TokenEvent

logger = get_logger("filters")

@dataclass
class WalletScore:
    address: str
    wins: int = 0
    losses: int = 0
    score: int = 0

class TokenFilter:
    """Fast sniper filter with dynamic smart wallet prioritization."""

    def __init__(self, config: BotConfig):
        self._config = config
        self._seen_mints: Set[str] = set()
        # address -> WalletScore
        self._wallet_scores: Dict[str, WalletScore] = {}

    def is_qualified(self, token: TokenEvent) -> Tuple[bool, float]:
        """Bypasses all safety checks for maximum speed, but allows for smart wallet prioritization."""
        if token.mint in self._seen_mints:
            return False, 0
            
        self._seen_mints.add(token.mint)
        
        # Base sniper size
        size = self._config.jupiter.buy_amount_sol
        
        # Optional: Boost size for high-score smart wallets
        creator_score = self._wallet_scores.get(token.creator, WalletScore(token.creator))
        if creator_score.score > 10:
            logger.info(f"SMART WALLET DETECTED: {token.creator} (Score: {creator_score.score})")
            size *= 1.5 # Boost buy for trusted devs/whales
        
        logger.info(f"SNIPING {token.symbol} | creator={token.creator} | size={size:.2f} SOL")
        return True, size

    def update_score(self, address: str, is_win: bool):
        """Update wallet score based on trade outcome (WIN/LOSS)."""
        if not address:
            return
            
        score_obj = self._wallet_scores.get(address, WalletScore(address))
        if is_win:
            score_obj.wins += 1
            score_obj.score += 2
        else:
            score_obj.losses += 1
            score_obj.score -= 1
            
        self._wallet_scores[address] = score_obj
        logger.info(f"Wallet {address} updated: Score={score_obj.score} (W:{score_obj.wins} L:{score_obj.losses})")

    def get_dynamic_fee(self, mint: str) -> int:
        """Simple fixed priority fee for speed."""
        return self._config.fee.base_fee_lamports

    def reset(self):
        self._seen_mints.clear()

    @property
    def seen_count(self) -> int:
        return len(self._seen_mints)
