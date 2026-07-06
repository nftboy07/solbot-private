"""Lightweight heuristic inference used when no trained model is available."""

from typing import Dict


class InferenceEngine:
    """Heuristic scorer combining AI, creator, and liquidity signals."""

    def predict(self, features: Dict[str, float]) -> float:
        ai = float(features.get("ai_score", 0.0)) / 100.0
        creator = float(features.get("creator_score", 50.0)) / 100.0
        liquidity = min(1.0, float(features.get("liquidity_sol", 0.0)) / 50.0)
        return max(0.0, min(1.0, ai * 0.5 + creator * 0.3 + liquidity * 0.2))