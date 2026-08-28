from __future__ import annotations

from pathlib import Path

import pytest

from policy_impact.harness.gate_engine import GateEngine
from policy_impact.harness.state import PolicyImpactState
from policy_impact.runtime.gate_catalog import (
    StateGateDecisionSink,
    build_default_gate_registry,
)
from policy_impact.runtime.gates import GateRunner
from policy_impact.skill_executors.policy_weekly_impact import run_policy_weekly_impact
from policy_impact.stages import generate_weekly_report


class TrackingReportGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def call(self, stage_name: str, tool_name: str, **kwargs):
        self.calls.append((stage_name, tool_name))
        raise AssertionError("publication gate must run before report_write")


class SuccessfulReportGateway:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls: list[tuple[str, str]] = []

    def call(self, stage_name: str, tool_name: str, **kwargs):
        self.calls.append((stage_name, tool_name))
        return {"path": str(self.root / str(kwargs["filename"]))}


def _gate_engine(state: PolicyImpactState) -> GateEngine:
    sink = StateGateDecisionSink(state)
    return GateEngine(
        runner=GateRunner(
            build_default_gate_registry(proof_resolver=sink),
            sink,
        ),
        run_id=state.run_id,
    )


def _assessment() -> dict:
    return {
        "assessment_id": "claim-1",
        "match_id": "match-1",
        "policy_id": "policy-1",
        "policy_title": "Policy title",
        "impact_level": "P2",
        "impact_type": "opportunity",
        "relevance_score": 70,
        "reasoning": ["Policy applies."],
        "company_evidence": [],
        "policy_evidence": {
            "policy_id": "policy-1",
            "clause_id": "clause-1",
            "text": "Eligible companies may apply.",
        },
        "missing_fields": [],
        "recommended_actions": [],
        "applicability": "direct",
        "binding_effect": "encouraged",
    }


def test_publication_block_happens_before_any_report_file_or_artifact_write() -> None:
    state = PolicyImpactState(company_id="company-block", run_id="run-block")
    state.impact_assessments = [_assessment()]
    state.review_result = {
        "passed": True,
        "claim_review_audit_refs": {"claim-1": "missing-review-proof"},
    }
    gateway = TrackingReportGateway()

    with pytest.raises(generate_weekly_report.PublicationGateBlocked):
        generate_weekly_report.run(
            state,
            gateway=gateway,
            gate_engine=_gate_engine(state),
        )

    assert gateway.calls == []
    assert state.report_paths == {}
    assert "run_artifact" not in state.artifacts


def test_valid_direct_pipeline_audits_every_business_gate_before_report_write() -> None:
    result = run_policy_weekly_impact(
        "company_001",
        date_from="2026-06-30",
        date_to="2026-07-05",
        refresh_live_sources=False,
        allow_fixture_fallback=True,
    )
    state = result.state
    records = [
        item
        for item in state.stage_gate_results
        if item.get("record_type") == "business_gate_evaluation"
    ]

    assert state.current_stage == "done"
    assert state.impact_assessments
    assert state.review_result["claim_review_audit_refs"]
    for assessment in state.impact_assessments:
        claim_id = assessment["assessment_id"]
        claim_records = [
            item
            for item in records
            if claim_id in item.get("input_refs", [])
        ]
        assert [item["gate_id"] for item in claim_records] == [
            "claim_schema",
            "evidence_binding",
            "review_verdict",
            "publication",
        ]
        assert all(item["decision"] == "pass" for item in claim_records)
    assert result.report_path
    assert Path(result.report_path).exists()


def test_successful_no_updates_collection_publishes_without_fake_claim(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        "policy_impact.harness.artifacts.project_root", lambda: tmp_path
    )
    monkeypatch.setattr(
        "policy_impact.memory.store.company_dir",
        lambda company_id: tmp_path / "companies" / company_id,
    )
    state = PolicyImpactState(company_id="company-no-updates", run_id="run-no-updates")
    state.source_manifest = {"no_updates": True, "collection_succeeded": True}
    gateway = SuccessfulReportGateway(tmp_path)

    generate_weekly_report.run(
        state,
        gateway=gateway,
        gate_engine=_gate_engine(state),
    )

    assert len(gateway.calls) == 2
    assert state.report_paths["report"]
    assert state.report_paths["html_report"]
    assert state.report_paths["run_artifact"]
    assert state.impact_assessments == []
    business_records = [
        item
        for item in state.stage_gate_results
        if item.get("record_type") == "business_gate_evaluation"
    ]
    assert [(item["gate_id"], item["decision"]) for item in business_records] == [
        ("publication", "pass")
    ]
    assert business_records[0]["audit_metadata"]["publication_kind"] == "no_updates"
