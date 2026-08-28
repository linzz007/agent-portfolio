from pathlib import Path
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_gate150_paired200_summary(gate_run: Path) -> None:
    write_json(
        gate_run / "gate150_summary.json",
        {
            "gate_name": "gate150",
            "decision": "paired200",
            "decision_reasons": ["gate150_criteria_passed"],
            "overall": {"correct": 335, "rows": 450, "datasets_at_least_reference": 2},
            "criteria": {
                "reference_correct": 333,
                "dataset_reference_correct": {"wtq": 105, "tabfact": 131, "crt": 97},
                "min_datasets_at_reference": 2,
            },
        },
    )


def assert_executable_when_supported(testcase: unittest.TestCase, path: Path) -> None:
    testcase.assertTrue(path.exists())
    if os.name != "nt":
        testcase.assertTrue(path.stat().st_mode & stat.S_IXUSR)


def assert_bash_syntax_when_supported(path: Path) -> None:
    if os.name != "nt":
        subprocess.run(["bash", "-n", str(path)], check=True)


def assert_checkpoint_script_stages_ignored_run_dir(
    testcase: unittest.TestCase,
    mact_root: Path,
    run_dir: Path,
    marker_relative_path: str,
) -> None:
    subprocess.run(["git", "-C", str(mact_root), "init"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (mact_root / ".gitignore").write_text("outputs/\n", encoding="utf-8")
    marker = run_dir / marker_relative_path
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text('{"ok": true}\n', encoding="utf-8")

    checkpoint = run_dir / "checkpoint_to_git.sh"
    assert_executable_when_supported(testcase, checkpoint)
    assert_bash_syntax_when_supported(checkpoint)
    if os.name == "nt":
        testcase.assertIn("git add -f -- \"$RUN_REL\"", checkpoint.read_text(encoding="utf-8"))
        return
    subprocess.run(["bash", str(checkpoint)], check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    staged = subprocess.run(
        ["git", "-C", str(mact_root), "diff", "--cached", "--name-only"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.splitlines()
    run_relative = run_dir.relative_to(mact_root)
    testcase.assertIn(str(run_relative / "checkpoint_to_git.sh"), staged)
    testcase.assertIn(str(run_relative / marker_relative_path), staged)


class PreparePaired200RunTests(unittest.TestCase):
    def test_cli_prepares_local_paired200_run_from_gate_manifest(self):
        """Catches final-candidate paired-200 expansion that still needs hand-written scripts."""
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            myagent_root = tmp / "MyAgent"
            mact_root = tmp / "MACT"
            gate_run = mact_root / "outputs" / "server_runs" / "model_a_gate50_20260730"
            paired_run = mact_root / "outputs" / "server_runs" / "model_a_paired200_20260731"
            myagent_root.mkdir()
            (gate_run / "vllm.env").parent.mkdir(parents=True)
            (gate_run / "vllm.env").write_text(
                "\n".join(
                    [
                        "export SERVED_MODEL_NAME=model-a-local",
                        "export LOCAL_VLLM_API_KEY=local-placeholder",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            write_json(
                gate_run / "gate_run_manifest.json",
                {
                    "backend": "local-vllm",
                    "model_tag": "model_a",
                    "served_model_name": "model-a-local",
                    "endpoints": ["http://127.0.0.1:8000/v1", "http://127.0.0.1:8001/v1"],
                },
            )
            write_gate150_paired200_summary(gate_run)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "server" / "prepare_paired200_run.py"),
                    "--myagent-root",
                    str(myagent_root),
                    "--mact-root",
                    str(mact_root),
                    "--gate-run-dir",
                    str(gate_run),
                    "--run-dir",
                    str(paired_run),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            manifest = json.loads((paired_run / "paired200_run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["model_tag"], "model_a")
            self.assertEqual(manifest["backend"], "local-vllm")
            self.assertEqual(manifest["paired_limit"], 200)
            self.assertEqual(manifest["source_gate_run_dir"], str(gate_run))
            self.assertEqual(
                manifest["datasets"],
                {
                    "wtq": "datasets_ready/blind_holdout_200_v1_2026-06-27/wtq.jsonl",
                    "tabfact": "datasets_ready/blind_holdout_200_v1_2026-06-27/tabfact.jsonl",
                    "crt": "datasets_ready/blind_holdout_200_v1_2026-06-27/crt.jsonl",
                },
            )

            scripts = [
                "run_myagent_paired200.sh",
                "run_mact_wtq_paired200.sh",
                "run_mact_tabfact_paired200.sh",
                "run_mact_crt_paired200.sh",
                "run_eval_and_compare.sh",
            ]
            for script_name in scripts:
                script = paired_run / script_name
                assert_executable_when_supported(self, script)
                assert_bash_syntax_when_supported(script)
            healthcheck_script = paired_run / "healthcheck_services.sh"
            assert_executable_when_supported(self, healthcheck_script)
            assert_bash_syntax_when_supported(healthcheck_script)
            healthcheck_text = healthcheck_script.read_text(encoding="utf-8")
            self.assertIn('bash scripts/server/healthcheck_vllm_pool.sh "$SOURCE_GATE_RUN_DIR/vllm.env"', healthcheck_text)

            myagent_script = (paired_run / "run_myagent_paired200.sh").read_text(encoding="utf-8")
            self.assertIn("--limit-per-task 200", myagent_script)
            self.assertIn("--output-root \"$PAIRED_RUN_DIR/myagent_paired200\"", myagent_script)
            self.assertIn("http://127.0.0.1:8000/v1,http://127.0.0.1:8001/v1", myagent_script)
            self.assertIn("--api-key-env LOCAL_VLLM_API_KEY", myagent_script)

            mact_wtq = (paired_run / "run_mact_wtq_paired200.sh").read_text(encoding="utf-8")
            self.assertIn("--task wtq", mact_wtq)
            self.assertIn("--dataset-path datasets_ready/blind_holdout_200_v1_2026-06-27/wtq.jsonl", mact_wtq)
            self.assertIn("--output-path \"$PAIRED_RUN_DIR/mact/wtq_mact_paired200.jsonl\"", mact_wtq)
            self.assertIn("--api-base http://127.0.0.1:8000/v1", mact_wtq)

            compare_script = (paired_run / "run_eval_and_compare.sh").read_text(encoding="utf-8")
            self.assertIn("code/compare_blind_results.py", compare_script)
            self.assertIn("--output \"$PAIRED_RUN_DIR/paired200_summary.json\"", compare_script)

            assert_checkpoint_script_stages_ignored_run_dir(
                self,
                mact_root,
                paired_run,
                "mact/wtq_mact_paired200.jsonl",
            )

    def test_cli_prepares_api_paired200_run_without_writing_secret(self):
        """Catches external paired-200 scripts that leak API key values into MACT outputs."""
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            myagent_root = tmp / "MyAgent"
            mact_root = tmp / "MACT"
            gate_run = mact_root / "outputs" / "server_runs" / "api_gate50_20260730"
            paired_run = mact_root / "outputs" / "server_runs" / "api_paired200_20260731"
            myagent_root.mkdir()
            (gate_run / "api.env").parent.mkdir(parents=True)
            (gate_run / "api.env").write_text(
                "\n".join(
                    [
                        "export API_PROVIDER=OpenRouter",
                        "export API_BASE_URL=https://openrouter.ai/api/v1",
                        "export SERVED_MODEL_NAME=qwen/qwen3-32b",
                        "export API_KEY_ENV=OPENROUTER_API_KEY",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            write_json(
                gate_run / "gate_run_manifest.json",
                {
                    "backend": "api",
                    "model_tag": "openrouter_qwen3",
                    "served_model_name": "qwen/qwen3-32b",
                    "api_key_env": "OPENROUTER_API_KEY",
                    "endpoints": ["https://openrouter.ai/api/v1"],
                },
            )
            write_gate150_paired200_summary(gate_run)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "server" / "prepare_paired200_run.py"),
                    "--myagent-root",
                    str(myagent_root),
                    "--mact-root",
                    str(mact_root),
                    "--gate-run-dir",
                    str(gate_run),
                    "--run-dir",
                    str(paired_run),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            myagent_script = (paired_run / "run_myagent_paired200.sh").read_text(encoding="utf-8")
            mact_script = (paired_run / "run_mact_wtq_paired200.sh").read_text(encoding="utf-8")
            healthcheck_script = paired_run / "healthcheck_services.sh"
            assert_executable_when_supported(self, healthcheck_script)
            assert_bash_syntax_when_supported(healthcheck_script)
            healthcheck_text = healthcheck_script.read_text(encoding="utf-8")
            self.assertIn('source "$SOURCE_GATE_RUN_DIR/api.env"', healthcheck_text)
            self.assertIn("healthcheck_openai_compatible.py", healthcheck_text)
            self.assertIn('--api-base-url "$API_BASE_URL"', healthcheck_text)
            self.assertIn('--model "$SERVED_MODEL_NAME"', healthcheck_text)
            self.assertIn('--api-key-env "$API_KEY_ENV"', healthcheck_text)
            self.assertIn('source "$SOURCE_GATE_RUN_DIR/api.env"', myagent_script)
            self.assertIn('--endpoints "$API_BASE_URL"', myagent_script)
            self.assertIn('--api-key-env "$API_KEY_ENV"', myagent_script)
            self.assertIn("--api-base \"$API_BASE_URL\"", mact_script)
            self.assertIn("--api-key-env \"$API_KEY_ENV\"", mact_script)
            readme = (paired_run / "README.md").read_text(encoding="utf-8")
            self.assertLess(readme.index("healthcheck_services.sh"), readme.index("run_myagent_paired200.sh"))

            all_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in paired_run.iterdir()
                if path.is_file() and path.suffix != ".json"
            )
            self.assertIn("OPENROUTER_API_KEY", all_text)
            self.assertNotIn("sk-", all_text)
            self.assertNotIn("real-key", all_text)

    def test_cli_rejects_paired200_run_when_gate150_decision_is_not_paired200(self):
        """Catches silently expanding no-go Gate-150 candidates into expensive paired-200 runs."""
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            myagent_root = tmp / "MyAgent"
            mact_root = tmp / "MACT"
            gate_run = mact_root / "outputs" / "server_runs" / "weak_gate50_20260730"
            paired_run = mact_root / "outputs" / "server_runs" / "weak_paired200_20260731"
            myagent_root.mkdir()
            (gate_run / "vllm.env").parent.mkdir(parents=True)
            (gate_run / "vllm.env").write_text(
                "export SERVED_MODEL_NAME=weak-local\nexport LOCAL_VLLM_API_KEY=local-placeholder\n",
                encoding="utf-8",
            )
            write_json(
                gate_run / "gate_run_manifest.json",
                {
                    "backend": "local-vllm",
                    "model_tag": "weak_model",
                    "served_model_name": "weak-local",
                    "endpoints": ["http://127.0.0.1:8000/v1"],
                },
            )
            write_json(
                gate_run / "gate150_summary.json",
                {
                    "gate_name": "gate150",
                    "decision": "no-go",
                    "decision_reasons": ["datasets_at_reference_below_threshold"],
                },
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "server" / "prepare_paired200_run.py"),
                    "--myagent-root",
                    str(myagent_root),
                    "--mact-root",
                    str(mact_root),
                    "--gate-run-dir",
                    str(gate_run),
                    "--run-dir",
                    str(paired_run),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("Gate-150 decision must be paired200", completed.stderr)
            self.assertFalse(paired_run.exists())


if __name__ == "__main__":
    unittest.main()
