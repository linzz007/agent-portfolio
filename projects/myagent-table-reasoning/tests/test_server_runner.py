import argparse
from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "server"))

from run_sharded_tqa import dataset_path_for_task, endpoint_list, task_list  # noqa: E402


def runner_args(**overrides):
    values = {
        "wtq_dataset": "",
        "tabfact_dataset": "",
        "crt_dataset": "",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class ServerRunnerTests(unittest.TestCase):
    def test_dataset_path_uses_default_relative_to_repo_root(self):
        repo_root = Path("/tmp/myagent")

        path = dataset_path_for_task(runner_args(), repo_root, "wtq")

        self.assertEqual(path, repo_root / "datasets_ready/full/wtq_unseen.jsonl")

    def test_dataset_path_accepts_relative_override(self):
        repo_root = Path("/tmp/myagent")

        path = dataset_path_for_task(
            runner_args(tabfact_dataset="datasets_ready/frozen/tabfact.jsonl"),
            repo_root,
            "tabfact",
        )

        self.assertEqual(path, repo_root / "datasets_ready/frozen/tabfact.jsonl")

    def test_dataset_path_accepts_absolute_override(self):
        path = dataset_path_for_task(
            runner_args(crt_dataset="/data/frozen/crt.jsonl"),
            Path("/tmp/myagent"),
            "crt",
        )

        self.assertEqual(path, Path("/data/frozen/crt.jsonl"))

    def test_endpoint_list_trims_and_strips_trailing_slash(self):
        self.assertEqual(
            endpoint_list(" http://127.0.0.1:8000/v1/ , http://127.0.0.1:8001/v1 "),
            ["http://127.0.0.1:8000/v1", "http://127.0.0.1:8001/v1"],
        )

    def test_task_list_rejects_unknown_tasks(self):
        with self.assertRaises(ValueError):
            task_list("wtq,unknown")


if __name__ == "__main__":
    unittest.main()
