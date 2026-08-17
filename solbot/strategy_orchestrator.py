"""
Strategy Orchestrator for SolBot V3.
Manages 4 distinct trading strategies, tracks live performance per strategy,
and provides automatic failover switching, 5-stage Fibonacci take-profit ladders,
and Telegram-controlled manual switching.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger("bot.strategy_orchestrator")


@dataclass
class StrategyProfile:
    name: str
    display_name: str
    description: str
    buy_amount_sol: float = 0.015
    max_positions: int = 3
    ai_min_score: int = 75
    min_mcap_usd: float = 10_000.0
    max_mcap_usd: float = 350_000.0
    max_dev_holding_pct: float = 0.035
    max_top10_holding_pct: float = 0.08
    min_buy_ratio: float = 0.68
    min_unique_buyers: int = 12
    # 5-Stage Fibonacci Take-Profit Ladder
    tp1_mult: float = 1.35
    tp1_pct: float = 0.30
    tp2_mult: float = 1.80
    tp2_pct: float = 0.25
    tp3_mult: float = 2.60
    tp3_pct: float = 0.20
    tp4_mult: float = 4.20
    tp4_pct: float = 0.15
    moonbag_pct: float = 0.10
    stop_loss_pct: float = 0.12
    trailing_stop_pct: float = 0.15
    break_even_trigger: float = 1.20
    break_even_floor: float = 1.05
    stale_liquidate_seconds: int = 120
    peak_drawdown_stop_pct: float = 0.10
    requires_whale_overlap: bool = False
    requires_runner_pattern: bool = False
    
    # Live stats per strategy
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    consecutive_losses: int = 0
    total_pnl_sol: float = 0.0
    recent_pnl: List[float] = field(default_factory=list)


class StrategyOrchestrator:
    """
    Manages 4 distinct strategies and automatically switches if consecutive losses occur.
    """

    STRATEGIES: Dict[str, StrategyProfile] = {
        "alpha_sniper": StrategyProfile(
            name="alpha_sniper",
            display_name="🎯 Safe Alpha Sniper",
            description="Strict AI Sentiment >= 75, Anti-Rug Dev Genome, dev holding <= 3.5%, 5-stage Fibonacci moonbag.",
            buy_amount_sol=0.015,
            max_positions=3,
            ai_min_score=75,
            min_mcap_usd=12_000.0,
            max_mcap_usd=300_000.0,
            max_dev_holding_pct=0.035,
            max_top10_holding_pct=0.08,
            min_buy_ratio=0.68,
            min_unique_buyers=12,
            tp1_mult=1.35,
            tp1_pct=0.30,
            tp2_mult=1.80,
            tp2_pct=0.25,
            tp3_mult=2.60,
            tp3_pct=0.20,
            tp4_mult=4.20,
            tp4_pct=0.15,
            moonbag_pct=0.10,
            stop_loss_pct=0.12,
            break_even_trigger=1.20,
        ),
        "runner_momentum": StrategyProfile(
            name="runner_momentum",
            display_name="⚡ Missed Runner Momentum",
            description="Matches 5x-7500x runner signatures, volume acceleration, buy ratio >= 70%.",
            buy_amount_sol=0.015,
            max_positions=2,
            ai_min_score=50,
            min_mcap_usd=4_000.0,
            max_mcap_usd=880_000.0,
            max_dev_holding_pct=0.04,
            max_top10_holding_pct=0.10,
            min_buy_ratio=0.70,
            min_unique_buyers=20,
            tp1_mult=1.50,
            tp1_pct=0.35,
            tp2_mult=2.50,
            tp2_pct=0.30,
            tp3_mult=5.00,
            tp3_pct=0.20,
            tp4_mult=10.00,
            tp4_pct=0.10,
            moonbag_pct=0.05,
            stop_loss_pct=0.12,
            break_even_trigger=1.25,
            requires_runner_pattern=True,
        ),
        "kol_whale_copy": StrategyProfile(
            name="kol_whale_copy",
            display_name="🐋 Whale & KOL Copy",
            description="Follows 3,718 smart whales & KOL buys. Requires >=2 smart wallet confirmations.",
            buy_amount_sol=0.015,
            max_positions=2,
            ai_min_score=55,
            min_mcap_usd=8_000.0,
            max_mcap_usd=500_000.0,
            max_dev_holding_pct=0.04,
            max_top10_holding_pct=0.10,
            min_buy_ratio=0.65,
            min_unique_buyers=10,
            tp1_mult=1.80,
            tp1_pct=0.40,
            tp2_mult=3.50,
            tp2_pct=0.30,
            tp3_mult=7.00,
            tp3_pct=0.15,
            tp4_mult=15.00,
            tp4_pct=0.10,
            moonbag_pct=0.05,
            stop_loss_pct=0.14,
            break_even_trigger=1.30,
            requires_whale_overlap=True,
        ),
        "conservative_rebalancer": StrategyProfile(
            name="conservative_rebalancer",
            display_name="🛡️ Capital Preservation",
            description="High safety floor ($30k+ MCAP), 0% dev holding, tight 8% stop loss, fast scalps.",
            buy_amount_sol=0.012,
            max_positions=2,
            ai_min_score=70,
            min_mcap_usd=30_000.0,
            max_mcap_usd=600_000.0,
            max_dev_holding_pct=0.01,
            max_top10_holding_pct=0.05,
            min_buy_ratio=0.70,
            min_unique_buyers=15,
            tp1_mult=1.25,
            tp1_pct=0.60,
            tp2_mult=1.60,
            tp2_pct=0.40,
            tp3_mult=2.50,
            tp3_pct=0.0,
            tp4_mult=3.00,
            tp4_pct=0.0,
            moonbag_pct=0.0,
            stop_loss_pct=0.08,
            break_even_trigger=1.15,
        ),
    }

    ROTATION_ORDER = [
        "alpha_sniper",
        "runner_momentum",
        "kol_whale_copy",
        "conservative_rebalancer"
    ]

    def __init__(self, initial_strategy: str = "alpha_sniper", active_strategy: Optional[str] = None, auto_switch: bool = True):
        strat = active_strategy or initial_strategy
        self.active_strategy_name = strat if strat in self.STRATEGIES else "alpha_sniper"
        self.auto_switch_enabled = auto_switch
        self.failover_consecutive_losses_threshold = 3
        self.failover_drawdown_sol_threshold = 0.04
        self._switch_callbacks = []
        logger.info(f"Initialized StrategyOrchestrator with active: {self.active_strategy_name}, auto-switch: {self.auto_switch_enabled}")

    @property
    def current(self) -> StrategyProfile:
        return self.STRATEGIES[self.active_strategy_name]

    @property
    def active_strategy(self) -> StrategyProfile:
        return self.STRATEGIES[self.active_strategy_name]

    def register_switch_callback(self, cb):
        """Register a callback that is called when strategy switches: cb(old_name, new_name)"""
        self._switch_callbacks.append(cb)

    def switch_strategy(self, strategy_name: str, reason: str = "manual") -> Tuple[bool, str]:
        """Switch the active strategy. Returns (success, message)."""
        norm_name = strategy_name.lower().strip()
        
        # Support aliases
        aliases = {
            "1": "alpha_sniper",
            "alpha": "alpha_sniper",
            "sniper": "alpha_sniper",
            "2": "runner_momentum",
            "runner": "runner_momentum",
            "momentum": "runner_momentum",
            "3": "kol_whale_copy",
            "kol": "kol_whale_copy",
            "whale": "kol_whale_copy",
            "copy": "kol_whale_copy",
            "4": "conservative_rebalancer",
            "conservative": "conservative_rebalancer",
            "safe": "conservative_rebalancer",
        }
        if norm_name in aliases:
            norm_name = aliases[norm_name]

        if norm_name not in self.STRATEGIES:
            logger.warning(f"Attempted to switch to invalid strategy: {strategy_name}")
            return False, f"Unknown strategy: {strategy_name}"

        old_strat = self.active_strategy_name
        self.active_strategy_name = norm_name
        msg = f"Switched strategy: {old_strat} -> {norm_name} ({reason})"
        logger.warning(f"🔄 {msg}")

        for cb in self._switch_callbacks:
            try:
                cb(old_strat, norm_name)
            except Exception as e:
                logger.error(f"Error in strategy switch callback: {e}")

        return True, msg

    def record_trade_result(self, pnl_sol: float, roi_pct: float) -> Optional[str]:
        """
        Record a closed trade result against the active strategy.
        Checks if auto-failover should trigger.
        Returns notification message if a failover occurred.
        """
        strat = self.active_strategy
        strat.total_trades += 1
        strat.total_pnl_sol += pnl_sol
        strat.recent_pnl.append(pnl_sol)
        if len(strat.recent_pnl) > 20:
            strat.recent_pnl.pop(0)

        if pnl_sol > 0:
            strat.wins += 1
            strat.consecutive_losses = 0
            logger.info(f"Strategy {strat.name} WIN: +{pnl_sol:.4f} SOL (+{roi_pct:.1f}%)")
        else:
            strat.losses += 1
            strat.consecutive_losses += 1
            logger.warning(f"Strategy {strat.name} LOSS: {pnl_sol:.4f} SOL ({roi_pct:.1f}%) [Consecutive Losses: {strat.consecutive_losses}]")

        # Auto-failover check
        if self.auto_switch_enabled:
            # Check 1: 3 consecutive losses
            should_failover = strat.consecutive_losses >= self.failover_consecutive_losses_threshold
            
            # Check 2: Recent drawdown exceeded
            recent_loss_sum = sum(p for p in strat.recent_pnl[-5:] if p < 0)
            if abs(recent_loss_sum) >= self.failover_drawdown_sol_threshold:
                should_failover = True

            if should_failover:
                # Find next strategy in rotation
                curr_idx = self.ROTATION_ORDER.index(self.active_strategy_name) if self.active_strategy_name in self.ROTATION_ORDER else 0
                next_strat = self.ROTATION_ORDER[(curr_idx + 1) % len(self.ROTATION_ORDER)]
                
                reason = f"Failover triggered: {strat.consecutive_losses} consecutive losses ({recent_loss_sum:.4f} SOL drawdown)"
                old_name = self.active_strategy_name
                self.switch_strategy(next_strat, reason=reason)
                
                # Reset consecutive losses on previous strategy to give it a fresh start next rotation
                strat.consecutive_losses = 0
                
                alert_msg = (
                    f"⚠️ <b>STRATEGY AUTO-FAILOVER TRIGGERED</b> ⚠️\n\n"
                    f"Strategy <b>{strat.display_name}</b> suffered consecutive losses.\n"
                    f"Rotated automatically to: <b>{self.active_strategy.display_name}</b>\n"
                    f"Reason: <i>{reason}</i>"
                )
                return alert_msg

        return None

    def get_dashboard_text(self) -> str:
        """Generate a rich Telegram scoreboard comparing all 4 strategies."""
        lines = ["🎛 <b>SOLBOT MULTI-STRATEGY CONTROL</b> 🎛\n"]
        lines.append(f"<b>Auto-Switch:</b> {'🟢 ENABLED' if self.auto_switch_enabled else '🔴 DISABLED'}\n")

        for idx, (key, strat) in enumerate(self.STRATEGIES.items(), 1):
            is_active = (key == self.active_strategy_name)
            marker = "👉 <b>[ACTIVE]</b> " if is_active else f"[{idx}] "
            win_rate = (strat.wins / strat.total_trades * 100) if strat.total_trades > 0 else 0.0
            pnl_str = f"+{strat.total_pnl_sol:.4f}" if strat.total_pnl_sol >= 0 else f"{strat.total_pnl_sol:.4f}"
            
            lines.append(
                f"{marker}<b>{strat.display_name}</b>\n"
                f"  • Size: <code>{strat.buy_amount_sol} SOL</code> | Max Bags: <code>{strat.max_positions}</code>\n"
                f"  • Win Rate: <code>{win_rate:.1f}%</code> ({strat.wins}W / {strat.losses}L / {strat.total_trades}T)\n"
                f"  • Net PnL: <code>{pnl_str} SOL</code> | Consec Losses: <code>{strat.consecutive_losses}</code>\n"
                f"  • <i>{strat.description}</i>\n"
            )

        lines.append("<b>Commands:</b>\n• <code>/strategy 1</code> - Safe Alpha Sniper\n• <code>/strategy 2</code> - Missed Runner Momentum\n• <code>/strategy 3</code> - Whale & KOL Copy\n• <code>/strategy 4</code> - Capital Preservation\n• <code>/autoswitch on|off</code> - Toggle Auto-Failover")
        return "\n".join(lines)
