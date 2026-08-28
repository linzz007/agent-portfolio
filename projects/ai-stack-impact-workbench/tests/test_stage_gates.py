from policy_impact.harness.stage_gates import evaluate_stage_gate
from policy_impact.harness.state import PolicyImpactState


def _impact_assessment(**overrides):
    assessment = {
        "assessment_id": "assessment_001",
        "match_id": "match_001",
        "policy_id": "policy_001",
        "policy_title": "Policy title",
        "impact_level": "P1",
        "impact_type": "opportunity",
        "relevance_score": 86,
        "reasoning": ["Policy and company profile overlap."],
        "company_evidence": [{"fact_id": "fact_001", "value": "AI manufacturing"}],
        "policy_evidence": {"clause_id": "clause_001", "text": "AI companies can apply."},
        "missing_fields": [],
        "recommended_actions": ["Prepare application materials."],
    }
    assessment.update(overrides)
    return assessment


def test_score_policy_impact_gate_rejects_high_impact_claim_without_evidence_binding():
    state = PolicyImpactState(company_id="company_test", run_id="run_test")
    state.impact_assessments = [
        _impact_assessment(
            company_evidence=[],
            policy_evidence={},
        )
    ]

    gate = evaluate_stage_gate(state, "score_policy_impact")

    assert gate["passed"] is False
    assert any("policy_evidence" in issue for issue in gate["issues"])
    assert any("company_evidence" in issue for issue in gate["issues"])


def test_score_policy_impact_gate_accepts_high_impact_claim_with_policy_and_company_evidence():
    state = PolicyImpactState(company_id="company_test", run_id="run_test")
    state.impact_assessments = [_impact_assessment()]

    gate = evaluate_stage_gate(state, "score_policy_impact")

    assert gate["passed"] is True
    assert gate["issues"] == []
