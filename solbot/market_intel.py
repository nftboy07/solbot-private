"""Real-time market intelligence engine for Solbot.

Core responsibilities:
- Live price tracking for all open positions via DexScreener
- Liquidity monitoring with drain detection
- Volume velocity and buy/sell imbalance analysis
- Holder count growth tracking
- Rug pattern detection (LP collapse, rapid dump, volume evaporation)
- Dynamic trailing stop updates based on volatility
- Momentum surge detection
- Shared in-memory market state cache
- Telegram alerts for rug warnings and momentum surges

Architecture:
    Background polling loop -> fetch data -> update cache -> detect signals
    -> trigger exits or adjust trailing stops -> alert via Telegram
"""

import asyncio
from collections import deque
from dataclasses import dataclass, field
from time import time
from typing import Callable, Coroutine, Optional

from solbot.birdeye import BirdeyeClient, BirdeyeTokenData
from solbot.dexscreener import DexPairData, DexScreenerClient
from solbot.logger import get_logger

logger = get_logger("market_intel")


# ── Market State Models ────────────────────────────────────────────────

@dataclass
class MarketSnapshot:
    """Point-in-time market state for a token."""
    mint: str
    price_sol: float = 0.0
    price_usd: float = 0.0
    liquidity_usd: float = 0.0
    volume_5m: float = 0.0
    volume_1h: float = 0.0
    buy_sell_ratio_5m: float = 1.0
    buy_sell_ratio_1h: float = 1.0
    market_cap: float = 0.0
    holder_count: int = 0
    price_change_5m: float = 0.0
    timestamp: float = field(default_factory=time)


@dataclass
class MarketState:
    """Tracked market state with history for a single token."""
    mint: str
    symbol: str = ""
    current: Optional[MarketSnapshot] = None
    history: deque = field(default_factory=lambda: deque(maxlen=60))  # ~5 min at 5s intervals

    # Tracking baselines (set at position open)
    baseline_liquidity_usd: float = 0.0
    baseline_holder_count: int = 0
    baseline_volume_5m: float = 0.0
    peak_liquidity_usd: float = 0.0
    peak_market_cap: float = 0.0
    last_holder_count: int = 0

    # Volatility tracking
    price_samples: deque = field(default_factory=lambda: deque(maxlen=30))

    @property
    def liquidity_change_pct(self) -> float:
        """Change in liquidity from baseline."""
        if self.baseline_liquidity_usd <= 0 or not self.current:
            return 0.0
        return ((self.current.liquidity_usd - self.baseline_liquidity_usd) / self.baseline_liquidity_usd) * 100

    @property
    def liquidity_from_peak_pct(self) -> float:
        """Drop from peak liquidity."""
        if self.peak_liquidity_usd <= 0 or not self.current:
            return 0.0
        return ((self.peak_liquidity_usd - self.current.liquidity_usd) / self.peak_liquidity_usd) * 100

    @property
    def holder_growth_pct(self) -> float:
        """Holder growth from baseline."""
        if self.baseline_holder_count <= 0 or not self.current:
            return 0.0
        return ((self.current.holder_count - self.baseline_holder_count) / self.baseline_holder_count) * 100

    @property
    def volatility(self) -> float:
        """Price volatility (standard deviation of recent price changes)."""
        if len(self.price_samples) < 3:
            return 0.0
        prices = list(self.price_samples)
        changes = [(prices[i] - prices[i-1]) / prices[i-1] * 100
                   for i in range(1, len(prices)) if prices[i-1] > 0]
        if not changes:
            return 0.0
        mean = sum(changes) / len(changes)
        variance = sum((c - mean) ** 2 for c in changes) / len(changes)
        return variance ** 0.5


@dataclass
class MarketSignal:
    """A detected market signal/event."""
    mint: str
    signal_type: str  # rug_liquidity, rug_volume, momentum_surge, holder_growth, etc.
    severity: str     # "warning", "critical", "info"
    message: str
    data: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time)


# Type for signal callbacks
SignalCallback = Callable[[MarketSignal], Coroutine]


# ── Configuration ──────────────────────────────────────────────────────

