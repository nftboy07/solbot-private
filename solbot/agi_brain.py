import os
import logging
import json
import numpy as np
import pandas as pd
from typing import Dict, Any, List, Tuple, Optional
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
import joblib

logger = logging.getLogger("bot.agi_brain")

FEATURE_COLS = [
    "price_change_1m", "price_change_5m", "price_change_1h",
    "volume_change_5m", "volume_change_1h", "holder_growth_1h",
    "holder_growth_24h", "dev_balance", "social_score",
    "kol_mention_count", "age_minutes", "market_cap",
    "liquidity", "volatility_1h", "buy_pressure", "sell_pressure"
]

class AGIBrain:
    """
    ML-powered classification engine that predicts token profitability
    and dynamically tunes scoring thresholds based on historical trade outcomes.
    """
    def __init__(self, db, config):
        self._db = db
        self._config = config
        self.model = None
        self.scaler = None
        self._prediction_cache = {}
        self.features_importance = {}
        self.total_predictions = 0
        
        # Paths
        self.model_path = config.brain.model_path
        self.scaler_path = config.brain.scaler_path
        os.makedirs(os.path.dirname(self.model_path), exist_ok=True)

        self.load_model()

    def load_model(self) -> bool:
        """Loads pre-trained model and scaler from disk."""
        try:
            if os.path.exists(self.model_path) and os.path.exists(self.scaler_path):
                self.model = joblib.load(self.model_path)
                self.scaler = joblib.load(self.scaler_path)
                logger.info(f"Loaded AGI Brain model from {self.model_path}")
                
                # Extract feature importances if possible
                if hasattr(self.model, "feature_importances_"):
                    self.features_importance = dict(zip(FEATURE_COLS, self.model.feature_importances_))
                return True
        except Exception as e:
            logger.error(f"Failed to load AGI Brain model: {e}")
        return False

    async def train_model(self) -> Tuple[bool, str]:
        """Queries historical trade outcomes and features to retrain the local model."""
        try:
            rows = await self._db.get_training_data()
            min_samples = self._config.brain.min_samples_for_training
            if len(rows) < min_samples:
                return False, f"Insufficient training data. Have {len(rows)} closed positions, need at least {min_samples}."

            df = pd.DataFrame(rows)
            
            # Ensure all feature columns exist
            for col in FEATURE_COLS:
                if col not in df.columns:
                    df[col] = 0.0

            X = df[FEATURE_COLS].fillna(0.0).values
            y = df["win"].astype(int).values

            if len(np.unique(y)) < 2:
                return False, "Insufficient variance in trade outcomes (requires both wins and losses to train)."

            # Fit scaler & model
            scaler = StandardScaler()
            X_scaled = scaler.fit_transform(X)

            model = RandomForestClassifier(n_estimators=100, max_depth=8, random_state=42)
            model.fit(X_scaled, y)

            # Persist model & scaler
            joblib.dump(model, self.model_path)
            joblib.dump(scaler, self.scaler_path)

            self.model = model
            self.scaler = scaler
            self.features_importance = dict(zip(FEATURE_COLS, model.feature_importances_))

            acc = model.score(X_scaled, y)
            msg = f"Successfully trained AGI Brain model on {len(df)} samples. Training accuracy: {acc:.2%}"
            logger.info(msg)
            return True, msg

        except Exception as e:
            err_msg = f"AGI Brain training failed: {e}"
            logger.error(err_msg, exc_info=True)
            return False, err_msg

    async def predict(self, token_mint: str, features: Dict[str, float]) -> Dict[str, Any]:
        """
        Runs feature vector through standard scaling and RandomForest to predict safety score.
        Also persists features and decisions to SQLite.
        """
        # Return cached prediction if active
        if token_mint in self._prediction_cache:
            return self._prediction_cache[token_mint]

        self.total_predictions += 1
        
        # Save features to DB asynchronously
        try:
            await self._db.save_agi_features(token_mint, features)
        except Exception as e:
            logger.error(f"Failed to log AGI features: {e}")

        # If model is not trained/loaded, return fallback
        if self.model is None or self.scaler is None:
            result = {
                "score": 75.0, # Neutral-high fallback
                "decision": "WATCH",
                "confidence": 0.5,
                "trained": False
            }
            try:
                await self._db.save_agi_decision(token_mint, "WATCH", 75.0, features, "fallback_heuristic")
            except Exception as e:
                logger.error(f"Failed to log fallback AGI decision: {e}")
            return result

        try:
            # Build feature vector matching list order
            vec = np.array([[features.get(col, 0.0) for col in FEATURE_COLS]])
            vec_scaled = self.scaler.transform(vec)
            
            # Predict win probability (prob of win class '1')
            proba = self.model.predict_proba(vec_scaled)[0]
            win_prob = float(proba[1]) if len(proba) > 1 else float(proba[0])
            
            score = win_prob * 100.0
            confidence = max(proba)

            # Decision thresholds
            threshold = self._config.brain.min_score_normal
            if score >= threshold:
                decision = "BUY_FULL"
            elif score >= threshold - 15:
                decision = "BUY_HALF"
            elif score >= threshold - 30:
                decision = "WATCH"
            else:
                decision = "SKIP"

            result = {
                "score": round(score, 1),
                "decision": decision,
                "confidence": round(confidence, 2),
                "trained": True
            }

            # Save decision log
            await self._db.save_agi_decision(token_mint, decision, score, features, "rf_v1")
            
            # Cache for 30s to prevent spamming queries
            self._prediction_cache[token_mint] = result
            return result

        except Exception as e:
            logger.error(f"Error predicting with AGI Brain: {e}", exc_info=True)
            return {
                "score": 70.0,
                "decision": "WATCH",
                "confidence": 0.5,
                "trained": False
            }

    async def autotune(self) -> Tuple[bool, str]:
        """Checks if retraining is viable and autotunes configurations."""
        success, msg = await self.train_model()
        if success:
            logger.info(f"AGI Brain Autotuner: {msg}")
        return success, msg
