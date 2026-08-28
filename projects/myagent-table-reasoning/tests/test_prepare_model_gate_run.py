from pathlib import Path
import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "server"))

from prepare_model_gate_run import GateRunConfig, prepare_gate_run, safe_slug  # noqa: E402


def assert_executable_when_supported(testcase: unittest.TestCase, path: Path) -> None:
    testcase.assertTrue(path.exists())
    if os.name != "nt":
        testcase.assertTrue(path.stat().st_mode & stat.S_IXUSR)


def assert_bash_syntax_when_supported(path: Path) -> None:
    if os.name != "nt":
        subprocess.run(["bash", "-n", str(path)], check=True)


def read_export_values(path: Path, names: list[str]) -> list[str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("export ") or "=" not in line:
            continue
        key, raw_value = line[len("export ") :].split("=", 1)
        parsed = shlex.split(raw_value)
        values[key] = parsed[0] if parsed else ""
    return [values.get(name, "") for name in names]


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


class PrepareModelGateRunTests(unittest.TestCase):
    def test_safe_slug_keeps_model_tags_path_safe(self):
        self.assertEqual(safe_slug("DeepSeek/R1 Distill Qwen-32B"), "DeepSeek_R1_Distill_Qwen-32B")
        self.assertEqual(safe_slug(""), "model")

    def test_prepare_gate_run_writes_executable_scripts_and_manifest(self):
        """Catches scaffold drift that would put new experiments outside MACT or use the wrong gates."""
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            myagent_root = tmp / "MyAgent"
            mact_root = tmp / "MACT"
            model_dir = tmp / "models" / "DeepSeek-R1-Distill-Qwen-32B"
            run_dir = mact_root / "outputs" / "server_runs" / "deepseek_r1_qwen32b_gate50_20260730_193500"
            model_dir.mkdir(parents=True)
            myagent_root.mkdir()

            manifest = prepare_gate_run(
                GateRunConfig(
                    myagent_root=myagent_root,
                    mact_root=mact_root,
                    model_id=model_dir,
                    model_tag="deepseek_r1_qwen32b",
                    served_model_name="deepseek-r1-qwen32b-local",
                    run_dir=run_dir,
                )
            )

            manifest_path = run_dir / "gate_run_manifest.json"
            manifest_json = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["run_dir"], str(run_dir))
            self.assertEqual(manifest_json["model_tag"], "deepseek_r1_qwen32b")
            self.assertEqual(manifest_json["gpu_groups"], "0,1;2,3")
            self.assertEqual(
                manifest_json["endpoints"],
                ["http://127.0.0.1:8000/v1", "http://127.0.0.1:8001/v1"],
            )
            self.assertEqual(manifest_json["gate_limits"], {"gate10": 10, "gate50": 50, "gate150": 150})

            env_values = read_export_values(run_dir / "vllm.env", ["MODEL_ID", "GPU_GROUPS", "SERVED_MODEL_NAME"])
            self.assertEqual(env_values, [str(model_dir), "0,1;2,3", "deepseek-r1-qwen32b-local"])

            gate10 = run_dir / "run_gate10.sh"
            gate50 = run_dir / "run_gate50.sh"
            gate150 = run_dir / "run_gate150.sh"
            gate10_text = gate10.read_text(encoding="utf-8")
            gate50_text = gate50.read_text(encoding="utf-8")
            gate150_text = gate150.read_text(encoding="utf-8")
            self.assertIn("--limit-per-task 10", gate10_text)
            self.assertIn("--limit-per-task 50", gate50_text)
            self.assertIn("--limit-per-task 150", gate150_text)
            self.assertIn("--output-root \"$RUN_DIR/myagent_gate10\"", gate10_text)
            self.assertIn("--output-root \"$RUN_DIR/myagent_gate50\"", gate50_text)
            self.assertIn("--output-root \"$RUN_DIR/myagent_gate150\"", gate150_text)
            self.assertIn("summarize_model_gate_results.py", gate10_text)
            self.assertIn("--gate-name gate10", gate10_text)
            self.assertIn("--output \"$RUN_DIR/gate10_summary.json\"", gate10_text)
            self.assertIn("--markdown-output \"$RUN_DIR/gate10_summary.md\"", gate10_text)
            self.assertIn("gate10_summary.json", gate50_text)
            self.assertIn("Gate-10 decision must be gate50", gate50_text)
            self.assertIn("http://127.0.0.1:8000/v1,http://127.0.0.1:8001/v1", gate50_text)
            self.assertIn("summarize_model_gate_results.py", gate50_text)
            self.assertIn("--output \"$RUN_DIR/gate50_summary.json\"", gate50_text)
            self.assertIn("--markdown-output \"$RUN_DIR/gate50_summary.md\"", gate50_text)
            self.assertIn("gate50_summary.json", gate150_text)
            self.assertIn("Gate-50 decision must be gate150", gate150_text)
            self.assertIn("summarize_model_gate_results.py", gate150_text)
            self.assertIn("--gate-name gate150", gate150_text)
            self.assertIn("--output \"$RUN_DIR/gate150_summary.json\"", gate150_text)
            self.assertIn("--markdown-output \"$RUN_DIR/gate150_summary.md\"", gate150_text)

            for script_name in (
                "start_services.sh",
                "healthcheck_services.sh",
                "run_gate10.sh",
                "run_gate50.sh",
                "run_gate150.sh",
                "stop_services.sh",
            ):
                script_path = run_dir / script_name
                assert_executable_when_supported(self, script_path)
                assert_bash_syntax_when_supported(script_path)

            readme = (run_dir / "README.md").read_text(encoding="utf-8")
            self.assertIn("Do not commit API keys", readme)
            self.assertIn("gate10_summary.json", readme)
            self.assertIn("gate50_summary.json", readme)
            self.assertIn("gate150_summary.json", readme)
            self.assertIn("run_gate150.sh", readme)
            self.assertIn("git add -f", readme)

            assert_checkpoint_script_stages_ignored_run_dir(
                self,
                mact_root,
                run_dir,
                "myagent_gate10/merged/wtq_deepseek-r1-qwen32b-local.jsonl",
            )

    def test_prepare_gate_run_rejects_known_tested_local_model_by_default(self):
        """Catches accidentally spending GPU time on a local model already ruled in/out by prior gates."""
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            myagent_root = tmp / "MyAgent"
            mact_root = tmp / "MACT"
            model_dir = tmp / "models" / "Qwen3-14B-AWQ"
            run_dir = mact_root / "outputs" / "server_runs" / "qwen3_14b_repeat_gate50"
            model_dir.mkdir(parents=True)
            myagent_root.mkdir()

            with self.assertRaisesRegex(ValueError, "known tested local model"):
                prepare_gate_run(
                    GateRunConfig(
                        myagent_root=myagent_root,
                        mact_root=mact_root,
                        model_id=model_dir,
                        model_tag="qwen3_14b_awq",
                        served_model_name="qwen3-14b-awq-local",
                        run_dir=run_dir,
                    )
                )

            self.assertFalse(run_dir.exists())

    def test_prepare_gate_run_allows_known_tested_model_with_explicit_override(self):
        """Catches override runs that are indistinguishable from fresh model screening."""
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            myagent_root = tmp / "MyAgent"
            mact_root = tmp / "MACT"
            model_dir = tmp / "models" / "Qwen3-14B-AWQ"
            run_dir = mact_root / "outputs" / "server_runs" / "qwen3_14b_repeat_gate50"
            model_dir.mkdir(parents=True)
            myagent_root.mkdir()

            manifest = prepare_gate_run(
                GateRunConfig(
                    myagent_root=myagent_root,
                    mact_root=mact_root,
                    model_id=model_dir,
                    model_tag="qwen3_14b_awq",
                    served_model_name="qwen3-14b-awq-local",
                    run_dir=run_dir,
                    allow_known_tested_model=True,
                )
            )

            manifest_json = json.loads((run_dir / "gate_run_manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["known_tested_model_override"])
            self.assertTrue(manifest_json["known_tested_model_override"])

    def test_cli_prepares_external_api_gate_run_without_writing_secret(self):
        """Catches external API candidates that require manual, non-recoverable script edits."""
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            myagent_root = tmp / "MyAgent"
            mact_root = tmp / "MACT"
            run_dir = mact_root / "outputs" / "server_runs" / "openrouter_qwen3_gate50_20260730_210000"
            myagent_root.mkdir()

            completed = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "server" / "prepare_model_gate_run.py"),
                    "--backend",
                    "api",
                    "--myagent-root",
                    str(myagent_root),
                    "--mact-root",
                    str(mact_root),
                    "--model-tag",
                    "openrouter_qwen3",
                    "--served-model-name",
                    "qwen/qwen3-32b",
                    "--run-dir",
                    str(run_dir),
                    "--api-provider",
                    "OpenRouter",
                    "--api-base-url",
                    "https://openrouter.ai/api/v1",
                    "--api-key-env",
                    "OPENROUTER_API_KEY",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            manifest = json.loads((run_dir / "gate_run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["backend"], "api")
            self.assertEqual(manifest["api_provider"], "OpenRouter")
            self.assertEqual(manifest["endpoints"], ["https://openrouter.ai/api/v1"])
            self.assertEqual(manifest["api_key_env"], "OPENROUTER_API_KEY")
            self.assertFalse((run_dir / "vllm.env").exists())
            self.assertTrue((run_dir / "api.env").exists())
            self.assertTrue((run_dir / "api_profile.md").exists())

            api_env = (run_dir / "api.env").read_text(encoding="utf-8")
            self.assertIn("export API_BASE_URL=https://openrouter.ai/api/v1", api_env)
            self.assertIn("export API_KEY_ENV=OPENROUTER_API_KEY", api_env)
            self.assertNotIn("present", api_env)

            gate10 = run_dir / "run_gate10.sh"
            gate50 = run_dir / "run_gate50.sh"
            healthcheck = run_dir / "healthcheck_services.sh"
            gate10_text = gate10.read_text(encoding="utf-8")
            gate50_text = gate50.read_text(encoding="utf-8")
            healthcheck_text = healthcheck.read_text(encoding="utf-8")
            self.assertIn("healthcheck_openai_compatible.py", healthcheck_text)
            self.assertIn('--api-base-url "$API_BASE_URL"', healthcheck_text)
            self.assertIn('--model "$SERVED_MODEL_NAME"', healthcheck_text)
            self.assertIn('--api-key-env "$API_KEY_ENV"', healthcheck_text)
            self.assertIn('source "$RUN_DIR/api.env"', gate10_text)
            self.assertIn('--endpoints "$API_BASE_URL"', gate10_text)
            self.assertIn('--api-key-env "$API_KEY_ENV"', gate10_text)
            self.assertIn("summarize_model_gate_results.py", gate10_text)
            self.assertIn("--gate-name gate10", gate10_text)
            self.assertIn("gate10_summary.json", gate50_text)
            self.assertIn("Gate-10 decision must be gate50", gate50_text)
            self.assertIn("summarize_model_gate_results.py", gate50_text)
            self.assertTrue((run_dir / "run_gate150.sh").exists())
            gate150_text = (run_dir / "run_gate150.sh").read_text(encoding="utf-8")
            self.assertIn('--output-root "$RUN_DIR/myagent_gate150"', gate150_text)
            self.assertIn("gate50_summary.json", gate150_text)
            self.assertIn("Gate-50 decision must be gate150", gate150_text)
            self.assertIn("summarize_model_gate_results.py", gate150_text)
            self.assertIn("--gate-name gate150", gate150_text)
            self.assertIn("--output \"$RUN_DIR/gate150_summary.json\"", gate150_text)

            for script_name in (
                "start_services.sh",
                "healthcheck_services.sh",
                "run_gate10.sh",
                "run_gate50.sh",
                "run_gate150.sh",
                "stop_services.sh",
            ):
                script_path = run_dir / script_name
                assert_executable_when_supported(self, script_path)
                assert_bash_syntax_when_supported(script_path)

            all_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in run_dir.iterdir()
                if path.is_file() and path.suffix != ".json"
            )
            self.assertNotIn("sk-", all_text)
            self.assertIn("OPENROUTER_API_KEY", all_text)

    def test_cli_prepares_external_api_gate_run_with_provider_defaults(self):
        """Catches hand-filled API base URL/key env mistakes for known OpenAI-compatible providers."""
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            myagent_root = tmp / "MyAgent"
            mact_root = tmp / "MACT"
            run_dir = mact_root / "outputs" / "server_runs" / "openrouter_qwen3_default_gate50"
            myagent_root.mkdir()

            completed = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "server" / "prepare_model_gate_run.py"),
                    "--backend",
                    "api",
                    "--myagent-root",
                    str(myagent_root),
                    "--mact-root",
                    str(mact_root),
                    "--model-tag",
                    "openrouter_qwen3",
                    "--model-name",
                    "qwen/qwen3-32b",
                    "--run-dir",
                    str(run_dir),
                    "--api-provider",
                    "OpenRouter",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            manifest = json.loads((run_dir / "gate_run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["api_base_url"], "https://openrouter.ai/api/v1")
            self.assertEqual(manifest["api_key_env"], "OPENROUTER_API_KEY")
            self.assertEqual(manifest["served_model_name"], "qwen/qwen3-32b")

            api_env = (run_dir / "api.env").read_text(encoding="utf-8")
            self.assertIn("export API_BASE_URL=https://openrouter.ai/api/v1", api_env)
            self.assertIn("export SERVED_MODEL_NAME=qwen/qwen3-32b", api_env)
            self.assertIn("export API_KEY_ENV=OPENROUTER_API_KEY", api_env)

    def test_cli_prepares_api_gate_run_from_readiness_audit_provider_profile(self):
        """Catches manually retyping provider defaults already present in readiness audit."""
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            myagent_root = tmp / "MyAgent"
            mact_root = tmp / "MACT"
            run_dir = mact_root / "outputs" / "server_runs" / "openrouter_from_audit_gate50"
            readiness_audit = tmp / "latest_experiment_readiness_audit.json"
            myagent_root.mkdir()
            readiness_audit.write_text(
                json.dumps(
                    {
                        "model_readiness": {
                            "api_provider_profiles": {
                                "OpenRouter": {
                                    "api_base_url": "https://openrouter.ai/api/v1",
                                    "api_key_env": "OPENROUTER_API_KEY",
                                    "requires_model_name": True,
                                }
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "server" / "prepare_model_gate_run.py"),
                    "--backend",
                    "api",
                    "--myagent-root",
                    str(myagent_root),
                    "--mact-root",
                    str(mact_root),
                    "--readiness-audit",
                    str(readiness_audit),
                    "--model-name",
                    "qwen/qwen3-32b",
                    "--run-dir",
                    str(run_dir),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            manifest = json.loads((run_dir / "gate_run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["backend"], "api")
            self.assertEqual(manifest["api_provider"], "OpenRouter")
            self.assertEqual(manifest["api_base_url"], "https://openrouter.ai/api/v1")
            self.assertEqual(manifest["api_key_env"], "OPENROUTER_API_KEY")
            self.assertEqual(manifest["served_model_name"], "qwen/qwen3-32b")
            self.assertEqual(manifest["readiness_audit_path"], str(readiness_audit.resolve()))

    def test_cli_explicit_api_provider_with_readiness_audit_uses_defaults_when_profile_absent(self):
        """Catches readiness metadata becoming mandatory after the user passes a tested provider."""
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            myagent_root = tmp / "MyAgent"
            mact_root = tmp / "MACT"
            run_dir = mact_root / "outputs" / "server_runs" / "openrouter_explicit_with_audit_gate50"
            readiness_audit = tmp / "latest_experiment_readiness_audit.json"
            myagent_root.mkdir()
            readiness_audit.write_text(
                json.dumps({"model_readiness": {"api_provider_profiles": {}}}),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "server" / "prepare_model_gate_run.py"),
                    "--backend",
                    "api",
                    "--myagent-root",
                    str(myagent_root),
                    "--mact-root",
                    str(mact_root),
                    "--readiness-audit",
                    str(readiness_audit),
                    "--api-provider",
                    "OpenRouter",
                    "--model-name",
                    "qwen/qwen3-32b",
                    "--run-dir",
                    str(run_dir),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            manifest = json.loads((run_dir / "gate_run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["api_provider"], "OpenRouter")
            self.assertEqual(manifest["api_base_url"], "https://openrouter.ai/api/v1")
            self.assertEqual(manifest["api_key_env"], "OPENROUTER_API_KEY")

    def test_cli_unknown_api_provider_without_endpoint_fails_without_traceback(self):
        """Catches confusing Python tracebacks when an API provider has no tested defaults."""
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            myagent_root = tmp / "MyAgent"
            mact_root = tmp / "MACT"
            myagent_root.mkdir()

            completed = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "server" / "prepare_model_gate_run.py"),
                    "--backend",
                    "api",
                    "--myagent-root",
                    str(myagent_root),
                    "--mact-root",
                    str(mact_root),
                    "--model-tag",
                    "custom_provider_qwen3",
                    "--model-name",
                    "provider/qwen3",
                    "--api-provider",
                    "CustomProvider",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertNotIn("Traceback", completed.stderr)
            self.assertIn("--api-base-url", completed.stderr)
            self.assertIn("--api-key-env", completed.stderr)

    def test_cli_prepares_local_gate_run_from_readiness_audit(self):
        """Catches manually copying model paths from readiness audit into the Gate prep command."""
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            myagent_root = tmp / "MyAgent"
            mact_root = tmp / "MACT"
            model_dir = tmp / "mounted" / "vendor" / "DeepSeek-R1-Distill-Qwen-32B"
            run_dir = mact_root / "outputs" / "server_runs" / "deepseek_auto_gate50"
            readiness_audit = tmp / "latest_experiment_readiness_audit.json"
            myagent_root.mkdir()
            model_dir.mkdir(parents=True)
            (model_dir / "config.json").write_text("{}", encoding="utf-8")
            readiness_audit.write_text(
                json.dumps(
                    {
                        "model_readiness": {
                            "untested_local_model_paths": {
                                "DeepSeek-R1-Distill-Qwen-32B": [str(model_dir)],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "server" / "prepare_model_gate_run.py"),
                    "--myagent-root",
                    str(myagent_root),
                    "--mact-root",
                    str(mact_root),
                    "--readiness-audit",
                    str(readiness_audit),
                    "--model-name",
                    "DeepSeek-R1-Distill-Qwen-32B",
                    "--run-dir",
                    str(run_dir),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            manifest = json.loads((run_dir / "gate_run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["model_id"], str(model_dir))
            self.assertEqual(manifest["model_tag"], "DeepSeek-R1-Distill-Qwen-32B")
            self.assertEqual(manifest["served_model_name"], "deepseek-r1-distill-qwen-32b-local")
            self.assertEqual(manifest["readiness_audit_path"], str(readiness_audit.resolve()))

    def test_cli_multiple_readiness_models_without_model_name_fails_without_traceback(self):
        """Catches confusing Python tracebacks when readiness audit has multiple local candidates."""
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            myagent_root = tmp / "MyAgent"
            mact_root = tmp / "MACT"
            readiness_audit = tmp / "latest_experiment_readiness_audit.json"
            myagent_root.mkdir()
            readiness_audit.write_text(
                json.dumps(
                    {
                        "model_readiness": {
                            "untested_local_model_paths": {
                                "DeepSeek-R1-Distill-Qwen-32B": [str(tmp / "deepseek")],
                                "Mistral-7B-Instruct": [str(tmp / "mistral")],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "server" / "prepare_model_gate_run.py"),
                    "--myagent-root",
                    str(myagent_root),
                    "--mact-root",
                    str(mact_root),
                    "--readiness-audit",
                    str(readiness_audit),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertNotIn("Traceback", completed.stderr)
            self.assertIn("--model-name", completed.stderr)
            self.assertIn("DeepSeek-R1-Distill-Qwen-32B", completed.stderr)
            self.assertIn("Mistral-7B-Instruct", completed.stderr)


if __name__ == "__main__":
    unittest.main()
