from policy_impact.official_legal_reference import resolve_official_legal_reference


def test_genai_article_17_returns_full_official_text_and_rejects_csrc_claim():
    result = resolve_official_legal_reference(
        "《生成式人工智能服务管理暂行办法》第十七条是不是要求所有金融 AI 取得证监会前置许可？"
    )

    assert result is not None
    assert "开展安全评估" in result["official_text"]
    assert "算法备案和变更、注销备案手续" in result["official_text"]
    assert "证监会前置许可" not in result["official_text"]
    assert "该说法错误" in result["answer"]
    assert result["official_url"].startswith("https://www.cac.gov.cn/")


def test_unrelated_law_question_does_not_match_registry():
    assert resolve_official_legal_reference("个人信息保护法第二十八条是什么？") is None


def test_pipl_article_55_returns_all_triggers_and_conditional_conclusion():
    result = resolve_official_legal_reference(
        "请依据《个人信息保护法》第55条判断 AI 问答是否必须做个人信息保护影响评估。"
    )

    assert result is not None
    assert "处理敏感个人信息" in result["official_text"]
    assert "利用个人信息进行自动化决策" in result["official_text"]
    assert "向境外提供个人信息" in result["official_text"]
    assert "条件触发，事实不足" in result["answer"]
    assert result["official_url"].startswith("https://www.npc.gov.cn/")
