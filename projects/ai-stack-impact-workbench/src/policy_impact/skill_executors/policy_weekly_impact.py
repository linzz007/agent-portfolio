"""Skill executor for weekly policy impact reports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import json
from pathlib import Path

from policy_impact.harness.graph import build_graph
from policy_impact.harness.gate_engine import GateEngine
from policy_impact.harness.model_gateway import ModelAdapter
from policy_impact.harness.state import PolicyImpactState
from policy_impact.harness.tool_gateway import get_tool_audit_log, reset_tool_audit_log
from policy_impact.memory.store import PolicyMemoryStore
from policy_impact.mcp_server.registry import register_all_tools
from policy_impact.policy_coverage import infer_regulatory_coverage_gaps
from policy_impact.query_company_facts import extract_query_company_facts
from policy_impact.runtime.gate_catalog import (
    StateGateDecisionSink,
    build_default_gate_registry,
)
from policy_impact.runtime.gates import GateRunner
from policy_impact.stages import (
    analyze_policy_applicability,
    extract_policy_clauses,
    fetch_recent_policies,
    generate_weekly_report,
    load_company_context,
    match_company_policy,
    policy_ingest_and_index,
    retrieve_relevant_clauses,
    review_evidence_and_risk,
    score_policy_impact,
)


@dataclass(frozen=True)
class PolicyWeeklyImpactResult:
    state: PolicyImpactState

    @property
    def report_path(self) -> str | None:
        return self.state.report_paths.get("report")

    @property
    def run_artifact_path(self) -> str | None:
        return self.state.report_paths.get("run_artifact")

    @property
    def summary(self) -> str:
        return (
            f"status={self.state.current_stage}, "
            f"policies={len(self.state.policy_documents)}, "
            f"assessments={len(self.state.impact_assessments)}"
        )


def build_handlers(
    model_adapter: ModelAdapter | None = None,
    *,
    gate_engine: GateEngine,
):
    return {
        "load_company_context": load_company_context.run,
        "fetch_recent_policies": fetch_recent_policies.run,
        "policy_ingest_and_index": policy_ingest_and_index.run,
        "retrieve_relevant_clauses": retrieve_relevant_clauses.run,
        "extract_policy_clauses": extract_policy_clauses.run,
        "match_company_policy": match_company_policy.run,
        "analyze_policy_applicability": lambda state: analyze_policy_applicability.run(
            state,
            model_adapter=model_adapter,
        ),
        "score_policy_impact": lambda state: score_policy_impact.run(
            state,
            gate_engine=gate_engine,
        ),
        "review_evidence_and_risk": lambda state: review_evidence_and_risk.run(
            state,
            model_adapter=model_adapter,
            gate_engine=gate_engine,
        ),
        "generate_weekly_report": lambda state: generate_weekly_report.run(
            state,
            gate_engine=gate_engine,
        ),
    }


def run_policy_weekly_impact(
    company_id: str = "company_001",
    model_adapter: ModelAdapter | None = None,
    *,
    run_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    lookback_days: int = 7,
    refresh_live_sources: bool = True,
    allow_fixture_fallback: bool = False,
    query: str = "",
    gate_runner: GateRunner | None = None,
) -> PolicyWeeklyImpactResult:
    register_all_tools()
    reset_tool_audit_log()
    end = date.fromisoformat(date_to) if date_to else date.today()
    start = date.fromisoformat(date_from) if date_from else end - timedelta(days=lookback_days)
    state = PolicyImpactState(
        company_id=company_id,
        **({"run_id": run_id} if run_id else {}),
        date_range={"date_from": start.isoformat(), "date_to": end.isoformat()},
        refresh_live_sources=refresh_live_sources,
        allow_fixture_fallback=allow_fixture_fallback,
        regulatory_coverage_gaps=infer_regulatory_coverage_gaps(query),
        query_company_facts=extract_query_company_facts(query),
    )
    if gate_runner is None:
        state_sink = StateGateDecisionSink(state)
        gate_runner = GateRunner(
            build_default_gate_registry(proof_resolver=state_sink),
            state_sink,
        )
    gate_engine = GateEngine(runner=gate_runner, run_id=state.run_id)
    graph = build_graph(
        build_handlers(model_adapter=model_adapter, gate_engine=gate_engine)
    )
    state = graph.run(state)
    state.tool_calls = get_tool_audit_log()
    _persist_final_audit(state)
    return PolicyWeeklyImpactResult(state=state)


def _persist_final_audit(state: PolicyImpactState) -> None:
    store = PolicyMemoryStore(state.company_id)
    store.save_tool_calls(state.run_id, state.tool_calls)
    artifact = state.report_paths.get("run_artifact")
    if artifact:
        path = Path(artifact)
        path.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        store.save_run_artifact(
            run_id=state.run_id,
            artifact_path=str(path),
            status=state.current_stage,
            metrics=state.quality_metrics,
        )
