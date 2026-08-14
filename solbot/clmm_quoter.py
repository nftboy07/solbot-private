"""Concentrated Liquidity (CLMM & DLMM) Math & Bin Quoting Engine."""

import math
from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass
class LiquidityRangeProposal:
    """Calculated concentrated liquidity bin ranges for market making."""
    mid_price: float
    lower_price: float
    upper_price: float
    bin_step_bps: int
    num_bins: int
    bin_allocations: List[Dict[str, float]]


class ConcentratedLiquidityQuoter:
    """Calculates optimal price bounds and bin distribution for Solana CLMM/DLMMs."""

    def calculate_range_proposal(
        self,
        mid_price: float,
        range_width_pct: float = 0.10,
        bin_step_bps: int = 25,
        total_deposit_sol: float = 0.50,
        curve_type: str = "gaussian",
    ) -> LiquidityRangeProposal:
        """
        Calculate symmetric concentrated liquidity range around current price.
        
        Args:
            mid_price: Current market pool price.
            range_width_pct: Width of range (+/- 10%).
            bin_step_bps: Width of each bin in basis points.
            total_deposit_sol: Total capital in SOL allocated.
            curve_type: 'uniform' or 'gaussian' weight distribution.
        """
        if mid_price <= 0:
            return LiquidityRangeProposal(0.0, 0.0, 0.0, bin_step_bps, 0, [])

        lower_price = mid_price * (1.0 - range_width_pct)
        upper_price = mid_price * (1.0 + range_width_pct)

        step_pct = bin_step_bps / 10000.0
        num_bins = max(5, int((range_width_pct * 2) / max(step_pct, 0.001)))
        # Force odd number of bins so middle bin sits exactly at mid_price
        if num_bins % 2 == 0:
            num_bins += 1

        allocations = []
        center_idx = num_bins // 2
        weights = []

        for i in range(num_bins):
            dist = i - center_idx
            if curve_type == "gaussian":
                w = math.exp(-0.5 * (dist / 2.0) ** 2)
            else:
                w = 1.0
            weights.append(w)

        total_weight = sum(weights)
        for i in range(num_bins):
            bin_price = mid_price * (1.0 + (i - center_idx) * step_pct)
            allocated_sol = (weights[i] / total_weight) * total_deposit_sol
            allocations.append({
                "bin_index": i,
                "price": round(bin_price, 6),
                "sol_amount": round(allocated_sol, 4),
            })

        return LiquidityRangeProposal(
            mid_price=mid_price,
            lower_price=round(lower_price, 6),
            upper_price=round(upper_price, 6),
            bin_step_bps=bin_step_bps,
            num_bins=num_bins,
            bin_allocations=allocations,
        )
