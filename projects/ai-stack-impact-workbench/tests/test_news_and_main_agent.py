from datetime import date, timedelta
from pathlib import Path

from policy_impact.conversation.main_agent import MainAgentService
from policy_impact.skill_executors.recent_news_report import (
    _analyze_company_impact,
    _apply_query_constraints,
    _evidence_excerpt,
    _filter_events_for_query,
    _format_company_evidence,
    _has_substantive_summary,
    _impact_chain,
    _join_complete_excerpts,
    _looks_complete_excerpt,
    _publisher_for_source_id,
    _diversify_analysis,
    _parse_query_constraints,
    _valid_source_event,
    run_recent_news_report,
)


def test_news_company_evidence_renders_fact_id_and_source_kind():
    rendered = _format_company_evidence(
        {
            "fact_id": "risk.data_security",
            "source_kind": "analysis_inference",
            "value": "需要核验数据授权和审计边界。",
        }
    )

    assert rendered == "[分析推断; fact_id: risk.data_security] 需要核验数据授权和审计边界。"


def test_news_content_gate_rejects_missing_or_placeholder_summary():
    assert _has_substantive_summary("") is False
    assert _has_substantive_summary("官方来源未提供变更摘要。") is False
    assert _has_substantive_summary("Added a verified tool permission change with a reproducible migration note.") is True


def test_news_excerpt_never_hard_cuts_a_release_item_without_marker():
    excerpt = _join_complete_excerpts(
        [
            "Added a complete first release-note item with enough verified detail.",
            "Fixed a complete second release-note item that would exceed the selected budget.",
        ],
        max_chars=80,
    )

    assert excerpt == "Added a complete first release-note item with enough verified detail."


def test_news_diversity_pass_fills_requested_count_from_same_source_when_needed():
    items = [
        {
            "event_id": f"event-{index}",
            "news_evidence": {"source_id": "official_vendor"},
        }
        for index in range(4)
    ]

    selected = _diversify_analysis(items, max_per_source=2, max_total=3)

    assert [item["event_id"] for item in selected] == ["event-0", "event-1", "event-2"]


def test_news_diversity_keeps_one_secondary_web_item_when_requested():
    items = [
        {
            "event_id": "official-a",
            "news_evidence": {"source_id": "anthropic_claude_code_releases", "source_level": "L2"},
        },
        {
            "event_id": "official-b",
            "news_evidence": {"source_id": "openai_codex_releases", "source_level": "L2"},
        },
        {
            "event_id": "official-c",
            "news_evidence": {"source_id": "mcp_specification_releases", "source_level": "L2"},
        },
        {
            "event_id": "web-news",
            "news_evidence": {"source_id": "google_news_agent_harness", "source_level": "L5"},
        },
    ]

    selected = _diversify_analysis(
        items,
        max_per_source=2,
        max_total=3,
        ensure_secondary_web=True,
    )

    assert [item["event_id"] for item in selected] == ["official-a", "official-b", "web-news"]


def test_news_impact_chain_is_specific_to_the_release_change():
    worktree = _impact_chain(
        ["agent_harness"],
        event_text="Fixed worktree subagent isolation from the main checkout",
    )
    wrapper = _impact_chain(
        ["agent_harness"],
        event_text="Added CLAUDE_CODE_PROCESS_WRAPPER for a corporate launcher",
    )
    resumed_model = _impact_chain(
        ["agent_harness"],
        event_text="Fixed explicit model override on resume and follow-up falling back to the parent model",
    )
    resumed_session = _impact_chain(
        ["agent_harness"],
        event_text="Fixed session resume notices for orphaned background tasks",
    )

    assert "主仓误写" in worktree[0]
    assert "企业进程包装器" in wrapper[0]
    assert "model_id" in resumed_model[0]
    assert "后台任务终态一致性" in resumed_session[0]
    assert worktree != wrapper


def test_mainstream_model_query_does_not_treat_cli_release_as_model_update():
    matched, coverage = _filter_events_for_query(
        "最近30天主流大模型在长上下文和工具调用方面的官方更新",
        [
            {
                "event_id": "claude-code-cli",
                "source_id": "anthropic_claude_code_releases",
                "title": "Claude Code CLI release",
                "summary": "Fixed context window display and tool permission hooks.",
            }
        ],
    )

    assert matched == []
    assert coverage == {"long_context": False, "tool_use": False}


