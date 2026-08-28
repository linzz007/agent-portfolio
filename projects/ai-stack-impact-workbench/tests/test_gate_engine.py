from __future__ import annotations

import pytest

from policy_impact.harness.contracts import EvidenceSpan, ImpactClaim
from policy_impact.harness.gate_engine import GateEngine
from policy_impact.harness.state import PolicyImpactState
from policy_impact.runtime.gate_catalog import (
    StateGateDecisionSink,
    build_default_gate_registry,
)
from policy_impact.runtime.gates import GateRunner


SNAPSHOT_HASH = "claim-snapshot-1"
EVIDENCE_REFS = ["policy:policy-1:clause-1"]


def _span(span_id: str = "clause-1") -> EvidenceSpan:
    return EvidenceSpan(
        source_id="policy-1",
        text="Eligible firms may apply for export credit support.",
        start_char=0,
        end_char=52,
        metadata={"span_id": span_id, "evidence_category": "policy"},
    )


def _claim(
    *,
    summary: str = "Company may qualify for export credit support.",
    severity: str = "P2",
    evidence_spans: list[EvidenceSpan] | None = None,
    company_refs: tuple[str, ...] = (),
) -> ImpactClaim:
    return ImpactClaim(
        claim_id="claim-1",
        company_id="company-1",
        policy_id="policy-1",
        summary=summary,
        impact_area="finance",
        severity=severity,
        evidence_spans=evidence_spans if evidence_spans is not None else [_span()],
        metadata={"company_evidence_refs": list(company_refs)},
    )


def _engine(state: PolicyImpactState) -> GateEngine:
    sink = StateGateDecisionSink(state)
    runner = GateRunner(
        build_default_gate_registry(proof_resolver=sink),
        sink,
    )
    return GateEngine(runner=runner, run_id=state.run_id)


def test_gate_engine_requires_injected_runner() -> None:
    with pytest.raises(TypeError):
        GateEngine()


def test_valid_claim_runs_schema_then_evidence_gate() -> None:
    state = PolicyImpactState(run_id="run-claim-pass")

    decision = _engine(state).evaluate_claim(_claim())

    business_records = [
        item
        for item in state.stage_gate_results
        if item.get("record_type") == "business_gate_evaluation"
    ]
    assert decision.passed is True
    assert [item["gate_id"] for item in business_records] == [
        "claim_schema",
        "evidence_binding",
    ]
    assert decision.metadata["gate_id"] == "evidence_binding"
    assert decision.metadata["gate_version"] == "1.0.0"
    assert decision.metadata["audit_ref"]


def test_schema_block_short_circuits_evidence_gate() -> None:
    state = PolicyImpactState(run_id="run-claim-block")

    decision = _engine(state).evaluate_claim(_claim(summary=""))

    business_records = [
        item
        for item in state.stage_gate_results
        if item.get("record_type") == "business_gate_evaluation"
    ]
    assert decision.passed is False
    assert [item["gate_id"] for item in business_records] == ["claim_schema"]
    assert decision.metadata["decision"] == "block"


def test_p1_claim_requires_both_policy_and_company_evidence() -> None:
    state = PolicyImpactState(run_id="run-p1")
    engine = _engine(state)

    blocked = engine.evaluate_claim(_claim(severity="P1"))
    passed = engine.evaluate_claim(
        _claim(
            severity="P1",
            company_refs=("company:fact-1",),
        ),
        span_id="score:claim-1:second",
    )

    assert blocked.passed is False
    assert passed.passed is True


def test_review_then_publication_uses_persisted_audit_ref() -> None:
    state = PolicyImpactState(run_id="run-publication")
    engine = _engine(state)
    review = engine.evaluate_review(
        {
            "claim_id": "claim-1",
            "reviewer_role": "deterministic_evidence_gate",
            "verdict": "pass",
            "unresolved_skeptic_count": 0,
            "review_refs": ["review:deterministic:claim-1"],
            "claim_snapshot_hash": SNAPSHOT_HASH,
            "evidence_refs": EVIDENCE_REFS,
        }
    )

    publication = engine.evaluate_publication(
        {
            "run_id": state.run_id,
            "claim_id": "claim-1",
            "review_audit_ref": review.metadata["audit_ref"],
            "claim_snapshot_hash": SNAPSHOT_HASH,
            "evidence_refs": EVIDENCE_REFS,
        }
    )

    assert review.passed is True
    assert publication.passed is True
    assert publication.metadata["gate_id"] == "publication"


