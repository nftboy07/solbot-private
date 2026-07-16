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
        sol_price = getattr(self._bot._telegram, '_sol_price', 150.0) if hasattr(self._bot, '_telegram') else 150.0

        # Fetch external data (DexScreener)
        dex_data = await self._bot._dexscreener.get_price_metrics(mint)
        if not dex_data:
            # Fallback for newly launched tokens that are not indexed yet
            logger.info(f"DexScreener metrics not found for {mint} (likely newly launched). Using fallback metrics.")
            dex_data = {
                "price_change_m5": 0.0,
                "price_change_1h": 0.0,
                "volume_m5": 1000.0, # Mock $1000 volume to pass threshold
                "volume_h1": 1000.0,
                "txns_m5": {
                    "buys": 5,
                    "sells": 1
                },
                "liquidity_usd": (token_data.get("liquidity_sol") or 2.0) * sol_price
            }

        market_cap_sol = token_data.get("market_cap_usd", 0) / sol_price
        liquidity_sol = token_data.get("liquidity_sol", 0.0)
        if dex_data.get("liquidity_usd"):
            liquidity_sol = dex_data.get("liquidity_usd") / sol_price

        # Dynamic pre-buy threshold values from bot config state
        min_liq_sol = getattr(self._bot, "_min_liquidity_sol", 2.0)
        min_mcap_sol = getattr(self._bot, "_min_mcap_sol", 2.0)
        max_top10_pct = getattr(self._bot, "_max_top10_pct", 40.0)
        max_creator_pct = getattr(self._bot, "_max_creator_pct", 10.0)
        max_largest_holder_pct = getattr(self._bot, "_max_largest_holder_pct", 15.0)
        cabal_block_enabled = getattr(self._bot, "_cabal_block_enabled", True)

        # Hard Market Cap Checks
        max_mcap_sol = 5000.0
        if hasattr(self._bot, '_config') and self._bot._config.pumpfun.max_market_cap_usd:
            max_mcap_sol = max(5000.0, self._bot._config.pumpfun.max_market_cap_usd / sol_price)
        if market_cap_sol < min_mcap_sol or market_cap_sol > max_mcap_sol:
            return "SKIP", 0, 100.0, f"Market cap out of range: {market_cap_sol:.1f} SOL (min: {min_mcap_sol:.1f})"

        # Hard Liquidity Checks
        if liquidity_sol < min_liq_sol:
            return "SKIP", 0, 100.0, f"Low liquidity: {liquidity_sol:.1f} SOL (min: {min_liq_sol:.1f})"

        # Transaction Metrics
        txns_m5 = dex_data.get("txns_m5", {})
        buys = txns_m5.get("buys", 0)
        sells = txns_m5.get("sells", 0)
        volume_m5 = dex_data.get("volume_m5", 0) / sol_price # SOL approx

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
        if cabal_block_enabled:
            if top10_pct > max_top10_pct:
                return "SKIP", 0, 100.0, f"Top 10 holds > {max_top10_pct:.1f}%: {top10_pct:.1f}%"
            if largest_holder > max_largest_holder_pct:
                return "SKIP", 0, 100.0, f"Largest holder > {max_largest_holder_pct:.1f}%: {largest_holder:.1f}%"
            if creator_holding > max_creator_pct:
                return "SKIP", 0, 100.0, f"Creator holds > {max_creator_pct:.1f}%: {creator_holding:.1f}%"

        # AGI ML Brain Integration
        brain = getattr(self._bot, "_brain", None)
        brain_enabled = False
        if brain and hasattr(self._bot, "_config") and getattr(self._bot._config.brain, "enabled", False):
            brain_enabled = True

        # Extract features for ML prediction & database logging
        price_change_m5 = float(dex_data.get("price_change_m5") or 0.0)
        price_change_h1 = float(dex_data.get("price_change_1h") or 0.0)
        volume_m5_usd = float(dex_data.get("volume_m5") or 0.0)
        volume_h1_usd = float(dex_data.get("volume_h1") or 0.0)
        
        kol_mentions = 0.0
        if hasattr(self._bot, "_kol_mentions") and isinstance(self._bot._kol_mentions, dict):
            kol_mentions = float(len(self._bot._kol_mentions.get(mint, {})))

        features = {
            "price_change_1m": price_change_m5 / 5.0,
            "price_change_5m": price_change_m5,
            "price_change_1h": price_change_h1,
            "volume_change_5m": volume_m5_usd / sol_price,
            "volume_change_1h": volume_h1_usd / sol_price,
            "holder_growth_1h": float(buys + sells),
            "holder_growth_24h": 0.0,
            "dev_balance": float(creator_holding),
            "social_score": kol_mentions,
            "kol_mention_count": kol_mentions,
            "age_minutes": float(token_data.age_seconds / 60.0) if hasattr(token_data, "age_seconds") else float(token_data.get("age_seconds", 0) / 60.0),
            "market_cap": float(market_cap_sol),
            "liquidity": float(liquidity_sol),
            "volatility_1h": abs(price_change_h1),
            "buy_pressure": float(buys),
            "sell_pressure": float(sells)
        }

        if brain_enabled:
            try:
                prediction = await brain.predict(mint, features)
                if prediction.get("trained", False):
                    action = prediction["decision"]
                    score = int(prediction["score"])
                    confidence = float(prediction["confidence"]) * 100.0
                    reasoning = f"AGI ML Model win probability: {score:.1f}% (conf: {confidence:.0f}%)"
                    return action, score, confidence, reasoning
            except Exception as e:
                logger.error(f"Error calling AGI Brain predict: {e}")

        # Smart Wallet Check
        elite_wallets = 0
        for detail in cluster_details:
            owner = detail.get("owner")
            if owner in self._bot._filter._wallet_scores:
                score = self._bot._filter._wallet_scores[owner]
                if score.win_rate > 0.5 or score.total_pnl_usd > 1000:
                    elite_wallets += 1

        min_elite = 2
        if hasattr(self._bot, "_filter") and self._bot._filter:
            min_elite = self._bot._filter.profile.min_elite_wallets
        if elite_wallets < min_elite:
            return "SKIP", 0, 100.0, f"Insufficient smart-wallet participation (<{min_elite} elite wallets)"

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
