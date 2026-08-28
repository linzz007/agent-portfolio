"""Stage: retrieve policy evidence."""

from __future__ import annotations

from policy_impact.harness.artifacts import write_json_artifact
from policy_impact.harness.state import PolicyImpactState
from policy_impact.harness.tool_gateway import ToolGateway


def _build_query(state: PolicyImpactState) -> str:
    profile = state.company_context_pack.get("profile", {})
    facts = state.company_context_pack.get("pinned_facts", [])
    parts = []
    parts.extend(str(x) for x in profile.get("business_keywords", []))
    parts.extend(str(x) for x in profile.get("current_goals", []))
    for fact in facts:
        parts.append(str(fact.get("value", "")))
        parts.extend(str(x) for x in fact.get("policy_relevance", []))
    return " ".join(p for p in parts if p)


def run(state: PolicyImpactState, gateway: ToolGateway | None = None) -> PolicyImpactState:
    gateway = gateway or ToolGateway()
    query = _build_query(state)
    state.evidence_hits = gateway.call(
        "retrieve_relevant_clauses",
        "policy_retrieve",
        query=query,
        chunks=state.policy_chunks,
        top_k=8,
    )
    write_json_artifact(
        state,
        "evidence_hits",
        state.evidence_hits,
        "data/policies/processed",
        "evidence_hits.json",
    )
    return state
