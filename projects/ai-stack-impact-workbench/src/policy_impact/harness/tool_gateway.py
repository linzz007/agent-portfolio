"""Tool gateway with stage-level allowlists."""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from typing import Any
from uuid import uuid4

from policy_impact.harness.permissions import PermissionEngine, build_default_permission_engine
from policy_impact.harness.state import utc_now_iso

ToolFn = Callable[..., Any]


@dataclass
class ToolCallRecord:
    call_id: str
    stage_name: str
    tool_name: str
    allowed: bool
    started_at: str
    finished_at: str | None = None
    status: str = "started"
    error: str | None = None
    argument_keys: tuple[str, ...] = ()
    result_summary: str = ""
    policy_decision: str = ""
    policy_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["argument_keys"] = list(self.argument_keys)
        return data


TOOL_REGISTRY: dict[str, ToolFn] = {}
TOOL_AUDIT_LOG: list[ToolCallRecord] = []
_TOOL_AUDIT_LOG_CONTEXT: ContextVar[list[ToolCallRecord] | None] = ContextVar(
    "policy_impact_tool_audit_log",
    default=None,
)

STAGE_ALLOWED_TOOLS: dict[str, set[str]] = {
    "load_company_context": {"company_profile_read", "company_context_pack_build", "memory_search"},
    "fetch_recent_policies": {"policy_fetch_recent"},
    "policy_ingest_and_index": {"policy_ingest"},
    "retrieve_relevant_clauses": {"policy_retrieve"},
    "extract_policy_clauses": {"policy_clause_extract"},
    "match_company_policy": {"company_wiki_search"},
    "generate_weekly_report": {"report_write"},
    "main_agent": {"company_wiki_search", "company_wiki_blueprint", "memory_search", "memory_save"},
    "news": {
        "news.load_items",
        "news.structure_events",
        "news.analyze_company_impact",
    },
}


class ToolGateway:
    def __init__(self, permission_engine: PermissionEngine | None = None) -> None:
        self.calls: list[ToolCallRecord] = []
        self.permission_engine = permission_engine or build_default_permission_engine(STAGE_ALLOWED_TOOLS)

    def call(self, stage_name: str, tool_name: str, *args: Any, **kwargs: Any) -> Any:
        policy_decision = self.permission_engine.decide(stage_name, tool_name)
        allowed = policy_decision.decision == "allow"
        record = ToolCallRecord(
            call_id=f"tool_{uuid4().hex[:10]}",
            stage_name=stage_name,
            tool_name=tool_name,
            allowed=allowed,
            started_at=utc_now_iso(),
            argument_keys=tuple(sorted(kwargs.keys())),
            policy_decision=policy_decision.decision,
            policy_reason=policy_decision.reason,
        )
        self.calls.append(record)
        _current_audit_log().append(record)
        if not allowed:
            record.status = "blocked"
            record.finished_at = utc_now_iso()
            record.error = policy_decision.reason
            raise PermissionError(record.error)
        tool = TOOL_REGISTRY.get(tool_name)
        if tool is None:
            record.status = "failed"
            record.finished_at = utc_now_iso()
            record.error = f"tool does not exist: {tool_name}"
            raise KeyError(record.error)
        try:
            result = tool(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            record.status = "failed"
            record.finished_at = utc_now_iso()
            record.error = repr(exc)
            raise
        record.status = "ok"
        record.finished_at = utc_now_iso()
        record.result_summary = summarize_tool_result(result)
        return result


def register_tool(name: str, fn: ToolFn) -> None:
    TOOL_REGISTRY[name] = fn


def summarize_tool_result(result: Any) -> str:
    if isinstance(result, list):
        return f"list[{len(result)}]"
    if isinstance(result, dict):
        return f"dict[{','.join(list(result.keys())[:8])}]"
    return type(result).__name__


def reset_tool_audit_log() -> None:
    _TOOL_AUDIT_LOG_CONTEXT.set([])
    TOOL_AUDIT_LOG.clear()


def get_tool_audit_log() -> list[dict[str, Any]]:
    return [record.to_dict() for record in _current_audit_log()]


def _current_audit_log() -> list[ToolCallRecord]:
    current = _TOOL_AUDIT_LOG_CONTEXT.get()
    if current is None:
        return TOOL_AUDIT_LOG
    return current
