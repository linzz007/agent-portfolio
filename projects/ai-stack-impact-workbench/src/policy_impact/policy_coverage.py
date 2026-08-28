"""Deterministic regulatory coverage boundaries for policy questions.

These records are not applicability findings.  They identify older baseline
frameworks that the recent-policy corpus does not currently evaluate.
"""

from __future__ import annotations

from typing import Any


_FRAMEWORKS = {
    "generative_ai": {
        "framework": "《生成式人工智能服务管理暂行办法》",
        "official_url": "https://www.cac.gov.cn/2023-07/13/c_<redacted-phone>29107.htm",
        "next_check": "核验服务是否面向境内公众，以及是否触发安全评估和算法备案要求。",
    },
    "algorithm_recommendation": {
        "framework": "《互联网信息服务算法推荐管理规定》",
        "official_url": "https://www.cac.gov.cn/2022-01/04/c_<redacted-phone>58238.htm",
        "next_check": "核验个性化推荐、排序、检索或调度决策是否属于算法推荐服务。",
    },
    "personal_information": {
        "framework": "《中华人民共和国个人信息保护法》",
        "official_url": "https://www.npc.gov.cn/npc/c2/c30834/202108/t20210820_313088.html",
        "next_check": "核验持仓、风险偏好、金融账户等数据的处理目的、必要性、授权和敏感个人信息规则。",
    },
    "investment_advisory": {
        "framework": "《证券投资顾问业务暂行规定》",
        "official_url": "https://neris.csrc.gov.cn/falvfagui/rdqsHeader/mainbody?body=&navbarId=3&secFutrsLawId=3636153f028c44e9a00de8ed06494385",
        "next_check": "由证券合规人员核验输出是否构成证券投资建议、主体资质和展业边界。",
    },
}


def infer_regulatory_coverage_gaps(query: str) -> list[dict[str, Any]]:
    """Return baseline frameworks that require a separate evidence pass."""

    text = str(query or "").lower()
    triggers: list[tuple[str, tuple[str, ...], str]] = [
        (
            "generative_ai",
            ("生成式", "大模型", "ai 投研", "ai投研", "ai 助手", "ai助手", "智能助手"),
            "问题涉及生成式 AI 或大模型服务",
        ),
        (
            "algorithm_recommendation",
            ("个性化", "推荐", "排序", "自选股", "持仓", "风险偏好", "调仓"),
            "问题涉及个性化、推荐、排序或调度决策",
        ),
        (
            "personal_information",
            ("持仓", "风险偏好", "金融账户", "个人信息", "用户画像", "行为数据"),
            "问题涉及个人信息或可能的敏感个人信息",
        ),
        (
            "investment_advisory",
            ("调仓建议", "买卖点", "投资建议", "个股解读", "一键交易", "券商交易", "投顾", "投资顾问"),
            "问题可能触及证券投资咨询或投资顾问业务边界",
        ),
    ]
    gaps: list[dict[str, Any]] = []
    for framework_id, terms, reason in triggers:
        matched = [term for term in terms if term in text]
        if not matched:
            continue
        framework = _FRAMEWORKS[framework_id]
        gaps.append(
            {
                "framework_id": framework_id,
                "framework": framework["framework"],
                "status": "outside_recent_policy_corpus_needs_verification",
                "trigger_reason": reason,
                "matched_query_terms": matched[:4],
                "official_url": framework["official_url"],
                "next_check": framework["next_check"],
                "applicability_conclusion": "not_assessed",
            }
        )
    return gaps
