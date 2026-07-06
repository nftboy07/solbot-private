import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set
from enum import Enum

logger = logging.getLogger(__name__)

class CircuitBreakerReason(Enum):
    RPC_LATENCY = "rpc_latency"
    TX_FAILURES = "consecutive_tx_failures"
    DAILY_PNL = "daily_pnl_threshold"
    SYSTEM_OFFLINE = "system_component_offline"
    MANUAL_KILL = "manual_kill_switch"

@dataclass
class RiskState:
    kill_switch_active: bool = False
    active_positions: Dict[str, float] = field(default_factory=dict)  # token_address: size_sol
    daily_pnl_sol: float = 0.0
    consecutive_failures: int = 0
    last_rpc_latency_ms: float = 0.0
    system_status: Dict[str, datetime] = field(default_factory=dict) # component: last_seen
    
    # Track daily stats for reset
    last_reset_time: datetime = field(default_factory=datetime.utcnow)

class RiskManager:
    """
    Solbot V3 Risk Manager - Phase 1 / Risk & Safety Guardrails.
    Enforces strict institutional-grade limits and circuit breakers.
    """
    
    def __init__(self, bankroll_sol: float = 10.0):
        # Phase 1 Canary Limits
        self.MAX_POSITION_SOL = 1.0  # Increased for dynamic sizes
        self.MAX_CONCURRENT_POSITIONS = 100 # Increased as requested
        self.MAX_DAILY_LOSS_SOL = 5.0
        self.MAX_EXPOSURE_SOL = 10.0 # Increased exposure cap
        
        # Circuit Breaker Thresholds
        self.MAX_RPC_LATENCY_MS = 250
        self.MAX_CONSECUTIVE_FAILURES = 3
        self.MAX_DAILY_PNL_PCT_LOSS = 0.10  # 10% of bankroll
        self.OFFLINE_TIMEOUT_SECONDS = 30
        
        # Position Limits
        self.MAX_TRADE_PCT_BANKROLL = 0.20
        self.MAX_EXPOSURE_PCT_BANKROLL = 0.80 # Allow up to 80% exposure
        
        self.bankroll_sol = bankroll_sol
        self.state = RiskState()
        self._lock = asyncio.Lock()

    async def _check_daily_reset(self):
        """Resets daily stats if a new UTC day has started."""
        now = datetime.utcnow()
        if now.date() > self.state.last_reset_time.date():
            logger.info("New trading day detected. Resetting daily risk metrics.")
            self.state.daily_pnl_sol = 0.0
            self.state.last_reset_time = now

    async def kill(self):
        """Immediately activates the global emergency kill switch."""
        async with self._lock:
            self.state.kill_switch_active = True
            logger.critical("EMERGENCY KILL SWITCH ACTIVATED")
            # Logic for broadcasting to Telegram would be handled by the observer/emitter
            return True

    async def resume(self):
        """Resets the kill switch. Use with caution."""
        async with self._lock:
            self.state.kill_switch_active = False
            self.state.consecutive_failures = 0
            logger.info("Kill switch deactivated. Resuming operations.")

    async def update_rpc_latency(self, latency_ms: float):
        async with self._lock:
            self.state.last_rpc_latency_ms = latency_ms

    async def report_tx_result(self, success: bool):
        async with self._lock:
            if success:
                self.state.consecutive_failures = 0
            else:
                self.state.consecutive_failures += 1
                if self.state.consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                    logger.warning(f"Circuit breaker: {self.MAX_CONSECUTIVE_FAILURES} consecutive failures.")

    async def update_component_heartbeat(self, component_name: str):
        async with self._lock:
            self.state.system_status[component_name] = datetime.utcnow()

    async def update_pnl(self, pnl_sol: float):
        async with self._lock:
            await self._check_daily_reset()
            self.state.daily_pnl_sol += pnl_sol

    def calculate_position_size(
        self,
        confidence_score: float,
        wallet_balance: float,
        floor_sol: float = 0.0,
        max_trade_pct: float = 0.02,
    ) -> float:
        """
        Position size from filter confidence (0-100):
        - 90+ = 0.02 SOL
        - 80-89 = 0.01 SOL
        - 70-79 = 0.005 SOL
        - 50-69 = floor_sol (degen default buy)
        - Below 50 = skip unless floor_sol set
        Capped at 2% of wallet balance.
        """
        if confidence_score >= 90:
            base_size = 0.02
        elif confidence_score >= 80:
            base_size = 0.01
        elif confidence_score >= 70:
            base_size = 0.005
        elif confidence_score >= 50 and floor_sol > 0:
            base_size = floor_sol
        elif floor_sol > 0 and confidence_score > 0:
            base_size = floor_sol
        else:
            return 0.0

        if wallet_balance <= 0:
            return 0.0
        pct = max(0.01, min(max_trade_pct, 0.20))
        max_risk_sol = wallet_balance * pct
        return max(0.0, min(base_size, max_risk_sol))

    async def can_trade(self, token_address: str, size_sol: float, wallet_balance: Optional[float] = None) -> tuple[bool, str]:
        """
        Validates if a new trade can be entered based on all Phase 1 guardrails.
        Returns (is_allowed, reason).
        """
        async with self._lock:
            await self._check_daily_reset()

            # 1. Kill Switch
            if self.state.kill_switch_active:
                return False, "Kill switch is active"

            # 2. RPC Latency Circuit Breaker
            if self.state.last_rpc_latency_ms > self.MAX_RPC_LATENCY_MS:
                return False, f"RPC latency too high: {self.state.last_rpc_latency_ms}ms"

            # 3. Consecutive Failures Circuit Breaker
            if self.state.consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                return False, f"Too many consecutive failures: {self.state.consecutive_failures}"

            # 4. Daily PnL Circuit Breaker
            pnl_loss_threshold = self.bankroll_sol * self.MAX_DAILY_PNL_PCT_LOSS
            if self.state.daily_pnl_sol <= -pnl_loss_threshold or self.state.daily_pnl_sol <= -self.MAX_DAILY_LOSS_SOL:
                return False, f"Daily PnL limit reached: {self.state.daily_pnl_sol} SOL"

            # 5. Component Heartbeat Circuit Breaker
            now = datetime.utcnow()
            for comp, last_seen in self.state.system_status.items():
                if (now - last_seen).total_seconds() > self.OFFLINE_TIMEOUT_SECONDS:
                    return False, f"System component offline: {comp}"

            # 6. Canary Limits: Max Concurrent Positions
            if len(self.state.active_positions) >= self.MAX_CONCURRENT_POSITIONS:
                return False, f"Max concurrent positions reached: {self.MAX_CONCURRENT_POSITIONS}"

            # 7. Canary Limits: Max Position Size
            if size_sol > self.MAX_POSITION_SOL:
                return False, f"Position size {size_sol} exceeds canary limit {self.MAX_POSITION_SOL}"

            # 8. Single trade size exceeds 2% of wallet balance
            if wallet_balance is not None:
                max_risk_sol = wallet_balance * 0.02
                if size_sol > max_risk_sol + 1e-6:
                    return False, f"Position size {size_sol} exceeds 2% of wallet balance ({max_risk_sol:.6f} SOL)"

            # 9. Canary Limits: Max Total Exposure
            current_exposure = sum(self.state.active_positions.values())
            if (current_exposure + size_sol) > self.MAX_EXPOSURE_SOL:
                return False, f"Total exposure would exceed {self.MAX_EXPOSURE_SOL} SOL"

            # 10. Total Exposure % of Bankroll
            if (current_exposure + size_sol) > (self.bankroll_sol * self.MAX_EXPOSURE_PCT_BANKROLL):
                return False, "Total exposure exceeds 80% of bankroll"

            return True, "Passed all risk checks"

    async def on_position_opened(self, token_address: str, size_sol: float):
        async with self._lock:
            self.state.active_positions[token_address] = size_sol
            logger.info(f"Position tracked: {token_address} with {size_sol} SOL")

    async def on_position_closed(self, token_address: str, pnl_sol: float):
        async with self._lock:
            if token_address in self.state.active_positions:
                del self.state.active_positions[token_address]
            await self._check_daily_reset()
            self.state.daily_pnl_sol += pnl_sol
            logger.info(f"Position closed: {token_address}. PnL: {pnl_sol} SOL. Daily PnL: {self.state.daily_pnl_sol} SOL")

    def disable_averaging_down(self):
        """Reference instruction: Disable automatic averaging down."""
        # This is a policy flag for the execution engine
        return True
