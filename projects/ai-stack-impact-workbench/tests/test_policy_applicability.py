from policy_impact.harness.gate_engine import GateEngine
from policy_impact.harness.state import PolicyImpactState
from policy_impact.runtime.gate_catalog import StateGateDecisionSink, build_default_gate_registry
from policy_impact.runtime.gates import GateRunner
from policy_impact.stages import analyze_policy_applicability, score_policy_impact
from policy_impact.stages.match_company_policy import _fact_hit


def _state(title: str, text: str, *, matches: bool = True) -> PolicyImpactState:
    state = PolicyImpactState(company_id="company_001", run_id="run_applicability")
    state.company_context_pack = {
        "profile": {"company_name": "示例公司"},
        "pinned_facts": [
            {
                "fact_id": "ai_product.wencai",
                "value": "示例产品是面向投资者的智能问答和投资决策工作助手",
                "importance": 5,
                "source_path": "02_ai_products.md",
                "source_kind": "public_disclosure",
            },
            {
                "fact_id": "risk.data_security",
                "value": "公司处理金融市场数据、用户行为数据和交易相关信息",
                "importance": 5,
                "source_path": "04_risks.md",
                "source_kind": "analysis_inference",
            },
        ],
        "retrieved_company_facts": [],
    }
    state.policy_documents = [
        {
            "policy_id": "policy_test",
            "title": title,
            "issuer": "测试主管部门",
            "source_url": "https://official.example/policy_test",
            "source_level": "L1",
            "text": text,
        }
    ]
    if matches:
        state.policy_matches = [
            {
                "match_id": "match_test",
                "policy_id": "policy_test",
                "policy_title": title,
                "clause_id": "clause_test",
                "clause_type": "compliance",
                "policy_evidence": {"text": text[:180]},
                "company_evidence": state.company_context_pack["pinned_facts"],
                "match_quality": {"max_semantic_score": 0.8},
            }
        ]
    return state


def _gate_engine(state: PolicyImpactState) -> GateEngine:
    sink = StateGateDecisionSink(state)
    return GateEngine(
        runner=GateRunner(build_default_gate_registry(proof_resolver=sink), sink),
        run_id=state.run_id,
    )


def test_policy_fact_match_preserves_source_provenance():
    hit = _fact_hit(
        {"agent", "policy"},
        {
            "fact_id": "risk.agent_harness",
            "value": "agent tool policy",
            "source_path": "wiki/04_risks.md",
            "source": "project_design",
            "source_kind": "project_design",
        },
    )

    assert hit is not None
    assert hit["source"] == "project_design"
    assert hit["source_kind"] == "project_design"


def test_explicit_work_assistant_exclusion_is_not_applicable():
    state = _state(
        "人工智能拟人化互动服务管理暂行办法",
        "第二条\n本办法适用于通过文字等方式模拟人格特征并提供持续性情感互动的服务。\n"
        "客户服务、智能问答、工作助手、教育科研等未提供持续性情感互动的，不适用本办法。",
    )

    analyze_policy_applicability.run(state)

    item = state.policy_applicability[0]
    assert item["applicability"] == "not_applicable"
    assert "explicit_scope_exclusion" in item["reason_codes"]
    assert any("不适用本办法" in span["text"] for span in item["policy_evidence_spans"])
    assert all(span["verified"] for span in item["policy_evidence_spans"])


def test_important_data_duty_stays_conditional_until_company_status_is_known():
    state = _state(
        "网络数据安全风险评估办法",
        "第二条\n在中华人民共和国境内开展网络数据安全风险评估，应当遵守本办法。\n"
        "第五条\n重要数据处理者应当每年度开展风险评估。鼓励一般数据处理者至少每3年开展一次风险评估。",
    )

    analyze_policy_applicability.run(state)
    score_policy_impact.run(state, gate_engine=_gate_engine(state))

    applicability = state.policy_applicability[0]
    assessment = state.impact_assessments[0]
    assert applicability["applicability"] == "conditional"
    assert any("重要数据处理者" in item for item in applicability["missing_company_facts"])
    assert assessment["impact_level"] not in {"P0", "P1"}
    assert assessment["applicability"] == "conditional"
    assert assessment["company_evidence"][0]["source_kind"] == "analysis_inference"


