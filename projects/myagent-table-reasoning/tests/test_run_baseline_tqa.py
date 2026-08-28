import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "code"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "server"))

from evaluate_results import summarize_rows  # noqa: E402
from run_baseline_tqa import _json_default, extract_direct_answer, run_row, run_worker  # noqa: E402


class FakeLLM:
    def __init__(self, response):
        self.responses = response if isinstance(response, list) else [response]
        self.calls = 0

    def __call__(self, prompt: str) -> str:
        self.calls += 1
        index = min(self.calls - 1, len(self.responses) - 1)
        return self.responses[index]

    def snapshot(self):
        return {
            "request_count": self.calls,
            "prompt_tokens": self.calls * 10,
            "completion_tokens": self.calls * 3,
            "total_tokens": self.calls * 13,
        }


def sample_row(answer="Italy"):
    return {
        "id": "row-1",
        "source_dataset": "wtq",
        "question": "which country is listed first?",
        "table_text": [["country", "score"], ["Italy", "10"], ["France", "8"]],
        "answer": [answer],
    }


class RunBaselineTqaTests(unittest.TestCase):
    def test_extract_direct_answer_prefers_json_payload(self):
        answer = extract_direct_answer('{"answer": "true", "reasoning_summary": "checked"}')

        self.assertEqual(answer, "true")

    def test_direct_cot_row_uses_common_evaluator_schema(self):
        row = run_row(
            sample_row(),
            baseline="direct_cot",
            llm_fn=FakeLLM('{"answer": "Italy", "reasoning_summary": "first row"}'),
        )
        summary, _ = summarize_rows([row])

        self.assertEqual(row["baseline_method"], "direct_cot")
        self.assertEqual(row["final_answer"], "Italy")
        self.assertEqual(row["api_metrics"]["total_tokens"], 13)
        self.assertEqual(summary["num_samples"], 1)
        self.assertEqual(summary["primary_accuracy"], 1.0)

    def test_single_agent_pandas_executes_final_answer_value(self):
        row = run_row(
            sample_row(),
            baseline="single_agent_pandas",
            llm_fn=FakeLLM("```python\nfinal_answer_value = df.iloc[0]['country']\n```"),
        )
        summary, _ = summarize_rows([row])

        self.assertEqual(row["baseline_method"], "single_agent_pandas")
        self.assertEqual(row["final_value"], "Italy")
        self.assertTrue(row["exec_success"])
        self.assertEqual(summary["primary_accuracy"], 1.0)

    def test_single_agent_pandas_repairs_failed_code_once(self):
        row = run_row(
            sample_row(),
            baseline="single_agent_pandas",
            llm_fn=FakeLLM(
                [
                    "```python\nfinal_answer_value = missing_name\n```",
                    "```python\nfinal_answer_value = df.iloc[0]['country']\n```",
                ]
            ),
            max_code_retries=1,
        )
        summary, _ = summarize_rows([row])

        self.assertEqual(row["final_value"], "Italy")
        self.assertEqual(row["api_metrics"]["request_count"], 2)
        self.assertEqual(len(row["pandas_attempts"]), 1)
        self.assertEqual(summary["primary_accuracy"], 1.0)

    def test_failure_row_uses_fallback_answer_for_scoring(self):
        row = run_row(
            sample_row(),
            baseline="single_agent_pandas",
            llm_fn=FakeLLM("```python\nfinal_answer_value = missing_name\n```"),
        )
        summary, _ = summarize_rows([row])

        self.assertFalse(row["exec_success"])
        self.assertIn("NameError", row["exec_error"])
        self.assertTrue(row["fallback_used"])
        self.assertEqual(row["pred_answer"], "unknown")
        self.assertEqual(row["error_type"], "execution_error")
        self.assertEqual(summary["num_failed_exec"], 1)
        self.assertEqual(summary["num_missing_answer"], 0)

    def test_pandas_timedelta_is_json_serializable_at_output_boundary(self):
        encoded = json.dumps(
            {"answer": pd.Timedelta("2 days 03:04:05")},
            default=_json_default,
        )

        self.assertEqual(json.loads(encoded), {"answer": "2 days 03:04:05"})

    def test_worker_respects_resume_count(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            dataset = tmp / "input.jsonl"
            output = tmp / "out.jsonl"
            rows = [sample_row("Italy"), {**sample_row("France"), "id": "row-2"}]
            dataset.write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )
            output.write_text(json.dumps({"id": "row-1", "final_answer": "Italy"}) + "\n", encoding="utf-8")
            args = type(
                "Args",
                (),
                {
                    "dataset_path": str(dataset),
                    "output_path": str(output),
                    "limit": 0,
                    "resume": True,
                    "baseline": "direct_cot",
                    "task": "wtq",
                },
            )()

            with patch(
                "run_baseline_tqa.build_llm_fn",
                return_value=FakeLLM('{"answer": "France"}'),
            ):
                run_worker(args)

            output_rows = [
                json.loads(line)
                for line in output.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual([row["id"] for row in output_rows], ["row-1", "row-2"])


if __name__ == "__main__":
    unittest.main()