@dataclass
class MarketIntelConfig:
    """Configuration for market intelligence engine."""
    # Polling
    poll_interval_seconds: float = 5.0
    birdeye_poll_interval_seconds: float = 30.0  # Birdeye has lower rate limits

    # Rug detection thresholds
    liquidity_drain_warning_pct: float = 30.0   # Warn at 30% LP drop
    liquidity_drain_critical_pct: float = 50.0  # Exit at 50% LP drop
    volume_collapse_threshold: float = 80.0     # Volume down 80% = dead
    sell_imbalance_warning: float = 3.0         # 3:1 sell:buy ratio
    sell_imbalance_critical: float = 5.0        # 5:1 sell:buy = dump

    # Momentum detection
    mcap_spike_threshold_pct: float = 100.0     # 2x mcap spike in monitoring window
    volume_surge_threshold_pct: float = 200.0   # 3x volume surge
    holder_growth_surge_pct: float = 50.0       # 50% holder growth = strong signal

    # Dynamic trailing stop
    dynamic_trailing_enabled: bool = True
    volatility_trailing_multiplier: float = 2.5  # trailing_stop = volatility * multiplier
    min_trailing_stop_pct: float = 10.0         # Never tighter than 10%
    max_trailing_stop_pct: float = 40.0         # Never wider than 40%


# ── Main Engine ────────────────────────────────────────────────────────

