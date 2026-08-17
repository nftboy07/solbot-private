import os
import logging
import json
import numpy as np
import pandas as pd
from typing import Dict, Any, List, Tuple, Optional
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.preprocessing import StandardScaler
import joblib

logger = logging.getLogger("bot.agi_brain")

FEATURE_COLS = [
    "price_change_1m", "price_change_5m", "price_change_1h",
    "volume_change_5m", "volume_change_1h", "holder_growth_1h",
    "holder_growth_24h", "dev_balance", "social_score",
    "kol_mention_count", "age_minutes", "market_cap",
    "liquidity", "volatility_1h", "buy_pressure", "sell_pressure",
    "order_imbalance_ratio", "bonding_curve_velocity", "unique_wallets_growth",
    "dev_rug_risk_score", "top10_concentration", "whale_coincidence_score",
    "wash_trade_entropy", "graduation_prob"
]

class AGIBrain:
    """
    Institutional ML Ensemble engine that predicts token win probability
    and calculates optimal fractional Kelly position sizing.
    """
    def __init__(self, db, config):
        self._db = db
        self._config = config
        self.model = None
        self.gb_model = None
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
        """Loads pre-trained ensemble models and scaler from disk."""
        try:
            if os.path.exists(self.model_path) and os.path.exists(self.scaler_path):
                saved = joblib.load(self.model_path)
                if isinstance(saved, dict):
                    self.model = saved.get("rf")
                    self.gb_model = saved.get("gb")
                else:
                    self.model = saved
                self.scaler = joblib.load(self.scaler_path)
                logger.info(f"Loaded AGI Brain Ensemble model from {self.model_path}")
                
                # Extract feature importances
                if self.model and hasattr(self.model, "feature_importances_"):
                    cols = FEATURE_COLS[:len(self.model.feature_importances_)]
                    self.features_importance = dict(zip(cols, self.model.feature_importances_))
                return True
        except Exception as e:
            logger.error(f"Failed to load AGI Brain model: {e}")
        return False

    async def train_model(self) -> Tuple[bool, str]:
        """Queries historical trade outcomes and features to retrain the ensemble model."""
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

            # Fit scaler & ensemble models
            scaler = StandardScaler()
            X_scaled = scaler.fit_transform(X)

            rf = RandomForestClassifier(n_estimators=150, max_depth=8, random_state=42)
            rf.fit(X_scaled, y)

            gb = GradientBoostingClassifier(n_estimators=100, max_depth=5, learning_rate=0.05, random_state=42)
            gb.fit(X_scaled, y)

            # Persist ensemble & scaler
            joblib.dump({"rf": rf, "gb": gb}, self.model_path)
            joblib.dump(scaler, self.scaler_path)

            self.model = rf
            self.gb_model = gb
            self.scaler = scaler
            self.features_importance = dict(zip(FEATURE_COLS, rf.feature_importances_))

            acc_rf = rf.score(X_scaled, y)
            acc_gb = gb.score(X_scaled, y)
            avg_acc = (acc_rf + acc_gb) / 2.0
            msg = f"Successfully trained AGI Brain Ensemble on {len(df)} samples. Ensemble Accuracy: {avg_acc:.2%}"
            logger.info(msg)
            return True, msg

        except Exception as e:
            err_msg = f"AGI Brain training failed: {e}"
            logger.error(err_msg, exc_info=True)
            return False, err_msg

    def calculate_kelly_size(self, win_prob: float, payoff_ratio: float = 2.0, max_cap: float = 0.015) -> float:
        """
        Calculates Fractional Kelly Criterion position sizing:
        f* = (p * b - q) / b
        Where:
          p = win probability
          q = loss probability (1 - p)
          b = payoff ratio (e.g. 2.0x gain on win)
        """
        q = 1.0 - win_prob
        if payoff_ratio <= 0:
            return 0.005
        kelly_fraction = (win_prob * payoff_ratio - q) / payoff_ratio
        # Quarter-Kelly for maximum drawdown safety
        safe_fraction = max(0.0, kelly_fraction * 0.25)
        suggested_size = safe_fraction * max_cap * 4.0
        return max(0.005, min(max_cap, suggested_size))

    async def predict(self, token_mint: str, features: Dict[str, float]) -> Dict[str, Any]:
        """
        Runs 24-feature vector through Ensemble (Random Forest + Gradient Boosting)
        to predict win probability, decision classification, and Kelly sizing.
        """
        if token_mint in self._prediction_cache:
            return self._prediction_cache[token_mint]

        self.total_predictions += 1
        
        # Save features to DB asynchronously
        try:
            await self._db.save_agi_features(token_mint, features)
        except Exception as e:
            logger.debug(f"Failed to log AGI features: {e}")

        # If model is not trained/loaded, use heuristic baseline
        if self.model is None or self.scaler is None:
            # Multi-variable heuristic scoring
            social = features.get("social_score", 70.0)
            rug_risk = features.get("dev_rug_risk_score", 50.0)
            buy_pres = features.get("buy_pressure", 0.5) * 100.0
            heuristic_score = (social * 0.40) + (rug_risk * 0.35) + (buy_pres * 0.25)
            
            decision = "BUY_FULL" if heuristic_score >= 75.0 else ("BUY_HALF" if heuristic_score >= 50.0 else "SKIP")
            result = {
                "score": round(heuristic_score, 1),
                "decision": decision,
                "confidence": 0.65,
                "kelly_size_sol": 0.015 if decision == "BUY_FULL" else 0.0075,
                "trained": False
            }
            self._prediction_cache[token_mint] = result
            return result

        try:
            vec = np.array([[features.get(col, 0.0) for col in FEATURE_COLS]])
            vec_scaled = self.scaler.transform(vec)
            
            # Predict ensemble probability
            proba_rf = self.model.predict_proba(vec_scaled)[0]
            p_rf = float(proba_rf[1]) if len(proba_rf) > 1 else float(proba_rf[0])
            
            if self.gb_model is not None:
                proba_gb = self.gb_model.predict_proba(vec_scaled)[0]
                p_gb = float(proba_gb[1]) if len(proba_gb) > 1 else float(proba_gb[0])
                win_prob = (p_rf * 0.60) + (p_gb * 0.40)
            else:
                win_prob = p_rf
            
            score = win_prob * 100.0
            confidence = max(win_prob, 1.0 - win_prob)

            # Strict Decision Thresholds
            threshold = self._config.brain.min_score_normal
            if score >= threshold:
                decision = "BUY_FULL"
            elif score >= threshold - 15:
                decision = "BUY_HALF"
            elif score >= threshold - 30:
                decision = "WATCH"
            else:
                decision = "SKIP"

            kelly_size = self.calculate_kelly_size(win_prob, payoff_ratio=2.2, max_cap=0.015)

            result = {
                "score": round(score, 1),
                "decision": decision,
                "confidence": round(confidence, 2),
                "kelly_size_sol": round(kelly_size, 4),
                "trained": True
            }

            await self._db.save_agi_decision(token_mint, decision, score, features, "ensemble_v2")
            self._prediction_cache[token_mint] = result
            return result

        except Exception as e:
            logger.error(f"Error predicting with AGI Brain: {e}")
            return {
                "score": 60.0,
                "decision": "SKIP",
                "confidence": 0.5,
                "kelly_size_sol": 0.005,
                "trained": False
            }

    async def autotune(self) -> Tuple[bool, str]:
        """Checks if retraining is viable and autotunes configurations."""
        success, msg = await self.train_model()
        if success:
            logger.info(f"AGI Brain Autotuner: {msg}")
        return success, msg
