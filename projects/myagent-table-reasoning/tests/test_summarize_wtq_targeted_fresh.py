import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "server"))

from summarize_wtq_targeted_fresh import summarize_fresh_validation  # noqa: E402


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


class SummarizeWtqTargetedFreshTests(unittest.TestCase):
    def test_all_targeted_rows_correct_passes_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            input_path = run_dir / "input" / "wtq_p4b_targeted_fix_affected_slice.jsonl"
            output_root = run_dir / "myagent_wtq_targeted_fix"
            merged_path = output_root / "merged" / "wtq_qwen3-32b-local.jsonl"
            eval_path = output_root / "eval" / "wtq_qwen3-32b-local_eval.json"
            projection_path = run_dir / "p4b_wtq_targeted_fix_projection.json"

            rows = [
                {
                    "id": "nu-a",
                    "source_dataset": "wtq",
                    "final_value": "1",
                    "answer": ["1"],
                    "answer_canonical": ["1.0"],
                },
                {
                    "id": "nu-b",
                    "source_dataset": "wtq",
                    "final_value": "2",
                    "answer": ["2"],
                    "answer_canonical": ["2.0"],
                },
            ]
            _write_jsonl(input_path, [{"id": row["id"]} for row in rows])
            _write_jsonl(merged_path, rows)
            _write_json(
                eval_path,
                {
                    "num_samples": 2,
                    "num_with_gold": 2,
                    "primary_accuracy": 1.0,
                    "num_failed_exec": 0,
                    "num_missing_answer": 0,
                    "avg_total_tokens": 123.5,
                    "avg_elapsed_seconds": 4.25,
                },
            )
            _write_json(
                projection_path,
                {"wrong_to_correct_ids": ["nu-a", "nu-b"], "wrong_to_correct": 2},
            )

            summary = summarize_fresh_validation(
                run_dir=run_dir,
                output_root=output_root,
                min_correct=2,
            )

            self.assertEqual(summary["decision"], "pass")
            self.assertEqual(summary["fresh"]["correct"], 2)
            self.assertEqual(summary["fresh"]["fresh_wrong_ids"], [])
            self.assertEqual(summary["coverage"]["missing_output_ids"], [])
            self.assertEqual(summary["projection"]["targeted_ids"], ["nu-a", "nu-b"])

    def test_wrong_targeted_row_requires_inspection_and_reports_wrong_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            input_path = run_dir / "input" / "wtq_p4b_targeted_fix_affected_slice.jsonl"
            output_root = run_dir / "myagent_wtq_targeted_fix"
            merged_path = output_root / "merged" / "wtq_qwen3-32b-local.jsonl"
            eval_path = output_root / "eval" / "wtq_qwen3-32b-local_eval.json"
            projection_path = run_dir / "p4b_wtq_targeted_fix_projection.json"

            rows = [
                {
                    "id": "nu-a",
                    "source_dataset": "wtq",
                    "final_value": "1",
                    "answer": ["1"],
                    "answer_canonical": ["1.0"],
                },
                {
                    "id": "nu-b",
                    "source_dataset": "wtq",
                    "final_value": "wrong",
                    "answer": ["2"],
                    "answer_canonical": ["2.0"],
                },
            ]
            _write_jsonl(input_path, [{"id": row["id"]} for row in rows])
            _write_jsonl(merged_path, rows)
            _write_json(
                eval_path,
                {
                    "num_samples": 2,
                    "num_with_gold": 2,
                    "primary_accuracy": 0.5,
                    "num_failed_exec": 0,
                    "num_missing_answer": 0,
                    "avg_total_tokens": 123.5,
                    "avg_elapsed_seconds": 4.25,
                },
            )
            _write_json(
                projection_path,
                {"wrong_to_correct_ids": ["nu-a", "nu-b"], "wrong_to_correct": 2},
            )

            summary = summarize_fresh_validation(
                run_dir=run_dir,
                output_root=output_root,
                min_correct=2,
            )

            self.assertEqual(summary["decision"], "inspect")
            self.assertEqual(summary["fresh"]["correct"], 1)
            self.assertEqual(summary["fresh"]["fresh_wrong_ids"], ["nu-b"])
            self.assertIn("correct_below_threshold", summary["decision_reasons"])


if __name__ == "__main__":
    unittest.main()
