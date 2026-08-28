"""Prompt builders used by auditable Agent Workbench skills."""

from __future__ import annotations

from typing import Any

from policy_impact.harness.context_manifest import estimate_tokens
from policy_impact.runtime.plugin_config import read_plugin_text


def _plugin_prompt(name: str, **replacements: str) -> str:
    text = read_plugin_text("prompts", name)
    for key, value in replacements.items():
        text = text.replace("{{" + key + "}}", value)
    return text.strip()


def build_general_chat_prompt(
    *,
    message: str,
    context_manifest: dict[str, Any],
    memories: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    history: list[dict[str, Any]],
    system_facts: list[str] | None = None,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Build a grounded chat prompt with an explicit context and output contract."""

    normalized_message = message.lower()
    visible_content = context_manifest.get("visible_content") or {}
    prompt_memories = list(
        visible_content["relevant_memories"]
        if "relevant_memories" in visible_content
        else memories
    )
    prompt_facts = list(
        visible_content["company_facts"]
        if "company_facts" in visible_content
        else facts
    )
    prompt_history = list(
        visible_content["recent_history"]
        if "recent_history" in visible_content
        else history[-8:]
    )
    prompt_artifacts = list(visible_content.get("recent_artifacts") or [])
    prompt_run_traces = list(visible_content.get("recent_run_traces") or [])
    prompt_subagent_results = list(visible_content.get("subagent_results") or [])
    prompt_message = str(visible_content.get("current_user_message", message))
    conversation_summary = visible_content.get("conversation_summary") or {}
    strict_retrieved_only = any(term in normalized_message for term in ("只基于", "仅基于", "only use", "based only on"))
    prompt_contract = {
        "contract_id": "general_chat.v1",
        "role": "main_agent",
        "loop_boundary": "runtime owns tools, permissions, memory writes, and trace",
        "required_output": {
            "type": "general_chat",
            "output": {"answer": "string"},
        },
        "context_controls": {
            "visible_keys": list(context_manifest.get("visible_keys", [])),
            "hidden_field_count": len(context_manifest.get("hidden_fields", {})),
            "memory_count": len(prompt_memories),
            "fact_count": len(prompt_facts),
            "history_message_count": len(prompt_history),
            "recent_artifact_count": len(prompt_artifacts),
            "recent_run_trace_count": len(prompt_run_traces),
            "subagent_result_count": len(prompt_subagent_results),
            "conversation_summary_present": bool(conversation_summary),
            "strict_retrieved_only": strict_retrieved_only,
            "token_budget": context_manifest.get("token_budget", {}),
            "context_policy": visible_content.get("context_policy") or {},
        },
    }
    system_prompt = _plugin_prompt(
        "general_chat_system.md",
        strict_retrieved_line=(
            "用户要求只基于检索证据；本轮禁止补充通用知识，证据不足时必须直接说明。"
            if strict_retrieved_only
            else "本轮没有额外的检索来源限制。"
        ),
    )
    evidence = {
        "company_facts": [
            {
                "fact_id": item.get("fact_id"),
                "value": item.get("value", ""),
                "source_path": item.get("source_path", ""),
                "source": item.get("source", ""),
                "source_kind": item.get("source_kind", "other"),
                "confidence": item.get("confidence", 0.5),
                "freshness": item.get("freshness", ""),
                "last_updated": item.get("last_updated", ""),
                "retrieval_score": item.get("retrieval_score", 0.0),
            }
            for item in prompt_facts
        ],
        "runtime_self_knowledge": list(system_facts or []),
        "long_term_memories": [
            {
                "memory_id": item.get("id"),
                "memory_type": item.get("memory_type", ""),
                "content": item.get("content", ""),
            }
            for item in prompt_memories
        ],
        "conversation_summary": conversation_summary,
        "recent_history": [
            {"role": item.get("role", ""), "content": item.get("content", "")}
            for item in prompt_history
        ],
        "recent_artifacts": prompt_artifacts,
        "recent_run_traces": prompt_run_traces,
        "subagent_results": prompt_subagent_results,
        "answer_requirements": (
            [
                "说明 Slash Skill 路由能力",
                "说明每轮运行会形成 AgentRun 和有序 AgentStep",
                "说明 Harness 会记录 Context、Memory、Model、Tool/Gate、Artifact 和 Trace",
            ]
            if (
                ("workbench" in message.lower() or "工作台" in message)
                and any(term in message for term in ("能做什么", "介绍", "能力"))
            )
            else []
        ),
    }
    user_prompt = "\n".join(
        [
            f"当前用户消息：{prompt_message}",
            f"上下文契约：{prompt_contract['context_controls']}",
            f"可用证据：{evidence}",
            "请严格按 JSON 契约回答。",
        ]
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    visible_budget = sum(
        int(value or 0) for value in (context_manifest.get("token_budget") or {}).values()
    )
    serialized_hard_limit = max(2400, visible_budget + 1800)
    serialized_tokens = estimate_tokens(messages)
    if serialized_tokens > serialized_hard_limit:
        raise ValueError(
            "serialized general-chat prompt exceeds hard token gate: "
            f"{serialized_tokens}>{serialized_hard_limit}"
        )
    prompt_contract["context_controls"]["serialized_prompt_tokens"] = serialized_tokens
    prompt_contract["context_controls"]["serialized_prompt_hard_limit"] = serialized_hard_limit
    return messages, prompt_contract


def build_conversation_summary_prompt(
    *,
    previous_summary: dict[str, Any] | None,
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Build an incremental, loss-aware conversation compaction request."""

    contract = {
        "contract_id": "conversation_summary.v1",
        "role": "context_compactor",
        "required_output": {
            "type": "conversation_summary",
            "output": {
                "summary": "string",
                "user_facts": ["string"],
                "decisions": ["string"],
                "open_loops": ["string"],
            },
        },
        "compaction_policy": {
            "incremental": True,
            "preserve_negation": True,
            "preserve_uncertainty": True,
            "no_new_facts": True,
            "max_items_per_array": 6,
        },
    }
    system = _plugin_prompt("conversation_summary_system.md")
    compact_messages = [
        {"role": item.get("role", ""), "content": str(item.get("content") or "")[:1200]}
        for item in messages
    ]
    user = "\n".join(
        [
            f"旧摘要：{previous_summary or {}}",
            f"新增待压缩消息：{compact_messages}",
            "请按契约输出增量合并后的摘要。",
        ]
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}], contract


