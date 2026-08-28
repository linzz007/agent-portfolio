from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "server"))


class ExperimentModelRegistryTests(unittest.TestCase):
    def test_known_tested_model_registry_is_shared_by_audit_and_gate_prep(self):
        """Catches audit and gate-prep drift on which local models are already tested."""
        from experiment_model_registry import KNOWN_TESTED_LOCAL_MODELS, known_tested_model_key
        import audit_qwen3_experiment_state
        import prepare_model_gate_run

        self.assertIs(audit_qwen3_experiment_state.KNOWN_TESTED_LOCAL_MODELS, KNOWN_TESTED_LOCAL_MODELS)
        self.assertIs(prepare_model_gate_run.KNOWN_TESTED_LOCAL_MODELS, KNOWN_TESTED_LOCAL_MODELS)

        for model_name in KNOWN_TESTED_LOCAL_MODELS:
            self.assertIsNotNone(known_tested_model_key(model_name), model_name)

        self.assertEqual(known_tested_model_key("qwen25_14b_awq"), "qwen2514bawq")
        self.assertIsNone(known_tested_model_key("DeepSeek-R1-Distill-Qwen-32B"))


if __name__ == "__main__":
    unittest.main()
