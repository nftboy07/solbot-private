"""Prometheus Metric Exporter and Historical Trade Replay Backtester."""

import time
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class BacktestTradeOutcome:
    """Outcome of a simulated trade in backtest mode."""
    mint: str
    entry_price: float
    exit_price: float
    pnl_sol: float
    pnl_pct: float
    exit_reason: str
    hold_duration_sec: float


@dataclass
class BacktestSummary:
    """Aggregated backtest performance metrics."""
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    net_pnl_sol: float
    max_drawdown_pct: float
    profit_factor: float


class PrometheusMetricsExporter:
    """Formats Solbot operational metrics into Prometheus text exposition format."""

    def __init__(self, bot):
        self._bot = bot

    def generate_metrics(self) -> str:
        """Render Prometheus metrics."""
        lines = [
            "# HELP solbot_active_positions Number of currently open positions",
            "# TYPE solbot_active_positions gauge",
            f"solbot_active_positions {len(getattr(self._bot, '_positions', {}))}",
            "",
            "# HELP solbot_wallet_sol Current wallet SOL balance",
            "# TYPE solbot_wallet_sol gauge",
            f"solbot_wallet_sol {getattr(self._bot._risk_manager, 'bankroll_sol', 1.0):.4f}",
            "",
            "# HELP solbot_ai_min_score Current AI qualification threshold",
            "# TYPE solbot_ai_min_score gauge",
            f"solbot_ai_min_score {getattr(self._bot, '_ai_min_score', 75)}",
        ]
        return "\n".join(lines) + "\n"


class HistoricalReplayBacktester:
    """Replays historical tick sequences to backtest take-profit and stop-loss rules."""

    def run_replay(
        self,
        mint: str,
        ticks: List[Dict[str, float]],
        buy_amount_sol: float = 0.10,
        stop_loss_pct: float = 0.20,
        take_profit_pct: float = 1.00,
        trailing_stop_pct: float = 0.15,
    ) -> Optional[BacktestTradeOutcome]:
        """Replay ticks: list of {'timestamp': float, 'price': float}."""
        if not ticks:
            return None

        entry_price = ticks[0]["price"]
        highest_price = entry_price
        start_time = ticks[0]["timestamp"]

        for tick in ticks[1:]:
            price = tick["price"]
            highest_price = max(highest_price, price)
            gain_pct = (price - entry_price) / entry_price
            drop_from_peak = (highest_price - price) / highest_price

            # Check Hard Take Profit
            if gain_pct >= take_profit_pct:
                pnl = buy_amount_sol * gain_pct
                return BacktestTradeOutcome(
                    mint=mint,
                    entry_price=entry_price,
                    exit_price=price,
                    pnl_sol=round(pnl, 4),
                    pnl_pct=round(gain_pct * 100.0, 2),
                    exit_reason="TAKE_PROFIT",
                    hold_duration_sec=tick["timestamp"] - start_time,
                )

            # Check Trailing Stop (if gained > 20%)
            if highest_price >= entry_price * 1.20 and drop_from_peak >= trailing_stop_pct:
                pnl = buy_amount_sol * gain_pct
                return BacktestTradeOutcome(
                    mint=mint,
                    entry_price=entry_price,
                    exit_price=price,
                    pnl_sol=round(pnl, 4),
                    pnl_pct=round(gain_pct * 100.0, 2),
                    exit_reason="TRAILING_STOP",
                    hold_duration_sec=tick["timestamp"] - start_time,
                )

            # Check Hard Stop Loss
            if gain_pct <= -stop_loss_pct:
                pnl = buy_amount_sol * gain_pct
                return BacktestTradeOutcome(
                    mint=mint,
                    entry_price=entry_price,
                    exit_price=price,
                    pnl_sol=round(pnl, 4),
                    pnl_pct=round(gain_pct * 100.0, 2),
                    exit_reason="STOP_LOSS",
                    hold_duration_sec=tick["timestamp"] - start_time,
                )

        # Stale exit at final tick
        final_price = ticks[-1]["price"]
        final_gain = (final_price - entry_price) / entry_price
        return BacktestTradeOutcome(
            mint=mint,
            entry_price=entry_price,
            exit_price=final_price,
            pnl_sol=round(buy_amount_sol * final_gain, 4),
            pnl_pct=round(final_gain * 100.0, 2),
            exit_reason="TIME_EXPIRATION",
            hold_duration_sec=ticks[-1]["timestamp"] - start_time,
        )