def build_research_report_prompt(
    message: str,
    context_manifest: dict[str, Any],
    memory_count: int,
    evidence: list[str] | None = None,
    conversation_summary: dict[str, Any] | None = None,
    recent_history: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Build the research-report model prompt and its traceable contract."""
    prompt_contract = {
        "contract_id": "research_report.v1",
        "role": "research_report_subagent",
        "loop_boundary": "model only proposes structure; runtime owns tools, memory writes, and artifacts",
        "required_behavior": [
            "Use the user task and visible context only.",
            "Do not invent sources, files, or runtime facts.",
            "Return a compact thesis and structured report sections.",
            "Separate conclusion, evidence, and recommendations.",
            "Keep uncertainty explicit when evidence is missing.",
            "Each array must contain exactly 2 concise items; each item must stay under 60 Chinese characters.",
            "The complete JSON response must stay under 900 Chinese characters without repetition.",
        ],
        "context_controls": {
            "visible_keys": list(context_manifest.get("visible_keys", [])),
            "hidden_field_count": len(context_manifest.get("hidden_fields", {})),
            "memory_count": memory_count,
            "conversation_summary_present": bool(conversation_summary),
            "history_message_count": len(recent_history or []),
            "token_budget": context_manifest.get("token_budget", {}),
        },
    }
    system_prompt = _plugin_prompt("research_report_system.md")
    user_prompt = "\n".join(
        [
            f"User task: {message}",
            f"Visible context keys: {', '.join(prompt_contract['context_controls']['visible_keys'])}",
            f"Memory items available: {memory_count}",
            f"Verified evidence available: {evidence or []}",
            f"Compacted conversation summary: {conversation_summary or {}}",
            f"Recent conversation messages: {recent_history or []}",
            f"Token budget: {prompt_contract['context_controls']['token_budget']}",
            "Length limit: complete JSON <= 900 Chinese characters; every array has exactly 2 items; every item <= 60 Chinese characters.",
            "Return the JSON object only. Do not wrap it in a Markdown code block.",
        ]
    )
    return (
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        prompt_contract,
    )
