"""Advanced Token Filter with Smart Wallet Scoring and Copytrade Tracking."""

from typing import Set, Tuple, Dict, List, Optional
from dataclasses import dataclass, field
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
    alias: Optional[str] = None

class TokenFilter:
    """Fast sniper filter with dynamic smart wallet prioritization and copytrade support."""

    def __init__(self, config: BotConfig):
        self._config = config
        self._seen_mints: Set[str] = set()
        # address -> WalletScore
        self._wallet_scores: Dict[str, WalletScore] = {}
        # List of addresses to explicitly follow for copytrading
        self._copy_targets: Set[str] = set()

    def is_qualified(self, token: TokenEvent) -> Tuple[bool, float]:
        """Bypasses all safety checks for maximum speed, but allows for smart wallet prioritization."""
        if token.mint in self._seen_mints:
            return False, 0
            
        if token.market_cap_usd < self._config.pumpfun.min_market_cap_usd:
            logger.info(f"SKIPPING {token.symbol}: MCAP {token.market_cap_usd:.0f} below minimum")
            return False, 0

        self._seen_mints.add(token.mint)
        
        # Base sniper size
        size = self._config.jupiter.buy_amount_sol
        
        # Boost size for high-score smart wallets (Trusted Devs/Whales)
        score_obj = self._wallet_scores.get(token.creator, WalletScore(token.creator))
        if score_obj.score > 10:
            logger.info(f"SMART WALLET DETECTED: {token.creator} (Score: {score_obj.score})")
            size *= 1.5 
        
        logger.info(f"SNIPING {token.symbol} | creator={token.creator} | size={size:.2f} SOL")
        return True, size

    def is_copy_target(self, address: str) -> bool:
        """Check if a wallet address is in our follow list."""
        return address in self._copy_targets

    def add_copy_target(self, address: str, alias: Optional[str] = None):
        self._copy_targets.add(address)
        if address not in self._wallet_scores:
            self._wallet_scores[address] = WalletScore(address, alias=alias)
        elif alias:
            self._wallet_scores[address].alias = alias
        logger.info(f"Added copytrade target: {address} (Alias: {alias})")

    def remove_copy_target(self, address: str):
        if address in self._copy_targets:
            self._copy_targets.remove(address)
            logger.info(f"Removed copytrade target: {address}")

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
        return self._config.fee.base_fee_lamports

    def reset(self):
        self._seen_mints.clear()

    @property
    def seen_count(self) -> int:
        return len(self._seen_mints)
