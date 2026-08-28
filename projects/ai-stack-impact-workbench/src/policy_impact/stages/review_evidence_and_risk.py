"""Stage: review unsupported or risky claims."""

from __future__ import annotations

from policy_impact.harness.artifacts import write_json_artifact
from policy_impact.harness.gate_engine import GateEngine, claim_payload_from_assessment
from policy_impact.harness.model_gateway import ModelAdapter
from policy_impact.harness.state import PolicyImpactState
from policy_impact.harness.subagents import SubagentRunner
from policy_impact.harness.tool_gateway import ToolGateway
from policy_impact.runtime.gate_catalog import canonical_claim_snapshot_hash
from policy_impact.skills.registry import SkillRegistry


def run(
    state: PolicyImpactState,
    model_adapter: ModelAdapter | None = None,
    *,
    gate_engine: GateEngine,
) -> PolicyImpactState:
    reviewed = []
    downgraded = []
    applicability_violations = []
    for item in state.impact_assessments:
        has_policy = bool(item.get("policy_evidence", {}).get("text"))
        has_company = bool(item.get("company_evidence"))
        copied = dict(item)
        if item.get("impact_level") in {"P0", "P1"} and (not has_policy or not has_company):
            copied["impact_level"] = "P2"
            copied.setdefault("reasoning", []).append("证据不足，复核阶段降级")
            downgraded.append(item.get("assessment_id"))
        relationship = str(item.get("applicability") or "conditional")
        if relationship == "not_applicable" and copied.get("impact_level") != "P4":
            copied["impact_level"] = "P4"
            copied["relevance_score"] = min(int(copied.get("relevance_score", 0)), 20)
            copied["recommended_actions"] = ["不进入整改清单；仅在产品或业务边界改变时重新评估。"]
            applicability_violations.append(str(item.get("assessment_id") or ""))
        if relationship == "insufficient_evidence" and copied.get("impact_level") != "P4":
            copied["impact_level"] = "P4"
            copied["relevance_score"] = min(int(copied.get("relevance_score", 0)), 28)
            applicability_violations.append(str(item.get("assessment_id") or ""))
        if relationship == "conditional" and copied.get("impact_level") in {"P0", "P1"}:
            copied["impact_level"] = "P2"
            applicability_violations.append(str(item.get("assessment_id") or ""))
        reviewed.append(copied)
    state.impact_assessments = reviewed
    state.review_result = {
        "passed": True,
        "downgraded_assessment_ids": downgraded,
        "unsupported_claim_rate": 0 if not reviewed else len(downgraded) / len(reviewed),
        "applicability_violation_ids": list(dict.fromkeys(applicability_violations)),
    }
    _run_skeptic_subagent(state, model_adapter=model_adapter)
    unresolved = _unresolved_skeptic_counts(state)
    review_audit_refs: dict[str, str] = {}
    review_details: list[dict] = []
    attempt = state.stage_retry_counts.get("review_evidence_and_risk", 0) + 1
    for assessment in state.impact_assessments:
        claim_payload = claim_payload_from_assessment(assessment)
        claim_id = str(claim_payload.get("claim_id") or "unknown")
        review_refs = [f"review:deterministic:{claim_id}"]
        evidence_refs = list(
            dict.fromkeys(
                [
                    *claim_payload.get("policy_evidence_refs", []),
                    *claim_payload.get("company_evidence_refs", []),
                ]
            )
        )
        decision = gate_engine.evaluate_review(
            {
                "run_id": state.run_id,
                "claim_id": claim_id,
                "reviewer_role": "deterministic_evidence_gate",
                "verdict": "pass",
                "unresolved_skeptic_count": unresolved.get(claim_id, 0)
                + unresolved.get("*", 0),
                "review_refs": review_refs,
                "claim_snapshot_hash": canonical_claim_snapshot_hash(claim_payload),
                "evidence_refs": evidence_refs,
            },
            span_id=f"review_evidence_and_risk:{claim_id}",
            attempt=attempt,
        )
        review_details.append(
            {
                "claim_id": claim_id,
                "reviewer_role": "deterministic_evidence_gate",
                "decision": decision.metadata.get("decision"),
                "audit_ref": decision.metadata.get("audit_ref"),
                "unresolved_skeptic_count": unresolved.get(claim_id, 0)
                + unresolved.get("*", 0),
            }
        )
        if not decision.passed:
            state.review_result["passed"] = False
            state.review_result["claim_review_details"] = review_details
            raise ReviewGateBlocked(
                f"{claim_id}: {'; '.join(decision.reasons) or 'review gate blocked'}"
            )
        review_audit_refs[claim_id] = str(decision.metadata["audit_ref"])
    state.review_result["claim_review_audit_refs"] = review_audit_refs
    state.review_result["claim_review_details"] = review_details
    state.quality_metrics["unsupported_claim_rate"] = state.review_result["unsupported_claim_rate"]
    write_json_artifact(
        state,
        "review_result",
        state.review_result,
        "data/policies/processed",
        "review_result.json",
    )
    return state


