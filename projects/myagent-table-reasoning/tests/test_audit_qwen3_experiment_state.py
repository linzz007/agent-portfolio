from pathlib import Path
import json
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "server"))

from audit_qwen3_experiment_state import build_audit, render_expert_summary  # noqa: E402


FULL200_RUN = "qwen3_32b_blind200_mact_full200_20260723"
CRT_CURRENT_RUN = "qwen3_32b_crt_full200_current_20260730_1822"
WTQ_REP_RUN = "qwen3_32b_wtq_extreme_fix_representative100_20260730_1805"


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class AuditQwen3ExperimentStateTests(unittest.TestCase):
    def test_build_audit_marks_stage_evidence_and_blocks_duplicate_runs(self):
        """Catches wrong acceptance arithmetic or treating known no-go models as new."""
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            myagent_root = tmp / "MyAgent"
            mact_root = tmp / "MACT"
            model_root = tmp / "models"
            for model_name in (
                "Qwen3-32B",
                "Qwen3-14B-AWQ",
                "Qwen2.5-14B-Instruct-AWQ",
                "Qwen2.5-3B-Instruct",
            ):
                (model_root / model_name).mkdir(parents=True)
                (model_root / model_name / "config.json").write_text("{}", encoding="utf-8")

            write_json(
                mact_root
                / "outputs"
                / "server_runs"
                / FULL200_RUN
                / "overall_mact_full200_summary.json",
                {
                    "datasets": {
                        "wtq": {
                            "myagent": {"correct": 131, "num_samples": 200},
                            "mact": {"correct": 148, "num_samples": 200},
                        },
                        "tabfact": {
                            "myagent": {"correct": 185, "num_samples": 200},
                            "mact": {"correct": 189, "num_samples": 200},
                        },
                        "crt": {
                            "myagent": {"correct": 137, "num_samples": 200},
                            "mact": {"correct": 113, "num_samples": 200},
                        },
                    },
                    "overall": {
                        "myagent": {
                            "correct": 453,
                            "num_samples": 600,
                            "primary_accuracy": 0.755,
                            "avg_total_tokens": 6497.355,
                            "num_failed_exec": 0,
                            "num_missing_answer": 0,
                        },
                        "mact": {
                            "correct": 450,
                            "num_samples": 600,
                            "primary_accuracy": 0.75,
                            "avg_total_tokens": 11382.946666666667,
                            "num_failed_exec": 5,
                            "num_missing_answer": 5,
                        },
                    },
                    "token_ratio_myagent_to_mact": 0.5707972803761415,
                    "acceptance_criteria": {
                        "overall_accuracy_at_least_mact": True,
                        "at_least_two_datasets_at_least_mact": False,
                        "token_ratio_at_most_0_75": True,
                        "execution_failure_rate_at_most_0_02": True,
                    },
                },
            )
            write_json(
                mact_root
                / "outputs"
                / "server_runs"
                / CRT_CURRENT_RUN
                / "crt_full200_current_comparison.json",
                {
                    "new_myagent": {
                        "primary_accuracy": 0.7,
                        "avg_total_tokens": 10839.165,
                        "num_failed_exec": 0,
                        "num_missing_answer": 0,
                    },
                    "old_myagent": {"primary_accuracy": 0.685},
                    "mact": {"primary_accuracy": 0.565, "avg_total_tokens": 12809.985},
                    "overall_if_replacing_crt_only": {
                        "myagent_correct": 456,
                        "myagent_rows": 600,
                        "myagent_accuracy": 0.76,
                        "myagent_avg_total_tokens": 6497.66,
                        "mact_correct": 450,
                        "mact_rows": 600,
                        "mact_accuracy": 0.75,
                        "mact_avg_total_tokens": 11382.946666666667,
                        "token_ratio": 0.5708240748441236,
                    },
                },
            )
            write_json(
                mact_root
                / "outputs"
                / "server_runs"
                / WTQ_REP_RUN
                / "wtq_representative100_extreme_fix_comparison.json",
                {
                    "new_myagent": {
                        "primary_accuracy": 0.69,
                        "avg_total_tokens": 6094.77,
                        "num_failed_exec": 0,
                        "num_missing_answer": 0,
                    },
                    "old_myagent": {"primary_accuracy": 0.69},
                    "mact": {"primary_accuracy": 0.79},
                    "old_to_new_transitions": {
                        "old_wrong_new_correct": {"count": 3},
                        "old_correct_new_wrong": {"count": 3},
                    },
                    "token_ratios": {
                        "new_vs_mact": 0.5789501181691757,
                        "new_vs_old_myagent": 1.009121344177175,
                    },
                },
            )

            audit = build_audit(
                myagent_root=myagent_root,
                mact_root=mact_root,
                model_roots=[model_root],
                env={},
            )

        self.assertTrue(audit["canonical_full200"]["overall_accuracy_at_least_mact"])
        self.assertEqual(audit["canonical_full200"]["myagent_correct"], 453)
        self.assertEqual(audit["canonical_full200"]["mact_correct"], 450)
        self.assertEqual(audit["canonical_full200"]["datasets_myagent_at_least_mact"], 1)
        self.assertFalse(audit["canonical_full200"]["strict_acceptance"])
        self.assertTrue(audit["canonical_full200"]["token_ratio_at_most_0_75"])

        staged = audit["current_crt_staged_composite"]
        self.assertEqual(staged["myagent_correct"], 456)
        self.assertEqual(staged["mact_correct"], 450)
        self.assertTrue(staged["overall_accuracy_at_least_mact"])
        self.assertTrue(staged["token_ratio_at_most_0_75"])

        wtq = audit["wtq_representative100"]
        self.assertEqual(wtq["net_recovered_rows"], 0)
        self.assertEqual(wtq["decision"], "do_not_expand_wtq_extreme_only_fix")

        readiness = audit["model_readiness"]
        self.assertFalse(readiness["can_start_new_experiment"])
        self.assertEqual(readiness["untested_local_models"], [])
        self.assertEqual(readiness["api_keys_present"], [])
        self.assertEqual(readiness["next_action"], "wait_for_new_model_or_api_key")

    def test_build_audit_allows_gate_when_unseen_model_or_api_key_exists(self):
        """Catches readiness logic that blocks real future candidates."""
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            myagent_root = tmp / "MyAgent"
            mact_root = tmp / "MACT"
            model_root = tmp / "models"
            (model_root / "DeepSeek-R1-Distill-Qwen-32B").mkdir(parents=True)
            (model_root / "DeepSeek-R1-Distill-Qwen-32B" / "config.json").write_text(
                "{}",
                encoding="utf-8",
            )

            write_json(
                mact_root
                / "outputs"
                / "server_runs"
                / FULL200_RUN
                / "overall_mact_full200_summary.json",
                {
                    "datasets": {},
                    "overall": {
                        "myagent": {"correct": 453, "num_samples": 600, "primary_accuracy": 0.755},
                        "mact": {"correct": 450, "num_samples": 600, "primary_accuracy": 0.75},
                    },
                    "token_ratio_myagent_to_mact": 0.57,
                    "acceptance_criteria": {
                        "overall_accuracy_at_least_mact": True,
                        "at_least_two_datasets_at_least_mact": False,
                        "token_ratio_at_most_0_75": True,
                        "execution_failure_rate_at_most_0_02": True,
                    },
                },
            )
            write_json(
                mact_root
                / "outputs"
                / "server_runs"
                / CRT_CURRENT_RUN
                / "crt_full200_current_comparison.json",
                {
                    "overall_if_replacing_crt_only": {
                        "myagent_correct": 456,
                        "myagent_rows": 600,
                        "myagent_accuracy": 0.76,
                        "mact_correct": 450,
                        "mact_rows": 600,
                        "mact_accuracy": 0.75,
                        "token_ratio": 0.57,
                    }
                },
            )
            write_json(
                mact_root
                / "outputs"
                / "server_runs"
                / WTQ_REP_RUN
                / "wtq_representative100_extreme_fix_comparison.json",
                {
                    "new_myagent": {"primary_accuracy": 0.69},
                    "old_myagent": {"primary_accuracy": 0.69},
                    "mact": {"primary_accuracy": 0.79},
                    "old_to_new_transitions": {
                        "old_wrong_new_correct": {"count": 3},
                        "old_correct_new_wrong": {"count": 3},
                    },
                    "token_ratios": {},
                },
            )

            audit = build_audit(
                myagent_root=myagent_root,
                mact_root=mact_root,
                model_roots=[model_root],
                env={"OPENAI_API_KEY": "present"},
            )

        self.assertTrue(audit["model_readiness"]["can_start_new_experiment"])
        self.assertEqual(
            audit["model_readiness"]["untested_local_models"],
            ["DeepSeek-R1-Distill-Qwen-32B"],
        )
        self.assertEqual(audit["model_readiness"]["api_keys_present"], ["OPENAI_API_KEY"])
        self.assertEqual(audit["model_readiness"]["next_action"], "run_gate10_then_gate50")

    def test_build_audit_discovers_nested_unseen_model_directories(self):
        """Catches mounted/HF-cache models being missed because discovery only checks one directory level."""
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            myagent_root = tmp / "MyAgent"
            mact_root = tmp / "MACT"
            model_root = tmp / "mounted"
            nested_model = model_root / "vendor" / "DeepSeek-R1-Distill-Qwen-32B"
            nested_model.mkdir(parents=True)
            (nested_model / "config.json").write_text("{}", encoding="utf-8")

            audit = build_audit(
                myagent_root=myagent_root,
                mact_root=mact_root,
                model_roots=[model_root],
                env={},
            )

        self.assertTrue(audit["model_readiness"]["can_start_new_experiment"])
        self.assertEqual(
            audit["model_readiness"]["untested_local_models"],
            ["DeepSeek-R1-Distill-Qwen-32B"],
        )
        self.assertEqual(
            audit["model_readiness"]["untested_local_model_paths"],
            {"DeepSeek-R1-Distill-Qwen-32B": [str(nested_model)]},
        )

    def test_build_audit_treats_known_model_alias_directory_as_tested(self):
        """Catches known model aliases being rediscovered as fresh candidates after remounting models."""
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            myagent_root = tmp / "MyAgent"
            mact_root = tmp / "MACT"
            model_root = tmp / "models"
            (model_root / "qwen25_14b_awq").mkdir(parents=True)
            (model_root / "qwen25_14b_awq" / "config.json").write_text("{}", encoding="utf-8")

            audit = build_audit(
                myagent_root=myagent_root,
                mact_root=mact_root,
                model_roots=[model_root],
                env={},
            )

        self.assertEqual(audit["model_readiness"]["untested_local_models"], [])
        self.assertFalse(audit["model_readiness"]["can_start_new_experiment"])

    def test_build_audit_allows_gate_for_openai_compatible_provider_keys(self):
        """Catches missing OpenAI-compatible API providers in readiness detection."""
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            myagent_root = tmp / "MyAgent"
            mact_root = tmp / "MACT"
            model_root = tmp / "models"

            write_json(
                mact_root
                / "outputs"
                / "server_runs"
                / FULL200_RUN
                / "overall_mact_full200_summary.json",
                {
                    "datasets": {},
                    "overall": {
                        "myagent": {"correct": 453, "num_samples": 600, "primary_accuracy": 0.755},
                        "mact": {"correct": 450, "num_samples": 600, "primary_accuracy": 0.75},
                    },
                    "token_ratio_myagent_to_mact": 0.57,
                    "acceptance_criteria": {
                        "overall_accuracy_at_least_mact": True,
                        "at_least_two_datasets_at_least_mact": False,
                        "token_ratio_at_most_0_75": True,
                        "execution_failure_rate_at_most_0_02": True,
                    },
                },
            )

            audit = build_audit(
                myagent_root=myagent_root,
                mact_root=mact_root,
                model_roots=[model_root],
                env={
                    "FIREWORKS_API_KEY": "present",
                    "OPENROUTER_API_KEY": "present",
                    "TOGETHER_API_KEY": "present",
                },
            )

        self.assertTrue(audit["model_readiness"]["can_start_new_experiment"])
        self.assertEqual(
            audit["model_readiness"]["api_keys_present"],
            ["FIREWORKS_API_KEY", "OPENROUTER_API_KEY", "TOGETHER_API_KEY"],
        )
        self.assertEqual(
            audit["model_readiness"]["api_provider_profiles"],
            {
                "OpenRouter": {
                    "api_base_url": "https://openrouter.ai/api/v1",
                    "api_key_env": "OPENROUTER_API_KEY",
                    "prepare_model_gate_run_args": ["--backend", "api", "--api-provider", "OpenRouter"],
                    "requires_model_name": True,
                }
            },
        )
        self.assertEqual(audit["model_readiness"]["next_action"], "run_gate10_then_gate50")

    def test_build_audit_detects_api_key_names_from_env_files_without_leaking_values(self):
        """Catches readiness missing keys stored in a run-specific .env file."""
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            myagent_root = tmp / "MyAgent"
            mact_root = tmp / "MACT"
            model_root = tmp / "models"
            env_file = tmp / "api.env"
            env_file.write_text(
                "\n".join(
                    [
                        "# provider credentials for local smoke test",
                        "export OPENROUTER_API_KEY=sk-test-secret-value",
                        "TOGETHER_API_KEY=",
                    ]
                ),
                encoding="utf-8",
            )

            audit = build_audit(
                myagent_root=myagent_root,
                mact_root=mact_root,
                model_roots=[model_root],
                env={},
                env_files=[env_file],
            )

        self.assertTrue(audit["model_readiness"]["can_start_new_experiment"])
        self.assertEqual(audit["model_readiness"]["api_keys_present"], ["OPENROUTER_API_KEY"])
        self.assertIn("OpenRouter", audit["model_readiness"]["api_provider_profiles"])
        self.assertEqual(
            audit["model_readiness"]["api_env_files_checked"],
            [{"path": str(env_file), "present": True}],
        )
        self.assertNotIn("sk-test-secret-value", json.dumps(audit, ensure_ascii=False))

    def test_build_audit_scans_default_server_env_files_but_skips_examples_and_backups(self):
        """Catches the PRD recovery command missing API keys stored in standard env files."""
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            myagent_root = tmp / "MyAgent"
            mact_root = tmp / "MACT"
            model_root = tmp / "models"
            server_config = myagent_root / "configs" / "server"
            server_config.mkdir(parents=True)
            active_env = server_config / "api.env"
            active_env.write_text("OPENROUTER_API_KEY=sk-active-secret\n", encoding="utf-8")
            (server_config / "api.env.example").write_text("TOGETHER_API_KEY=sk-example-secret\n", encoding="utf-8")
            (server_config / "api.env.bak.20260730").write_text(
                "FIREWORKS_API_KEY=sk-backup-secret\n",
                encoding="utf-8",
            )

            audit = build_audit(
                myagent_root=myagent_root,
                mact_root=mact_root,
                model_roots=[model_root],
                env={},
            )

        readiness = audit["model_readiness"]
        self.assertTrue(readiness["can_start_new_experiment"])
        self.assertEqual(readiness["api_keys_present"], ["OPENROUTER_API_KEY"])
        self.assertEqual(readiness["api_env_files_checked"], [{"path": str(active_env), "present": True}])
        serialized = json.dumps(audit, ensure_ascii=False)
        self.assertNotIn("sk-active-secret", serialized)
        self.assertNotIn("sk-example-secret", serialized)
        self.assertNotIn("sk-backup-secret", serialized)

    def test_render_expert_summary_states_claims_limits_and_next_action(self):
        """Catches patent-facing summaries that overclaim or omit gating instructions."""
        audit = {
            "evidence_complete": True,
            "canonical_full200": {
                "myagent_correct": 453,
                "myagent_rows": 600,
                "myagent_accuracy": 0.755,
                "mact_correct": 450,
                "mact_rows": 600,
                "mact_accuracy": 0.75,
                "token_ratio": 0.5707972803761415,
                "datasets_myagent_at_least_mact": 1,
                "strict_acceptance": False,
            },
            "current_crt_staged_composite": {
                "present": True,
                "myagent_correct": 456,
                "myagent_rows": 600,
                "myagent_accuracy": 0.76,
                "mact_correct": 450,
                "mact_rows": 600,
                "mact_accuracy": 0.75,
                "token_ratio": 0.5708240748441236,
            },
            "wtq_representative100": {
                "new_myagent_accuracy": 0.69,
                "old_myagent_accuracy": 0.69,
                "mact_accuracy": 0.79,
                "recovered_rows": 3,
                "regressed_rows": 3,
                "net_recovered_rows": 0,
                "decision": "do_not_expand_wtq_extreme_only_fix",
            },
            "model_readiness": {
                "can_start_new_experiment": False,
                "untested_local_models": [],
                "api_keys_present": [],
                "next_action": "wait_for_new_model_or_api_key",
            },
        }

        summary = render_expert_summary(audit)

        self.assertIn("Qwen3-32B vs MACT 阶段证据摘要", summary)
        self.assertIn("canonical full200", summary)
        self.assertIn("453/600", summary)
        self.assertIn("450/600", summary)
        self.assertIn("57.1%", summary)
        self.assertIn("current CRT staged composite", summary)
        self.assertIn("456/600", summary)
        self.assertIn("不能写成三个数据集全部超过 MACT", summary)
        self.assertIn("WTQ representative100", summary)
        self.assertIn("恢复 3 条、回退 3 条、净收益 0 条", summary)
        self.assertIn("等待新增/挂载候选模型或提供外部 API key", summary)


if __name__ == "__main__":
    unittest.main()
