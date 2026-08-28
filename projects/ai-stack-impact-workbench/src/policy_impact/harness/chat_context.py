"""Budgeted context compilation for Agent Workbench chat turns."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from policy_impact.harness.context_manifest import estimate_tokens


@dataclass(frozen=True)
class ChatContextConfig:
    recent_message_limit: int = 8
    history_token_budget: int = 1600
    artifact_token_budget: int = 600
    run_trace_token_budget: int = 700
    summary_token_budget: int = 700
    fact_token_budget: int = 1800
    memory_token_budget: int = 700
    task_token_budget: int = 600
    tool_token_budget: int = 300
    system_token_budget: int = 900

    def manifest_budget(self) -> dict[str, int]:
        return {
            "system": self.system_token_budget,
            "task": self.task_token_budget,
            "history": self.history_token_budget,
            "artifact": self.artifact_token_budget,
            "run_trace": self.run_trace_token_budget,
            "summary": self.summary_token_budget,
            "profile": self.fact_token_budget,
            "memory": self.memory_token_budget,
            "tool": self.tool_token_budget,
        }


def compile_chat_context(
    *,
    message: str,
    skill: dict[str, Any],
    history: list[dict[str, Any]],
    conversation_summary: dict[str, Any] | None,
    memories: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    tool_policy: dict[str, Any],
    config: ChatContextConfig | None = None,
) -> dict[str, Any]:
    """Compile the exact bounded values that may be sent to a model."""

    cfg = config or ChatContextConfig()
    recent_candidates = history[-cfg.recent_message_limit :]
    recent_history, history_dropped = _fit_records(
        [
            {
                "role": str(item.get("role") or ""),
                "content": str(item.get("content") or ""),
            }
            for item in recent_candidates
        ],
        cfg.history_token_budget,
        keep_recent=True,
    )
    recent_artifacts, artifact_dropped = _fit_records(
        _extract_recent_artifacts(recent_candidates),
        cfg.artifact_token_budget,
        keep_recent=True,
    )
    summary = _fit_mapping(conversation_summary or {}, cfg.summary_token_budget)
    relevant_memories, memory_dropped = _fit_records(
        [
            {
                "memory_id": item.get("id"),
                "memory_type": item.get("memory_type", ""),
                "content": item.get("content", ""),
                "importance": item.get("importance", 0.0),
            }
            for item in memories
        ],
        cfg.memory_token_budget,
    )
    company_facts, fact_dropped = _fit_records(
        [
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
            for item in facts
        ],
        cfg.fact_token_budget,
    )
    compiled = {
        "current_user_message": _clip_text(str(message or ""), cfg.task_token_budget),
        "selected_skill": deepcopy(skill),
        "recent_history": recent_history,
        "recent_artifacts": recent_artifacts,
        "conversation_summary": summary,
        "relevant_memories": relevant_memories,
        "company_facts": company_facts,
        "tool_policy_summary": deepcopy(tool_policy),
    }
    compiled["context_policy"] = {
        "policy_id": "chat_context.budgeted.v1",
        "recent_message_limit": cfg.recent_message_limit,
        "history_messages_available": len(history),
        "history_messages_included": len(recent_history),
        "history_messages_dropped": max(0, len(history) - len(recent_history)),
        "history_budget_dropped": history_dropped,
        "artifact_items_included": len(recent_artifacts),
        "artifact_items_dropped": artifact_dropped,
        "memory_items_available": len(memories),
        "memory_items_included": len(relevant_memories),
        "memory_items_dropped": memory_dropped,
        "company_facts_available": len(facts),
        "company_facts_included": len(company_facts),
        "company_facts_dropped": fact_dropped,
        "summary_present": bool(summary),
        "token_budget": cfg.manifest_budget(),
    }
    return compiled


def _extract_recent_artifacts(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in messages:
        metadata = item.get("metadata") or {}
        if not isinstance(metadata, dict):
            continue
        artifacts = metadata.get("artifacts") or {}
        if not isinstance(artifacts, dict) or not artifacts:
            continue
        artifact_items = [
            {"artifact_type": str(key), "path": str(value)}
            for key, value in sorted(artifacts.items())
            if isinstance(value, str) and value.strip()
        ]
        if not artifact_items:
            continue
        records.append(
            {
                "message_id": item.get("message_id"),
                "run_id": metadata.get("run_id"),
                "skill_id": metadata.get("skill_id"),
                "artifact_keys": [record["artifact_type"] for record in artifact_items],
                "artifacts": artifact_items,
                "answer_preview": _clip_text(str(item.get("content") or ""), 180),
            }
        )
    return records


def _fit_mapping(value: dict[str, Any], token_budget: int) -> dict[str, Any]:
    copied = deepcopy(value)
    if not copied or estimate_tokens(copied) <= token_budget:
        return copied
    for key in ("open_loops", "decisions", "user_facts"):
        items = copied.get(key)
        if isinstance(items, list):
            copied[key] = [_clip_text(str(item), 80) for item in items[:5]]
    if "summary" in copied:
        copied["summary"] = _clip_text(str(copied.get("summary") or ""), max(120, token_budget - 180))
    while estimate_tokens(copied) > token_budget:
        reduced = False
        for key in ("open_loops", "decisions", "user_facts"):
            items = copied.get(key)
            if isinstance(items, list) and items:
                items.pop()
                reduced = True
                break
        if not reduced:
            copied = _fit_summary_only(str(copied.get("summary") or ""), token_budget)
            break
    return copied


def _fit_summary_only(text: str, token_budget: int) -> dict[str, str]:
    candidate = {"summary": str(text or "")}
    if estimate_tokens(candidate) <= token_budget:
        return candidate
    suffix = "..."
    low, high = 0, len(text)
    best = ""
    while low <= high:
        middle = (low + high) // 2
        clipped = text[:middle].rstrip()
        value = clipped + suffix if clipped else ""
        if estimate_tokens({"summary": value}) <= token_budget:
            best = value
            low = middle + 1
        else:
            high = middle - 1
    return {"summary": best} if best else {}


def _fit_records(
    records: list[dict[str, Any]],
    token_budget: int,
    *,
    keep_recent: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    ordered = list(reversed(records)) if keep_recent else list(records)
    selected: list[dict[str, Any]] = []
    for record in ordered:
        candidate = deepcopy(record)
        if "content" in candidate:
            candidate["content"] = _clip_text(str(candidate.get("content") or ""), 320)
        if "value" in candidate:
            candidate["value"] = _clip_text(str(candidate.get("value") or ""), 260)
        trial = [*selected, candidate]
        if estimate_tokens(trial) > token_budget:
            continue
        selected.append(candidate)
    if keep_recent:
        selected.reverse()
    return selected, max(0, len(records) - len(selected))


def _clip_text(text: str, token_budget: int) -> str:
    value = str(text or "")
    if estimate_tokens(value) <= token_budget:
        return value
    suffix = "..."
    low, high = 0, len(value)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = value[:middle].rstrip() + suffix
        if estimate_tokens(candidate) <= token_budget:
            low = middle
        else:
            high = middle - 1
    clipped = value[:low].rstrip()
    if not clipped:
        return suffix if estimate_tokens(suffix) <= token_budget else ""
    return clipped + suffix
