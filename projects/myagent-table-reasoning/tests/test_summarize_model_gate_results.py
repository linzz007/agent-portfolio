from pathlib import Path
import json
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "server"))

from summarize_model_gate_results import summarize_gate_results, render_markdown  # noqa: E402


def write_eval(path: Path, *, samples: int, accuracy: float, tokens: float, failed: int = 0, missing: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "result_schema": "myagent",
                "num_samples": samples,
                "num_with_gold": samples,
                "primary_accuracy": accuracy,
                "num_failed_exec": failed,
                "num_missing_answer": missing,
                "avg_total_tokens": tokens,
            }
        ),
        encoding="utf-8",
    )


class SummarizeModelGateResultsTests(unittest.TestCase):
    def test_gate50_when_gate10_smoke_has_all_outputs_low_failures_and_tokens(self):
        """Catches Gate-10 smoke runs that need a machine-readable go/no-go decision."""
        with tempfile.TemporaryDirectory() as tmp_name:
            gate_root = Path(tmp_name) / "myagent_gate10"
            write_eval(gate_root / "eval" / "wtq_model_eval.json", samples=10, accuracy=0.60, tokens=6000)
            write_eval(gate_root / "eval" / "tabfact_model_eval.json", samples=10, accuracy=0.80, tokens=3000)
            write_eval(gate_root / "eval" / "crt_model_eval.json", samples=10, accuracy=0.50, tokens=7000)

            summary = summarize_gate_results(
                gate_root=gate_root,
                model_tag="smoke_candidate",
                gate_name="gate10",
                mact_avg_tokens=11262.41,
            )
            markdown = render_markdown(summary)

        self.assertEqual(summary["criteria"]["reference_correct"], 0)
        self.assertEqual(summary["decision"], "gate50")
        self.assertIn("gate10_criteria_passed", summary["decision_reasons"])
        self.assertIn("Gate-10 Summary", markdown)
        self.assertIn("gate50", markdown)

    def test_no_go_when_gate10_smoke_has_any_bad_rows(self):
        """Catches expanding from a smoke run with failed execution or missing answers."""
        with tempfile.TemporaryDirectory() as tmp_name:
            gate_root = Path(tmp_name) / "myagent_gate10"
            write_eval(gate_root / "eval" / "wtq_model_eval.json", samples=10, accuracy=0.60, tokens=6000)
            write_eval(gate_root / "eval" / "tabfact_model_eval.json", samples=10, accuracy=0.80, tokens=3000)
            write_eval(gate_root / "eval" / "crt_model_eval.json", samples=10, accuracy=0.50, tokens=7000, failed=1)

            summary = summarize_gate_results(
                gate_root=gate_root,
                model_tag="broken_smoke_candidate",
                gate_name="gate10",
                mact_avg_tokens=11262.41,
            )

        self.assertEqual(summary["decision"], "no-go")
        self.assertIn("failure_rate_above_threshold", summary["decision_reasons"])

    def test_no_go_when_correct_below_reference(self):
        """Catches expanding a model whose Gate-50 accuracy is below the Qwen3-32B reference."""
        with tempfile.TemporaryDirectory() as tmp_name:
            gate_root = Path(tmp_name) / "myagent_gate50"
            write_eval(gate_root / "eval" / "wtq_model_eval.json", samples=50, accuracy=0.74, tokens=6300)
            write_eval(gate_root / "eval" / "tabfact_model_eval.json", samples=50, accuracy=0.88, tokens=2500)
            write_eval(gate_root / "eval" / "crt_model_eval.json", samples=50, accuracy=0.54, tokens=13200)

            summary = summarize_gate_results(
                gate_root=gate_root,
                model_tag="qwen3_14b_awq",
                reference_correct=124,
                mact_avg_tokens=11262.41,
            )

        self.assertEqual(summary["overall"]["correct"], 108)
        self.assertEqual(summary["overall"]["rows"], 150)
        self.assertEqual(summary["decision"], "no-go")
        self.assertIn("overall_correct_below_reference", summary["decision_reasons"])

    def test_gate150_when_accuracy_failures_and_tokens_pass(self):
        """Catches blocking a model that satisfies the Gate-50 expansion criteria."""
        with tempfile.TemporaryDirectory() as tmp_name:
            gate_root = Path(tmp_name) / "myagent_gate50"
            write_eval(gate_root / "eval" / "wtq_model_eval.json", samples=50, accuracy=0.70, tokens=6560.84)
            write_eval(gate_root / "eval" / "tabfact_model_eval.json", samples=50, accuracy=0.96, tokens=2100.0)
            write_eval(gate_root / "eval" / "crt_model_eval.json", samples=50, accuracy=0.82, tokens=13138.0)

            summary = summarize_gate_results(
                gate_root=gate_root,
                model_tag="qwen3_32b",
                reference_correct=124,
                mact_avg_tokens=11262.41,
            )
            markdown = render_markdown(summary)

        self.assertEqual(summary["overall"]["correct"], 124)
        self.assertEqual(summary["decision"], "gate150")
        self.assertLess(summary["overall"]["token_ratio_to_mact"], 0.75)
        self.assertIn("Gate-50 Summary", markdown)
        self.assertIn("qwen3_32b", markdown)
        self.assertIn("124/150", markdown)
        self.assertIn("gate150", markdown)

    def test_no_go_when_failed_and_missing_together_exceed_failure_budget(self):
        """Catches undercounting bad rows by taking max(failed, missing) instead of a conservative total."""
        with tempfile.TemporaryDirectory() as tmp_name:
            gate_root = Path(tmp_name) / "myagent_gate50"
            write_eval(gate_root / "eval" / "wtq_model_eval.json", samples=50, accuracy=0.70, tokens=6000, failed=2, missing=2)
            write_eval(gate_root / "eval" / "tabfact_model_eval.json", samples=50, accuracy=0.96, tokens=2500)
            write_eval(gate_root / "eval" / "crt_model_eval.json", samples=50, accuracy=0.82, tokens=9000)

            summary = summarize_gate_results(
                gate_root=gate_root,
                model_tag="anomalous_candidate",
                reference_correct=124,
                mact_avg_tokens=11262.41,
            )

        self.assertEqual(summary["overall"]["correct"], 124)
        self.assertEqual(summary["per_dataset"]["wtq"]["bad_rows"], 4)
        self.assertEqual(summary["overall"]["bad_rows"], 4)
        self.assertEqual(summary["decision"], "no-go")
        self.assertIn("failure_rate_above_threshold", summary["decision_reasons"])

    def test_paired200_when_gate150_reference_failures_and_tokens_pass(self):
        """Catches Gate-150 results that still require hand-written paired-200 decisions."""
        with tempfile.TemporaryDirectory() as tmp_name:
            gate_root = Path(tmp_name) / "myagent_gate150"
            write_eval(gate_root / "eval" / "wtq_model_eval.json", samples=150, accuracy=0.70, tokens=6248.95)
            write_eval(gate_root / "eval" / "tabfact_model_eval.json", samples=150, accuracy=0.88, tokens=2657.49)
            write_eval(gate_root / "eval" / "crt_model_eval.json", samples=150, accuracy=0.6533333333, tokens=12484.09)

            summary = summarize_gate_results(
                gate_root=gate_root,
                model_tag="candidate_model",
                gate_name="gate150",
                mact_avg_tokens=11262.41,
            )
            markdown = render_markdown(summary)

        self.assertEqual(summary["criteria"]["reference_correct"], 333)
        self.assertEqual(summary["overall"]["correct"], 335)
        self.assertEqual(summary["overall"]["rows"], 450)
        self.assertEqual(summary["decision"], "paired200")
        self.assertIn("gate150_criteria_passed", summary["decision_reasons"])
        self.assertIn("Gate-150 Summary", markdown)
        self.assertIn("335/450", markdown)
        self.assertIn("paired200", markdown)

    def test_no_go_when_gate150_overall_passes_but_too_few_datasets_match_reference(self):
        """Catches overall-only Gate-150 expansion when dataset-level evidence is too weak."""
        with tempfile.TemporaryDirectory() as tmp_name:
            gate_root = Path(tmp_name) / "myagent_gate150"
            write_eval(gate_root / "eval" / "wtq_model_eval.json", samples=150, accuracy=0.9933333333, tokens=2000)
            write_eval(gate_root / "eval" / "tabfact_model_eval.json", samples=150, accuracy=0.84, tokens=2000)
            write_eval(gate_root / "eval" / "crt_model_eval.json", samples=150, accuracy=0.5133333333, tokens=2000)

            summary = summarize_gate_results(
                gate_root=gate_root,
                model_tag="lopsided_candidate",
                gate_name="gate150",
                mact_avg_tokens=11262.41,
            )

        self.assertEqual(summary["overall"]["correct"], 352)
        self.assertEqual(summary["criteria"]["dataset_reference_correct"], {"wtq": 105, "tabfact": 131, "crt": 97})
        self.assertEqual(summary["overall"]["datasets_at_least_reference"], 1)
        self.assertEqual(summary["decision"], "no-go")
        self.assertIn("datasets_at_reference_below_threshold", summary["decision_reasons"])

    def test_incomplete_when_any_dataset_eval_is_missing(self):
        """Catches silent decisions from partial WTQ/TabFact/CRT outputs."""
        with tempfile.TemporaryDirectory() as tmp_name:
            gate_root = Path(tmp_name) / "myagent_gate50"
            write_eval(gate_root / "eval" / "wtq_model_eval.json", samples=50, accuracy=0.70, tokens=6500)
            write_eval(gate_root / "eval" / "tabfact_model_eval.json", samples=50, accuracy=0.96, tokens=2100)

            summary = summarize_gate_results(
                gate_root=gate_root,
                model_tag="partial_model",
                reference_correct=124,
                mact_avg_tokens=11262.41,
            )

        self.assertEqual(summary["decision"], "incomplete")
        self.assertEqual(summary["missing_tasks"], ["crt"])


if __name__ == "__main__":
    unittest.main()
