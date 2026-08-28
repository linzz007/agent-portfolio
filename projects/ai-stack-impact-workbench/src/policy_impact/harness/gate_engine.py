"""Compatibility views over the authoritative versioned Gate runtime."""

from __future__ import annotations

from typing import Any

from policy_impact.harness.contracts import GateDecision as GateDecisionView
from policy_impact.harness.contracts import ImpactClaim
from policy_impact.runtime.gates import GateDecision, GateRunner


class GateEngine:
    """Translate domain inputs and delegate every decision to one injected runner."""

    def __init__(self, *, runner: GateRunner, run_id: str, attempt: int = 1) -> None:
        self._runner = runner
        self._run_id = str(run_id).strip()
        self._attempt = attempt
        if not self._run_id:
            raise ValueError("GateEngine run_id must not be blank")

    def evaluate_claim(
        self,
        claim: ImpactClaim | dict[str, Any],
        *,
        span_id: str | None = None,
        attempt: int | None = None,
    ) -> GateDecisionView:
        payload = self._claim_payload(claim)
        span = span_id or f"score_policy_impact:{payload.get('claim_id') or 'unknown'}"
        schema_decision = self._runner.evaluate(
            run_id=self._run_id,
            span_id=span,
            gate_id="claim_schema",
            version="1.0.0",
            payload=payload,
            attempt=attempt or self._attempt,
        )
        if schema_decision.decision != "pass":
            return self._view(schema_decision)
        evidence_decision = self._runner.evaluate(
            run_id=self._run_id,
            span_id=span,
            gate_id="evidence_binding",
            version="1.0.0",
            payload=payload,
            attempt=attempt or self._attempt,
        )
        return self._view(evidence_decision)

    def evaluate_review(
        self,
        payload: dict[str, Any],
        *,
        span_id: str | None = None,
        attempt: int | None = None,
    ) -> GateDecisionView:
        payload = dict(payload)
        payload.setdefault("run_id", self._run_id)
        claim_id = str(payload.get("claim_id") or "unknown")
        decision = self._runner.evaluate(
            run_id=self._run_id,
            span_id=span_id or f"review_evidence_and_risk:{claim_id}",
            gate_id="review_verdict",
            version="1.0.0",
            payload=payload,
            attempt=attempt or self._attempt,
        )
        return self._view(decision)

    def evaluate_publication(
        self,
        payload: dict[str, Any],
        *,
        span_id: str | None = None,
        attempt: int | None = None,
    ) -> GateDecisionView:
        payload = dict(payload)
        payload.setdefault("run_id", self._run_id)
        claim_id = str(payload.get("claim_id") or "unknown")
        decision = self._runner.evaluate(
            run_id=self._run_id,
            span_id=span_id or f"generate_weekly_report:{claim_id}",
            gate_id="publication",
            version="1.0.0",
            payload=payload,
            attempt=attempt or self._attempt,
        )
        return self._view(decision)

    @staticmethod
    def _claim_payload(claim: ImpactClaim | dict[str, Any]) -> dict[str, Any]:
        if isinstance(claim, dict):
            return dict(claim)
        policy_refs: list[str] = []
        company_refs: list[str] = [
            str(ref)
            for ref in claim.metadata.get("company_evidence_refs") or []
        ]
        for span in claim.evidence_spans:
            raw_ref = str(
                span.metadata.get("span_id")
                or span.metadata.get("id")
                or span.source_id
            ).strip()
            category = str(
                span.metadata.get("evidence_category") or "policy"
            ).strip()
            if category == "company":
                company_refs.append(
                    raw_ref if raw_ref.startswith("company:") else f"company:{raw_ref}"
                )
            else:
                policy_refs.append(
                    raw_ref
                    if raw_ref.startswith("policy:")
                    else f"policy:{claim.policy_id}:{raw_ref}"
                )
        return {
            "claim_id": claim.claim_id,
            "claim_text": claim.summary,
            "impact_level": claim.severity,
            "policy_evidence_refs": policy_refs,
            "company_evidence_refs": company_refs,
        }

    @staticmethod
    def _view(decision: GateDecision) -> GateDecisionView:
        repair_action = (
            decision.model_dump(mode="json").get("repair_action")
            if decision.repair_action is not None
            else None
        )
        return GateDecisionView(
            gate_name=decision.gate_id,
            passed=decision.decision == "pass",
            reasons=[] if decision.decision == "pass" else [decision.reason],
            metadata={
                "gate_id": decision.gate_id,
                "gate_version": decision.gate_version,
                "decision": decision.decision,
                "severity": (
                    "info"
                    if decision.decision == "pass"
                    else "warn" if decision.decision == "repair" else "block"
                ),
                "input_refs": list(decision.input_refs),
                "evidence_refs": list(decision.input_refs),
                "output_ref": decision.output_ref,
                "repair_action": repair_action,
                "audit_ref": decision.audit_ref,
                "evaluation_id": decision.evaluation_id,
                "schema_name": decision.schema_name,
                "definition_fingerprint": decision.definition_fingerprint,
                "input_hash": decision.input_hash,
                "attempt": decision.attempt,
                "audit_metadata": decision.model_dump(mode="json").get(
                    "audit_metadata", {}
                ),
            },
        )


def claim_payload_from_assessment(assessment: dict[str, Any]) -> dict[str, Any]:
    claim_id = str(assessment.get("assessment_id") or "").strip()
    policy_id = str(assessment.get("policy_id") or "").strip()
    policy_evidence = assessment.get("policy_evidence")
    policy_evidence = policy_evidence if isinstance(policy_evidence, dict) else {}
    policy_refs: list[str] = []
    primary_ref = str(
        policy_evidence.get("clause_id")
        or policy_evidence.get("evidence_chunk_id")
        or ""
    ).strip()
    if policy_id and primary_ref:
        policy_refs.append(f"policy:{policy_id}:{primary_ref}")
    for span in policy_evidence.get("additional_spans") or []:
        if not isinstance(span, dict):
            continue
        span_ref = str(
            span.get("span_id")
            or span.get("clause_id")
            or span.get("evidence_chunk_id")
            or ""
        ).strip()
        if policy_id and span_ref:
            policy_refs.append(f"policy:{policy_id}:{span_ref}")
    company_refs = [
        f"company:{fact_id}"
        for evidence in assessment.get("company_evidence") or []
        if isinstance(evidence, dict)
        if (fact_id := str(evidence.get("fact_id") or "").strip())
    ]
    return {
        "claim_id": claim_id,
        "claim_text": " ".join(
            str(item).strip()
            for item in assessment.get("reasoning") or []
            if str(item).strip()
        ),
        "impact_level": str(assessment.get("impact_level") or ""),
        "policy_evidence_refs": list(dict.fromkeys(policy_refs)),
        "company_evidence_refs": list(dict.fromkeys(company_refs)),
    }


__all__ = ["GateEngine", "claim_payload_from_assessment"]