def test_recent_news_report_generates_html_artifact():
    result = run_recent_news_report("company_001", refresh_live_sources=False)
    assert result["status"] == "done"
    assert result["event_count"] >= 1
    html_path = Path(result["html_report_path"])
    assert html_path.exists()
    html = html_path.read_text(encoding="utf-8")
    assert "中文执行摘要" in html
    assert "具体影响分析" in html
    assert "具体影响链条" in html
    assert "中文摘要" in html
    assert "原始来源摘要" in html
    assert "待确认" in html
    assert "验证指标" in html
    assert Path(result["artifact_path"]).exists()


def test_broad_wiki_impact_report_infers_business_relevant_topics():
    events = [
        {
            "event_id": "claude-code-permission",
            "source_id": "anthropic_claude_code_releases",
            "title": "Claude Code release",
            "summary": "Fixed PreToolUse permission checks for background agent tasks and subagent sandbox behavior.",
            "published_at": date.today().isoformat(),
        },
        {
            "event_id": "openai-enterprise",
            "source_id": "openai_news",
            "title": "From assistance to execution: How enterprises put AI to work",
            "summary": "Enterprises are adopting Codex and agentic AI systems to move from assistance to execution.",
            "published_at": date.today().isoformat(),
        },
    ]

    matched, coverage = _filter_events_for_query(
        "请基于当前 Wiki 画像，生成最近一周外部新闻、技术发布和行业动态对我的影响报告",
        events,
    )

    assert [item["event_id"] for item in matched] == ["claude-code-permission", "openai-enterprise"]
    assert coverage["agent_harness"] is True
    assert coverage["developer_tools"] is True


def test_main_agent_routes_wiki_blueprint():
    result = MainAgentService("company_001").ask("企业信息模板应该怎么写？", session_id="test_blueprint")
    assert result["intent"] == "wiki_blueprint"
    assert result["blueprint"]["files"]


def test_named_developer_tool_update_query_filters_research_noise_and_tracks_dimensions():
    events = [
        {
            "event_id": "release_context_tool",
            "title": "Claude Code release",
            "summary": "Fixed context window reset and improved PreToolUse hook permission decisions for subagents.",
            "source_id": "anthropic_claude_code_releases",
            "published_at": "2026-07-15T00:00:00+00:00",
        },
        {
            "event_id": "release_cost",
            "title": "Codex release",
            "summary": "New inference pricing reduces inference cost and improves throughput.",
            "source_id": "openai_codex_releases",
            "published_at": "2026-07-14T00:00:00+00:00",
        },
        {
            "event_id": "paper_noise",
            "title": "Agentic quantum paper",
            "summary": "An agent tool system for quantum attacks.",
            "source_id": "arxiv_agent_papers",
            "published_at": "2026-07-15T00:00:00+00:00",
        },
    ]

    matched, coverage = _filter_events_for_query(
        "Claude Code 和 Codex 在推理成本、长上下文和 Agent 工具调用方面的官方更新",
        events,
    )

    assert {item["event_id"] for item in matched} == {"release_context_tool", "release_cost"}
    assert coverage == {"inference_cost": True, "long_context": True, "tool_use": True}
    assert all(item["source_id"] != "arxiv_agent_papers" for item in matched)


def test_inference_cost_requires_strong_evidence_not_ui_cost_or_price_words():
    events = [
        {
            "event_id": "session_counter",
            "title": "Claude Code release",
            "summary": "Fixed /clear not resetting the session cost counter in the statusline.",
            "source_id": "anthropic_claude_code_releases",
            "published_at": "2026-07-15T00:00:00+00:00",
        },
        {
            "event_id": "model_picker",
            "title": "Claude Code release",
            "summary": "Fixed model picker rows printing a price for a different model.",
            "source_id": "anthropic_claude_code_releases",
            "published_at": "2026-07-14T00:00:00+00:00",
        },
    ]

    matched, coverage = _filter_events_for_query("主流大模型推理成本的官方更新", events)

    assert matched == []
    assert coverage == {"inference_cost": False}


def test_news_excerpt_selects_the_matching_release_item():
    summary = (
        "Added vim mode remaps. "
        "Fixed context window reset after auto-compact. "
        "Fixed an unrelated terminal rendering issue."
    )

    excerpt = _evidence_excerpt(summary, ["long_context"])

    assert "context window" in excerpt
    assert "vim mode" not in excerpt
    assert "terminal rendering" not in excerpt


