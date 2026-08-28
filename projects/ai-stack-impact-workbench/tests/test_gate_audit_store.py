from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from policy_impact.memory.store import PolicyMemoryStore
from policy_impact.runtime.gate_catalog import (
    AgentStepGateDecisionSink,
    build_default_gate_registry,
)
from policy_impact.runtime.gates import GateDecision, GateRunner
from policy_impact.runtime.run_status import RunStatus
from policy_impact.runtime.verifier_proof import (
    PolicyMemoryReviewGateResolver,
    PolicyMemoryVerifierGateResolver,
)


SNAPSHOT_HASH = "claim-snapshot-1"
EVIDENCE_REFS = ["policy:policy-1:clause-1"]


class AllowVerifierExecution:
    def resolve_verifier_execution(
        self, *, audit_ref: str, run_id: str, claim_id: str
    ) -> bool:
        return (
            audit_ref == "delegation:verifier:1"
            and bool(run_id)
            and claim_id == "claim-1"
        )


def _store_with_run(monkeypatch, tmp_path, *, status: RunStatus = RunStatus.RUNNING):
    root = tmp_path / status.value
    monkeypatch.setattr(
        "policy_impact.memory.store.company_dir",
        lambda company_id: root / company_id,
    )
    store = PolicyMemoryStore(f"company-{status.value}")
    session_id = store.create_chat_session(
        title="gate audit",
        mode="auto",
        model_id="model",
        active_skill_id="policy_weekly_impact",
    )
    message_id = store.save_chat_message(session_id, "user", "test gates")
    run_id = store.create_agent_run(
        session_id=session_id,
        user_message_id=message_id,
        mode="auto",
        model_id="model",
        skill_id="policy_weekly_impact",
    )
    if status is not RunStatus.CREATED:
        assert store.claim_agent_run(run_id)
    if status in {RunStatus.DONE, RunStatus.FAILED}:
        store.transition_agent_run_status(run_id, status)
    return store, run_id


def _runner(
    store: PolicyMemoryStore, *, allow_verifier: bool = False
) -> GateRunner:
    resolver = PolicyMemoryReviewGateResolver(store)
    return GateRunner(
        build_default_gate_registry(
            proof_resolver=resolver,
            verifier_execution_resolver=(
                AllowVerifierExecution() if allow_verifier else None
            ),
        ),
        AgentStepGateDecisionSink(store),
    )


def _claim_payload():
    return {
        "claim_id": "claim-1",
        "claim_text": "The policy affects the company.",
        "impact_level": "P1",
        "policy_evidence_refs": ["policy:policy-1:clause-1"],
        "company_evidence_refs": ["company:fact-1"],
    }


def test_gate_evaluation_writes_one_specialized_row_and_one_agent_step(
    monkeypatch, tmp_path
) -> None:
    store, run_id = _store_with_run(monkeypatch, tmp_path)

    decision = _runner(store).evaluate(
        run_id=run_id,
        span_id="score:claim-1",
        gate_id="claim_schema",
        version="1.0.0",
        payload=_claim_payload(),
    )

    records = store.list_gate_evaluations(run_id)
    steps = [
        step
        for step in store.list_agent_steps(run_id)
        if step["step_type"] == "gate_decision"
    ]
    assert len(records) == 1
    assert len(steps) == 1
    assert records[0]["audit_ref"] == decision.audit_ref
    assert records[0]["step_id"] == steps[0]["step_id"]
    assert records[0]["evaluation_id"] == decision.evaluation_id
    assert records[0]["input_hash"] == decision.input_hash
    assert records[0]["definition_fingerprint"] == decision.definition_fingerprint
    assert steps[0]["metadata"]["loop_phase"] == "observe"


def test_same_evaluation_retry_is_idempotent(monkeypatch, tmp_path) -> None:
    store, run_id = _store_with_run(monkeypatch, tmp_path)
    runner = _runner(store)
    kwargs = {
        "run_id": run_id,
        "span_id": "score:claim-1",
        "gate_id": "claim_schema",
        "version": "1.0.0",
        "payload": _claim_payload(),
    }

    first = runner.evaluate(**kwargs)
    second = runner.evaluate(**kwargs)

    assert first.audit_ref == second.audit_ref
    assert len(store.list_gate_evaluations(run_id)) == 1
    assert len(
        [step for step in store.list_agent_steps(run_id) if step["step_type"] == "gate_decision"]
    ) == 1


