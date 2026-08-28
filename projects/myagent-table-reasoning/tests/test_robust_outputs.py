import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "code"))

from robust_outputs import apply_fallback_answer, classify_error, fallback_answer_for_row  # noqa: E402


class RobustOutputsTests(unittest.TestCase):
    def test_fallback_answers_are_non_empty_and_do_not_use_gold(self):
        self.assertEqual(
            fallback_answer_for_row(
                {
                    "source_dataset": "tabfact",
                    "statement": "the highest attendance is 100.",
                    "answer": "true",
                }
            ),
            "false",
        )
        self.assertEqual(
            fallback_answer_for_row(
                {
                    "source_dataset": "crt",
                    "question": "is the proportion greater than one half?",
                    "answer": "Yes",
                }
            ),
            "No",
        )
        self.assertEqual(
            fallback_answer_for_row(
                {
                    "source_dataset": "wtq",
                    "question": "which country is listed first?",
                    "answer": ["Italy"],
                }
            ),
            "unknown",
        )

    def test_error_classification_marks_context_overflow(self):
        self.assertEqual(
            classify_error("BadRequestError: maximum context length exceeded"),
            "context_overflow",
        )

    def test_apply_fallback_answer_adds_diagnostic_fields(self):
        row = apply_fallback_answer(
            {
                "id": "r1",
                "source_dataset": "wtq",
                "question": "how many rows are there?",
            },
            error_message="NameError: missing",
            retry_count=2,
        )

        self.assertEqual(row["pred_answer"], "0")
        self.assertTrue(row["fallback_used"])
        self.assertEqual(row["retry_count"], 2)
        self.assertEqual(row["error_type"], "execution_error")
        self.assertTrue(row["execution_error"])


if __name__ == "__main__":
    unittest.main()
