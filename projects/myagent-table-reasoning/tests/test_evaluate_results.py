import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "code"))

from evaluate_results import (  # noqa: E402
    dataset_accuracy,
    exact_match,
    gold_for_em,
    prediction_for_em,
    summarize_rows,
)


class EvaluateResultsTests(unittest.TestCase):
    def test_wtq_uses_canonical_denotation_and_requires_matching_cardinality(self):
        canonical_number = {
            "source_dataset": "wtq",
            "final_value": 17,
            "answer": ["17 years"],
            "answer_canonical": ["17.0"],
        }
        complete_set = {
            "source_dataset": "wtq",
            "final_value": ["2004", "2005", "2006"],
            "answer": ["2004", "2005", "2006"],
            "answer_canonical": ["2004.0", "2005.0", "2006.0"],
        }
        incomplete_set = dict(complete_set, final_value=["2004", "2005"])

        self.assertTrue(dataset_accuracy(canonical_number))
        self.assertTrue(dataset_accuracy(complete_set))
        self.assertFalse(dataset_accuracy(incomplete_set))

    def test_wtq_string_normalization_matches_official_rules(self):
        row = {
            "source_dataset": "wtq",
            "pred_answer": "Stephane Goubert.",
            "answer": ["Stéphane Goubert"],
            "answer_canonical": ["Stéphane Goubert"],
        }

        self.assertTrue(dataset_accuracy(row))

    def test_wtq_string_normalization_removes_stray_escape_quotes(self):
        row = {
            "source_dataset": "wtq",
            "final_value": '\\Home Again\\""',
            "answer": ["Home Again"],
            "answer_canonical": ["Home Again"],
        }

        self.assertTrue(dataset_accuracy(row))

    def test_wtq_target_matches_raw_string_or_canonical_number(self):
        raw_string_match = {
            "source_dataset": "wtq",
            "final_value": "$12 billion",
            "answer": ["$12 billion"],
            "answer_canonical": ["12000000000.0"],
        }
        canonical_number_match = dict(raw_string_match, final_value=12000000000)
        wrong_format = dict(raw_string_match, final_value="$12.0 billion")

        self.assertTrue(dataset_accuracy(raw_string_match))
        self.assertTrue(dataset_accuracy(canonical_number_match))
        self.assertFalse(dataset_accuracy(wrong_format))

    def test_wtq_boolean_predictions_match_yes_no_denotations(self):
        yes_row = {
            "source_dataset": "wtq",
            "final_value": True,
            "answer": ["yes"],
            "answer_canonical": ["yes"],
        }
        no_row = {
            "source_dataset": "wtq",
            "final_value": False,
            "answer": ["no"],
            "answer_canonical": ["no"],
        }

        self.assertTrue(dataset_accuracy(yes_row))
        self.assertTrue(dataset_accuracy(no_row))

    def test_tabfact_uses_canonical_binary_accuracy(self):
        true_row = {
            "source_dataset": "tabfact",
            "pred_answer": "True.",
            "answer": ["true"],
        }
        false_row = {
            "source_dataset": "tabfact",
            "final_value": "No",
            "answer": ["false"],
        }

        self.assertTrue(dataset_accuracy(true_row))
        self.assertTrue(dataset_accuracy(false_row))

    def test_crt_uses_normalized_text_and_numeric_matching(self):
        label_row = {
            "source_dataset": "crt",
            "pred_answer": "Yes.",
            "answer": ["yes"],
        }
        numeric_row = {
            "source_dataset": "crt",
            "final_value": 9.1730004,
            "answer": ["9.173"],
        }

        self.assertTrue(dataset_accuracy(label_row))
        self.assertTrue(dataset_accuracy(numeric_row))

    def test_crt_ordered_multi_value_answer_matches_delimited_gold(self):
        correct = {
            "source_dataset": "crt",
            "final_value": [3, 12],
            "answer": ["3, 12"],
        }
        reversed_values = dict(correct, final_value=[12, 3])

        self.assertTrue(dataset_accuracy(correct))
        self.assertFalse(dataset_accuracy(reversed_values))

    def test_crt_percentage_question_accepts_numeric_percent_value(self):
        row = {
            "source_dataset": "crt",
            "question": "What percentage of schools qualify?",
            "final_value": 20.0,
            "answer": ["20%"],
        }

        self.assertTrue(dataset_accuracy(row))

    def test_exact_match_accepts_any_valid_gold_answer(self):
        self.assertTrue(exact_match("Italy", ["Italy", "Italian Republic"]))
        self.assertFalse(exact_match("France", ["Italy", "Italian Republic"]))

    def test_mact_prediction_and_gold_fields_are_detected(self):
        row = {"pred_answer": "42", "answer": "42"}
        self.assertEqual(prediction_for_em(row), "42")
        self.assertEqual(gold_for_em(row), "42")

    def test_mact_summary_uses_api_metrics_and_full_table_ratio(self):
        rows = [
            {
                "pred_answer": "42",
                "answer": "42",
                "api_metrics": {
                    "request_count": 3,
                    "prompt_tokens": 90,
                    "completion_tokens": 15,
                    "prompt_tokens_est": 80,
                    "completion_tokens_est": 12,
                    "total_tokens_est": 92,
                },
                "elapsed_seconds_total": 2.5,
            },
            {
                "pred_answer": "no",
                "answer": "yes",
                "api_metrics": {
                    "request_count": 1,
                    "prompt_tokens": 30,
                    "completion_tokens": 5,
                    "prompt_tokens_est": 20,
                    "completion_tokens_est": 8,
                    "total_tokens_est": 28,
                },
                "elapsed_seconds_total": 1.5,
            },
        ]

        summary, anomalies = summarize_rows(rows)

        self.assertEqual(summary["result_schema"], "mact")
        self.assertEqual(summary["exact_match"], 0.5)
        self.assertEqual(summary["primary_accuracy"], 0.5)
        self.assertEqual(summary["avg_llm_calls"], 2.0)
        self.assertEqual(summary["avg_prompt_tokens"], 60.0)
        self.assertEqual(summary["avg_completion_tokens"], 10.0)
        self.assertEqual(summary["avg_total_tokens"], 70.0)
        self.assertEqual(summary["avg_prompt_tokens_est"], 50.0)
        self.assertEqual(summary["avg_completion_tokens_est"], 10.0)
        self.assertEqual(summary["avg_total_tokens_est"], 60.0)
        self.assertEqual(summary["avg_elapsed_seconds"], 2.0)
        self.assertEqual(summary["avg_compression_ratio"], 1.0)
        self.assertEqual(summary["token_measurement"], "api_usage")
        self.assertEqual(len(anomalies), 1)

    def test_myagent_summary_keeps_routing_and_estimated_token_metrics(self):
        rows = [
            {
                "final_answer": "42",
                "gold_answer": "42",
                "route_type": "SIMPLE",
                "difficulty_level": "easy",
                "simple_lookup_success": True,
                "simple_lookup_value": "42",
                "llm_metrics": {
                    "llm_call_count": 2,
                    "prompt_tokens_est": 40,
                    "completion_tokens_est": 5,
                    "total_tokens_est": 45,
                },
                "compression_info": {"compression_ratio": 0.25},
                "elapsed_seconds_total": 1.0,
            }
        ]

        summary, anomalies = summarize_rows(rows)

        self.assertEqual(summary["result_schema"], "myagent")
        self.assertEqual(summary["exact_match"], 1.0)
        self.assertEqual(summary["route_distribution"], {"SIMPLE": 1})
        self.assertEqual(summary["avg_llm_calls"], 2.0)
        self.assertEqual(summary["avg_total_tokens"], 45.0)
        self.assertEqual(summary["avg_compression_ratio"], 0.25)
        self.assertEqual(summary["token_measurement"], "estimated")
        self.assertEqual(anomalies, [])

    def test_myagent_summary_prefers_actual_api_usage(self):
        rows = [
            {
                "source_dataset": "wtq",
                "final_value": "42",
                "answer": ["42"],
                "answer_canonical": ["42.0"],
                "api_metrics": {
                    "request_count": 3,
                    "prompt_tokens": 90,
                    "completion_tokens": 10,
                    "total_tokens": 100,
                },
                "llm_metrics": {
                    "llm_call_count": 3,
                    "prompt_tokens_est": 40,
                    "completion_tokens_est": 5,
                    "total_tokens_est": 45,
                },
            }
        ]

        summary, _ = summarize_rows(rows)

        self.assertEqual(summary["avg_total_tokens"], 100.0)
        self.assertEqual(summary["token_measurement"], "api_usage")
        self.assertEqual(summary["avg_total_tokens_est"], 45.0)

    def test_summary_reports_risk_strata_accuracy_and_tokens(self):
        rows = [
            {
                "source_dataset": "wtq",
                "final_value": "a",
                "answer": ["a"],
                "risk_level": "light",
                "api_metrics": {"prompt_tokens": 80, "completion_tokens": 20},
            },
            {
                "source_dataset": "wtq",
                "final_value": "b",
                "answer": ["c"],
                "observability": {"risk_level": "high"},
                "api_metrics": {"prompt_tokens": 250, "completion_tokens": 50},
            },
        ]

        summary, _ = summarize_rows(rows)

        self.assertEqual(summary["risk_strata"]["light"]["accuracy"], 1.0)
        self.assertEqual(summary["risk_strata"]["light"]["avg_total_tokens"], 100.0)
        self.assertEqual(summary["risk_strata"]["high"]["accuracy"], 0.0)
        self.assertEqual(summary["risk_strata"]["high"]["avg_total_tokens"], 300.0)


if __name__ == "__main__":
    unittest.main()
