import asyncio
from typing import Dict, Optional
from enum import Enum
from dataclasses import dataclass

class WalletTier(Enum):
    PRIORITY_A = "A"
    PRIORITY_B = "B"
    PRIORITY_C = "C"

@dataclass
class SizingRules:
    min_size_sol: float
    max_size_sol: float
    confidence_multiplier: float

class PositionSizer:
    def __init__(self):
        self._tier_rules: Dict[WalletTier, SizingRules] = {
            WalletTier.PRIORITY_A: SizingRules(min_size_sol=0.5, max_size_sol=5.0, confidence_multiplier=1.0),
            WalletTier.PRIORITY_B: SizingRules(min_size_sol=0.1, max_size_sol=1.0, confidence_multiplier=0.8),
            WalletTier.PRIORITY_C: SizingRules(min_size_sol=0.01, max_size_sol=0.1, confidence_multiplier=0.5),
        }

    async def calculate_size(self, confidence_score: float, tier: WalletTier) -> float:
        """
        Calculates position size based on confidence (0.0 to 1.0) and wallet tier.
        """
        rules = self._tier_rules.get(tier)
        if not rules:
            return 0.0

        # Dynamic size calculation: base_size scaled by confidence and tier multiplier
        raw_size = rules.max_size_sol * confidence_score * rules.confidence_multiplier
        
        # Clamp between min and max
        final_size = max(rules.min_size_sol, min(raw_size, rules.max_size_sol))
        
        return round(final_size, 4)

    async def get_sizing_report(self, confidence_score: float) -> Dict[str, float]:
        results = {}
        for tier in WalletTier:
            results[tier.value] = await self.calculate_size(confidence_score, tier)
        return results
