"""Stage: ingest policy docs and build chunks."""

from __future__ import annotations

from policy_impact.harness.artifacts import write_json_artifact
from policy_impact.harness.state import PolicyImpactState
from policy_impact.harness.tool_gateway import ToolGateway


def run(state: PolicyImpactState, gateway: ToolGateway | None = None) -> PolicyImpactState:
    gateway = gateway or ToolGateway()
    state.policy_chunks = gateway.call("policy_ingest_and_index", "policy_ingest", policy_documents=state.policy_documents)
    write_json_artifact(
        state,
        "policy_chunks",
        state.policy_chunks,
        "data/policies/processed",
        "policy_chunks.json",
    )
    return state
