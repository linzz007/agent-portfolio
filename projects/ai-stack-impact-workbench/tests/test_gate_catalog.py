from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from policy_impact.runtime.gate_catalog import (
    ClaimGateInput,
    InMemoryReviewGateProofResolver,
    PublicationGateInput,
    ReviewGateInput,
    build_default_gate_registry,
)
from policy_impact.runtime.gates import (
    GateAuditPersistenceError,
    GateDecision,
    GateDefinition,
    GateRunner,
    GateRegistry,
)


class RecordingSink:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, GateDecision]] = []

    def record(self, run_id: str, span_id: str, decision: GateDecision) -> str:
        self.records.append((run_id, span_id, decision))
        return f"audit:{decision.evaluation_id}"


def _runner(*, proof_resolver=None) -> tuple[GateRunner, RecordingSink]:
    sink = RecordingSink()
    registry = build_default_gate_registry(
        proof_resolver=proof_resolver or InMemoryReviewGateProofResolver()
    )
    return GateRunner(registry, sink), sink


def _claim_payload(
    *,
    impact_level: str = "P1",
    policy_refs: list[str] | tuple[str, ...] = ("policy:policy-1:clause-1",),
    company_refs: list[str] | tuple[str, ...] = ("company:fact-1",),
) -> dict[str, Any]:
    return {
        "claim_id": "claim-1",
        "claim_text": "The policy affects the company.",
        "impact_level": impact_level,
        "policy_evidence_refs": policy_refs,
        "company_evidence_refs": company_refs,
    }


def test_default_gate_catalog_has_exact_versioned_contracts() -> None:
    registry = build_default_gate_registry(
        proof_resolver=InMemoryReviewGateProofResolver()
    )

    contracts = {
        (
            definition.gate_id,
            definition.version,
            definition.input_schema.__name__,
            definition.hard_constraint,
            definition.repairable,
            definition.allowed_repair_actions,
        )
        for definition in registry.definitions()
    }

    assert contracts == {
        ("claim_schema", "1.0.0", "ClaimGateInput", True, False, ()),
        ("evidence_binding", "1.0.0", "ClaimGateInput", True, False, ()),
        (
            "review_verdict",
            "1.0.0",
            "ReviewGateInput",
            True,
            True,
            ("retry_verification",),
        ),
        (
            "publication",
            "1.0.0",
            "PublicationGateInput",
            True,
            True,
            ("downgrade", "retry_verification"),
        ),
    }


@pytest.mark.parametrize("impact_level", ["P0", "P1"])
def test_p0_p1_require_policy_and_company_evidence(impact_level: str) -> None:
    runner, _ = _runner()

    passed = runner.evaluate(
        run_id="run-1",
        span_id="score:claim-1",
        gate_id="evidence_binding",
        version="1.0.0",
        payload=_claim_payload(impact_level=impact_level),
    )
    missing_company = runner.evaluate(
        run_id="run-1",
        span_id="score:claim-1:missing-company",
        gate_id="evidence_binding",
        version="1.0.0",
        payload=_claim_payload(impact_level=impact_level, company_refs=()),
    )
    missing_policy = runner.evaluate(
        run_id="run-1",
        span_id="score:claim-1:missing-policy",
        gate_id="evidence_binding",
        version="1.0.0",
        payload=_claim_payload(impact_level=impact_level, policy_refs=()),
    )

    assert passed.decision == "pass"
    assert missing_company.decision == "block"
    assert missing_policy.decision == "block"


@pytest.mark.parametrize("impact_level", ["P2", "P3", "P4"])
def test_p2_p4_require_policy_evidence_but_not_company_evidence(
    impact_level: str,
) -> None:
    runner, _ = _runner()

    passed = runner.evaluate(
        run_id="run-1",
        span_id=f"score:{impact_level}:pass",
        gate_id="evidence_binding",
        version="1.0.0",
        payload=_claim_payload(impact_level=impact_level, company_refs=()),
    )
    blocked = runner.evaluate(
        run_id="run-1",
        span_id=f"score:{impact_level}:block",
        gate_id="evidence_binding",
        version="1.0.0",
        payload=_claim_payload(
            impact_level=impact_level,
            policy_refs=(),
            company_refs=("company:fact-1",),
        ),
    )

    assert passed.decision == "pass"
    assert blocked.decision == "block"


