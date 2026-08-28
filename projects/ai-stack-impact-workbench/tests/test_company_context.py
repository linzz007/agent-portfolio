from policy_impact.company_wiki.indexer import build_company_context_pack


def test_company_context_pack_contains_pinned_facts():
    pack = build_company_context_pack("company_001")
    assert pack["company_id"] == "company_001"
    assert pack["pinned_facts"]
    assert any(fact["fact_id"] == "business.core_product" for fact in pack["pinned_facts"])