def test_news_excerpt_for_broad_publisher_query_keeps_complete_release_items():
    excerpt = _evidence_excerpt(
        "What's changed Added a verified subagent streaming field with an auditable schema. "
        "Fixed a separate worktree isolation issue with a reproducible regression case.",
        [],
    )

    assert "Added a verified subagent streaming field" in excerpt
    assert excerpt != "What's changed"
    assert "来源未提供摘要" not in excerpt


def test_news_excerpt_rejects_a_source_fragment_ending_in_a_stop_word():
    assert _looks_complete_excerpt("Fixed worktree routing after the final sparse path was") is False
    assert _looks_complete_excerpt("Fixed worktree routing after the final sparse path was restored") is True


def test_news_excerpt_for_broad_query_prioritizes_harness_relevant_change():
    excerpt = _evidence_excerpt(
        "Added a generic elapsed-time counter with enough release detail for display. "
        "Fixed worktree subagent isolation from the main checkout with a regression test.",
        [],
    )

    assert excerpt.startswith("Fixed worktree subagent isolation")


def test_news_company_evidence_excludes_user_preferences():
    context = {
        "pinned_facts": [
            {
                "fact_id": "preference.analysis_focus",
                "value": "用户希望重点分析 Agent Harness",
                "source_path": "memory/preferences.json",
                "importance": 5,
            },
            {
                "fact_id": "ai_product.wencai",
                "value": "示例产品是面向投资者的智能问答产品",
                "source_path": "wiki/02_ai_products.md",
                "importance": 5,
            },
            {
                "fact_id": "ai_product.ifind",
                "value": "iFinD 为机构提供数据终端与投研决策支持",
                "source_path": "wiki/02_ai_products.md",
                "importance": 5,
            },
        ],
        "retrieved_company_facts": [],
    }
    events = [
        {
            "event_id": "release_tool",
            "title": "Agent tool release",
            "summary": "Improved tool call permission and subagent trace.",
            "category": "market_signal",
            "source": "Vendor release",
            "source_id": "anthropic_claude_code_releases",
            "source_level": "L2",
            "published_at": "2026-07-15T00:00:00+00:00",
            "url": "https://example.invalid/release",
            "matched_query_topics": ["tool_use"],
            "query_match_score": 48,
        }
    ]

    analysis = _analyze_company_impact(
        "company_001",
        context,
        events,
        query="对示例产品和 iFinD 的工具调用有什么影响",
    )

    evidence_ids = {item["fact_id"] for item in analysis[0]["company_evidence"]}
    assert evidence_ids == {"ai_product.wencai", "ai_product.ifind"}
    assert "preference.analysis_focus" not in evidence_ids
    assert analysis[0]["missing_company_facts"]
    assert analysis[0]["verification_metrics"]


def test_explicit_news_window_count_and_primary_source_constraints():
    query = (
        "请汇总过去 30 天（2026-06-16 至 2026-07-16）与金融科技企业研发决策直接相关的 3 条"
        "大模型或 Agent 技术新闻。每条必须给官方一手来源；不要使用未来日期、二手媒体。"
    )
    constraints = _parse_query_constraints(query)
    events = [
        {"event_id": "inside", "source_id": "openai_codex_releases", "published_at": "2026-07-15T00:00:00Z"},
        {"event_id": "outside", "source_id": "mcp_specification_releases", "published_at": "2026-05-29T00:00:00Z"},
        {"event_id": "future", "source_id": "anthropic_claude_code_releases", "published_at": "2026-07-17T00:00:00Z"},
        {"event_id": "paper", "source_id": "arxiv_agent_papers", "published_at": "2026-07-14T00:00:00Z"},
    ]

    selected, diagnostics = _apply_query_constraints(events, constraints)

    assert constraints["requested_count"] == 3
    assert constraints["date_from"] == "2026-06-16"
    assert constraints["date_to"] == "2026-07-16"
    assert [item["event_id"] for item in selected] == ["inside"]
    assert diagnostics["outside_date_window"] == 2
    assert diagnostics["non_primary_source"] == 1


