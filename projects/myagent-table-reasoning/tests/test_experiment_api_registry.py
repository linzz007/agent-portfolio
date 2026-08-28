from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "server"))


class ExperimentApiRegistryTests(unittest.TestCase):
    def test_api_provider_registry_is_shared_by_audit_and_gate_prep(self):
        """Catches audit and gate-prep drift on API key names or tested provider defaults."""
        from experiment_api_registry import API_KEY_NAMES, api_provider_defaults, provider_profiles_for_api_keys
        import audit_qwen3_experiment_state
        import prepare_model_gate_run

        self.assertIs(audit_qwen3_experiment_state.API_KEY_NAMES, API_KEY_NAMES)
        self.assertIs(prepare_model_gate_run.api_provider_defaults, api_provider_defaults)

        self.assertEqual(
            api_provider_defaults("OpenRouter"),
            {
                "provider": "OpenRouter",
                "api_base_url": "https://openrouter.ai/api/v1",
                "api_key_env": "OPENROUTER_API_KEY",
            },
        )
        self.assertEqual(
            provider_profiles_for_api_keys(["OPENROUTER_API_KEY"]),
            {
                "OpenRouter": {
                    "api_base_url": "https://openrouter.ai/api/v1",
                    "api_key_env": "OPENROUTER_API_KEY",
                    "prepare_model_gate_run_args": ["--backend", "api", "--api-provider", "OpenRouter"],
                    "requires_model_name": True,
                }
            },
        )


if __name__ == "__main__":
    unittest.main()
