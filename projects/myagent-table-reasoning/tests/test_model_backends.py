import argparse
import os
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "code"))

from model_backends import (  # noqa: E402
    DEEPSEEK_API_BASE,
    add_model_backend_args,
    build_llm_fn,
    resolve_model_provider,
)


class FakeCompletions:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content="final answer")
        usage = SimpleNamespace(prompt_tokens=11, completion_tokens=3)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            usage=usage,
        )


class FakeOpenAIClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=FakeCompletions())


class RecordingClientFactory:
    def __init__(self):
        self.calls = []
        self.client = FakeOpenAIClient()

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.client


def make_args(**overrides):
    values = {
        "model_provider": "deepseek",
        "plan_model_name": "deepseek-v4-flash",
        "api_base": DEEPSEEK_API_BASE,
        "api_key_env": "DEEPSEEK_API_KEY",
        "thinking": "disabled",
        "temperature": 0.0,
        "max_tokens": 2048,
        "api_timeout": 120.0,
        "api_max_retries": 5,
        "model_path": "",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class DeepSeekBackendTests(unittest.TestCase):
    def test_deepseek_uses_official_api_and_selected_model(self):
        factory = RecordingClientFactory()
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=False):
            llm_fn = build_llm_fn(make_args(), openai_client_factory=factory)
            result = llm_fn("question")

        self.assertEqual(result, "final answer")
        self.assertEqual(
            llm_fn.snapshot(),
            {
                "request_count": 1,
                "prompt_tokens": 11,
                "completion_tokens": 3,
                "total_tokens": 14,
            },
        )
        self.assertEqual(
            factory.calls,
            [{
                "api_key": "<redacted>",
                "base_url": DEEPSEEK_API_BASE,
                "timeout": 120.0,
                "max_retries": 5,
            }],
        )
        request = factory.client.chat.completions.calls[0]
        self.assertEqual(request["model"], "deepseek-v4-flash")
        self.assertEqual(request["messages"], [{"role": "user", "content": "question"}])
        self.assertEqual(request["extra_body"], {"thinking": {"type": "disabled"}})
        self.assertEqual(request["temperature"], 0.0)
        self.assertEqual(request["max_tokens"], 2048)
        self.assertFalse(request["stream"])

    def test_deepseek_thinking_can_be_enabled_by_parameter(self):
        factory = RecordingClientFactory()
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=False):
            llm_fn = build_llm_fn(
                make_args(thinking="enabled"),
                openai_client_factory=factory,
            )
            llm_fn("question")

        request = factory.client.chat.completions.calls[0]
        self.assertEqual(request["extra_body"], {"thinking": {"type": "enabled"}})

    def test_deepseek_flash_alias_uses_supported_model_name(self):
        factory = RecordingClientFactory()
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=False):
            llm_fn = build_llm_fn(
                make_args(plan_model_name="deepseek-v4flash"),
                openai_client_factory=factory,
            )
            llm_fn("question")

        request = factory.client.chat.completions.calls[0]
        self.assertEqual(request["model"], "deepseek-v4-flash")

    def test_openai_compatible_provider_uses_custom_endpoint_and_key_variable(self):
        factory = RecordingClientFactory()
        args = make_args(
            model_provider="openai_compatible",
            plan_model_name="provider-model-name",
            api_base="https://provider.example/v1",
            api_key_env="PROVIDER_API_KEY",
        )
        with patch.dict(os.environ, {"PROVIDER_API_KEY": "provider-key"}, clear=False):
            llm_fn = build_llm_fn(args, openai_client_factory=factory)
            result = llm_fn("question")

        self.assertEqual(result, "final answer")
        self.assertEqual(
            factory.calls,
            [
                {
                    "api_key": "<redacted>",
                    "base_url": "https://provider.example/v1",
                    "timeout": 120.0,
                    "max_retries": 5,
                }
            ],
        )
        request = factory.client.chat.completions.calls[0]
        self.assertEqual(request["model"], "provider-model-name")
        self.assertNotIn("extra_body", request)

    def test_openai_compatible_qwen3_disables_thinking_by_default(self):
        factory = RecordingClientFactory()
        args = make_args(
            model_provider="openai_compatible",
            plan_model_name="qwen3-32b-local",
            api_base="http://127.0.0.1:8000/v1",
            api_key_env="LOCAL_VLLM_API_KEY",
        )
        with patch.dict(os.environ, {"LOCAL_VLLM_API_KEY": "EMPTY"}, clear=False):
            llm_fn = build_llm_fn(args, openai_client_factory=factory)
            llm_fn("question")

        request = factory.client.chat.completions.calls[0]
        self.assertEqual(
            request["extra_body"],
            {"chat_template_kwargs": {"enable_thinking": False}},
        )

    def test_openai_compatible_qwen3_can_enable_thinking_by_parameter(self):
        factory = RecordingClientFactory()
        args = make_args(
            model_provider="openai_compatible",
            plan_model_name="qwen3-32b-local",
            api_base="http://127.0.0.1:8000/v1",
            api_key_env="LOCAL_VLLM_API_KEY",
            thinking="enabled",
        )
        with patch.dict(os.environ, {"LOCAL_VLLM_API_KEY": "EMPTY"}, clear=False):
            llm_fn = build_llm_fn(args, openai_client_factory=factory)
            llm_fn("question")

        request = factory.client.chat.completions.calls[0]
        self.assertEqual(
            request["extra_body"],
            {"chat_template_kwargs": {"enable_thinking": True}},
        )

    def test_openai_compatible_complete_can_override_max_tokens_per_call(self):
        factory = RecordingClientFactory()
        args = make_args(
            model_provider="openai_compatible",
            plan_model_name="qwen3-32b-local",
            api_base="http://127.0.0.1:8000/v1",
            api_key_env="LOCAL_VLLM_API_KEY",
            max_tokens=2048,
        )
        with patch.dict(os.environ, {"LOCAL_VLLM_API_KEY": "EMPTY"}, clear=False):
            llm_fn = build_llm_fn(args, openai_client_factory=factory)
            llm_fn.complete("verify", temperature=0.0, max_tokens=512)
            llm_fn("normal")

        requests = factory.client.chat.completions.calls
        self.assertEqual(requests[0]["max_tokens"], 512)
        self.assertEqual(requests[1]["max_tokens"], 2048)
        self.assertEqual(llm_fn.snapshot()["request_count"], 2)

    def test_missing_api_key_fails_before_any_request(self):
        factory = RecordingClientFactory()
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "DEEPSEEK_API_KEY"):
                build_llm_fn(make_args(), openai_client_factory=factory)

        self.assertEqual(factory.calls, [])

    def test_auto_provider_preserves_existing_model_selection(self):
        self.assertEqual(resolve_model_provider(make_args(model_provider="auto")), "deepseek")
        self.assertEqual(
            resolve_model_provider(make_args(model_provider="auto", plan_model_name="gpt-4o")),
            "azure",
        )
        self.assertEqual(
            resolve_model_provider(make_args(model_provider="auto", plan_model_name="Qwen2.5")),
            "local",
        )

    def test_shared_arguments_have_reproducible_defaults(self):
        parser = argparse.ArgumentParser()
        add_model_backend_args(parser)
        args = parser.parse_args([])

        self.assertEqual(args.model_provider, "auto")
        self.assertEqual(args.api_base, DEEPSEEK_API_BASE)
        self.assertEqual(args.api_key_env, "DEEPSEEK_API_KEY")
        self.assertEqual(args.thinking, "disabled")
        self.assertEqual(args.temperature, 0.0)
        self.assertEqual(args.max_tokens, 2048)
        self.assertEqual(args.api_max_retries, 5)


class EntryPointTests(unittest.TestCase):
    def test_experiment_entry_points_expose_backend_flags_without_local_llm_imports(self):
        expected_flags = (
            "--model_provider",
            "--api_base",
            "--api_key_env",
            "--thinking",
            "--output_path",
        )
        for script_name in ("run_wtq_myagent.py", "tqa.py"):
            with self.subTest(script=script_name):
                result = subprocess.run(
                    [sys.executable, str(PROJECT_ROOT / "code" / script_name), "--help"],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                stdout = result.stdout.decode("ascii", errors="ignore")
                stderr = result.stderr.decode("ascii", errors="ignore")
                self.assertEqual(result.returncode, 0, msg=stderr)
                for flag in expected_flags:
                    self.assertIn(flag, stdout)


if __name__ == "__main__":
    unittest.main()
