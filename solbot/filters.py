import logging
from dataclasses import dataclass, field
from typing import Dict, Set, Tuple, Optional
from solbot.models import TokenEvent
from solbot.config import BotConfig
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey

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
        self._bonding_curve_program = "5Q54rBzEX3eVSgth9n5tz8X9uNfP6vdw8z4Z1D7B"

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

    async def check_supply_bubbles(self, mint: str, rpc_client: AsyncClient) -> bool:
        """
        On-chain Bubblemaps check using Solana RPC.
        Returns False if suspicious insider clusters (top holders > 10% excluding curve) are found.
        """
        try:
            mint_pubkey = Pubkey.from_string(mint)
            
            # Fetch supply info
            supply_resp = await rpc_client.get_token_supply(mint_pubkey)
            if not supply_resp.value: return True
            total_supply = int(supply_resp.value.amount)
            
            # Fetch top 10 holders
            holders_resp = await rpc_client.get_token_largest_accounts(mint_pubkey)
            if not holders_resp.value: return True
            
            for account in holders_resp.value:
                # In pump.fun, the largest account is usually the bonding curve
                # We exclude it to find insider bubbles.
                # Note: get_token_largest_accounts returns the Token Account address, not the Owner.
                # However, for a simple 'individual holder > 10%' check, we can just look at the account balance.
                
                pct = (int(account.amount.amount) / total_supply) * 100
                
                # If an individual token account holds > 10% of supply, it's likely a bubble
                # unless it's the bonding curve. We check the balance: if it's the massive 
                # curve account (often > 50% early on), we ignore it. 
                # If a secondary account has > 10%, we flag it.
                if pct > 10 and pct < 90:
                    logger.warning(f"Insider cluster detected for {mint}: Account holds {pct:.1f}%")
                    return False
            
            return True
        except Exception as e:
            logger.error(f"Error in check_supply_bubbles: {e}")
            return True # Neutral fallback

    def add_copy_target(self, address: str):
        self._copy_targets.add(address)

    def is_copy_target(self, address: str) -> bool:
        return address in self._copy_targets

    def get_dynamic_fee(self, mint: str) -> int:
        """Returns priority fee in lamports."""
        return int(0.001 * 1e9)
