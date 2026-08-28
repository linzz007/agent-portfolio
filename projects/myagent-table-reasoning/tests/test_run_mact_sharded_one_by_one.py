import argparse
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "server"))

from run_mact_sharded_one_by_one import run_dataset, split_contiguous  # noqa: E402


class FakeProcess:
    commands = []

    def __init__(self, command, **_kwargs):
        self.command = command
        self.returncode = 0
        FakeProcess.commands.append(command)
        dataset_path = Path(command[command.index("--dataset-path") + 1])
        output_path = Path(command[command.index("--output-path") + 1])
        rows = [
            json.loads(line)
            for line in dataset_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as handle:
            for row in rows:
                out = dict(row)
                out["pred_answer"] = row.get("answer", "")
                handle.write(json.dumps(out) + "\n")

    def wait(self):
        return self.returncode


class RunMactShardedOneByOneTests(unittest.TestCase):
    def test_split_contiguous_preserves_order_and_covers_all_rows(self):
        rows = [{"id": str(index)} for index in range(5)]

        shards = split_contiguous(rows, 2)

        self.assertEqual([start for start, _ in shards], [0, 2])
        self.assertEqual([[row["id"] for row in shard] for _, shard in shards], [["0", "1"], ["2", "3", "4"]])

    def test_run_dataset_resumes_prefix_and_merges_shards_in_order(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            dataset_path = tmp / "dataset.jsonl"
            output_path = tmp / "out.jsonl"
            samples = [{"id": f"row-{index}", "answer": str(index)} for index in range(5)]
            dataset_path.write_text(
                "".join(json.dumps(sample) + "\n" for sample in samples),
                encoding="utf-8",
            )
            output_path.write_text(json.dumps({"id": "row-0", "pred_answer": "0"}) + "\n", encoding="utf-8")
            args = argparse.Namespace(
                myagent_root=str(PROJECT_ROOT),
                mact_root="/repo/MACT",
                dataset_path=str(dataset_path),
                output_path=str(output_path),
                log_dir=str(tmp / "logs"),
                shard_dir=str(tmp / "shards"),
                task="wtq",
                python_executable=sys.executable,
                plan_model_name="qwen3-32b-local",
                code_model_name="qwen3-32b-local",
                model_provider="openai_compatible",
                endpoints="http://127.0.0.1:8000/v1,http://127.0.0.1:8001/v1",
                api_key_env="LOCAL_VLLM_API_KEY",
                thinking="disabled",
                temperature=0.0,
                max_tokens=2048,
                api_timeout=180.0,
                api_max_retries=5,
                plan_sample=1,
                code_sample=1,
                max_step=3,
                max_actual_step=3,
                temp_dir=str(tmp / "tmp"),
                limit=None,
                resume=True,
            )
            FakeProcess.commands = []

            with patch("run_mact_sharded_one_by_one.subprocess.Popen", FakeProcess):
                run_dataset(args)

            rows = [
                json.loads(line)
                for line in output_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual([row["id"] for row in rows], [f"row-{index}" for index in range(5)])
            self.assertEqual(len(FakeProcess.commands), 2)
            self.assertIn("http://127.0.0.1:8000/v1", FakeProcess.commands[0])
            self.assertIn("http://127.0.0.1:8001/v1", FakeProcess.commands[1])


if __name__ == "__main__":
    unittest.main()
