from __future__ import annotations

from pathlib import Path

from policy_impact.company_wiki import loader as wiki_loader
from policy_impact.mcp_server.tools_company import company_wiki_search


def _seed_company(tmp_path: Path) -> None:
    company_dir = tmp_path / "data" / "companies" / "company_test"
    wiki_dir = company_dir / "wiki"
    wiki_dir.mkdir(parents=True)
    (company_dir / "company.yaml").write_text(
        '{"company_id":"company_test","company_name":"示例公司"}',
        encoding="utf-8",
    )
    (wiki_dir / "facts.md").write_text(
        """---
domain: business
freshness: active
last_updated: 2026-07-17
---
## FACT: business.core_product
- value: 围绕资本市场生态提供 AI+金融信息服务。
- importance: 5
- confidence: 0.95
- source: https://example.com/annual-report.pdf

## FACT: ai_product.wencai
- value: 示例产品面向个人投资者提供金融信息服务。
- importance: 5
- confidence: 0.95
- source: https://example.com/annual-report.pdf

## FACT: ai_product.ifind
- value: iFinD 面向金融机构提供数据终端和投研支持。
- importance: 5
- confidence: 0.95
- source: https://example.com/annual-report.pdf

## FACT: risk.investment_advice
- value: 金融 AI 输出可能被理解为投资建议。
- importance: 4
- confidence: 0.75
- source: analysis_inference_from_disclosed_business

## FACT: risk.agent_harness
- value: 项目要求所有高风险工具经过门控。
- importance: 4
- confidence: 0.9
- source: project_design
""",
        encoding="utf-8",
    )


def test_company_search_recalls_named_products_and_labels_provenance(tmp_path, monkeypatch):
    _seed_company(tmp_path)
    monkeypatch.setattr(wiki_loader, "project_root", lambda: tmp_path, raising=False)

    results = company_wiki_search(
        "company_test",
        "示例公司的核心业务是什么，示例产品和 iFinD 分别服务谁？请区分公开披露、分析推断和项目设计。",
        top_k=8,
    )
    by_id = {item["fact_id"]: item for item in results}

    assert list(by_id)[:3] == [
        "ai_product.wencai",
        "ai_product.ifind",
        "business.core_product",
    ]
    assert {"risk.investment_advice", "risk.agent_harness"}.issubset(by_id)
    assert by_id["ai_product.wencai"]["source_kind"] == "public_disclosure"
    assert by_id["ai_product.ifind"]["source_kind"] == "public_disclosure"
    assert by_id["risk.investment_advice"]["source_kind"] == "analysis_inference"
    assert by_id["risk.agent_harness"]["source_kind"] == "project_design"

    loaded_by_id = {
        item["fact_id"]: item
        for item in wiki_loader.load_company_knowledge("company_test")["facts"]
    }
    assert loaded_by_id["business.core_product"]["source_kind"] == "public_disclosure"
    assert loaded_by_id["risk.investment_advice"]["source_kind"] == "analysis_inference"
    assert loaded_by_id["risk.agent_harness"]["source_kind"] == "project_design"