@pytest.mark.parametrize(
    ("policy_refs", "company_refs"),
    [
        (("",), ("company:fact-1",)),
        (("policy:policy-1:clause-1", "policy:policy-1:clause-1"), ("company:fact-1",)),
        (("company:fact-1",), ("company:fact-2",)),
        (("policy:policy-1:clause-1",), ("policy:policy-1:clause-2",)),
    ],
    ids=["blank", "duplicate", "company-as-policy", "policy-as-company"],
)
def test_blank_duplicate_or_wrong_category_evidence_fails_closed(
    policy_refs: tuple[str, ...],
    company_refs: tuple[str, ...],
) -> None:
    runner, sink = _runner()

    decision = runner.evaluate(
        run_id="run-1",
        span_id="score:invalid-ref",
        gate_id="claim_schema",
        version="1.0.0",
        payload=_claim_payload(policy_refs=policy_refs, company_refs=company_refs),
    )

    assert decision.decision == "block"
    assert len(sink.records) == 1


class Payload(BaseModel):
    refs: tuple[str, ...]


@pytest.mark.parametrize(
    ("failure_kind", "evaluator"),
    [
        (
            "identity",
            lambda payload: GateDecision(
                gate_id="wrong",
                gate_version="1.0.0",
                decision="pass",
                reason="wrong identity",
                input_refs=payload.refs,
            ),
        ),
        (
            "illegal-repair",
            lambda payload: GateDecision(
                gate_id="custom",
                gate_version="1.0.0",
                decision="repair",
                reason="unsupported repair",
                input_refs=payload.refs,
                repair_action={"action": "bypass"},
            ),
        ),
        (
            "ref-mismatch",
            lambda payload: GateDecision(
                gate_id="custom",
                gate_version="1.0.0",
                decision="pass",
                reason="wrong refs",
                input_refs=("forged",),
            ),
        ),
    ],
)
def test_identity_illegal_repair_and_ref_mismatch_are_persisted_blocks(
    failure_kind: str,
    evaluator,
) -> None:
    registry = GateRegistry()
    registry.register(
        GateDefinition(
            gate_id="custom",
            version="1.0.0",
            description="test gate",
            input_schema=Payload,
            hard_constraint=True,
            repairable=True,
            allowed_repair_actions=("retry_verification",),
            input_ref_extractor=lambda payload: payload.refs,
        ),
        evaluator,
    )
    sink = RecordingSink()

    decision = GateRunner(registry, sink).evaluate(
        run_id="run-1",
        span_id=f"span:{failure_kind}",
        gate_id="custom",
        version="1.0.0",
        payload={"refs": ["ref-1"]},
    )

    assert decision.decision == "block"
    assert len(sink.records) == 1
    assert sink.records[0][2].decision == "block"


def test_schema_and_evaluator_failures_are_sanitized_and_persisted() -> None:
    class StrictPayload(BaseModel):
        value: int

    registry = GateRegistry()
    registry.register(
        GateDefinition(
            gate_id="fails",
            version="1.0.0",
            description="failing evaluator",
            input_schema=StrictPayload,
            hard_constraint=True,
            repairable=False,
            input_ref_extractor=lambda payload: (),
        ),
        lambda payload: (_ for _ in ()).throw(RuntimeError("private-secret")),
    )
    sink = RecordingSink()
    runner = GateRunner(registry, sink)

    schema_failure = runner.evaluate(
        run_id="run-1",
        span_id="span:schema",
        gate_id="fails",
        version="1.0.0",
        payload={"value": "not-an-int"},
    )
    evaluator_failure = runner.evaluate(
        run_id="run-1",
        span_id="span:evaluator",
        gate_id="fails",
        version="1.0.0",
        payload={"value": 1},
    )

    assert schema_failure.decision == "block"
    assert evaluator_failure.decision == "block"
    assert "private-secret" not in evaluator_failure.reason
    assert len(sink.records) == 2


def test_catalog_schema_names_are_public_contracts() -> None:
    assert ClaimGateInput.__name__ == "ClaimGateInput"
    assert ReviewGateInput.__name__ == "ReviewGateInput"
    assert PublicationGateInput.__name__ == "PublicationGateInput"


def test_sink_failure_prevents_gate_decision_from_escaping() -> None:
    class FailingSink:
        def record(self, run_id: str, span_id: str, decision: GateDecision) -> str:
            raise RuntimeError("PRIVATE_DATABASE_DETAIL")

    runner = GateRunner(
        build_default_gate_registry(
            proof_resolver=InMemoryReviewGateProofResolver()
        ),
        FailingSink(),
    )

    with pytest.raises(GateAuditPersistenceError) as exc_info:
        runner.evaluate(
            run_id="run-1",
            span_id="score:claim-1",
            gate_id="claim_schema",
            version="1.0.0",
            payload=_claim_payload(),
        )

    assert "PRIVATE_DATABASE_DETAIL" not in str(exc_info.value)
