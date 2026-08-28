"""Context router for stage-specific state visibility."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from policy_impact.harness.artifacts import project_root
from policy_impact.harness.context_manifest import build_context_manifest
from policy_impact.harness.contracts import ContextManifestRecord
from policy_impact.harness.state import PolicyImpactState


@dataclass(frozen=True)
class ContextPackage:
    stage_name: str
    agent_prompt_path: str
    runtime_doc_paths: list[str]
    visible_state: dict[str, Any]


STAGE_AGENT_PROMPTS = {
    "extract_policy_clauses": "prompts/agents/clause_agent.md",
    "match_company_policy": "prompts/agents/policy_match_agent.md",
    "analyze_policy_applicability": "prompts/agents/policy_applicability_agent.md",
    "score_policy_impact": "prompts/agents/impact_scoring_agent.md",
    "generate_weekly_report": "prompts/agents/report_agent.md",
}


STAGE_VISIBLE_FIELDS = {
    "load_company_context": ["company_id", "date_range", "errors", "warnings"],
    "fetch_recent_policies": ["company_context_pack", "date_range"],
    "policy_ingest_and_index": ["policy_documents"],
    "retrieve_relevant_clauses": ["company_context_pack", "policy_documents"],
    "extract_policy_clauses": ["evidence_hits"],
    "match_company_policy": ["company_context_pack", "policy_clauses"],
    "analyze_policy_applicability": ["applicability_context"],
    "score_policy_impact": ["policy_matches", "policy_applicability"],
    "review_evidence_and_risk": ["impact_assessments"],
    "generate_weekly_report": ["company_context_pack", "impact_assessments", "review_result"],
}


RUNTIME_DOCS = {
    "extract_policy_clauses": [
        "docs/runtime/global_rules.md",
        "docs/runtime/final_output_format.md",
    ],
    "match_company_policy": [
        "docs/runtime/global_rules.md",
        "docs/runtime/final_output_format.md",
    ],
    "analyze_policy_applicability": [
        "docs/runtime/global_rules.md",
        "docs/runtime/final_output_format.md",
    ],
    "score_policy_impact": [
        "docs/runtime/global_rules.md",
        "docs/runtime/final_output_format.md",
    ],
    "generate_weekly_report": [
        "docs/runtime/global_rules.md",
        "docs/runtime/final_output_format.md",
    ],
}


def build_context_package(stage_name: str, state: PolicyImpactState) -> ContextPackage:
    state_dict = state.to_dict()
    visible = {
        key: compact_for_prompt(key, state_dict.get(key), state.artifacts)
        for key in STAGE_VISIBLE_FIELDS.get(stage_name, [])
    }
    return ContextPackage(
        stage_name=stage_name,
        agent_prompt_path=STAGE_AGENT_PROMPTS.get(stage_name, ""),
        runtime_doc_paths=RUNTIME_DOCS.get(stage_name, []),
        visible_state=visible,
    )


def build_stage_context_manifest(
    stage_name: str,
    state: PolicyImpactState,
    agent_role: str = "",
    manifest_sequence: int | None = None,
) -> ContextManifestRecord:
    return build_context_manifest(
        state=state,
        stage_name=stage_name,
        agent_role=agent_role or stage_name,
        visible_fields=STAGE_VISIBLE_FIELDS.get(stage_name, []),
        manifest_sequence=manifest_sequence,
    )


def read_context_file(relative_path: str) -> str:
    if not relative_path:
        return ""
    path = project_root() / relative_path
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def context_file_exists(relative_path: str) -> bool:
    return bool(relative_path) and (project_root() / relative_path).exists()


def compact_for_prompt(field: str, value: Any, artifacts: dict[str, str]) -> Any:
    if isinstance(value, list):
        return {
            "count": len(value),
            "sample": value[:3],
            "artifact_hint": _artifact_hint_for_field(field, artifacts),
        }
    if isinstance(value, dict):
        if field == "artifacts":
            return value
        return {
            "keys": list(value.keys()),
            "sample": dict(list(value.items())[:8]),
            "artifact_hint": _artifact_hint_for_field(field, artifacts),
        }
    return value


def _artifact_hint_for_field(field: str, artifacts: dict[str, str]) -> str | None:
    mapping = {
        "company_context_pack": "company_context_pack",
        "policy_documents": "policy_documents",
        "policy_chunks": "policy_chunks",
        "evidence_hits": "evidence_hits",
        "policy_clauses": "policy_clauses",
        "policy_matches": "policy_matches",
        "policy_applicability": "policy_applicability",
        "impact_assessments": "impact_assessments",
        "review_result": "review_result",
    }
    artifact_key = mapping.get(field)
    if artifact_key:
        return artifacts.get(artifact_key)
    return None
