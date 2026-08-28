"""Stage: load company context."""

from __future__ import annotations

from policy_impact.company_wiki.loader import load_company_knowledge
from policy_impact.harness.artifacts import write_json_artifact
from policy_impact.harness.state import PolicyImpactState
from policy_impact.harness.tool_gateway import ToolGateway
from policy_impact.memory.store import PolicyMemoryStore


def run(state: PolicyImpactState, gateway: ToolGateway | None = None) -> PolicyImpactState:
    gateway = gateway or ToolGateway()
    profile = gateway.call("load_company_context", "company_profile_read", company_id=state.company_id)
    context_pack = gateway.call("load_company_context", "company_context_pack_build", company_id=state.company_id)
    raw_preferences = gateway.call(
        "load_company_context",
        "memory_search",
        company_id=state.company_id,
        query="政策报告 输出顺序 偏好 明确不适用 条件适用",
        top_k=20,
    )
    report_preferences = [
        item
        for item in raw_preferences
        if str(item.get("memory_type") or "") == "explicit_user_memory"
        and any(
            marker in str(item.get("content") or "")
            for marker in ("政策", "报告", "不适用", "条件适用", "输出顺序", "先列")
        )
    ]
    context_pack = dict(context_pack)
    context_pack["user_preferences"] = report_preferences
    if state.query_company_facts:
        context_pack["retrieved_company_facts"] = [
            *list(context_pack.get("retrieved_company_facts") or []),
            *state.query_company_facts,
        ]
    state.company_context = profile
    state.company_context_pack = context_pack
    knowledge = load_company_knowledge(state.company_id)
    PolicyMemoryStore(state.company_id).index_company_knowledge(
        knowledge["profile"],
        knowledge["pages"],
    )
    write_json_artifact(
        state,
        "company_context_pack",
        context_pack,
        "data/context",
        "company_context_pack.json",
    )
    return state
