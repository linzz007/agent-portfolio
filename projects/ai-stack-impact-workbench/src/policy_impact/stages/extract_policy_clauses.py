"""Stage: extract policy clauses."""

from __future__ import annotations

from policy_impact.harness.artifacts import write_json_artifact
from policy_impact.harness.state import PolicyImpactState
from policy_impact.harness.tool_gateway import ToolGateway
from policy_impact.memory.store import PolicyMemoryStore


def run(state: PolicyImpactState, gateway: ToolGateway | None = None) -> PolicyImpactState:
    gateway = gateway or ToolGateway()
    state.policy_clauses = gateway.call(
        "extract_policy_clauses",
        "policy_clause_extract",
        evidence_hits=state.evidence_hits,
    )
    PolicyMemoryStore(state.company_id).index_policy_documents(
        state.policy_documents,
        clauses=state.policy_clauses,
    )
    write_json_artifact(
        state,
        "policy_clauses",
        state.policy_clauses,
        "data/policies/processed",
        "policy_clauses.json",
    )
    return state
