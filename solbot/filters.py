import logging
import asyncio
import aiohttp
import json
from dataclasses import dataclass, field
from typing import Dict, Set, Tuple, List, Optional
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
    """Filters tokens based on V4 safety, EV criteria, volume, and copy-trade targets."""
    
    def __init__(self, config: BotConfig):
        self._config = config
        self._copy_targets: Set[str] = set()
        self._wallet_scores: Dict[str, WalletScore] = {}
        self._blacklisted_tokens: Set[str] = set()
        self._bonding_curve_program = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

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
            count = 0
            for account in holders_resp.value:
                if str(account.address) == str(bonding_curve):
                    continue
                top10_sum += int(account.amount.amount)
                count += 1
                if count >= 10:
                    break
                    
            top10_pct = (top10_sum / circulating_supply) * 100.0
            
            # Avoid Top 10 > 50%
            if top10_pct > 50.0:
                logger.warning(f"Top 10 holders hold {top10_pct:.2f}% of supply (exceeds 50%)")
                return False, top10_pct
                
            return True, top10_pct
        except Exception as e:
            logger.error(f"Error in analyze_holders for {mint}: {e}")
            return False, 0.0

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
        """Check if a token meets all snipe criteria. Returns (is_qualified, default_size, confidence_score)."""
        if token.mint in self._blacklisted_tokens:
            return False, 0.0, 0.0

        # 1. Marketcap: 25 SOL - 80 SOL
        market_cap_sol = token.market_cap_usd / sol_price if sol_price > 0 else 30.0
        if not (25.0 <= market_cap_sol <= 80.0):
            logger.info(f"Skipping {token.symbol}: Market cap {market_cap_sol:.2f} SOL outside V4 range [25, 80]")
            return False, 0.0, 0.0
        
        # 2. Age: 5 seconds - 120 seconds
        age = token.age_seconds
        if not (5.0 <= age <= 120.0):
            logger.info(f"Skipping {token.symbol}: Age {age:.2f}s outside V4 range [5, 120]")
            return False, 0.0, 0.0

        # 3. Liquidity: minimum 28 SOL
        if token.liquidity_sol < 28.0:
            logger.info(f"Skipping {token.symbol}: Liquidity {token.liquidity_sol:.2f} SOL < 28 SOL")
            return False, 0.0, 0.0

        # 4. Initial Buy: 0.5 SOL - 8 SOL
        if not (0.5 <= token.initial_buy_sol <= 8.0):
            logger.info(f"Skipping {token.symbol}: Creator initial buy {token.initial_buy_sol:.2f} SOL outside range [0.5, 8.0]")
            return False, 0.0, 0.0

        # 5. Creator Buy: less than 5%
        creator_pct = (token.initial_buy_sol / (30.0 + token.initial_buy_sol)) * 1.073 * 100.0
        if creator_pct >= 5.0:
            logger.info(f"Skipping {token.symbol}: Creator holds {creator_pct:.2f}% of supply (>= 5%)")
            return False, 0.0, 0.0

        # 6. Metadata and Image Check via IPFS gateway
        meta_passed = await self.check_ipfs_metadata(token.uri)
        if not meta_passed:
            logger.info(f"Skipping {token.symbol}: Metadata/image check failed")
            return False, 0.0, 0.0

        # RPC Checks
        rpc_url = self._config.solana.rpc_url
        async with AsyncClient(rpc_url) as rpc_client:
            # 7. No Freeze Authority & Mint Authority Revoked
            authorities_passed = await self.verify_mint_authorities(token.mint, rpc_client)
            if not authorities_passed:
                logger.info(f"Skipping {token.symbol}: Mint/Freeze authority checks failed")
                return False, 0.0, 0.0

            # 8. Holder Analysis
            holders_passed, top10_pct = await self.analyze_holders(token.mint, rpc_client)
            if not holders_passed:
                logger.info(f"Skipping {token.symbol}: Top 10 holder concentration too high ({top10_pct:.1f}%)")
                return False, 0.0, 0.0

            # 9. Bundle Detector
            bundle_passed = await self.check_bundles(token.mint, rpc_client)
            if not bundle_passed:
                logger.info(f"Skipping {token.symbol}: Bundled transactions check failed")
                return False, 0.0, 0.0

        # 10. Expected Value (EV) check
        ev, p_win = self.calculate_expected_value(ai_score, creator_score, top10_pct)
        if ev <= 0.0:
            logger.info(f"Skipping {token.symbol}: Expected Value is non-positive ({ev:.4f})")
            return False, 0.0, 0.0

        logger.info(f"Qualified {token.symbol}! EV: {ev:.4f}, Win Prob: {p_win*100:.1f}%, Confidence: {p_win*100:.1f}%")
        return True, self._config.jupiter.buy_amount_sol, p_win * 100.0

    def add_copy_target(self, address: str):
        self._copy_targets.add(address)

    def is_copy_target(self, address: str) -> bool:
        return address in self._copy_targets

    def get_dynamic_fee(self, mint: str) -> int:
        return int(0.001 * 1e9)
