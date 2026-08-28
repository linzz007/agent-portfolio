from policy_impact.query_company_facts import extract_query_company_facts


def test_query_facts_preserve_explicit_product_constraints_without_legal_inference():
    facts = extract_query_company_facts(
        "AI 投研助手不能直接下单、不向用户收费、不会输出明确买卖点，只做公开市场信息解读；"
        "个性化只根据自选股，不读取持仓和风险偏好。"
    )
    by_id = {item["fact_id"]: item["value"] for item in facts}

    assert "不能直接下单" in by_id["query.action.direct_order"]
    assert "不向用户单独收费" in by_id["query.business.fee"]
    assert "不输出明确买卖点" in by_id["query.output.buy_sell_points"]
    assert "不读取或不使用用户持仓" in by_id["query.data.holdings"]
    assert "不读取或不使用用户风险偏好" in by_id["query.data.risk_preference"]
    assert all(item["ephemeral"] for item in facts)


def test_later_denial_wins_when_full_conversation_contains_earlier_positive_fact():
    facts = extract_query_company_facts(
        "前序用户事实：会根据用户持仓和风险偏好生成建议。当前用户补充：不读取持仓和风险偏好。"
    )
    by_id = {item["fact_id"]: item["value"] for item in facts}

    assert "不读取" in by_id["query.data.holdings"]
    assert "不读取" in by_id["query.data.risk_preference"]
