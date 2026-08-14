"""Dynamic Compute Unit (CU) and Priority Fee Optimizer for Solana."""

import logging
from dataclasses import dataclass
from typing import Dict, Optional

logger = logging.getLogger("bot.cu_optimizer")


@dataclass
class OptimizedComputeBudget:
    """Optimized compute unit limit and price for a transaction."""
    compute_unit_limit: int
    micro_lamports_per_cu: int
    estimated_fee_sol: float
    reason: str


class ComputeUnitOptimizer:
    """Calculates minimal necessary Compute Units and optimal priority fee pricing."""

    # Baseline required compute units per DEX action
    CU_PROFILES: Dict[str, int] = {
        "pump_fun_buy": 110_000,
        "pump_fun_sell": 95_000,
        "raydium_amm_swap": 140_000,
        "raydium_clmm_swap": 185_000,
        "meteora_dlmm_swap": 195_000,
        "orca_whirlpool_swap": 160_000,
        "jupiter_routed_swap": 280_000,
        "token_transfer": 45_000,
        "jito_tip": 30_000,
    }

    def __init__(self, default_cu_limit: int = 200_000, safety_margin_pct: float = 1.15):
        self.default_cu_limit = default_cu_limit
        self.safety_margin_pct = safety_margin_pct
        self._historical_cu_usage: Dict[str, list] = {}

    def get_optimal_budget(
        self,
        action_type: str,
        priority_fee_sol: float = 0.0001,
        simulated_units: Optional[int] = None,
    ) -> OptimizedComputeBudget:
        """
        Calculate optimal CU limit and micro-lamports price for an action.
        
        Args:
            action_type: One of the supported CU_PROFILES or 'custom'.
            priority_fee_sol: Total priority fee intended in SOL.
            simulated_units: Exact CU returned from simulateTransaction if available.
        """
        if simulated_units and simulated_units > 0:
            cu_limit = int(simulated_units * self.safety_margin_pct)
            reason = f"Simulated ({simulated_units} CU + {int((self.safety_margin_pct-1)*100)}% margin)"
        else:
            base_cu = self.CU_PROFILES.get(action_type, self.default_cu_limit)
            cu_limit = int(base_cu * self.safety_margin_pct)
            reason = f"Profile '{action_type}' ({base_cu} base CU)"

        # Cap CU between 40,000 and 1,400,000 (Solana max)
        cu_limit = max(40_000, min(1_400_000, cu_limit))

        # Convert priority_fee_sol to micro-lamports per CU:
        # total_lamports = priority_fee_sol * 1e9
        # micro_lamports_total = total_lamports * 1e6
        # micro_lamports_per_cu = micro_lamports_total / cu_limit
        total_micro_lamports = (priority_fee_sol * 1e9) * 1e6
        micro_lamports_per_cu = int(total_micro_lamports / max(cu_limit, 1))

        # Clamp micro_lamports_per_cu to a realistic minimum (10,000) and maximum (10,000,000)
        micro_lamports_per_cu = max(5_000, min(20_000_000, micro_lamports_per_cu))

        actual_fee_sol = (cu_limit * micro_lamports_per_cu) / (1e15)

        return OptimizedComputeBudget(
            compute_unit_limit=cu_limit,
            micro_lamports_per_cu=micro_lamports_per_cu,
            estimated_fee_sol=actual_fee_sol,
            reason=reason,
        )

    def record_actual_cu(self, action_type: str, actual_cu: int):
        """Record on-chain consumed CU to adaptively refine safety margins."""
        if actual_cu > 0:
            history = self._historical_cu_usage.setdefault(action_type, [])
            history.append(actual_cu)
            if len(history) > 50:
                history.pop(0)
