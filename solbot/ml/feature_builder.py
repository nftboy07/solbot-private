from typing import Any, Dict

class FeatureBuilder:
    """Prepares feature vectors for ML inference."""
    
    @staticmethod
    async def build_features(data: Dict[str, Any]) -> List[float]:
        """
        Builds feature vector including:
        - creator_score
        - buy_velocity
        - volume_acceleration
        """
        features = [
            data.get("creator_score", 0.0),
            data.get("buy_velocity", 0.0),
            data.get("volume_acceleration", 0.0)
        ]
        return features
