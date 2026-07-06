from solbot.ml.inference import InferenceEngine


def test_inference_prefers_high_ai_and_creator():
    engine = InferenceEngine()
    high = engine.predict({"ai_score": 90, "creator_score": 80, "liquidity_sol": 30})
    low = engine.predict({"ai_score": 20, "creator_score": 30, "liquidity_sol": 1})
    assert high > low