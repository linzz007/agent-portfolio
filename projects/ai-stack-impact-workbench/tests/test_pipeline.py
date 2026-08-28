from policy_impact.skill_executors.policy_weekly_impact import run_policy_weekly_impact


def test_weekly_policy_pipeline_runs():
    requested_run_id = "run-policy-pipeline-test"
    result = run_policy_weekly_impact(
        "company_001",
        run_id=requested_run_id,
        date_from="2026-06-30",
        date_to="2026-07-05",
        refresh_live_sources=False,
        allow_fixture_fallback=True,
    )
    assert result.state.run_id == requested_run_id
    assert result.state.current_stage == "done"
    assert result.report_path
    assert result.state.report_paths.get("html_report")
    assert result.run_artifact_path
    assert result.state.impact_assessments
    assert result.state.stage_gate_results
    assert result.state.review_bundles
    roles = {item["role"] for item in result.state.review_bundles}
    assert "applicability_analyst" in roles
    assert roles & {"skeptic", "skeptic_rule_engine"}
    if "skeptic_rule_engine" in roles:
        assert result.state.model_calls == []
