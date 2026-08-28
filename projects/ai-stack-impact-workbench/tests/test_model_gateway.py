from __future__ import annotations

import pytest

from policy_impact.harness.model_config import DEFAULT_MODEL_ID
from policy_impact.harness.model_gateway import (
    AnthropicCompatibleModelAdapter,
    ModelInvocationError,
)


def test_max_token_retry_discards_truncated_response() -> None:
    adapter = AnthropicCompatibleModelAdapter(
        model_id="deepseek-v4-flash",
        base_url="https://example.invalid",
        api_key="<redacted>",
        max_attempts=2,
    )
    requests: list[list[dict[str, str]]] = []

    def fake_request(_system: str, messages: list[dict[str, str]]) -> dict:
        requests.append([dict(item) for item in messages])
        if len(requests) == 1:
            return {
                "id": "truncated",
                "stop_reason": "max_tokens",
                "content": [{"type": "text", "text": "x" * 5000}],
            }
        return {
            "id": "recovered",
            "stop_reason": "end_turn",
            "content": [
                {
                    "type": "text",
                    "text": '{"type":"general_chat","output":{"answer":"已恢复"}}',
                }
            ],
        }

    adapter._request = fake_request  # type: ignore[method-assign]
    result = adapter.complete(
        [{"role": "user", "content": "请回答"}],
        "general_chat.v1",
    )

    assert result.payload["output"]["answer"] == "已恢复"
    assert len(requests) == 2
    assert len(requests[1]) == 1
    assert "完整 JSON 不超过 900" in requests[1][0]["content"]
    assert "x" * 100 not in requests[1][0]["content"]
    assert result.metadata["attempts"] == 2
    assert result.metadata["retry_errors"]


def test_policy_applicability_accepts_top_level_assessments_envelope() -> None:
    adapter = AnthropicCompatibleModelAdapter(
        model_id="deepseek-v4-flash",
        base_url="https://example.invalid",
        api_key="<redacted>",
        max_attempts=1,
    )
    adapter._request = lambda _system, _messages: {  # type: ignore[method-assign]
        "id": "policy-envelope",
        "stop_reason": "end_turn",
        "content": [
            {
                "type": "text",
                "text": '{"assessments":[{"policy_id":"p1","applicability":"conditional"}]}',
            }
        ],
    }

    result = adapter.complete(
        [{"role": "user", "content": "classify"}],
        "policy_applicability.v1",
    )

    assert result.payload["type"] == "final"
    assert result.payload["output"]["assessments"][0]["policy_id"] == "p1"


def test_general_chat_recovers_answer_from_malformed_json_after_retries() -> None:
    adapter = AnthropicCompatibleModelAdapter(
        model_id="deepseek-v4-flash",
        base_url="https://example.invalid",
        api_key="<redacted>",
        max_attempts=1,
    )
    adapter._request = lambda _system, _messages: {  # type: ignore[method-assign]
        "id": "malformed",
        "stop_reason": "end_turn",
        "content": [
            {
                "type": "text",
                "text": "{\"type\":\"general_chat\",\"output\":{\"answer\":\"可恢复回答\"",
            }
        ],
    }

    result = adapter.complete([{"role": "user", "content": "answer"}], "general_chat.v1")

    assert result.payload["output"]["answer"] == "可恢复回答"
    assert result.metadata["contract_recovered"] is True


def test_from_env_uses_the_same_effective_model_parameters(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("ANTHROPIC_MODEL", DEFAULT_MODEL_ID)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.test/anthropic/")
    monkeypatch.setenv("AGENT_WORKBENCH_MODEL_MAX_TOKENS", "1536")
    monkeypatch.setenv("AGENT_WORKBENCH_MODEL_TEMPERATURE", "0")
    monkeypatch.setenv("AGENT_WORKBENCH_MODEL_TIMEOUT", "45")
    monkeypatch.setenv("AGENT_WORKBENCH_MODEL_ATTEMPTS", "2")

    adapter = AnthropicCompatibleModelAdapter.from_env()

    assert adapter.model_id == DEFAULT_MODEL_ID
    assert adapter.base_url == "https://example.test/anthropic"
    assert adapter.max_tokens == 1536
    assert adapter.temperature == 0
    assert adapter.timeout_seconds == 45
    assert adapter.max_attempts == 2


def test_non_retryable_auth_error_stops_after_one_attempt() -> None:
    adapter = AnthropicCompatibleModelAdapter(
        model_id=DEFAULT_MODEL_ID,
        base_url="https://example.invalid",
        api_key="<redacted>",
        max_attempts=3,
    )
    calls = 0

    def fail_auth(_system: str, _messages: list[dict[str, str]]) -> dict:
        nonlocal calls
        calls += 1
        raise ModelInvocationError("HTTP 401: unauthorized")

    adapter._request = fail_auth  # type: ignore[method-assign]

    with pytest.raises(ModelInvocationError, match="已尝试 1 次"):
        adapter.complete([{"role": "user", "content": "answer"}], "general_chat.v1")
    assert calls == 1
