"""Extract explicit, ephemeral company facts from a policy conversation."""

from __future__ import annotations

import re
from typing import Any


def extract_query_company_facts(query: str) -> list[dict[str, Any]]:
    text = str(query or "")
    lowered = text.lower()
    facts: list[dict[str, Any]] = []

    def add(fact_id: str, value: str) -> None:
        facts.append(
            {
                "fact_id": fact_id,
                "value": value,
                "text": value,
                "importance": 5,
                "confidence": 1.0,
                "source_path": "conversation/current_policy_run",
                "policy_relevance": ["query_explicit_fact"],
                "ephemeral": True,
            }
        )

    if any(term in lowered for term in ("ai 投研助手", "ai投研助手", "智能投研助手")):
        add("query.product.ai_research_assistant", "用户描述了面向个人投资者的 AI 投研助手产品。")

    holdings_denied = any(
        term in text for term in ("不读取持仓", "不会读取持仓", "不使用持仓", "没有读取持仓", "不基于持仓")
    )
    if holdings_denied:
        add("query.data.holdings", "用户明确说明该产品不读取或不使用用户持仓。")
    elif "持仓" in text:
        add("query.data.holdings", "用户描述该产品会使用用户持仓生成结果。")

    risk_denied = any(
        term in text
        for term in ("不读取风险偏好", "不会读取风险偏好", "不使用风险偏好", "不基于风险偏好", "没有读取风险偏好")
    ) or bool(re.search(r"不(?:读取|使用|基于)[^。；\n]{0,16}风险偏好", text))
    if risk_denied:
        add("query.data.risk_preference", "用户明确说明该产品不读取或不使用用户风险偏好。")
    elif "风险偏好" in text:
        add("query.data.risk_preference", "用户描述该产品会使用用户风险偏好生成结果。")

    if "自选股" in text:
        add("query.data.watchlist", "用户说明个性化仅使用或包含用户自选股信息。")
    if any(term in text for term in ("不能直接下单", "不直接下单", "不会直接下单", "不支持直接下单")):
        add("query.action.direct_order", "用户明确说明该产品不能直接下单。")
    elif any(term in text for term in ("一键跳转到券商交易", "一键跳转券商交易", "跳转到券商交易")):
        add("query.action.broker_jump", "用户描述该产品可跳转到券商交易页面，是否形成交易指令尚未说明。")
    if any(term in text for term in ("不收费", "不向用户收费", "不单独收费", "没有收费")):
        add("query.business.fee", "用户明确说明该功能不向用户单独收费。")
    if any(term in text for term in ("不输出明确买卖点", "不会输出明确买卖点", "没有明确买卖点", "不提供明确买卖点")):
        add("query.output.buy_sell_points", "用户明确说明该产品不输出明确买卖点。")
    elif any(term in text for term in ("买卖点", "买入建议", "卖出建议", "调仓建议")):
        add("query.output.investment_suggestion", "用户描述该产品会输出买卖点或组合调仓类建议。")
    if any(term in text for term in ("公开市场信息解读", "公开市场信息的解读", "仅做公开市场", "只做公开市场")):
        add("query.output.public_market_explanation", "用户明确说明输出仅为公开市场信息解读。")

    return facts