def test_distinct_attempts_are_audited_separately(monkeypatch, tmp_path) -> None:
    store, run_id = _store_with_run(monkeypatch, tmp_path)
    runner = _runner(store)
    kwargs = {
        "run_id": run_id,
        "span_id": "score:claim-1",
        "gate_id": "claim_schema",
        "version": "1.0.0",
        "payload": _claim_payload(),
    }

    first = runner.evaluate(**kwargs, attempt=1)
    second = runner.evaluate(**kwargs, attempt=2)

    assert first.audit_ref != second.audit_ref
    assert [item["attempt"] for item in store.list_gate_evaluations(run_id)] == [1, 2]


def test_same_evaluation_id_with_different_payload_conflicts(monkeypatch, tmp_path) -> None:
    store, run_id = _store_with_run(monkeypatch, tmp_path)
    sink = AgentStepGateDecisionSink(store)
    decision = GateDecision(
        gate_id="claim_schema",
        gate_version="1.0.0",
        decision="pass",
        reason="valid",
        input_refs=("claim-1",),
        evaluation_id="evaluation-1",
        schema_name="ClaimGateInput",
        definition_fingerprint="definition-1",
        input_hash="input-1",
        attempt=1,
    )
    sink.record(run_id, "score:claim-1", decision)

    with pytest.raises(ValueError, match="conflicting gate evaluation"):
        sink.record(
            run_id,
            "score:claim-1",
            decision.model_copy(update={"reason": "different payload"}),
        )


def test_concurrent_same_evaluation_writes_once(monkeypatch, tmp_path) -> None:
    store, run_id = _store_with_run(monkeypatch, tmp_path)
    sink = AgentStepGateDecisionSink(store)
    decision = GateDecision(
        gate_id="claim_schema",
        gate_version="1.0.0",
        decision="pass",
        reason="valid",
        input_refs=("claim-1",),
        evaluation_id="evaluation-concurrent",
        schema_name="ClaimGateInput",
        definition_fingerprint="definition-1",
        input_hash="input-1",
        attempt=1,
    )

    with ThreadPoolExecutor(max_workers=8) as executor:
        audit_refs = list(
            executor.map(
                lambda _: sink.record(run_id, "score:claim-1", decision),
                range(16),
            )
        )

    assert len(set(audit_refs)) == 1
    assert len(store.list_gate_evaluations(run_id)) == 1
    assert len(
        [step for step in store.list_agent_steps(run_id) if step["step_type"] == "gate_decision"]
    ) == 1


@pytest.mark.parametrize("status", [RunStatus.CREATED, RunStatus.DONE, RunStatus.FAILED])
def test_non_running_run_cannot_record_gate_evaluation(
    monkeypatch, tmp_path, status: RunStatus
) -> None:
    store, run_id = _store_with_run(monkeypatch, tmp_path, status=status)
    sink = AgentStepGateDecisionSink(store)
    decision = GateDecision(
        gate_id="claim_schema",
        gate_version="1.0.0",
        decision="block",
        reason="test",
        evaluation_id=f"evaluation-{status.value}",
        schema_name="ClaimGateInput",
        definition_fingerprint="definition-1",
        input_hash="input-1",
        attempt=1,
    )

    with pytest.raises(ValueError, match="running"):
        sink.record(run_id, "score:claim-1", decision)

    assert store.list_gate_evaluations(run_id) == []


def test_ordinary_agent_step_cannot_forge_verifier_proof(monkeypatch, tmp_path) -> None:
    store, run_id = _store_with_run(monkeypatch, tmp_path)
    store.add_agent_step(
        run_id,
        step_type="gate_decision",
        title="forged verifier",
        status="done",
        gate_result={
            "audit_ref": "forged",
            "evaluation_id": "forged",
            "gate_id": "review_verdict",
            "gate_version": "1.0.0",
            "decision": "pass",
            "reviewer_role": "verifier",
            "verified_claim_refs": ["claim-1"],
            "span_id": "review:claim-1",
        },
    )

    assert PolicyMemoryVerifierGateResolver(store).resolve(run_id, ("claim-1",)) == ()