def test_future_effective_date_is_a_runtime_enforced_status():
    state = _state(
        "网络数据安全风险评估办法",
        "《网络数据安全风险评估办法》现予公布，自2026年8月20日起施行。\n"
        "第二条\n在中华人民共和国境内开展网络数据安全风险评估，应当遵守本办法。\n"
        "第五条\n重要数据处理者应当每年度开展风险评估。\n"
        "第二十五条\n本办法自2026年8月20日起施行。\n关闭\n中央网络安全和信息化委员会办公室\n京ICP备123号",
    )
    state.date_range = {"date_from": "2026-06-16", "date_to": "2026-07-16"}

    analyze_policy_applicability.run(state)
    score_policy_impact.run(state, gate_engine=_gate_engine(state))

    applicability = state.policy_applicability[0]
    assessment = state.impact_assessments[0]
    assert applicability["effective_date"] == "2026-08-20"
    assert applicability["effective_status"] == "not_yet_effective"
    assert applicability["as_of_date"] == "2026-07-16"
    assert "截至 2026-07-16 尚未施行" in applicability["scope_summary"]
    assert "不得表述为 2026-07-16 已生效义务" in applicability["recommended_actions"][0]
    assert any("施行" in span["text"] for span in applicability["policy_evidence_spans"])
    assert all("京ICP备" not in span["text"] and "关闭" not in span["text"] for span in applicability["policy_evidence_spans"])
    assert assessment["effective_status"] == "not_yet_effective"


def test_notice_without_attachment_is_insufficient_evidence():
    state = _state(
        "关于印发《人工智能科技伦理审查与服务办法（试行）》的通知",
        "现将《人工智能科技伦理审查与服务办法（试行）》印发给你们，请结合实际贯彻执行。附件请下载查看。",
    )

    analyze_policy_applicability.run(state)
    score_policy_impact.run(state, gate_engine=_gate_engine(state))

    assert state.policy_applicability[0]["applicability"] == "insufficient_evidence"
    assert state.impact_assessments[0]["impact_level"] == "P4"
    assert "获取并校验政策附件全文" in state.impact_assessments[0]["recommended_actions"][0]


def test_unrelated_policy_is_filtered_without_forced_relevance():
    state = _state(
        "深远海养殖装备补贴办法",
        "第一条\n支持深远海养殖平台和渔业装备购置。\n第二条\n申报主体应为海洋渔业企业。",
        matches=False,
    )

    analyze_policy_applicability.run(state)
    score_policy_impact.run(state, gate_engine=_gate_engine(state))

    assert state.policy_applicability[0]["applicability"] == "not_applicable"
    assert state.impact_assessments[0]["impact_level"] == "P4"
    assert state.impact_assessments[0]["recommended_actions"] == [
        "不进入整改或项目清单；仅在产品形态、服务对象或数据范围改变时重新评估。"
    ]


def test_applicability_context_excludes_preferences_and_limits_payload():
    state = _state(
        "网络数据安全风险评估办法",
        "\n".join(
            f"第{index}条\n重要数据处理者应当开展第{index}项风险评估。"
            for index in ("一", "二", "三", "四", "五", "六", "七")
        ),
    )
    state.company_context_pack["pinned_facts"].extend(
        [
            {
                "fact_id": "preference.report_style",
                "value": "用户希望报告简洁",
                "importance": 5,
                "source_path": "memory/preferences.json",
            },
            {
                "fact_id": "risk.agent_harness",
                "value": "系统应由 Harness 约束工具调用",
                "importance": 5,
                "source_path": "wiki/04_risks.md",
            },
        ]
    )

    analyze_policy_applicability.run(state)

    context = state.applicability_context[0]
    assert len(context["policy_spans"]) <= 4
    assert len(context["company_facts"]) <= 6
    fact_ids = {item["fact_id"] for item in context["company_facts"]}
    assert "preference.report_style" not in fact_ids
    assert "risk.agent_harness" not in fact_ids
