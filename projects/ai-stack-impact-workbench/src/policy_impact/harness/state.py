"""Shared state for one policy impact run."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal
from uuid import uuid4


StageName = Literal[
    "initialized",
    "load_company_context",
    "fetch_recent_policies",
    "policy_ingest_and_index",
    "retrieve_relevant_clauses",
    "extract_policy_clauses",
    "match_company_policy",
    "analyze_policy_applicability",
    "score_policy_impact",
    "review_evidence_and_risk",
    "generate_weekly_report",
    "done",
    "failed",
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_date_range(days: int = 7) -> dict[str, str]:
    today = date.today()
    start = today - timedelta(days=days)
    return {"date_from": start.isoformat(), "date_to": today.isoformat()}


@dataclass
class PolicyImpactState:
    """A complete policy impact run state.

    The shape intentionally follows the insight_engine state pattern: stages
    add structured fields, gates validate them, and artifacts preserve evidence.
    """

    company_id: str = "company_001"
    run_id: str = field(default_factory=lambda: uuid4().hex)
    created_at: str = field(default_factory=utc_now_iso)
    current_stage: StageName = "initialized"
    date_range: dict[str, str] = field(default_factory=default_date_range)
    refresh_live_sources: bool = True
    allow_fixture_fallback: bool = False

    company_context: dict[str, Any] = field(default_factory=dict)
    current_user_message: str = ""
    selected_skill: dict[str, Any] = field(default_factory=dict)
    recent_history: list[dict[str, Any]] = field(default_factory=list)
    recent_artifacts: list[dict[str, Any]] = field(default_factory=list)
    recent_run_traces: list[dict[str, Any]] = field(default_factory=list)
    conversation_summary: dict[str, Any] = field(default_factory=dict)
    relevant_memories: list[dict[str, Any]] = field(default_factory=list)
    company_facts: list[dict[str, Any]] = field(default_factory=list)
    tool_policy_summary: dict[str, Any] = field(default_factory=dict)
    context_policy: dict[str, Any] = field(default_factory=dict)
    company_context_pack: dict[str, Any] = field(default_factory=dict)
    query_company_facts: list[dict[str, Any]] = field(default_factory=list)
    policy_documents: list[dict[str, Any]] = field(default_factory=list)
    policy_chunks: list[dict[str, Any]] = field(default_factory=list)
    evidence_hits: list[dict[str, Any]] = field(default_factory=list)
    policy_clauses: list[dict[str, Any]] = field(default_factory=list)
    policy_matches: list[dict[str, Any]] = field(default_factory=list)
    applicability_context: list[dict[str, Any]] = field(default_factory=list)
    policy_applicability: list[dict[str, Any]] = field(default_factory=list)
    regulatory_coverage_gaps: list[dict[str, Any]] = field(default_factory=list)
    impact_assessments: list[dict[str, Any]] = field(default_factory=list)
    review_context: list[dict[str, Any]] = field(default_factory=list)
    review_result: dict[str, Any] = field(default_factory=dict)
    report_paths: dict[str, str] = field(default_factory=dict)
    memory_updates: list[dict[str, Any]] = field(default_factory=list)
    correction_proposals: list[dict[str, Any]] = field(default_factory=list)
    source_manifest: dict[str, Any] = field(default_factory=dict)
    report_session_id: str = ""

    artifacts: dict[str, str] = field(default_factory=dict)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    context_manifests: list[dict[str, Any]] = field(default_factory=list)
    model_calls: list[dict[str, Any]] = field(default_factory=list)
    delegation_evidence: list[dict[str, Any]] = field(default_factory=list)
    review_bundles: list[dict[str, Any]] = field(default_factory=list)
    impact_tickets: list[dict[str, Any]] = field(default_factory=list)
    checkpoints: list[dict[str, Any]] = field(default_factory=list)
    replay_reports: list[dict[str, Any]] = field(default_factory=list)
    eval_reports: list[dict[str, Any]] = field(default_factory=list)
    benchmark_reports: list[dict[str, Any]] = field(default_factory=list)
    quality_metrics: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    stage_retry_counts: dict[str, int] = field(default_factory=dict)
    stage_gate_results: list[dict[str, Any]] = field(default_factory=list)
    stage_trace: list[dict[str, Any]] = field(default_factory=list)

    def mark_stage(self, stage: StageName) -> None:
        self.current_stage = stage

    def add_artifact(self, key: str, path: str) -> None:
        self.artifacts[key] = path

    def add_error(self, stage: str, message: str, detail: Any = None) -> None:
        self.errors.append({"stage": stage, "message": message, "detail": detail, "at": utc_now_iso()})

    def add_warning(self, stage: str, message: str, detail: Any = None) -> None:
        self.warnings.append({"stage": stage, "message": message, "detail": detail, "at": utc_now_iso()})

    def add_stage_gate_result(self, result: dict[str, Any]) -> None:
        self.stage_gate_results.append(result)

    def add_stage_trace(self, trace: dict[str, Any]) -> None:
        self.stage_trace.append(trace)

    def add_context_manifest(self, manifest: dict[str, Any]) -> None:
        self.context_manifests.append(manifest)

    def add_model_call(self, model_call: dict[str, Any]) -> None:
        self.model_calls.append(model_call)

    def add_delegation_evidence(self, evidence: dict[str, Any]) -> None:
        self.delegation_evidence.append(evidence)

    def add_review_bundle(self, review_bundle: dict[str, Any]) -> None:
        self.review_bundles.append(review_bundle)

    def add_impact_ticket(self, impact_ticket: dict[str, Any]) -> None:
        self.impact_tickets.append(impact_ticket)

    def add_checkpoint(self, checkpoint: dict[str, Any]) -> None:
        self.checkpoints.append(checkpoint)

    def add_replay_report(self, replay_report: dict[str, Any]) -> None:
        self.replay_reports.append(replay_report)

    def add_eval_report(self, eval_report: dict[str, Any]) -> None:
        self.eval_reports.append(eval_report)

    def add_benchmark_report(self, benchmark_report: dict[str, Any]) -> None:
        self.benchmark_reports.append(benchmark_report)

    def increment_stage_retry(self, stage: str) -> int:
        count = self.stage_retry_counts.get(stage, 0) + 1
        self.stage_retry_counts[stage] = count
        return count

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
