from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "code"))

from compare_blind_results import compare_dataset_rows, exact_mcnemar_p  # noqa: E402


class CompareBlindResultsTests(unittest.TestCase):
    def test_exact_mcnemar_is_one_for_balanced_disagreement(self):
        self.assertEqual(exact_mcnemar_p(1, 1), 1.0)

    def test_compare_dataset_rows_uses_paired_ids(self):
        base = [
            {"id": "a", "source_dataset": "crt", "answer": ["yes"]},
            {"id": "b", "source_dataset": "crt", "answer": ["no"]},
        ]
        my_rows = [
            dict(base[0], final_value="yes", api_metrics={"total_tokens": 10}),
            dict(base[1], final_value="yes", api_metrics={"total_tokens": 10}),
        ]
        mact_rows = [
            dict(base[0], pred_answer="no", api_metrics={"total_tokens": 30}),
            dict(base[1], pred_answer="no", api_metrics={"total_tokens": 30}),
        ]

        result = compare_dataset_rows(my_rows, mact_rows)

        self.assertEqual(result["paired"]["myagent_only"], 1)
        self.assertEqual(result["paired"]["mact_only"], 1)
        self.assertEqual(result["myagent"]["primary_accuracy"], 0.5)
        self.assertEqual(result["mact"]["primary_accuracy"], 0.5)

    def test_compare_dataset_rows_reports_token_ratio_and_risk_deltas(self):
        base = [
            {"id": "1", "source_dataset": "crt", "answer": ["yes"], "risk_level": "light"},
            {"id": "2", "source_dataset": "crt", "answer": ["no"], "risk_level": "high"},
        ]
        my_rows = [
            dict(base[0], final_value="yes", api_metrics={"prompt_tokens": 60, "completion_tokens": 10}),
            dict(base[1], final_value="yes", api_metrics={"prompt_tokens": 70, "completion_tokens": 10}),
        ]
        mact_rows = [
            dict(base[0], pred_answer="yes", api_metrics={"prompt_tokens": 90, "completion_tokens": 10}),
            dict(base[1], pred_answer="no", api_metrics={"prompt_tokens": 90, "completion_tokens": 10}),
        ]

        result = compare_dataset_rows(my_rows, mact_rows)

        self.assertEqual(result["token_ratio_myagent_to_mact"], 0.75)
        self.assertTrue(result["token_ratio_at_most_0_75"])
        self.assertEqual(result["risk_accuracy_deltas"]["light"], 0.0)
        self.assertEqual(result["risk_accuracy_deltas"]["high"], -1.0)


if __name__ == "__main__":
    unittest.main()
