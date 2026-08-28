from pathlib import Path
import argparse
import json
import sys
import tempfile
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "code"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "server"))

from evaluate_results import summarize_rows  # noqa: E402
from run_mact_one_by_one import build_mact_command, failure_row, run_dataset  # noqa: E402


class RunMactOneByOneTests(unittest.TestCase):
    def test_failure_row_is_counted_as_exec_error_with_fallback_answer(self):
        sample = {
            "id": "wtq-1",
            "source_dataset": "wtq",
            "question": "how many rows?",
            "table_text": [["A"], ["x"]],
            "answer": ["1"],
        }

        row = failure_row(
            sample,
            error_message="BadRequestError: context length exceeded",
            elapsed_seconds=3.25,
            returncode=0,
            log_path=Path("/tmp/mact.log"),
        )
        summary, _ = summarize_rows([row])

        self.assertEqual(row["pred_answer"], "0")
        self.assertTrue(row["fallback_used"])
        self.assertEqual(row["error_type"], "context_overflow")
        self.assertTrue(row["context_overflow"])
        self.assertIn("context length exceeded", row["exec_error"])
        self.assertEqual(summary["num_samples"], 1)
        self.assertEqual(summary["num_failed_exec"], 1)
        self.assertEqual(summary["num_missing_answer"], 0)
        self.assertEqual(summary["primary_accuracy"], 0.0)

    def test_build_mact_command_preserves_context_safe_parameters(self):
        command = build_mact_command(
            python_executable=Path("/env/bin/python"),
            mact_root=Path("/repo/MACT"),
            task="wtq",
            dataset_path=Path("/tmp/sample.jsonl"),
            output_path=Path("/tmp/out.jsonl"),
            plan_model_name="qwen3-32b-local",
            code_model_name="qwen3-32b-local",
            model_provider="openai_compatible",
            api_base="http://127.0.0.1:8000/v1",
            api_key_env="LOCAL_VLLM_API_KEY",
            thinking="disabled",
            temperature=0.0,
            max_tokens=1024,
            api_timeout=180.0,
            api_max_retries=5,
            plan_sample=1,
            code_sample=1,
            max_step=3,
            max_actual_step=3,
        )

        self.assertEqual(command[0], "/env/bin/python")
        self.assertIn("/repo/MACT/code/tqa.py", command)
        self.assertIn("--task", command)
        self.assertIn("wtq", command)
        self.assertIn("--max_tokens", command)
        self.assertIn("1024", command)
        self.assertIn("--model_provider", command)
        self.assertIn("openai_compatible", command)

    def test_run_dataset_respects_limit(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            dataset_path = tmp / "dataset.jsonl"
            output_path = tmp / "out.jsonl"
            log_path = tmp / "run.log"
            samples = [
                {"id": "row-1", "answer": "a"},
                {"id": "row-2", "answer": "b"},
                {"id": "row-3", "answer": "c"},
            ]
            dataset_path.write_text(
                "".join(json.dumps(sample) + "\n" for sample in samples),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                dataset_path=str(dataset_path),
                output_path=str(output_path),
                log_path=str(log_path),
                limit=2,
                resume=False,
                temp_dir=str(tmp / "temp"),
                task="wtq",
            )

            def fake_run_one_sample(*, sample, **_kwargs):
                row = dict(sample)
                row["pred_answer"] = row["answer"]
                return row

            with patch("run_mact_one_by_one.run_one_sample", fake_run_one_sample):
                run_dataset(args)

            rows = [
                json.loads(line)
                for line in output_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([row["id"] for row in rows], ["row-1", "row-2"])


if __name__ == "__main__":
    unittest.main()
