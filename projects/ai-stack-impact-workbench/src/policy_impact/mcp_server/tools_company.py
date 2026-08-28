"""Company MCP-style tools."""

from __future__ import annotations

from policy_impact.company_wiki.guide import build_wiki_blueprint
from policy_impact.company_wiki.indexer import build_company_context_pack
from policy_impact.company_wiki.loader import classify_source_kind, load_company_profile, load_company_knowledge
from policy_impact.company_wiki.updater import create_update_proposal, apply_update, CorrectionProposal
from policy_impact.rag.lexical import tokenize


_QUERY_EXPANSIONS = {
    "所在地": "总部 位于 杭州 浙江 地址",
    "地址": "总部 位于 杭州 浙江 所在地",
    "股票代码": "上市主体 证券代码 300033",
    "服务谁": "服务对象 客户 个人端 机构端",
    "服务对象": "客户 个人端 机构端",
    "公开事实": "公开披露 年报 公告 public disclosure",
    "公开披露": "年报 公告 public disclosure",
    "分析推断": "analysis inference 风险 推断",
    "项目设计": "project design harness",
    "报告偏好": "preference report style 用户关注点 输出顺序",
    "输出偏好": "preference report style 用户关注点 输出顺序",
    "用户偏好": "preference report style 用户关注点",
    "rag": "rag knowledge 知识库 检索",
}


_FACT_HINTS = {
    "示例产品": {"ai_product.wencai", "business.customer_segments"},
    "ifind": {"ai_product.ifind", "business.customer_segments"},
    "核心业务": {"business.core_product"},
    "收入": {"business.revenue_structure"},
    "所在地": {"profile.location"},
    "地址": {"profile.location"},
    "股票代码": {"profile.identity"},
    "rag": {"ai_product.rag_and_knowledge"},
    "bizfinbench": {"qualification.ai_benchmark"},
    "报告偏好": {"preference.report_style"},
    "输出偏好": {"preference.report_style"},
    "用户偏好": {"preference.analysis_focus", "preference.report_style"},
}


def _expanded_query(query: str) -> str:
    normalized = str(query or "").lower()
    additions = [value for marker, value in _QUERY_EXPANSIONS.items() if marker in normalized]
    return " ".join([str(query or ""), *additions]).strip()


def company_profile_read(company_id: str) -> dict:
    return load_company_profile(company_id)


def company_context_pack_build(company_id: str, task: str = "weekly_policy_impact") -> dict:
    return build_company_context_pack(company_id, task=task)


def company_wiki_blueprint(company_id: str) -> dict:
    """Return the recommended Company Wiki folder contract and missing fields."""

    return build_wiki_blueprint(company_id)


def company_wiki_search(company_id: str, query: str, top_k: int = 5) -> list[dict]:
    """Search Company Wiki facts with metadata-aware lexical ranking.

    The small per-company corpus does not justify an embedding dependency, but
    it still needs domain aliases and source provenance in the ranking.  The
    returned score is trace evidence; it is not presented as a calibrated
    relevance probability.
    """

    knowledge = load_company_knowledge(company_id)
    normalized_query = str(query or "").lower()
    terms = [term for term in tokenize(_expanded_query(query)) if term]
    scored = []
    for fact in knowledge["facts"]:
        source_kind = classify_source_kind(str(fact.get("source") or ""))
        text = " ".join(
            [
                str(fact.get("fact_id") or ""),
                str(fact.get("domain") or ""),
                str(fact.get("value") or ""),
                str(fact.get("text") or ""),
                str(fact.get("source") or ""),
                source_kind,
                " ".join(str(x) for x in fact.get("policy_relevance", [])),
            ]
        )
        fact_terms = set(tokenize(text))
        overlap_count = len(fact_terms.intersection(terms))
        phrase_boost = 0.0
        fact_id = str(fact.get("fact_id") or "")
        for marker, hinted_fact_ids in _FACT_HINTS.items():
            if marker in normalized_query and fact_id in hinted_fact_ids:
                phrase_boost += 12.0
        if any(marker in normalized_query for marker in ("公开事实", "公开披露", "公告", "年报")):
            phrase_boost += 4.0 if source_kind == "public_disclosure" else 0.0
        if any(marker in normalized_query for marker in ("分析推断", "推断", "假设")):
            phrase_boost += 6.0 if source_kind == "analysis_inference" else 0.0
        if "项目设计" in normalized_query:
            phrase_boost += 6.0 if source_kind == "project_design" else 0.0
        if any(marker in normalized_query for marker in ("用户偏好", "报告偏好", "输出偏好", "关注点")):
            phrase_boost += 6.0 if source_kind in {"user_confirmed", "project_design"} else 0.0
        score = (
            overlap_count
            + phrase_boost
            + int(fact.get("importance", 0)) * 0.1
            + float(fact.get("confidence", 0.5)) * 0.05
        )
        if overlap_count > 0 or phrase_boost > 0:
            enriched = dict(fact)
            enriched["source_kind"] = source_kind
            enriched["retrieval_score"] = round(score, 6)
            enriched["matched_term_count"] = overlap_count
            scored.append((score, fact_id, enriched))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [fact for _, _, fact in scored[:top_k]]


def company_update_proposal_create(company_id: str, target_file: str, target_fact_id: str, old_claim: str, new_claim: str) -> dict:
    return create_update_proposal(company_id, target_file, target_fact_id, old_claim, new_claim).to_dict()


def company_update_apply(payload: dict) -> dict:
    proposal = CorrectionProposal(**payload)
    return apply_update(proposal)