class ReviewGateBlocked(RuntimeError):
    pass


def _unresolved_skeptic_counts(state: PolicyImpactState) -> dict[str, int]:
    counts: dict[str, int] = {}
    for bundle in state.review_bundles:
        if bundle.get("role") != "skeptic" or bundle.get("stop_reason") != "success":
            continue
        output = bundle.get("output") or {}
        for counterexample in output.get("counterexamples") or []:
            if not isinstance(counterexample, dict):
                continue
            claim_ref = str(counterexample.get("claim_ref") or "*").strip() or "*"
            counts[claim_ref] = counts.get(claim_ref, 0) + 1
    return counts


def _run_skeptic_subagent(
    state: PolicyImpactState,
    model_adapter: ModelAdapter | None,
) -> None:
    state.review_context = [_compact_assessment(item) for item in state.impact_assessments]
    deterministic_review = {
        "verification_status": "verified" if not state.review_result["downgraded_assessment_ids"] else "needs_review",
        "final_decision": "publish",
        "downgraded_assessment_ids": state.review_result["downgraded_assessment_ids"],
        "unsupported_claim_rate": state.review_result["unsupported_claim_rate"],
        "notes": [
            "Checked policy scope, obligation subject, exclusions, and missing trigger facts.",
            "Deterministic evidence rules remain the publishing authority.",
        ],
    }
    if model_adapter is None:
        state.add_review_bundle(
            {
                "role": "skeptic_rule_engine",
                "output": deterministic_review,
                "stop_reason": "model_not_configured",
                "context_manifest_id": "",
            }
        )
        return

    review_artifact_ref = f"review_context:{state.run_id}"
    runner = SubagentRunner(
        model_adapter=model_adapter,
        tool_gateway=ToolGateway(),
        skill_manifest=SkillRegistry().get_manifest("policy_weekly_impact"),
    )
    result = runner.run(
        state,
        "skeptic",
        task=(
            f"First call artifact_read for {review_artifact_ref}. Challenge unsupported "
            "high-impact claims, obligation-subject mistakes, explicit exclusions, and "
            "keyword-only relevance. Return only counterexample_set.v1 with "
            "counterexamples (claim_ref, issue, evidence_refs) and notes. Every "
            f"counterexample evidence_refs must contain only {review_artifact_ref}. "
            "Do not make a publish, block, verdict, recommendation, or final decision."
        ),
        input_artifact_refs=(review_artifact_ref,),
        artifact_payloads={
            review_artifact_ref: {
                "assessments": state.review_context,
                "deterministic_review": deterministic_review,
            }
        },
    )
    if result.stop_reason != "success":
        state.add_warning(
            "review_evidence_and_risk",
            "Skeptic 子智能体失败，发布决定回退到确定性证据门控",
            result.stop_reason,
        )
        state.add_review_bundle(
            {
                "role": "skeptic_rule_engine",
                "output": deterministic_review,
                "stop_reason": result.stop_reason,
                "context_manifest_id": result.context_manifest_id,
            }
        )
        return
    state.add_review_bundle(result.to_dict())


def _compact_assessment(item: dict) -> dict:
    policy_evidence = item.get("policy_evidence") if isinstance(item.get("policy_evidence"), dict) else {}
    company_evidence = item.get("company_evidence") if isinstance(item.get("company_evidence"), list) else []
    return {
        "assessment_id": item.get("assessment_id"),
        "impact_level": item.get("impact_level"),
        "applicability": item.get("applicability"),
        "binding_effect": item.get("binding_effect"),
        "missing_company_facts": item.get("missing_fields") or [],
        "reason_codes": item.get("reason_codes") or [],
        "claim": str(item.get("claim") or item.get("summary") or "")[:240],
        "policy_evidence": {
            "present": bool(policy_evidence.get("text")),
            "text": str(policy_evidence.get("text") or "")[:320],
        },
        "company_evidence": [
            {
                "fact_id": evidence.get("fact_id"),
                "value": str(evidence.get("value") or "")[:180],
            }
            for evidence in company_evidence[:3]
            if isinstance(evidence, dict)
        ],
    }