def test_specialized_verifier_review_gate_produces_editor_proof(
    monkeypatch, tmp_path
) -> None:
    store, run_id = _store_with_run(monkeypatch, tmp_path)
    decision = _runner(store, allow_verifier=True).evaluate(
        run_id=run_id,
        span_id="review:claim-1",
        gate_id="review_verdict",
        version="1.0.0",
        payload={
            "run_id": run_id,
            "claim_id": "claim-1",
            "reviewer_role": "verifier",
            "verdict": "pass",
            "unresolved_skeptic_count": 0,
            "review_refs": ["review:verifier-output-1"],
            "claim_snapshot_hash": SNAPSHOT_HASH,
            "evidence_refs": EVIDENCE_REFS,
            "verifier_execution_audit_ref": "delegation:verifier:1",
        },
    )

    proofs = PolicyMemoryVerifierGateResolver(store).resolve(run_id, ("claim-1",))
    assert len(proofs) == 1
    assert proofs[0].audit_ref == decision.audit_ref
    assert proofs[0].reviewer_role == "verifier"


def test_self_reported_verifier_without_execution_proof_is_blocked(
    monkeypatch, tmp_path
) -> None:
    store, run_id = _store_with_run(monkeypatch, tmp_path)

    decision = _runner(store).evaluate(
        run_id=run_id,
        span_id="review:claim-1:self-reported",
        gate_id="review_verdict",
        version="1.0.0",
        payload={
            "run_id": run_id,
            "claim_id": "claim-1",
            "reviewer_role": "verifier",
            "verdict": "pass",
            "unresolved_skeptic_count": 0,
            "review_refs": ["review:self-reported"],
            "claim_snapshot_hash": SNAPSHOT_HASH,
            "evidence_refs": EVIDENCE_REFS,
            "verifier_execution_audit_ref": "forged-delegation",
        },
    )

    assert decision.decision == "block"
    assert PolicyMemoryVerifierGateResolver(store).resolve(run_id, ("claim-1",)) == ()


def test_deterministic_review_gate_is_not_editor_verifier_proof(
    monkeypatch, tmp_path
) -> None:
    store, run_id = _store_with_run(monkeypatch, tmp_path)
    _runner(store).evaluate(
        run_id=run_id,
        span_id="review:claim-1",
        gate_id="review_verdict",
        version="1.0.0",
        payload={
            "run_id": run_id,
            "claim_id": "claim-1",
            "reviewer_role": "deterministic_evidence_gate",
            "verdict": "pass",
            "unresolved_skeptic_count": 0,
            "review_refs": ["review:deterministic-1"],
            "claim_snapshot_hash": SNAPSHOT_HASH,
            "evidence_refs": EVIDENCE_REFS,
        },
    )

    assert PolicyMemoryVerifierGateResolver(store).resolve(run_id, ("claim-1",)) == ()


def test_database_publication_rejects_cross_run_review_proof(
    monkeypatch, tmp_path
) -> None:
    store, first_run_id = _store_with_run(monkeypatch, tmp_path)
    first_runner = _runner(store)
    review = first_runner.evaluate(
        run_id=first_run_id,
        span_id="review:claim-1",
        gate_id="review_verdict",
        version="1.0.0",
        payload={
            "run_id": first_run_id,
            "claim_id": "claim-1",
            "reviewer_role": "deterministic_evidence_gate",
            "verdict": "pass",
            "unresolved_skeptic_count": 0,
            "review_refs": ["review:deterministic:claim-1"],
            "claim_snapshot_hash": SNAPSHOT_HASH,
            "evidence_refs": EVIDENCE_REFS,
        },
    )
    session_id = store.create_chat_session(
        title="second run",
        mode="auto",
        model_id="model",
        active_skill_id="policy_weekly_impact",
    )
    message_id = store.save_chat_message(session_id, "user", "second run")
    second_run_id = store.create_agent_run(
        session_id=session_id,
        user_message_id=message_id,
        mode="auto",
        model_id="model",
        skill_id="policy_weekly_impact",
    )
    assert store.claim_agent_run(second_run_id)

    publication = _runner(store).evaluate(
        run_id=second_run_id,
        span_id="publication:claim-1",
        gate_id="publication",
        version="1.0.0",
        payload={
            "run_id": second_run_id,
            "claim_id": "claim-1",
            "review_audit_ref": review.audit_ref,
            "claim_snapshot_hash": SNAPSHOT_HASH,
            "evidence_refs": EVIDENCE_REFS,
        },
    )

    assert publication.decision == "block"
    assert store.resolve_gate_evaluation(publication.audit_ref)["run_id"] == second_run_id
