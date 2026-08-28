from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "code"))

from calibrate_risk_policy import choose_policy  # noqa: E402


class CalibrateRiskPolicyTests(unittest.TestCase):
    def test_choose_policy_prefers_accuracy_under_token_limit(self):
        rows = [
            {
                "risk": 0.2,
                "my_correct": True,
                "mact_tokens": 1000,
                "light_tokens": 200,
                "medium_tokens": 500,
                "high_tokens": 900,
            },
            {
                "risk": 0.6,
                "my_correct": False,
                "mact_tokens": 1000,
                "light_tokens": 200,
                "medium_tokens": 500,
                "high_tokens": 900,
            },
            {
                "risk": 0.8,
                "my_correct": True,
                "mact_tokens": 1000,
                "light_tokens": 200,
                "medium_tokens": 500,
                "high_tokens": 900,
            },
        ]

        result = choose_policy(rows)

        self.assertLessEqual(result["avg_token_ratio"], 0.75)
        self.assertIn(result["light_threshold"], [0.20, 0.25, 0.30])
        self.assertIn(result["high_threshold"], [0.50, 0.55, 0.60])

    def test_choose_policy_does_not_return_gold_or_ids(self):
        rows = [
            {
                "id": "known",
                "gold": "secret",
                "risk": 0.9,
                "my_correct": True,
                "mact_tokens": 1000,
                "light_tokens": 200,
                "medium_tokens": 500,
                "high_tokens": 900,
            }
        ]

        result = choose_policy(rows)

        self.assertNotIn("gold", str(result).lower())
        self.assertNotIn("known", str(result).lower())


if __name__ == "__main__":
    unittest.main()
