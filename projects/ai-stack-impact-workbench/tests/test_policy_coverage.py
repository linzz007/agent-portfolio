from policy_impact.policy_coverage import infer_regulatory_coverage_gaps


def test_personalized_ai_investment_assistant_exposes_baseline_framework_gaps():
    gaps = infer_regulatory_coverage_gaps(
        "AI 投研助手根据用户持仓和风险偏好生成个股解读、组合调仓建议，并一键跳转券商交易。"
    )

    ids = {item["framework_id"] for item in gaps}
    assert ids == {
        "generative_ai",
        "algorithm_recommendation",
        "personal_information",
        "investment_advisory",
    }
    assert all(item["applicability_conclusion"] == "not_assessed" for item in gaps)
    assert all(item["official_url"].startswith("https://") for item in gaps)


def test_plain_policy_question_does_not_invent_unrelated_framework_gaps():
    assert infer_regulatory_coverage_gaps("网络数据安全风险评估办法何时施行？") == []
