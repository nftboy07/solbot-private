import logging
import asyncio
import aiohttp
import json
import os
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Set, Tuple, List
from solbot.models import TokenEvent
from solbot.config import BotConfig
from solbot.filter_profiles import FilterProfile, get_profile, default_profile_name
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey

logger = logging.getLogger("bot.filters")

# Major established mints. These are never a "new pump.fun launch", and buying one
# corrupts everything downstream: a live paper run sniped USDC, whose multi-billion
# market cap against a fresh-launch entry price booked a 2,200,000x ROI, which would
# trip every take-profit rung at once and poison the stats the AI tuner reads.
ESTABLISHED_MINTS: Set[str] = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
    "So11111111111111111111111111111111111111112",   # wSOL
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # BONK
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",   # JUP
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So",   # mSOL
    "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs",  # ETH (Wormhole)
    "9n4nbM75f5Ui33ZbPYXn59EwSgE8CGsHtAeTH5YFeJ9E",  # BTC (Sollet)
}

@dataclass
class WalletScore:
    address: str
    alias: Optional[str] = None
    score: int = 0
    total_trades: int = 0
    win_rate: float = 0.0

class TokenFilter:
    """Filters tokens based on V4 safety, EV criteria, volume, and copy-trade targets."""
    
    def __init__(self, config: BotConfig):
        self._config = config
        self._copy_targets: Set[str] = set()
        self._wallet_scores: Dict[str, WalletScore] = {}
        self._blacklisted_tokens: Set[str] = set()
        self._bonding_curve_program = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
        self._profile: FilterProfile = get_profile(default_profile_name())
        self._on_skip: Optional[Callable[[str], None]] = None

    @property
    def profile(self) -> FilterProfile:
        return self._profile

    def set_profile(self, profile: FilterProfile) -> None:
        self._profile = profile
        logger.info("Filter profile set to %s", profile.name)

    def set_skip_callback(self, callback: Optional[Callable[[str], None]]) -> None:
        self._on_skip = callback

    def _resolve_rpc_url(self) -> str:
        pool = os.getenv("SOLANA_RPC_POOL", "")
        for url in pool.split(","):
            url = url.strip()
            if url:
                return url
        return self._config.solana.rpc_url

    def _reject(self, reason: str) -> Tuple[bool, float, float]:
        if self._on_skip:
            self._on_skip(reason)
        return False, 0.0, 0.0

    async def check_ipfs_metadata(self, uri: str) -> bool:
        """Fetch metadata JSON from IPFS directly to bypass Cloudflare API block."""
        if not uri:
            return False
        try:
            url = uri
            if uri.startswith("ipfs://"):
                ipfs_hash = uri.split("ipfs://")[-1]
                url = f"https://cf-ipfs.com/ipfs/{ipfs_hash}"
            
            # Use a quick timeout to prevent blocking
            timeout = aiohttp.ClientTimeout(total=5.0)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        meta = await resp.json()
                        if meta.get("name") and (meta.get("image") or meta.get("image_uri")):
                            return True
        except Exception as e:
            logger.debug(f"Error checking IPFS metadata from {uri}: {e}")
        return False

    async def verify_mint_authorities(self, mint: str, rpc_client: AsyncClient) -> bool:
        """Verify mint authority is revoked and freeze authority is disabled."""
        try:
            mint_pubkey = Pubkey.from_string(mint)
            resp = await rpc_client.get_account_info(mint_pubkey)
            if not resp.value:
                return False
            
            data = resp.value.data
            # Layout of Mint Account:
            # Offset 0: mint_authority option (4 bytes)
            # Offset 46: freeze_authority option (4 bytes)
            if len(data) >= 82:
                mint_auth_option = int.from_bytes(data[0:4], byteorder='little')
                freeze_auth_option = int.from_bytes(data[46:50], byteorder='little')
                
                mint_revoked = (mint_auth_option == 0)
                freeze_revoked = (freeze_auth_option == 0)
                
                return mint_revoked and freeze_revoked
            return False
        except Exception as e:
            logger.error(f"Error verifying mint authorities for {mint}: {e}")
            return False
        
    async def analyze_holders(self, mint: str, rpc_client: AsyncClient) -> Tuple[bool, float]:
        """Analyze top 10 holder concentration excluding the bonding curve."""
        try:
            mint_pubkey = Pubkey.from_string(mint)
            supply_resp = await rpc_client.get_token_supply(mint_pubkey)
            if not supply_resp.value:
                return True, 0.0
            total_supply = int(supply_resp.value.amount)
            
            bonding_curve, _ = Pubkey.find_program_address(
                [b"bonding-curve", bytes(mint_pubkey)],
                Pubkey.from_string(self._bonding_curve_program)
            )
            
            curve_balance_resp = await rpc_client.get_token_account_balance(bonding_curve)
            curve_balance = 0
            if curve_balance_resp.value:
                curve_balance = int(curve_balance_resp.value.amount)
                
            circulating_supply = total_supply - curve_balance
            if circulating_supply <= 0:
                return True, 0.0
                
            holders_resp = await rpc_client.get_token_largest_accounts(mint_pubkey)
            if not holders_resp.value:
                return True, 0.0
                
            top10_sum = 0
            largest_holder_pct = 0.0
            count = 0
            for account in holders_resp.value:
                if str(account.address) == str(bonding_curve):
                    continue
                holder_amt = int(account.amount.amount)
                holder_pct = (holder_amt / circulating_supply) * 100.0 if circulating_supply > 0 else 0.0
                if holder_pct > largest_holder_pct:
                    largest_holder_pct = holder_pct
                top10_sum += holder_amt
                count += 1
                if count >= 10:
                    break
                    
            top10_pct = (top10_sum / circulating_supply) * 100.0 if circulating_supply > 0 else 0.0
            
            # Avoid Top 10 > 25% or single whale > 5% outside bonding curve
            if top10_pct > 25.0:
                logger.warning(f"Top 10 holders hold {top10_pct:.2f}% of supply (exceeds 25% safety cap)")
                return False, top10_pct
            if largest_holder_pct > 5.0:
                logger.warning(f"Single whale holds {largest_holder_pct:.2f}% of supply (exceeds 5% safety cap)")
                return False, top10_pct
                
            return True, top10_pct
        except Exception as e:
            logger.debug("Holder analysis unavailable for %s: %s (passing)", mint, e)
            return True, 0.0

    async def check_bundles(self, mint: str, rpc_client: AsyncClient) -> bool:
        """Detect bundled wallets funded by same address holding > 30% of supply."""
        try:
            mint_pubkey = Pubkey.from_string(mint)
            holders_resp = await rpc_client.get_token_largest_accounts(mint_pubkey)
            if not holders_resp.value:
                return True
                
            account_pubkeys = [account.address for account in holders_resp.value[:10]]
            if not account_pubkeys:
                return True
                
            info_resp = await rpc_client.get_multiple_accounts(account_pubkeys)
            if not info_resp.value:
                return True
                
            owners = []
            for acc in info_resp.value:
                if acc and len(acc.data) >= 64:
                    owner_bytes = acc.data[32:64]
                    owner_pubkey = Pubkey(owner_bytes)
                    owners.append(str(owner_pubkey))
                    
            unique_owners = list(set(owners))
            
            # Quick check funding signatures in parallel for top unique owners
            async def get_funder(owner_addr: str) -> Optional[str]:
                try:
                    owner_pubkey = Pubkey.from_string(owner_addr)
                    sigs_resp = await rpc_client.get_signatures_for_address(owner_pubkey, limit=10)
                    if sigs_resp.value:
                        oldest_sig = sigs_resp.value[-1].signature
                        tx_resp = await rpc_client.get_transaction(oldest_sig, max_supported_transaction_version=0)
                        if tx_resp.value and tx_resp.value.transaction:
                            keys = tx_resp.value.transaction.transaction.message.account_keys
                            if len(keys) > 0:
                                return str(keys[0])
                except:
                    pass
                return None

            tasks = [get_funder(owner) for owner in unique_owners[:5]]
            results = await asyncio.gather(*tasks)
            
            funding_map = {}
            for owner, funder in zip(unique_owners[:5], results):
                if funder:
                    funding_map[owner] = funder
                    
            funder_counts = {}
            for owner, funder in funding_map.items():
                if funder not in funder_counts:
                    funder_counts[funder] = []
                funder_counts[funder].append(owner)
                
            supply_resp = await rpc_client.get_token_supply(mint_pubkey)
            if not supply_resp.value:
                return True
            total_supply = int(supply_resp.value.amount)
            
            owner_balances = {}
            for account, owner in zip(holders_resp.value[:10], owners):
                owner_balances[owner] = int(account.amount.amount)
                
            for funder, linked_owners in funder_counts.items():
                if len(linked_owners) > 1:
                    combined_balance = sum(owner_balances.get(owner, 0) for owner in linked_owners)
                    combined_pct = (combined_balance / total_supply) * 100.0
                    if combined_pct > 30.0:
                        logger.warning(f"Bundle detected for {mint}: Funder {funder} holds {combined_pct:.2f}% of supply via linked wallets")
                        return False
            return True
        except Exception as e:
            logger.error(f"Error in check_bundles for {mint}: {e}")
            return False

    def calculate_expected_value(self, ai_score: float, creator_score: float, top10_pct: float) -> Tuple[float, float]:
        """Calculates win probability and expected value (EV)."""
        p_win = (ai_score / 100.0) * 0.60
        creator_adj = (creator_score - 50.0) / 50.0 * 0.20
        p_win += creator_adj
        
        if top10_pct < 25.0:
            p_win += 0.10
        elif top10_pct > 40.0:
            p_win -= 0.10
            
        p_win = max(0.05, min(0.95, p_win))
        p_loss = 1.0 - p_win
        
        # V4 metrics: Win avg +250%, Loss avg -90%
        avg_win_pct = 2.50
        avg_loss_pct = 0.90
        
        ev = (p_win * avg_win_pct) - (p_loss * avg_loss_pct)
        return ev, p_win

    async def is_qualified(self, token: TokenEvent, sol_price: float = 150.0, ai_score: float = 80.0, creator_score: float = 50.0) -> Tuple[bool, float, float]:
        """Check if a token meets snipe criteria for the active filter profile."""
        p = self._profile
        if token.mint in ESTABLISHED_MINTS:
            logger.warning(
                "Skipping %s (%s): established mint, not a new launch",
                token.symbol, token.mint[:8],
            )
            return self._reject("established mint")
        if token.mint in self._blacklisted_tokens:
            return self._reject("mint blacklisted")

        market_cap_sol = token.market_cap_usd / sol_price if sol_price > 0 else 30.0
        if not (p.min_mcap_sol <= market_cap_sol <= p.max_mcap_sol):
            logger.info(
                "Skipping %s: Market cap %.2f SOL outside %s range [%.1f, %.1f]",
                token.symbol, market_cap_sol, p.name, p.min_mcap_sol, p.max_mcap_sol,
            )
            return self._reject(f"mcap {market_cap_sol:.1f} SOL")

        age = token.age_seconds
        if not (p.min_age_seconds <= age <= p.max_age_seconds):
            logger.info(
                "Skipping %s: Age %.2fs outside %s range [%.1f, %.1f]",
                token.symbol, age, p.name, p.min_age_seconds, p.max_age_seconds,
            )
            return self._reject(f"age {age:.1f}s")

        if token.liquidity_sol < p.min_liquidity_sol:
            logger.info(
                "Skipping %s: Liquidity %.2f SOL < %.1f SOL (%s)",
                token.symbol, token.liquidity_sol, p.min_liquidity_sol, p.name,
            )
            return self._reject(f"liquidity {token.liquidity_sol:.1f} SOL")

        if not (p.min_initial_buy_sol <= token.initial_buy_sol <= p.max_initial_buy_sol):
            logger.info(
                "Skipping %s: Creator initial buy %.2f SOL outside %s range [%.1f, %.1f]",
                token.symbol, token.initial_buy_sol, p.name, p.min_initial_buy_sol, p.max_initial_buy_sol,
            )
            return self._reject(f"initial buy {token.initial_buy_sol:.2f} SOL")

        creator_pct = (token.initial_buy_sol / (30.0 + token.initial_buy_sol)) * 1.073 * 100.0
        if creator_pct >= p.max_creator_pct:
            logger.info(
                "Skipping %s: Creator holds %.2f%% of supply (>= %.1f%%)",
                token.symbol, creator_pct, p.max_creator_pct,
            )
            return self._reject(f"creator hold {creator_pct:.1f}%")

        if p.require_metadata:
            meta_passed = await self.check_ipfs_metadata(token.uri)
            if not meta_passed:
                logger.info("Skipping %s: Metadata/image check failed (%s)", token.symbol, p.name)
                return self._reject("metadata failed")

        top10_pct = 0.0
        rpc_url = self._resolve_rpc_url()
        async with AsyncClient(rpc_url) as rpc_client:
            if p.require_authorities:
                authorities_passed = await self.verify_mint_authorities(token.mint, rpc_client)
                if not authorities_passed:
                    logger.info("Skipping %s: Mint/Freeze authority checks failed (%s)", token.symbol, p.name)
                    return self._reject("authorities failed")

            if p.require_holder_check:
                holders_passed, top10_pct = await self.analyze_holders(token.mint, rpc_client)
                if not holders_passed:
                    logger.info(
                        "Skipping %s: Top 10 holder concentration too high (%.1f%%) (%s)",
                        token.symbol, top10_pct, p.name,
                    )
                    return self._reject(f"top10 {top10_pct:.1f}%")

            if p.require_bundle_check:
                bundle_passed = await self.check_bundles(token.mint, rpc_client)
                if not bundle_passed:
                    logger.info("Skipping %s: Bundled transactions check failed (%s)", token.symbol, p.name)
                    return self._reject("bundle detected")

        ev, p_win = self.calculate_expected_value(ai_score, creator_score, top10_pct)
        if p.require_ev_positive and ev <= 0.0:
            logger.info("Skipping %s: Expected Value is non-positive (%.4f) (%s)", token.symbol, ev, p.name)
            return self._reject(f"ev {ev:.3f}")

        logger.info(
            "Qualified %s [%s]! EV: %.4f, Win Prob: %.1f%%, Confidence: %.1f%%",
            token.symbol, p.name, ev, p_win * 100.0, p_win * 100.0,
        )
        return True, self._config.jupiter.buy_amount_sol, p_win * 100.0

    def add_copy_target(self, address: str):
        self._copy_targets.add(address)

    def is_copy_target(self, address: str) -> bool:
        return address in self._copy_targets

    def get_dynamic_fee(self, mint: str) -> int:
        return int(0.001 * 1e9)
