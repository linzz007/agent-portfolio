"""Stage: fetch recent policies."""

from __future__ import annotations

from policy_impact.harness.artifacts import write_json_artifact
from policy_impact.harness.state import PolicyImpactState
from policy_impact.harness.tool_gateway import ToolGateway
from policy_impact.memory.store import PolicyMemoryStore
from policy_impact.policy_data.repository import get_policy_source_manifest


def run(state: PolicyImpactState, gateway: ToolGateway | None = None) -> PolicyImpactState:
    gateway = gateway or ToolGateway()
    profile = state.company_context_pack.get("profile", {})
    region = []
    raw_region = profile.get("region") or {}
    if isinstance(raw_region, dict):
        region = [v for v in raw_region.values() if v]
    industry_keywords = profile.get("business_keywords", [])
    state.policy_documents = gateway.call(
        "fetch_recent_policies",
        "policy_fetch_recent",
        company_id=state.company_id,
        region=region,
        industry_keywords=industry_keywords,
        date_from=state.date_range["date_from"],
        date_to=state.date_range["date_to"],
        refresh_live_sources=state.refresh_live_sources,
        allow_fixture_fallback=state.allow_fixture_fallback,
    )
    source_runtime = get_policy_source_manifest()
    state.source_manifest = {
        "date_range": state.date_range,
        "region": region,
        "industry_keywords": industry_keywords,
        "policy_count": len(state.policy_documents),
        "source_levels": sorted({doc.get("source_level", "") for doc in state.policy_documents}),
        **source_runtime,
    }
    PolicyMemoryStore(state.company_id).index_policy_documents(state.policy_documents)
    write_json_artifact(
        state,
        "policy_documents",
        state.policy_documents,
        "data/policies/processed",
        "policy_documents.json",
    )
    return state
