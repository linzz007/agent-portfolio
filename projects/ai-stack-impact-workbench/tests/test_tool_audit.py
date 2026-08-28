import json
from pathlib import Path

from policy_impact.harness.tool_gateway import ToolGateway, get_tool_audit_log, reset_tool_audit_log
from policy_impact.mcp_server.registry import register_all_tools
from policy_impact.skill_executors.policy_weekly_impact import run_policy_weekly_impact


def test_run_artifact_contains_tool_audit():
    result = run_policy_weekly_impact(
        "company_001",
        date_from="2026-06-30",
        date_to="2026-07-05",
        refresh_live_sources=False,
        allow_fixture_fallback=True,
    )
    assert result.state.tool_calls
    path = Path(result.run_artifact_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["tool_calls"]
    assert all("stage_name" in call and "tool_name" in call for call in payload["tool_calls"])
    assert any(
        call["stage_name"] == "match_company_policy" and call["tool_name"] == "company_wiki_search"
        for call in payload["tool_calls"]
    )


def test_tool_audit_reset_starts_an_isolated_run_log():
    register_all_tools()
    reset_tool_audit_log()
    first_gateway = ToolGateway()
    first_gateway.call("main_agent", "memory_search", company_id="company_001", query="policy", top_k=1)
    assert len(get_tool_audit_log()) == 1

    reset_tool_audit_log()
    second_gateway = ToolGateway()
    second_gateway.call("main_agent", "memory_search", company_id="company_001", query="news", top_k=1)
    calls = get_tool_audit_log()

    assert len(calls) == 1
    assert calls[0]["argument_keys"] == ["company_id", "query", "top_k"]


def test_news_structure_events_dispatches_deterministically_without_network():
    register_all_tools()
    reset_tool_audit_log()
    gateway = ToolGateway()
    raw_items = [
        {
            "title": "监管部门发布数据安全检查要求",
            "source": "test_source",
            "published_at": "2026-07-17T00:00:00+00:00",
            "url": "https://example.invalid/news/1",
            "summary": "监管检查强调数据安全、权限审计和合规记录。",
        },
        {
            "title": "监管部门发布数据安全检查要求",
            "source": "duplicate_source",
            "published_at": "2026-07-17T00:00:00+00:00",
            "url": "https://example.invalid/news/duplicate",
            "summary": "重复标题不应生成第二个事件。",
        },
    ]

    first = gateway.call("news", "news.structure_events", raw_items=raw_items)
    second = gateway.call("news", "news.structure_events", raw_items=raw_items)

    assert first == second
    assert [item["title"] for item in first["cleaned_items"]] == [
        "监管部门发布数据安全检查要求"
    ]
    assert [event["category"] for event in first["structured_events"]] == [
        "compliance_risk"
    ]
