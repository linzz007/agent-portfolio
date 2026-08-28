from __future__ import annotations

from policy_impact.harness.chat_context import ChatContextConfig, compile_chat_context
from policy_impact.harness.context_manifest import estimate_tokens
from policy_impact.harness.prompt_builder import build_general_chat_prompt


def test_chinese_token_estimator_does_not_treat_four_cjk_chars_as_one_token():
    assert estimate_tokens("示例公司智能问答产品") >= 5
    assert estimate_tokens("Agent Runtime") < estimate_tokens("示例公司智能问答产品上下文管理")


def test_compile_chat_context_keeps_recent_history_and_enforces_each_budget():
    history = [
        {"role": "user" if index % 2 == 0 else "assistant", "content": f"第{index}轮" + "上下文" * 160}
        for index in range(14)
    ]
    memories = [
        {"id": f"m{index}", "memory_type": "preference", "content": "长期偏好" * 140, "importance": 0.9}
        for index in range(6)
    ]
    facts = [
        {
            "fact_id": f"fact.{index}",
            "value": "公开披露事实" * 120,
            "source": "https://example.com/report.pdf",
            "source_kind": "public_disclosure",
            "confidence": 0.9,
        }
        for index in range(8)
    ]
    config = ChatContextConfig(
        recent_message_limit=6,
        history_token_budget=280,
        summary_token_budget=120,
        fact_token_budget=360,
        memory_token_budget=180,
    )

    compiled = compile_chat_context(
        message="请继续核验示例产品和 iFinD。",
        skill={"id": "general_chat", "mode": "chat"},
        history=history,
        conversation_summary={"summary": "旧对话摘要" * 100, "open_loops": ["核验 iFinD"]},
        memories=memories,
        facts=facts,
        tool_policy={"decision_order": ["deny", "ask", "allow"]},
        config=config,
    )

    policy = compiled["context_policy"]
    assert policy["history_messages_available"] == 14
    assert policy["history_messages_included"] <= 6
    assert policy["history_messages_dropped"] >= 8
    assert policy["history_budget_dropped"] >= 1
    assert policy["artifact_items_included"] == 0
    assert policy["memory_items_dropped"] >= 1
    assert policy["company_facts_dropped"] >= 1
    assert estimate_tokens(compiled["recent_history"]) <= config.history_token_budget
    assert estimate_tokens(compiled["recent_artifacts"]) <= config.artifact_token_budget
    assert estimate_tokens(compiled["conversation_summary"]) <= config.summary_token_budget
    assert estimate_tokens(compiled["relevant_memories"]) <= config.memory_token_budget
    assert estimate_tokens(compiled["company_facts"]) <= config.fact_token_budget


def test_prompt_builder_never_falls_back_from_explicit_empty_compiled_lists():
    manifest = {
        "visible_keys": [
            "current_user_message",
            "recent_history",
            "recent_artifacts",
            "recent_run_traces",
            "relevant_memories",
            "company_facts",
        ],
        "visible_content": {
            "current_user_message": "已裁剪的问题",
            "recent_history": [],
            "recent_artifacts": [],
            "recent_run_traces": [],
            "relevant_memories": [],
            "company_facts": [],
            "conversation_summary": {},
            "context_policy": {"policy_id": "chat_context.budgeted.v1"},
        },
        "hidden_fields": {},
        "token_budget": {
            "system": 900,
            "task": 600,
            "history": 1600,
            "artifact": 600,
            "run_trace": 700,
            "summary": 700,
            "profile": 1800,
            "memory": 700,
            "tool": 300,
        },
    }

    messages, contract = build_general_chat_prompt(
        message="原始未裁剪问题",
        context_manifest=manifest,
        memories=[{"id": "must-not-return", "content": "全局记忆"}],
        facts=[{"fact_id": "must.not_return", "value": "原始企业事实"}],
        history=[{"role": "user", "content": "原始历史"}],
    )
    serialized = str(messages)

    assert "已裁剪的问题" in serialized
    assert "原始未裁剪问题" not in serialized
    assert "must-not-return" not in serialized
    assert "must.not_return" not in serialized
    assert contract["context_controls"]["memory_count"] == 0
    assert contract["context_controls"]["fact_count"] == 0
    assert contract["context_controls"]["history_message_count"] == 0
    assert contract["context_controls"]["recent_artifact_count"] == 0
    assert contract["context_controls"]["recent_run_trace_count"] == 0
    assert "完整 JSON 不超过 900 个中文字符" in messages[0]["content"]
    assert contract["context_controls"]["serialized_prompt_tokens"] <= contract["context_controls"][
        "serialized_prompt_hard_limit"
    ]
