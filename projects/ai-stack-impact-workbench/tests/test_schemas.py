from policy_impact.schemas import ImpactAssessment, validation_errors


def test_impact_assessment_schema_rejects_missing_reasoning():
    issues = validation_errors(
        ImpactAssessment,
        [
            {
                "assessment_id": "impact_001",
                "match_id": "match_001",
                "policy_id": "policy_001",
                "policy_title": "test",
                "impact_level": "P1",
                "impact_type": "opportunity",
                "relevance_score": 80,
                "reasoning": [],
                "company_evidence": [],
                "policy_evidence": {"text": "evidence"},
            }
        ],
    )
    assert issues
