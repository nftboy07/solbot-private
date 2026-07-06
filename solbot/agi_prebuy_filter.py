import asyncio
import logging
from typing import Dict, Tuple

logger = logging.getLogger("bot.agi_prebuy")

class AGIPreBuyFilter:
    """
    Implements the multi-variable momentum and risk filter.
    Scores out of 100 based on distribution, buys, and smart money.
    """
    def __init__(self, bot):
        self._bot = bot

    async def evaluate_token(self, mint: str, token_data: Dict) -> Tuple[str, int, float, str]:
        """
        Evaluates a token according to the strict AGI ruleset.
        Returns: (action, score, confidence, reasoning)
        Action is one of: "BUY_FULL", "BUY_HALF", "WATCH", "SKIP"
        """
        # Fetch external data (DexScreener)
        dex_data = await self._bot._dexscreener.get_price_metrics(mint)
        if not dex_data:
            return "SKIP", 0, 0.0, "Could not fetch DexScreener data"

        market_cap_sol = token_data.get("market_cap_usd", 0) / 150.0  # Approx SOL conversion, assuming SOL=$150
        liquidity_sol = token_data.get("liquidity_sol", 0)
        if dex_data.get("liquidity_usd"):
            liquidity_sol = dex_data.get("liquidity_usd") / 150.0

        # Hard Market Cap Checks
        sol_price = getattr(self._bot._telegram, '_sol_price', 150.0) if hasattr(self._bot, '_telegram') else 150.0
        max_mcap_sol = 5000.0
        if hasattr(self._bot, '_config') and self._bot._config.pumpfun.max_market_cap_usd:
            max_mcap_sol = max(5000.0, self._bot._config.pumpfun.max_market_cap_usd / sol_price)
        if market_cap_sol < 5.0 or market_cap_sol > max_mcap_sol:
            return "SKIP", 0, 100.0, f"Market cap out of range: {market_cap_sol:.1f} SOL"

        # Hard Liquidity Checks
        if liquidity_sol < 25:
            return "SKIP", 0, 100.0, f"Low liquidity: {liquidity_sol:.1f} SOL"

        # Transaction Metrics
        txns_m5 = dex_data.get("txns_m5", {})
        buys = txns_m5.get("buys", 0)
        sells = txns_m5.get("sells", 0)
        volume_m5 = dex_data.get("volume_m5", 0) / 150.0 # SOL approx

        if volume_m5 < 3:
            return "SKIP", 0, 100.0, f"Volume too low: {volume_m5:.1f} SOL"

        if buys + sells > 0 and sells / (buys + sells) > 0.65:
            return "SKIP", 0, 100.0, f"High sell ratio: {sells} sells vs {buys} buys (dumping)"

        buy_sell_ratio = buys / max(1, sells)

        # Holder Distribution and Clusters
        risk_score, cluster_size, cluster_details = await self._bot._cluster_mapper.analyze_token_cluster(mint, await self._bot._pump_client._get_rpc_url())
        
        top10_pct = sum([d.get("share_pct", 0) for d in cluster_details])
        creator_holding = next((d.get("share_pct", 0) for d in cluster_details if d.get("address") == token_data.get("creator")), 0)
        largest_holder = max([d.get("share_pct", 0) for d in cluster_details]) if cluster_details else 0

        # Hard Risk Checks
        if top10_pct > 35:
            return "SKIP", 0, 100.0, f"Top 10 holds > 35%: {top10_pct:.1f}%"
        if largest_holder > 10:
            return "SKIP", 0, 100.0, f"Largest holder > 10%: {largest_holder:.1f}%"
        if creator_holding > 8:
            return "SKIP", 0, 100.0, f"Creator holds > 8%: {creator_holding:.1f}%"

        # Smart Wallet Check
        elite_wallets = 0
        for detail in cluster_details:
            owner = detail.get("owner")
            if owner in self._bot._filter._wallet_scores:
                score = self._bot._filter._wallet_scores[owner]
                if score.win_rate > 0.5 or score.total_pnl_usd > 1000:
                    elite_wallets += 1

        if elite_wallets < 2:
            return "SKIP", 0, 100.0, "Insufficient smart-wallet participation (<2 elite wallets)"

        # Calculate Weights
        score = 0
        
        # Smart Wallets (25%)
        if elite_wallets >= 5: score += 25
        elif elite_wallets >= 3: score += 20
        elif elite_wallets >= 2: score += 10
        
        # Holder Distribution (20%)
        if top10_pct < 20: score += 20
        elif top10_pct < 30: score += 10

        # Buy/Sell Ratio (15%)
        if buy_sell_ratio >= 2.5: score += 15
        elif buy_sell_ratio >= 1.5: score += 10

        # Volume Growth & Base Volume (10%)
        if volume_m5 >= 20: score += 10
        elif volume_m5 >= 10: score += 5

        # Holder Growth (10%)
        # Simulated based on txns count
        if (buys + sells) > 100: score += 10
        elif (buys + sells) > 50: score += 5

        # Creator Reputation (10%)
        if creator_holding < 2: score += 10
        elif creator_holding <= 5: score += 5

        # Liquidity (5%)
        if liquidity_sol >= 35: score += 5

        # Social Momentum (5%)
        kol_mentions = len(self._bot._kol_mentions.get(mint, {}))
        if kol_mentions > 0: score += 5

        # Execution Logic
        action = "SKIP"
        if score >= 90:
            action = "BUY_FULL"
        elif score >= 80:
            action = "BUY_HALF"
        elif score >= 70:
            action = "WATCH"

        reasoning = f"Score {score}: SmartWallets={elite_wallets}, Top10={top10_pct:.1f}%, B/S={buys}/{sells}, Vol={volume_m5:.1f}SOL"
        return action, score, 90.0, reasoning
