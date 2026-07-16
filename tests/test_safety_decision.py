import unittest

from solbot.safety_decision import evaluate_safety_analysis


class SafetyDecisionTests(unittest.TestCase):
    def test_degraded_fallback_without_hard_flags_passes_high_threshold(self):
        decision = evaluate_safety_analysis(
            {
                "score": 80,
                "is_premine": False,
                "is_honeypot": False,
                "reason": "Safety scan fallback used (APIs rate-limited or unavailable).",
                "scan_status": "degraded",
                "is_fallback": True,
            },
            min_score=90,
        )

        self.assertTrue(decision.allowed)
        self.assertTrue(decision.degraded)
        self.assertEqual(decision.reason_code, "degraded_fallback")

    def test_legacy_fallback_reason_without_metadata_passes_high_threshold(self):
        decision = evaluate_safety_analysis(
            {
                "score": 80,
                "is_premine": False,
                "is_honeypot": False,
                "reason": "Safety scan fallback used (APIs rate-limited or unavailable).",
            },
            min_score=90,
        )

        self.assertTrue(decision.allowed)
        self.assertTrue(decision.degraded)
        self.assertEqual(decision.reason_code, "degraded_fallback")

    def test_honeypot_flag_rejects_even_when_scan_is_degraded(self):
        decision = evaluate_safety_analysis(
            {
                "score": 80,
                "is_premine": False,
                "is_honeypot": True,
                "reason": "Safety scan fallback used (APIs rate-limited or unavailable).",
                "scan_status": "degraded",
                "is_fallback": True,
            },
            min_score=90,
        )

        self.assertFalse(decision.allowed)
        self.assertTrue(decision.hard_risk)
        self.assertEqual(decision.reason_code, "honeypot")

    def test_real_low_score_rejects(self):
        decision = evaluate_safety_analysis(
            {
                "score": 74,
                "is_premine": False,
                "is_honeypot": False,
                "reason": "Weak holder distribution and poor creator history.",
                "scan_status": "ok",
                "is_fallback": False,
            },
            min_score=75,
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason_code, "low_score")


if __name__ == "__main__":
    unittest.main()
