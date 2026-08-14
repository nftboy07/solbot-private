"""High-Frequency Feature Store & Order Flow Toxicity (VPIN) Calculator."""

import time
from collections import deque
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class TradeTick:
    """Individual trade tick."""
    timestamp: float
    is_buy: bool
    amount_sol: float
    price_sol: float


@dataclass
class TokenFeatures:
    """Calculated real-time features for a token mint."""
    mint: str
    volume_1m_sol: float
    volume_5m_sol: float
    buy_ratio_1m: float
    buy_ratio_5m: float
    vpin_toxicity: float
    price_momentum_pct: float
    unique_buyers_count: int
    trade_count_1m: int


class RealTimeFeatureStore:
    """Maintains rolling windows of trade ticks per mint for ML inference."""

    def __init__(self, max_window_seconds: float = 300.0):
        self._window_sec = max_window_seconds
        self._ticks: Dict[str, deque] = {}
        self._buyers: Dict[str, set] = {}

    def record_trade(self, mint: str, is_buy: bool, amount_sol: float, price_sol: float, buyer_wallet: Optional[str] = None):
        """Record an incoming trade event."""
        now = time.time()
        tick = TradeTick(timestamp=now, is_buy=is_buy, amount_sol=amount_sol, price_sol=price_sol)
        if mint not in self._ticks:
            self._ticks[mint] = deque()
            self._buyers[mint] = set()

        q = self._ticks[mint]
        q.append(tick)
        if buyer_wallet and is_buy:
            self._buyers[mint].add(buyer_wallet)

        # Evict ticks older than max_window_seconds
        while q and (now - q[0].timestamp) > self._window_sec:
            q.popleft()

    def get_features(self, mint: str) -> TokenFeatures:
        """Calculate real-time ML features including VPIN and momentum."""
        now = time.time()
        ticks = self._ticks.get(mint, deque())

        vol_1m = 0.0
        vol_5m = 0.0
        buy_vol_1m = 0.0
        buy_vol_5m = 0.0
        trades_1m = 0

        prices_1m = []

        for t in ticks:
            age = now - t.timestamp
            if age <= 300.0:
                vol_5m += t.amount_sol
                if t.is_buy:
                    buy_vol_5m += t.amount_sol
            if age <= 60.0:
                vol_1m += t.amount_sol
                trades_1m += 1
                prices_1m.append(t.price_sol)
                if t.is_buy:
                    buy_vol_1m += t.amount_sol

        buy_ratio_1m = (buy_vol_1m / vol_1m) if vol_1m > 0 else 0.5
        buy_ratio_5m = (buy_vol_5m / vol_5m) if vol_5m > 0 else 0.5

        # VPIN calculation: Volume-Synchronized Probability of Toxicity
        # VPIN = |buy_volume - sell_volume| / total_volume
        sell_vol_5m = max(0.0, vol_5m - buy_vol_5m)
        vpin = abs(buy_vol_5m - sell_vol_5m) / max(vol_5m, 0.0001)

        # Price momentum (% change over 1m)
        momentum_pct = 0.0
        if len(prices_1m) >= 2 and prices_1m[0] > 0:
            momentum_pct = ((prices_1m[-1] - prices_1m[0]) / prices_1m[0]) * 100.0

        unique_buyers = len(self._buyers.get(mint, set()))

        return TokenFeatures(
            mint=mint,
            volume_1m_sol=vol_1m,
            volume_5m_sol=vol_5m,
            buy_ratio_1m=buy_ratio_1m,
            buy_ratio_5m=buy_ratio_5m,
            vpin_toxicity=vpin,
            price_momentum_pct=momentum_pct,
            unique_buyers_count=unique_buyers,
            trade_count_1m=trades_1m,
        )