def test_named_publisher_whitelist_never_uses_an_unrequested_source_to_fill_count():
    query = (
        "请查找 2026-07-01 至 2026-07-15 发布的 3 条新闻，主题只限 OpenAI、Anthropic、"
        "Google DeepMind 的官方产品或研究发布；不足 3 条就明确不足。"
    )
    constraints = _parse_query_constraints(query)
    events = [
        {"event_id": "anthropic", "source_id": "anthropic_claude_code_releases", "published_at": "2026-07-15"},
        {"event_id": "openai", "source_id": "openai_codex_releases", "published_at": "2026-07-14"},
        {"event_id": "huggingface", "source_id": "huggingface_blog", "published_at": "2026-07-13"},
        {"event_id": "langgraph", "source_id": "langgraph_releases", "published_at": "2026-07-12"},
    ]

    selected, diagnostics = _apply_query_constraints(events, constraints)

    assert constraints["publisher_restriction"] is True
    assert constraints["requested_publishers"] == ["OpenAI", "Anthropic", "Google DeepMind"]
    assert constraints["allowed_publisher_source_ids"] == [
        "anthropic_claude_code_releases",
        "openai_codex_releases",
    ]
    assert constraints["unavailable_publishers"] == ["Google DeepMind"]
    assert [item["event_id"] for item in selected] == ["anthropic", "openai"]
    assert diagnostics["outside_requested_publishers"] == 2


def test_named_publisher_window_parses_explicit_start_and_end_times():
    constraints = _parse_query_constraints(
        "请查找 2026-07-01 00:00 至 2026-07-15 23:59（北京时间）发布的 3 条新闻"
    )

    assert constraints["date_from"] == "2026-07-01"
    assert constraints["date_to"] == "2026-07-15"
    assert constraints["query_timezone"] == "Asia/Shanghai"
    assert constraints["datetime_from"] == "2026-07-01T00:00:00+08:00"
    assert constraints["datetime_to"] == "2026-07-15T23:59:00+08:00"


def test_beijing_datetime_window_compares_the_complete_instant_not_the_utc_date():
    constraints = _parse_query_constraints(
        "请查找 2026-07-01 00:00 至 2026-07-15 23:59（北京时间）发布的新闻"
    )
    events = [
        {
            "event_id": "inside-after-utc-conversion",
            "source_id": "anthropic_claude_code_releases",
            "published_at": "2026-06-30T16:30:00+00:00",
        },
        {
            "event_id": "outside-after-utc-conversion",
            "source_id": "anthropic_claude_code_releases",
            "published_at": "2026-07-15T23:02:35+00:00",
        },
    ]

    selected, diagnostics = _apply_query_constraints(events, constraints)

    assert [item["event_id"] for item in selected] == ["inside-after-utc-conversion"]
    assert selected[0]["published_at_query_timezone"] == "2026-07-01T00:30:00+08:00"
    assert diagnostics["outside_datetime_window"] == 1


def test_owned_website_constraint_rejects_official_github_release_channels():
    constraints = _parse_query_constraints(
        "主题只限 OpenAI、Anthropic、Google DeepMind；每条必须来自发布方自己的官网或官方研究博客。"
    )
    events = [
        {
            "event_id": "anthropic-github",
            "source_id": "anthropic_claude_code_releases",
            "published_at": "2026-07-14T00:00:00+00:00",
        },
        {
            "event_id": "openai-github",
            "source_id": "openai_codex_releases",
            "published_at": "2026-07-14T00:00:00+00:00",
        },
    ]

    selected, diagnostics = _apply_query_constraints(events, constraints)

    assert constraints["publisher_owned_web_only"] is True
    assert constraints["publisher_source_scope"] == "publisher_owned_web_or_research_blog"
    assert constraints["allowed_publisher_source_ids"] == []
    assert constraints["unavailable_publishers"] == ["OpenAI", "Anthropic", "Google DeepMind"]
    assert selected == []
    assert diagnostics["outside_requested_publishers"] == 2


def test_news_source_has_an_explicit_publisher_mapping():
    assert _publisher_for_source_id("anthropic_claude_code_releases") == "Anthropic"
    assert _publisher_for_source_id("openai_codex_releases") == "OpenAI"
    assert _publisher_for_source_id("unknown") == ""


