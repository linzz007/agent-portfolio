from pathlib import Path
import sys
import unittest

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "code"))

from dataset_profiles import infer_dataset_hints  # noqa: E402


class DatasetProfileTests(unittest.TestCase):
    def test_wtq_plural_which_question_requests_list(self):
        df = pd.DataFrame({"Games": ["2004 Athens", "2008 Beijing"], "Total": [6, 7]})

        hints = infer_dataset_hints(
            "wtq",
            "Which games had above 2 total medals awarded?",
            df,
        )

        self.assertEqual(hints.kind, "list")
        self.assertTrue(hints.reasoning_required)

    def test_wtq_singular_entity_and_difference_questions_are_not_lists(self):
        df = pd.DataFrame({"Diver": ["A", "B"], "Points": [2, 1]})

        singular = infer_dataset_hints(
            "wtq",
            "Which diver has the least points in the final?",
            df,
        )
        difference = infer_dataset_hints(
            "wtq",
            "What is the difference between the most and least times?",
            df,
        )

        self.assertNotEqual(singular.kind, "list")
        self.assertNotEqual(difference.kind, "list")

    def test_wtq_alternative_comparison_declares_answer_labels(self):
        df = pd.DataFrame({"Decimal": [42, 51]})

        hints = infer_dataset_hints(
            "wtq",
            "Are there more decimal numbers in the 40s or 50s?",
            df,
        )

        self.assertEqual(hints.kind, "label")
        self.assertEqual(hints.allowed_labels, ("40s", "50s", "equal"))
        self.assertTrue(hints.reasoning_required)

    def test_tabfact_numeric_statement_requires_reasoning(self):
        df = pd.DataFrame({"nation": ["italy"], "silver": [3]})

        hints = infer_dataset_hints(
            "tabfact",
            "3 nations have 3 silver medals",
            df,
        )

        self.assertEqual(hints.answer_mode, "true_false")
        self.assertTrue(hints.reasoning_required)

    def test_tabfact_temporal_comparison_requires_reasoning(self):
        df = pd.DataFrame({"country": ["Japan", "United States"], "date": [1, 2]})

        hints = infer_dataset_hints(
            "tabfact",
            "The album was released in the United States after Japan.",
            df,
        )

        self.assertTrue(hints.reasoning_required)

    def test_crt_home_or_away_is_not_yes_no(self):
        df = pd.DataFrame({"home / away": ["home", "away"], "result": ["w", "l"]})

        hints = infer_dataset_hints(
            "crt",
            "Was the team more successful playing at home or away?",
            df,
        )

        self.assertEqual(hints.answer_mode, "")
        self.assertEqual(hints.allowed_labels, ("home", "away"))

    def test_crt_integer_metric_average_keeps_fractional_precision(self):
        df = pd.DataFrame({"decile": [4, 4, 4], "roll": [100, 300, 469]})

        hints = infer_dataset_hints(
            "crt",
            "What is the average Roll for schools with Decile of 4?",
            df,
        )

        self.assertEqual(hints.decimal_places, 2)

    def test_crt_percentage_change_average_keeps_decimal_precision(self):
        df = pd.DataFrame({"% (1960)": [13.2], "% (2040)": [12.8]})

        hints = infer_dataset_hints(
            "crt",
            "What is the average percentage change between 1960 and 2040?",
            df,
        )

        self.assertEqual(hints.decimal_places, 3)

    def test_crt_double_count_requests_tuple(self):
        df = pd.DataFrame({"res": ["win"], "method": ["decision"]})

        hints = infer_dataset_hints(
            "crt",
            "How many wins came by decision and how many by finish?",
            df,
        )

        self.assertEqual(hints.kind, "tuple")
        self.assertEqual(hints.arity, 2)

    def test_crt_relational_yes_no_requires_reasoning(self):
        df = pd.DataFrame({"company": ["A", "A"], "activity": ["x", "y"]})

        hints = infer_dataset_hints(
            "crt",
            "Are any subsidiaries involved in multiple activities?",
            df,
        )

        self.assertTrue(hints.reasoning_required)

    def test_profile_does_not_accept_gold_or_identifier_inputs(self):
        with self.assertRaises(TypeError):
            infer_dataset_hints(
                "wtq",
                "Which games qualified?",
                pd.DataFrame({"Games": ["A"]}),
                gold_answer=["A"],
            )


if __name__ == "__main__":
    unittest.main()
