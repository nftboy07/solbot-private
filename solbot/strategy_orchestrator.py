"""
Strategy Orchestrator for SolBot V3.
Manages 4 distinct trading strategies, tracks live performance per strategy,
and provides automatic failover switching and Telegram-controlled manual switching.
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
    buy_amount_sol: float = 0.02
    max_positions: int = 3
    ai_min_score: int = 75
    min_mcap_usd: float = 10_000.0
    max_mcap_usd: float = 350_000.0
    max_dev_holding_pct: float = 0.03
    max_top10_holding_pct: float = 0.07
    min_buy_ratio: float = 0.65
    min_unique_buyers: int = 15
    tp1_mult: float = 1.40
    tp1_pct: float = 0.35
    tp2_mult: float = 2.00
    tp2_pct: float = 0.30
    tp3_mult: float = 3.50
    tp3_pct: float = 0.20
    moonbag_pct: float = 0.15
    stop_loss_pct: float = 0.10
    trailing_stop_pct: float = 0.20
    break_even_trigger: float = 1.20
    break_even_floor: float = 1.02
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
            description="Strict AI Sentiment >= 75, Anti-Rug Dev Genome, dev holding <= 3%, 4-tier moonbag.",
            buy_amount_sol=0.02,
            max_positions=3,
            ai_min_score=75,
            min_mcap_usd=12_000.0,
            max_mcap_usd=300_000.0,
            max_dev_holding_pct=0.03,
            max_top10_holding_pct=0.07,
            min_buy_ratio=0.65,
            min_unique_buyers=15,
            tp1_mult=1.40,
            tp1_pct=0.35,
            tp2_mult=2.00,
            tp2_pct=0.30,
            tp3_mult=3.50,
            tp3_pct=0.20,
            moonbag_pct=0.15,
            stop_loss_pct=0.10,
            break_even_trigger=1.20,
        ),
        "runner_momentum": StrategyProfile(
            name="runner_momentum",
            display_name="⚡ Missed Runner Momentum",
            description="Matches 5x-7500x runner signatures, volume acceleration, buy ratio >= 70%.",
            buy_amount_sol=0.02,
            max_positions=2,
            ai_min_score=50,
            min_mcap_usd=4_000.0,
            max_mcap_usd=880_000.0,
            max_dev_holding_pct=0.04,
            max_top10_holding_pct=0.10,
            min_buy_ratio=0.70,
            min_unique_buyers=25,
            tp1_mult=1.50,
            tp1_pct=0.40,
            tp2_mult=2.50,
            tp2_pct=0.30,
            tp3_mult=5.00,
            tp3_pct=0.20,
            moonbag_pct=0.10,
            stop_loss_pct=0.12,
            break_even_trigger=1.25,
            requires_runner_pattern=True,
        ),
        "kol_whale_copy": StrategyProfile(
            name="kol_whale_copy",
            display_name="🐋 Whale & KOL Copy",
            description="Follows 3,717 smart whales & KOL buys. Requires >=2 smart wallet confirmations.",
            buy_amount_sol=0.025,
            max_positions=2,
            ai_min_score=55,
            min_mcap_usd=8_000.0,
            max_mcap_usd=500_000.0,
            max_dev_holding_pct=0.05,
            max_top10_holding_pct=0.12,
            min_buy_ratio=0.60,
            min_unique_buyers=10,
            tp1_mult=2.00,
            tp1_pct=0.50,
            tp2_mult=4.00,
            tp2_pct=0.30,
            tp3_mult=8.00,
            tp3_pct=0.10,
            moonbag_pct=0.10,
            stop_loss_pct=0.15,
            break_even_trigger=1.30,
            requires_whale_overlap=True,
        ),
        "conservative_rebalancer": StrategyProfile(
            name="conservative_rebalancer",
            display_name="🛡️ Capital Preservation",
            description="High safety floor ($30k+ MCAP), 0% dev holding, tight 8% stop loss, fast scalps.",
            buy_amount_sol=0.015,
            max_positions=2,
            ai_min_score=70,
            min_mcap_usd=30_000.0,
            max_mcap_usd=600_000.0,
            max_dev_holding_pct=0.01,
            max_top10_holding_pct=0.05,
            min_buy_ratio=0.70,
            min_unique_buyers=20,
            tp1_mult=1.25,
            tp1_pct=0.60,
            tp2_mult=1.60,
            tp2_pct=0.40,
            tp3_mult=2.50,
            tp3_pct=0.0,
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

    def __init__(self, initial_strategy: str = "alpha_sniper", auto_switch: bool = True):
        self.active_strategy_name = initial_strategy if initial_strategy in self.STRATEGIES else "alpha_sniper"
        self.auto_switch_enabled = auto_switch
        self.switch_history: List[Dict[str, Any]] = []
        self._on_switch_callback = None
        logger.info(f"Initialized StrategyOrchestrator with active: {self.active_strategy_name}, auto-switch: {self.auto_switch_enabled}")

    @property
    def current(self) -> StrategyProfile:
        return self.STRATEGIES[self.active_strategy_name]

    def set_callback(self, callback):
        self._on_switch_callback = callback

    def switch_strategy(self, strategy_name: str, reason: str = "Manual TG Command") -> Tuple[bool, str]:
        """Manually or programmatically switch active strategy."""
        normalized = strategy_name.lower().strip()
        # Aliases
        alias_map = {
            "1": "alpha_sniper", "alpha": "alpha_sniper", "sniper": "alpha_sniper",
            "2": "runner_momentum", "runner": "runner_momentum", "momentum": "runner_momentum",
            "3": "kol_whale_copy", "whale": "kol_whale_copy", "copy": "kol_whale_copy", "kol": "kol_whale_copy",
            "4": "conservative_rebalancer", "conservative": "conservative_rebalancer", "safe": "conservative_rebalancer", "scalp": "conservative_rebalancer",
        }
        target = alias_map.get(normalized, normalized)

        if target not in self.STRATEGIES:
            return False, f"Unknown strategy '{strategy_name}'. Available: alpha, momentum, copy, conservative"

        if target == self.active_strategy_name:
            return True, f"Strategy is already set to <b>{self.current.display_name}</b>"

        old_strat = self.active_strategy_name
        self.active_strategy_name = target
        self.switch_history.append({
            "timestamp": time.time(),
            "from": old_strat,
            "to": target,
            "reason": reason
        })
        logger.info(f"Switched strategy: {old_strat} -> {target} (Reason: {reason})")
        
        if self._on_switch_callback:
            try:
                self._on_switch_callback(old_strat, target, reason)
            except Exception as e:
                logger.error(f"Error in strategy switch callback: {e}")

        return True, f"✅ Switched active strategy to <b>{self.current.display_name}</b>\n<i>Reason: {reason}</i>"

    def record_trade_result(self, pnl_sol: float, roi_pct: float) -> Optional[str]:
        """
        Record a closed trade PnL and check if auto-failover should trigger.
        Returns notification message string if auto-switched, else None.
        """
        curr = self.current
        curr.total_trades += 1
        curr.total_pnl_sol += pnl_sol
        curr.recent_pnl.append(pnl_sol)
        if len(curr.recent_pnl) > 5:
            curr.recent_pnl.pop(0)

        if pnl_sol > 0 or roi_pct > 0:
            curr.wins += 1
            curr.consecutive_losses = 0
            logger.info(f"Strategy {curr.name} WIN: +{pnl_sol:.4f} SOL (+{roi_pct:.1f}%)")
        else:
            curr.losses += 1
            curr.consecutive_losses += 1
            logger.warning(f"Strategy {curr.name} LOSS: {pnl_sol:.4f} SOL ({roi_pct:.1f}%) [Consecutive Losses: {curr.consecutive_losses}]")

        # Auto-Switch Failover Trigger: 3 consecutive losses OR 5-trade net loss > 0.04 SOL
        if self.auto_switch_enabled:
            recent_net = sum(curr.recent_pnl)
            should_switch = False
            switch_reason = ""

            if curr.consecutive_losses >= 3:
                should_switch = True
                switch_reason = f"3 consecutive losses in {curr.display_name}"
            elif len(curr.recent_pnl) >= 4 and recent_net < -0.04:
                should_switch = True
                switch_reason = f"Drawdown threshold hit ({recent_net:.4f} SOL over last {len(curr.recent_pnl)} trades)"

            if should_switch:
                curr_idx = self.ROTATION_ORDER.index(self.active_strategy_name)
                next_strat = self.ROTATION_ORDER[(curr_idx + 1) % len(self.ROTATION_ORDER)]
                _, msg = self.switch_strategy(next_strat, reason=f"Auto-Failover: {switch_reason}")
                return (
                    f"⚠️ <b>STRATEGY AUTO-FAILOVER TRIGGERED</b> ⚠️\n\n"
                    f"• <b>Previous:</b> <code>{curr.display_name}</code>\n"
                    f"• <b>Trigger:</b> <i>{switch_reason}</i>\n"
                    f"• <b>New Active Strategy:</b> <b>{self.current.display_name}</b>\n"
                    f"• <b>Parameters:</b> Size={self.current.buy_amount_sol} SOL | SL={self.current.stop_loss_pct*100:.0f}%"
                )

        return None

    def get_dashboard_text(self) -> str:
        """Render comprehensive Telegram status dashboard for all 4 strategies."""
        lines = [
            "🧠 <b>MULTI-STRATEGY CONTROL & FAILOVER ENGINE</b> 🧠\n",
            f"• <b>Active Strategy:</b> <b>{self.current.display_name}</b>",
            f"• <b>Auto-Failover:</b> <code>{'🟢 ENABLED' if self.auto_switch_enabled else '🔴 DISABLED'}</code>",
            f"• <b>Active Sizing:</b> <code>{self.current.buy_amount_sol} SOL</code> (Max {self.current.max_positions} bags)\n",
            "<b>Strategy Scoreboard:</b>"
        ]

        for name in self.ROTATION_ORDER:
            s = self.STRATEGIES[name]
            is_active = (name == self.active_strategy_name)
            marker = "👉 " if is_active else "• "
            win_rate = (s.wins / s.total_trades * 100) if s.total_trades > 0 else 0.0
            lines.append(
                f"{marker}<b>{s.display_name}</b> {'[ACTIVE]' if is_active else ''}\n"
                f"  Win Rate: <code>{win_rate:.1f}%</code> ({s.wins}W / {s.losses}L) | PnL: <code>{s.total_pnl_sol:+.4f} SOL</code>\n"
                f"  <i>{s.description}</i>"
            )

        lines.extend([
            "\n<b>Telegram Controls:</b>",
            "• <code>/strategy alpha</code> (1) — Safe AI Alpha",
            "• <code>/strategy runner</code> (2) — Momentum Breakout",
            "• <code>/strategy whale</code> (3) — Whale Copy",
            "• <code>/strategy safe</code> (4) — Capital Preservation",
            "• <code>/autoswitch on|off</code> — Toggle Auto Failover",
            "• <code>/setsize 0.02</code> — Set Buy Amount",
            "• <code>/setmaxbags 3</code> — Set Max Bags",
            "• <code>/panic</code> — Emergency Sell All Bags",
        ])

        return "\n".join(lines)