def test_financial_enterprise_context_does_not_require_financial_words_in_vendor_news():
    events = [
        {
            "event_id": "agent_release",
            "title": "Codex Agent runtime update",
            "summary": "Improved agent tool SDK and runtime reliability.",
            "source_id": "openai_codex_releases",
            "published_at": "2026-07-15T00:00:00+00:00",
        }
    ]

    matched, coverage = _filter_events_for_query(
        "与金融科技企业研发决策相关的大模型或 Agent 技术新闻",
        events,
    )

    assert [item["event_id"] for item in matched] == ["agent_release"]
    assert "financial_ai" not in coverage


def test_broad_agent_news_query_rejects_generic_agent_and_model_papers():
    events = [
        {
            "event_id": "coding-agent-release",
            "title": "Claude Code release",
            "summary": "Fixed subagent permission checks and session resume model override.",
            "source_id": "anthropic_claude_code_releases",
            "published_at": "2026-07-15T00:00:00+00:00",
        },
        {
            "event_id": "robot-paper",
            "title": "Humanoid foundation model",
            "summary": "A model for embodied agents performs robot control.",
            "source_id": "arxiv_agent_papers",
            "published_at": "2026-07-16T00:00:00+00:00",
        },
    ]

    matched, coverage = _filter_events_for_query(
        "最近 AI Agent 平台、模型产品和开发者工具新闻",
        events,
    )

    assert [item["event_id"] for item in matched] == ["coding-agent-release"]
    assert coverage["agent_harness"] is True
    assert coverage["model_products"] is False
    assert coverage["developer_tools"] is True


def test_latest_news_defaults_to_30_days_and_excludes_papers_without_request():
    constraints = _parse_query_constraints("请生成最新 Agent Harness 新闻报告")
    today = date.today()
    events = [
        {
            "event_id": "release",
            "source_id": "anthropic_claude_code_releases",
            "published_at": today.isoformat(),
        },
        {
            "event_id": "paper",
            "source_id": "arxiv_agent_papers",
            "published_at": today.isoformat(),
        },
    ]

    selected, diagnostics = _apply_query_constraints(events, constraints)

    assert constraints["date_to"] == today.isoformat()
    assert constraints["date_from"] == (today - timedelta(days=30)).isoformat()
    assert [item["event_id"] for item in selected] == ["release"]
    assert diagnostics["research_paper_excluded"] == 1


def test_recent_news_parses_calendar_month_window():
    constraints = _parse_query_constraints(
        "请分析最近6个月 Agent Harness、Claude Code、Codex 相关官方发布或新闻"
    )
    today = date.today()
    month_index = today.year * 12 + today.month - 1 - 6
    expected_year = month_index // 12
    expected_month = month_index % 12 + 1
    next_month_index = expected_year * 12 + expected_month
    next_year = next_month_index // 12
    next_month = next_month_index % 12 + 1
    last_day = (date(next_year, next_month, 1) - timedelta(days=1)).day
    expected_from = date(expected_year, expected_month, min(today.day, last_day))

    assert constraints["date_to"] == today.isoformat()
    assert constraints["date_from"] == expected_from.isoformat()
    assert constraints["date_from"] != (today - timedelta(days=30)).isoformat()


def test_official_release_or_news_query_keeps_web_news_layer():
    constraints = _parse_query_constraints(
        "请分析最近6个月 Agent Harness、Claude Code、Codex 相关官方发布或新闻"
    )

    assert constraints["include_web_news"] is True
    assert constraints["official_primary_only"] is False


def test_explicit_paper_request_keeps_arxiv_events():
    constraints = _parse_query_constraints("请汇总最近 30 天的 Agent Harness 论文")
    events = [
        {
            "event_id": "paper",
            "source_id": "arxiv_agent_papers",
            "published_at": date.today().isoformat(),
        }
    ]

    selected, diagnostics = _apply_query_constraints(events, constraints)

    assert [item["event_id"] for item in selected] == ["paper"]
    assert diagnostics["research_paper_excluded"] == 0


def test_configured_primary_source_event_passes_source_validation():
    assert _valid_source_event(
        {
            "source_id": "openai_codex_releases",
            "published_at": "2026-07-15T00:00:00+00:00",
            "retrieved_at": "2026-07-16T00:00:00+00:00",
            "url": "https://github.com/openai/codex/releases/tag/v1.0.0",
        }
    ) is True