class MarketIntelEngine:
    """Real-time market intelligence engine.

    Runs a background polling loop that:
    1. Fetches live market data for all tracked tokens
    2. Updates the shared market state cache
    3. Runs signal detection algorithms
    4. Triggers callbacks for exits, alerts, and trailing stop adjustments
    """

    def __init__(
        self,
        config: MarketIntelConfig,
        dex_client: DexScreenerClient,
        birdeye_client: BirdeyeClient,
    ):
        self._config = config
        self._dex = dex_client
        self._birdeye = birdeye_client
        self._states: dict[str, MarketState] = {}  # mint -> MarketState
        self._running = False
        self._poll_task: Optional[asyncio.Task] = None
        self._birdeye_task: Optional[asyncio.Task] = None
        self._signal_callbacks: list[SignalCallback] = []
        self._lock = asyncio.Lock()

    @property
    def states(self) -> dict[str, MarketState]:
        """Access the shared market state cache."""
        return self._states

    def add_signal_callback(self, callback: SignalCallback):
        """Register a callback for market signals."""
        self._signal_callbacks.append(callback)

    async def start(self):
        """Start the market intelligence polling loops."""
        self._running = True
        self._poll_task = asyncio.create_task(self._dex_poll_loop())
        if self._birdeye.enabled:
            self._birdeye_task = asyncio.create_task(self._birdeye_poll_loop())
        logger.info("Market intelligence engine started")

    async def stop(self):
        """Stop all polling loops."""
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        if self._birdeye_task:
            self._birdeye_task.cancel()
            try:
                await self._birdeye_task
            except asyncio.CancelledError:
                pass
        logger.info("Market intelligence engine stopped")

    async def track_token(self, mint: str, symbol: str = ""):
        """Start tracking a token's market state.

        Called when a position is opened.
        """
        async with self._lock:
            if mint not in self._states:
                self._states[mint] = MarketState(mint=mint, symbol=symbol)
                logger.info(f"Tracking market state: {symbol} ({mint[:12]}...)")

    async def untrack_token(self, mint: str):
        """Stop tracking a token.

        Called when a position is fully closed.
        """
        async with self._lock:
            removed = self._states.pop(mint, None)
            if removed:
                logger.info(f"Untracked: {removed.symbol} ({mint[:12]}...)")

    def get_state(self, mint: str) -> Optional[MarketState]:
        """Get current market state for a token."""
        return self._states.get(mint)

    def get_dynamic_trailing_stop(self, mint: str) -> Optional[float]:
        """Calculate dynamic trailing stop based on volatility.

        Returns:
            Trailing stop percentage, or None if not enough data.
        """
        if not self._config.dynamic_trailing_enabled:
            return None

        state = self._states.get(mint)
        if not state or state.volatility <= 0:
            return None

        # Trailing stop = volatility * multiplier, clamped
        dynamic_pct = state.volatility * self._config.volatility_trailing_multiplier
        dynamic_pct = max(dynamic_pct, self._config.min_trailing_stop_pct)
        dynamic_pct = min(dynamic_pct, self._config.max_trailing_stop_pct)

        return dynamic_pct

    # ── Polling Loops ──────────────────────────────────────────────────

    async def _dex_poll_loop(self):
        """Main DexScreener polling loop."""
        while self._running:
            try:
                await self._fetch_and_update_dex()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"DexScreener poll error: {e}")

            await asyncio.sleep(self._config.poll_interval_seconds)

    async def _birdeye_poll_loop(self):
        """Birdeye polling loop (lower frequency)."""
        while self._running:
            try:
                await self._fetch_and_update_birdeye()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Birdeye poll error: {e}")

            await asyncio.sleep(self._config.birdeye_poll_interval_seconds)

    async def _fetch_and_update_dex(self):
        """Fetch DexScreener data for all tracked tokens and update state."""
        async with self._lock:
            mints = list(self._states.keys())

        if not mints:
            return

        # Batch fetch from DexScreener
        pair_data = await self._dex.get_tokens_batch(mints)

        # Update states and detect signals
        signals: list[MarketSignal] = []
        for mint, dex_data in pair_data.items():
            state = self._states.get(mint)
            if not state:
                continue

            snapshot = MarketSnapshot(
                mint=mint,
                price_sol=dex_data.price_sol,
                price_usd=dex_data.price_usd,
                liquidity_usd=dex_data.liquidity_usd,
                volume_5m=dex_data.volume_5m,
                volume_1h=dex_data.volume_1h,
                buy_sell_ratio_5m=dex_data.buy_sell_ratio_5m,
                buy_sell_ratio_1h=dex_data.buy_sell_ratio_1h,
                market_cap=dex_data.market_cap,
                price_change_5m=dex_data.price_change_5m,
            )

            # Update state
            state.current = snapshot
            state.history.append(snapshot)
            if dex_data.price_sol > 0:
                state.price_samples.append(dex_data.price_sol)

            # Set baselines on first fetch
            if state.baseline_liquidity_usd <= 0:
                state.baseline_liquidity_usd = dex_data.liquidity_usd
                state.baseline_volume_5m = dex_data.volume_5m
                state.peak_liquidity_usd = dex_data.liquidity_usd
                state.peak_market_cap = dex_data.market_cap

            # Update peaks
            if dex_data.liquidity_usd > state.peak_liquidity_usd:
                state.peak_liquidity_usd = dex_data.liquidity_usd
            if dex_data.market_cap > state.peak_market_cap:
                state.peak_market_cap = dex_data.market_cap

            # Detect signals
            new_signals = self._detect_signals(state, dex_data)
            signals.extend(new_signals)

        # Fire callbacks for detected signals
        for signal in signals:
            for callback in self._signal_callbacks:
                try:
                    await callback(signal)
                except Exception as e:
                    logger.error(f"Signal callback error: {e}")

    async def _fetch_and_update_birdeye(self):
        """Fetch Birdeye data (holder counts) for tracked tokens."""
        async with self._lock:
            mints = list(self._states.keys())

        for mint in mints:
            state = self._states.get(mint)
            if not state:
                continue

            overview = await self._birdeye.get_token_overview(mint)
            if overview:
                # Update holder count in state
                state.last_holder_count = state.current.holder_count if state.current else 0
                if state.current:
                    state.current.holder_count = overview.holder_count

                # Set baseline
                if state.baseline_holder_count <= 0:
                    state.baseline_holder_count = overview.holder_count

                # Check for holder growth surge
                if state.baseline_holder_count > 0:
                    growth = state.holder_growth_pct
                    if growth >= self._config.holder_growth_surge_pct:
                        signal = MarketSignal(
                            mint=mint,
                            signal_type="holder_growth_surge",
                            severity="info",
                            message=(
                                f"Holder growth surge: {state.symbol} | "
                                f"+{growth:.0f}% ({state.baseline_holder_count} → {overview.holder_count})"
                            ),
                            data={"holder_count": overview.holder_count, "growth_pct": growth},
                        )
                        for callback in self._signal_callbacks:
                            try:
                                await callback(signal)
                            except Exception as e:
                                logger.error(f"Signal callback error: {e}")

    # ── Signal Detection ───────────────────────────────────────────────

    def _detect_signals(self, state: MarketState, dex: DexPairData) -> list[MarketSignal]:
        """Run all signal detection algorithms on updated state."""
        signals: list[MarketSignal] = []

        # 1. Liquidity drain detection
        liq_signal = self._detect_liquidity_drain(state)
        if liq_signal:
            signals.append(liq_signal)

        # 2. Sell imbalance / dump detection
        dump_signal = self._detect_sell_dump(state, dex)
        if dump_signal:
            signals.append(dump_signal)

        # 3. Volume collapse
        vol_signal = self._detect_volume_collapse(state, dex)
        if vol_signal:
            signals.append(vol_signal)

        # 4. Market cap spike (momentum)
        mcap_signal = self._detect_mcap_spike(state, dex)
        if mcap_signal:
            signals.append(mcap_signal)

        # 5. Volume surge (momentum)
        vsurge_signal = self._detect_volume_surge(state, dex)
        if vsurge_signal:
            signals.append(vsurge_signal)

        return signals

    def _detect_liquidity_drain(self, state: MarketState) -> Optional[MarketSignal]:
        """Detect liquidity being pulled from the pool."""
        drop_from_peak = state.liquidity_from_peak_pct

        if drop_from_peak >= self._config.liquidity_drain_critical_pct:
            return MarketSignal(
                mint=state.mint,
                signal_type="rug_liquidity_critical",
                severity="critical",
                message=(
                    f"CRITICAL LP DRAIN: {state.symbol} | "
                    f"-{drop_from_peak:.0f}% from peak | "
                    f"${state.current.liquidity_usd:,.0f} remaining"
                ),
                data={
                    "drop_pct": drop_from_peak,
                    "current_liq": state.current.liquidity_usd if state.current else 0,
                    "peak_liq": state.peak_liquidity_usd,
                },
            )
        elif drop_from_peak >= self._config.liquidity_drain_warning_pct:
            return MarketSignal(
                mint=state.mint,
                signal_type="rug_liquidity_warning",
                severity="warning",
                message=(
                    f"LP drain warning: {state.symbol} | "
                    f"-{drop_from_peak:.0f}% from peak"
                ),
                data={"drop_pct": drop_from_peak},
            )

        return None

    def _detect_sell_dump(self, state: MarketState, dex: DexPairData) -> Optional[MarketSignal]:
        """Detect heavy sell pressure / dump pattern."""
        # Invert ratio: we want sell/buy
        if dex.buy_sell_ratio_5m <= 0:
            return None

        sell_buy_ratio = 1.0 / dex.buy_sell_ratio_5m

        if sell_buy_ratio >= self._config.sell_imbalance_critical:
            return MarketSignal(
                mint=state.mint,
                signal_type="rug_sell_dump",
                severity="critical",
                message=(
                    f"DUMP DETECTED: {state.symbol} | "
                    f"sell:buy = {sell_buy_ratio:.1f}:1 (5m) | "
                    f"price change: {dex.price_change_5m:+.1f}%"
                ),
                data={"sell_buy_ratio": sell_buy_ratio, "price_change_5m": dex.price_change_5m},
            )
        elif sell_buy_ratio >= self._config.sell_imbalance_warning:
            return MarketSignal(
                mint=state.mint,
                signal_type="sell_pressure_warning",
                severity="warning",
                message=(
                    f"High sell pressure: {state.symbol} | "
                    f"sell:buy = {sell_buy_ratio:.1f}:1"
                ),
                data={"sell_buy_ratio": sell_buy_ratio},
            )

        return None

    def _detect_volume_collapse(self, state: MarketState, dex: DexPairData) -> Optional[MarketSignal]:
        """Detect volume drying up (token dying)."""
        if state.baseline_volume_5m <= 0:
            return None

        vol_change = ((dex.volume_5m - state.baseline_volume_5m) / state.baseline_volume_5m) * 100

        if vol_change <= -self._config.volume_collapse_threshold:
            return MarketSignal(
                mint=state.mint,
                signal_type="volume_collapse",
                severity="warning",
                message=(
                    f"Volume collapse: {state.symbol} | "
                    f"{vol_change:.0f}% from baseline | "
                    f"current 5m vol: ${dex.volume_5m:,.0f}"
                ),
                data={"volume_change_pct": vol_change, "current_volume": dex.volume_5m},
            )

        return None

    def _detect_mcap_spike(self, state: MarketState, dex: DexPairData) -> Optional[MarketSignal]:
        """Detect rapid market cap spikes (momentum or manipulation)."""
        if state.peak_market_cap <= 0 or not state.history or len(state.history) < 3:
            return None

        # Compare current to 1 minute ago (12 snapshots at 5s intervals)
        lookback = min(12, len(state.history) - 1)
        old_snapshot = state.history[-lookback - 1] if lookback < len(state.history) else state.history[0]

        if old_snapshot.market_cap <= 0:
            return None

        mcap_change = ((dex.market_cap - old_snapshot.market_cap) / old_snapshot.market_cap) * 100

        if mcap_change >= self._config.mcap_spike_threshold_pct:
            return MarketSignal(
                mint=state.mint,
                signal_type="momentum_mcap_spike",
                severity="info",
                message=(
                    f"MCAP SURGE: {state.symbol} | "
                    f"+{mcap_change:.0f}% in ~{lookback * 5}s | "
                    f"${dex.market_cap:,.0f}"
                ),
                data={"mcap_change_pct": mcap_change, "current_mcap": dex.market_cap},
            )

        return None

    def _detect_volume_surge(self, state: MarketState, dex: DexPairData) -> Optional[MarketSignal]:
        """Detect volume surges (strong momentum)."""
        if state.baseline_volume_5m <= 0:
            return None

        vol_change = ((dex.volume_5m - state.baseline_volume_5m) / state.baseline_volume_5m) * 100

        if vol_change >= self._config.volume_surge_threshold_pct:
            return MarketSignal(
                mint=state.mint,
                signal_type="momentum_volume_surge",
                severity="info",
                message=(
                    f"Volume surge: {state.symbol} | "
                    f"+{vol_change:.0f}% from baseline | "
                    f"5m vol: ${dex.volume_5m:,.0f}"
                ),
                data={"volume_change_pct": vol_change},
            )

        return None
