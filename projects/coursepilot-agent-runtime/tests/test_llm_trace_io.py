from types import SimpleNamespace

from core.llm.openai_compat import LLMClient
from core.metrics import trace_scope


class _Completions:
    def create(self, **_kwargs):
        message = SimpleNamespace(
            role="assistant",
            content="完整模型输出：答案是 42。",
            tool_calls=None,
            reasoning_content="hidden reasoning should not be traced",
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=7),
        )


def test_chat_llm_call_trace_records_full_input_and_output():
    client = LLMClient.__new__(LLMClient)
    client.model = "test-model"
    client.provider = "test-provider"
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))
    messages = [
        {"role": "system", "content": "你是老师。"},
        {"role": "user", "content": "请完整回答这个问题。"},
    ]

    with trace_scope({"request_id": "req"}) as trace:
        result = client.chat(messages, temperature=0.1, max_tokens=100)

    event = trace.events[-1]
    assert result == "完整模型输出：答案是 42。"
    assert event["type"] == "llm_call"
    assert event["input_messages"] == messages
    assert event["output_message"]["role"] == "assistant"
    assert event["output_message"]["content"] == "完整模型输出：答案是 42。"
    assert "reasoning_content" not in event["output_message"]