def test_publication_rejects_missing_or_cross_run_review_ref() -> None:
    first_state = PolicyImpactState(run_id="run-first")
    first_engine = _engine(first_state)
    review = first_engine.evaluate_review(
        {
            "claim_id": "claim-1",
            "reviewer_role": "deterministic_evidence_gate",
            "verdict": "pass",
            "unresolved_skeptic_count": 0,
            "review_refs": ["review:deterministic:claim-1"],
            "claim_snapshot_hash": SNAPSHOT_HASH,
            "evidence_refs": EVIDENCE_REFS,
        }
    )
    second_state = PolicyImpactState(run_id="run-second")
    second_engine = _engine(second_state)

    missing = second_engine.evaluate_publication(
        {
            "run_id": second_state.run_id,
            "claim_id": "claim-1",
            "review_audit_ref": "missing",
            "claim_snapshot_hash": SNAPSHOT_HASH,
            "evidence_refs": EVIDENCE_REFS,
        }
    )
    cross_run = second_engine.evaluate_publication(
        {
            "run_id": second_state.run_id,
            "claim_id": "claim-1",
            "review_audit_ref": review.metadata["audit_ref"],
            "claim_snapshot_hash": SNAPSHOT_HASH,
            "evidence_refs": EVIDENCE_REFS,
        },
        span_id="publication:claim-1:cross-run",
    )

    assert missing.passed is False
    assert cross_run.passed is False


@pytest.mark.parametrize(
    ("snapshot_hash", "evidence_refs"),
    [
        ("mutated-snapshot", EVIDENCE_REFS),
        (SNAPSHOT_HASH, ["policy:policy-1:different-clause"]),
    ],
)
def test_publication_rejects_claim_or_evidence_changed_after_review(
    snapshot_hash, evidence_refs
) -> None:
    state = PolicyImpactState(run_id="run-mutated-claim")
    engine = _engine(state)
    review = engine.evaluate_review(
        {
            "claim_id": "claim-1",
            "reviewer_role": "deterministic_evidence_gate",
            "verdict": "pass",
            "unresolved_skeptic_count": 0,
            "review_refs": ["review:deterministic:claim-1"],
            "claim_snapshot_hash": SNAPSHOT_HASH,
            "evidence_refs": EVIDENCE_REFS,
        }
    )

    publication = engine.evaluate_publication(
        {
            "run_id": state.run_id,
            "claim_id": "claim-1",
            "review_audit_ref": review.metadata["audit_ref"],
            "claim_snapshot_hash": snapshot_hash,
            "evidence_refs": evidence_refs,
        }
    )

    assert publication.passed is False


def test_direct_state_sink_does_not_trust_forged_public_stage_record() -> None:
    state = PolicyImpactState(run_id="run-forged-state-proof")
    engine = _engine(state)
    state.add_stage_gate_result(
        {
            "record_type": "business_gate_evaluation",
            "audit_ref": "state-gate-evaluation:forged",
            "evaluation_id": "forged",
            "run_id": state.run_id,
            "span_id": "review:claim-1",
            "gate_id": "review_verdict",
            "gate_version": "1.0.0",
            "decision": "pass",
            "audit_metadata": {
                "claim_id": "claim-1",
                "reviewer_role": "deterministic_evidence_gate",
                "review_refs": ["review:forged"],
                "claim_snapshot_hash": SNAPSHOT_HASH,
                "evidence_refs": EVIDENCE_REFS,
            },
        }
    )

    publication = engine.evaluate_publication(
        {
            "run_id": state.run_id,
            "claim_id": "claim-1",
            "review_audit_ref": "state-gate-evaluation:forged",
            "claim_snapshot_hash": SNAPSHOT_HASH,
            "evidence_refs": EVIDENCE_REFS,
        }
    )

    assert publication.passed is False


def test_mutating_mirrored_state_gate_record_does_not_change_authoritative_proof() -> None:
    state = PolicyImpactState(run_id="run-mirrored-record")
    engine = _engine(state)
    review = engine.evaluate_review(
        {
            "claim_id": "claim-1",
            "reviewer_role": "deterministic_evidence_gate",
            "verdict": "pass",
            "unresolved_skeptic_count": 0,
            "review_refs": ["review:deterministic:claim-1"],
            "claim_snapshot_hash": SNAPSHOT_HASH,
            "evidence_refs": EVIDENCE_REFS,
        }
    )
    mirrored = next(
        item
        for item in state.stage_gate_results
        if item.get("audit_ref") == review.metadata["audit_ref"]
    )
    mirrored["audit_metadata"]["claim_snapshot_hash"] = "forged"

    publication = engine.evaluate_publication(
        {
            "run_id": state.run_id,
            "claim_id": "claim-1",
            "review_audit_ref": review.metadata["audit_ref"],
            "claim_snapshot_hash": SNAPSHOT_HASH,
            "evidence_refs": EVIDENCE_REFS,
        }
    )

    assert publication.passed is True


def test_repair_is_never_mapped_to_compatibility_pass() -> None:
    state = PolicyImpactState(run_id="run-repair")
    decision = _engine(state).evaluate_review(
        {
            "claim_id": "claim-1",
            "reviewer_role": "verifier",
            "verdict": "uncertain",
            "unresolved_skeptic_count": 1,
            "review_refs": ["review:verifier:claim-1"],
            "claim_snapshot_hash": SNAPSHOT_HASH,
            "evidence_refs": EVIDENCE_REFS,
            "requested_repair": "retry_verification",
        }
    )

    assert decision.passed is False
    assert decision.metadata["decision"] == "repair"
    assert decision.metadata["repair_action"] == {
        "action": "retry_verification"
    }
