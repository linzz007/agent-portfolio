"""Skill executor for recent news and market-signal reports.

The executor consumes live primary-source feeds first, persists a reproducible
snapshot, then structures events and maps them to Company Wiki evidence. Local
fixtures are explicit and can never pass the production evidence gate.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from html import escape
import json
from pathlib import Path
import re
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from policy_impact.company_wiki.indexer import build_company_context_pack
from policy_impact.company_wiki.loader import company_dir
from policy_impact.harness.artifacts import project_root
from policy_impact.harness.state import utc_now_iso
from policy_impact.rag.lexical import tokenize
from policy_impact.rag.retriever import HybridRetriever
from policy_impact.source_connectors import collect_news_sources, load_latest_news_snapshot
from policy_impact.source_connectors.http_client import domain_allowed
from policy_impact.source_connectors.ingestion import load_source_config


def run_recent_news_report(
    company_id: str = "company_001",
    query: str = "",
    *,
    refresh_live_sources: bool = True,
) -> dict[str, Any]:
    run_id = uuid4().hex
    raw_items, source_manifest = _load_news_items(refresh_live_sources=refresh_live_sources)
    cleaned_items = _clean_news_items(raw_items)
    data_mode = _news_data_mode(cleaned_items)
    query_constraints = _parse_query_constraints(query)
    source_disclosure = _source_disclosure(source_manifest, data_mode, query_constraints)
    if _official_model_update_query(query):
        source_disclosure += (
            "；本轮官方产品源覆盖 Claude Code 与 OpenAI Codex 发布，"
            "不等同于底层模型 API、定价和能力更新的全量清单"
        )
    if query_constraints.get("date_from") and query_constraints.get("date_to"):
        source_disclosure += (
            f"；查询窗口 {query_constraints['date_from']} 至 {query_constraints['date_to']}"
        )
        if query_constraints.get("datetime_from") and query_constraints.get("datetime_to"):
            source_disclosure += (
                f"（{query_constraints['query_timezone']}："
                f"{query_constraints['datetime_from']} 至 {query_constraints['datetime_to']}）"
            )
    if query_constraints.get("count_explicit"):
        source_disclosure += f"；最多输出 {query_constraints['requested_count']} 条"
    if query_constraints.get("publisher_restriction"):
        source_disclosure += "；发布主体仅限 " + "、".join(query_constraints["requested_publishers"])
        if query_constraints.get("unavailable_publishers"):
            if query_constraints.get("publisher_owned_web_only"):
                source_disclosure += (
                    "；当前未接入满足发布方自有官网或官方研究博客要求的来源："
                    + "、".join(query_constraints["unavailable_publishers"])
                )
            else:
                source_disclosure += "；当前未接入官方源：" + "、".join(query_constraints["unavailable_publishers"])
    context_pack = build_company_context_pack(company_id, task="recent_news_report")
    events = _structure_events(cleaned_items)
    constrained_events, constraint_diagnostics = _apply_query_constraints(events, query_constraints)
    matched_events, topic_coverage = _filter_events_for_query(query, constrained_events)
    publishable_events = [
        item
        for item in matched_events
        if _valid_source_event(item)
        and _has_substantive_summary(
            _evidence_excerpt(
                str(item.get("summary") or ""),
                list(item.get("matched_query_topics") or []),
            )
        )
    ]
    quality_diagnostics = {
        "matched_count": len(matched_events),
        "publishable_count": len(publishable_events),
        "invalid_source_count": sum(1 for item in matched_events if not _valid_source_event(item)),
        "missing_or_weak_summary_count": sum(
            1
            for item in matched_events
            if not _has_substantive_summary(
                _evidence_excerpt(
                    str(item.get("summary") or ""),
                    list(item.get("matched_query_topics") or []),
                )
            )
        ),
    }
    if topic_coverage:
        topic_coverage = {
            topic: any(topic in (item.get("matched_query_topics") or []) for item in publishable_events)
            for topic in topic_coverage
        }
    all_topics_covered = all(topic_coverage.values()) if topic_coverage else True
    relevance_gate = (
        "allow"
        if data_mode != "fixture" and publishable_events
        else "ask"
    )
    if data_mode == "fixture":
        gate_reason = "当前数据来自本地验收样例，不能作为实时新闻证据"
    elif not matched_events:
        gate_reason = "当前来源快照没有与用户问题匹配的事件"
    elif not publishable_events:
        gate_reason = "匹配事件缺少可审计来源字段或实质性官方摘要，内容质量门控拒绝发布"
    elif not all_topics_covered:
        missing = [_topic_label(topic) for topic, covered in topic_coverage.items() if not covered]
        gate_reason = "部分主题有新闻证据；缺失主题不会生成结论：" + ", ".join(missing)
    elif len(publishable_events) < query_constraints["requested_count"]:
        gate_reason = (
            f"来源与主题门控通过，但请求 {query_constraints['requested_count']} 条，"
            f"当前时间窗口只有 {len(publishable_events)} 条通过内容质量门控；"
            "系统不会用窗口外、摘要缺失或低质量来源补足"
        )
    else:
        gate_reason = "来源可信边界、时间字段和主题覆盖均通过新闻相关性门控"
    analysis = _diversify_analysis(
        _analyze_company_impact(company_id, context_pack, publishable_events, query=query),
        max_per_source=2,
        max_total=query_constraints["requested_count"],
        ensure_secondary_web=bool(query_constraints.get("include_web_news")),
    )
    report_md = _render_report_md(
        company_id,
        context_pack,
        analysis,
        query=query,
        source_disclosure=source_disclosure,
        gate_reason=gate_reason,
        source_manifest=source_manifest,
    )
    report_html = _render_report_html(
        company_id,
        context_pack,
        analysis,
        run_id,
        query=query,
        source_disclosure=source_disclosure,
        gate_reason=gate_reason,
        source_manifest=source_manifest,
    )

    report_dir = company_dir(company_id) / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    run_suffix = run_id[:8]
    report_path = report_dir / f"{today}-recent-news-impact-{run_suffix}.md"
    html_path = report_dir / f"{today}-recent-news-impact-{run_suffix}.html"
    report_path.write_text(report_md, encoding="utf-8")
    html_path.write_text(report_html, encoding="utf-8")

    artifact_dir = company_dir(company_id) / "runs" / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / f"{today}-news-run_artifact.json"
    artifact = {
        "run_id": run_id,
        "company_id": company_id,
        "created_at": utc_now_iso(),
        "skill": "recent_news_report",
        "query": query,
        "data_mode": data_mode,
        "source_disclosure": source_disclosure,
        "source_manifest": source_manifest,
        "topic_coverage": topic_coverage,
        "query_constraints": query_constraints,
        "constraint_diagnostics": constraint_diagnostics,
        "quality_diagnostics": quality_diagnostics,
        "relevance_gate": relevance_gate,
        "gate_reason": gate_reason,
        "raw_items": raw_items,
        "cleaned_items": cleaned_items,
        "structured_events": events,
        "analysis": analysis,
        "report_path": str(report_path),
        "html_report_path": str(html_path),
    }
    artifact_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "status": "done",
        "run_id": run_id,
        "report_path": str(report_path),
        "html_report_path": str(html_path),
        "artifact_path": str(artifact_path),
        "event_count": len(events),
        "matched_event_count": len(matched_events),
        "publishable_event_count": len(publishable_events),
        "selected_event_count": len(analysis),
        "data_mode": data_mode,
        "source_disclosure": source_disclosure,
        "source_manifest": source_manifest,
        "topic_coverage": topic_coverage,
        "query_constraints": query_constraints,
        "constraint_diagnostics": constraint_diagnostics,
        "quality_diagnostics": quality_diagnostics,
        "relevance_gate": relevance_gate,
        "gate_reason": gate_reason,
        "top_matches": [
            {
                "title": item["title"],
                "impact_level": item["impact_level"],
                "publisher": item["news_evidence"].get("publisher", ""),
                "source": item["news_evidence"].get("source", ""),
                "source_level": item["news_evidence"].get("source_level", ""),
                "published_at": (
                    item["news_evidence"].get("published_at_query_timezone")
                    or item["news_evidence"].get("published_at", "")
                ),
                "url": item["news_evidence"].get("url", ""),
                "summary": item["news_evidence"].get("summary", ""),
                "matched_topics": item.get("matched_query_topics") or [],
                "impact_chain": item.get("impact_chain") or [],
                "missing_company_facts": item.get("missing_company_facts") or [],
                "verification_metrics": item.get("verification_metrics") or [],
                "company_evidence": [
                    _format_company_evidence(fact)
                    for fact in item.get("company_evidence") or []
                ][:3],
                "recommended_action": item["recommended_action"],
            }
            for item in analysis
        ],
    }


def _load_news_items(*, refresh_live_sources: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if refresh_live_sources:
        live = collect_news_sources()
        live_items = list(live.get("items") or [])
        if live_items or live.get("collection_succeeded"):
            return live_items, {
                "mode": "fresh_snapshot_cache" if live.get("cache_hit") else "live",
                "collection_succeeded": bool(live.get("collection_succeeded")),
                "source_statuses": live.get("source_statuses") or [],
                "snapshot_path": live.get("snapshot_path") or "",
                "collected_at": live.get("collected_at"),
                "cache_hit": bool(live.get("cache_hit")),
            }

    snapshot = load_latest_news_snapshot()
    snapshot_items = list(snapshot.get("items") or [])
    if snapshot_items:
        return snapshot_items, {
            "mode": "source_snapshot",
            "collection_succeeded": True,
            "source_statuses": snapshot.get("source_statuses") or [],
            "snapshot_path": snapshot.get("snapshot_path") or "",
            "collected_at": snapshot.get("captured_at"),
        }

    news_dir = project_root() / "data" / "news" / "raw"
    items: list[dict[str, Any]] = []
    for path in sorted(news_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                items.append(json.loads(line))
    if items:
        return items, {
            "mode": "fixture",
            "collection_succeeded": False,
            "source_statuses": [],
            "snapshot_path": "",
        }
    return [
        {
            "title": "多地推动中小企业数字化转型城市试点",
            "source": "local_seed",
            "published_at": date.today().isoformat(),
            "url": "",
            "summary": "地方工信部门继续推进中小企业数字化转型，鼓励软件服务商参与诊断、改造和平台服务。",
        },
        {
            "title": "数据安全合规检查覆盖重点软件服务企业",
            "source": "local_seed",
            "published_at": date.today().isoformat(),
            "url": "",
            "summary": "监管部门强调个人信息保护、数据分类分级和安全管理制度，软件企业需要完善内部流程。",
        },
    ], {
        "mode": "fixture",
        "collection_succeeded": False,
        "source_statuses": [],
        "snapshot_path": "",
    }


def _clean_news_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    cleaned = []
    for item in items:
        title = str(item.get("title", "")).strip()
        if not title or title in seen:
            continue
        seen.add(title)
        cleaned.append(
            {
                "news_id": f"news_{len(cleaned) + 1:03d}",
                "title": title,
                "source": str(item.get("source", "")),
                "source_id": str(item.get("source_id", "")),
                "source_level": str(item.get("source_level", "TEST")),
                "source_type": str(item.get("source_type", "local_fixture")),
                "published_at": str(item.get("published_at", "")),
                "retrieved_at": str(item.get("retrieved_at", "")),
                "url": str(item.get("url", "")),
                "summary": str(item.get("summary", "")),
                "topics": list(item.get("topics") or []),
                "data_mode": str(item.get("data_mode") or "fixture"),
                "is_fixture": bool(item.get("is_fixture", item.get("source") == "local_seed")),
                "content_hash": str(item.get("content_hash", "")),
            }
        )
    return cleaned


def _news_data_mode(items: list[dict[str, Any]]) -> str:
    if not items:
        return "fixture"
    if all(item.get("source") == "local_seed" or not item.get("url") for item in items):
        return "fixture"
    modes = {str(item.get("data_mode") or "") for item in items}
    return "live" if "live" in modes else "source_snapshot"


def _healthy_source_count(manifest: dict[str, Any]) -> int:
    return sum(1 for item in manifest.get("source_statuses") or [] if item.get("status") == "ok")


def _source_disclosure(
    source_manifest: dict[str, Any],
    data_mode: str,
    query_constraints: dict[str, Any],
) -> str:
    healthy_count = _healthy_source_count(source_manifest)
    if data_mode == "fixture":
        return "本地验收样例（非实时新闻）"
    if data_mode == "live":
        prefix = "5 分钟内的来源快照" if source_manifest.get("cache_hit") else "实时抓取的来源快照"
    else:
        prefix = "已录制的来源快照（保留原始 URL、发布时间与抓取时间）"
    if query_constraints.get("official_primary_only"):
        scope = "官方/一手来源"
    elif query_constraints.get("include_web_news"):
        scope = "官方源 + 全网新闻线索"
    else:
        scope = "配置新闻源"
    return f"{prefix}（{healthy_count} 个来源成功，范围：{scope}）"


def _valid_source_event(event: dict[str, Any]) -> bool:
    source_id = str(event.get("source_id") or "")
    source = next(
        (
            item
            for item in load_source_config().get("news_sources", [])
            if str(item.get("source_id") or "") == source_id
        ),
        None,
    )
    if source is None:
        return False
    return bool(
        event.get("published_at")
        and event.get("retrieved_at")
        and domain_allowed(str(event.get("url") or ""), list(source.get("allowed_domains") or []))
    )


def _has_substantive_summary(value: Any) -> bool:
    text = " ".join(str(value or "").split())
    if len(text) < 40:
        return False
    placeholders = (
        "官方来源未提供变更摘要",
        "来源未提供摘要",
        "no summary provided",
        "summary unavailable",
    )
    return not any(marker in text.lower() for marker in placeholders)


_EXPLICIT_DATE_RANGE = re.compile(
    r"(?P<start>\d{4}-\d{1,2}-\d{1,2})(?:[ T](?P<start_time>\d{1,2}:\d{2}(?::\d{2})?))?"
    r"\s*(?:至|到|~|—|–)\s*"
    r"(?P<end>\d{4}-\d{1,2}-\d{1,2})(?:[ T](?P<end_time>\d{1,2}:\d{2}(?::\d{2})?))?"
)
_PRIMARY_TECH_NEWS_SOURCES = {
    "anthropic_claude_code_releases",
    "openai_codex_releases",
    "langgraph_releases",
    "mcp_specification_releases",
    "huggingface_blog",
    "openai_news",
}

_SECONDARY_WEB_NEWS_SOURCES = {
    "techcrunch_ai",
    "theverge_ai",
    "google_news_agent_harness",
    "google_news_claude_code_codex",
}

_PUBLISHER_SOURCE_GROUPS = {
    "OpenAI": {
        "aliases": ("openai",),
        "source_ids": {"openai_codex_releases"},
        "owned_web_source_ids": set(),
    },
    "Anthropic": {
        "aliases": ("anthropic",),
        "source_ids": {"anthropic_claude_code_releases"},
        "owned_web_source_ids": set(),
    },
    "Google DeepMind": {
        "aliases": ("google deepmind", "deepmind"),
        "source_ids": set(),
        "owned_web_source_ids": set(),
    },
    "Hugging Face": {
        "aliases": ("hugging face", "huggingface"),
        "source_ids": {"huggingface_blog"},
        "owned_web_source_ids": {"huggingface_blog"},
    },
    "LangGraph": {
        "aliases": ("langgraph",),
        "source_ids": {"langgraph_releases"},
        "owned_web_source_ids": set(),
    },
    "Model Context Protocol": {
        "aliases": ("model context protocol",),
        "source_ids": {"mcp_specification_releases"},
        "owned_web_source_ids": set(),
    },
}


def _publisher_for_source_id(source_id: str) -> str:
    value = str(source_id or "")
    for publisher, config in _PUBLISHER_SOURCE_GROUPS.items():
        if value in set(config.get("source_ids") or set()):
            return publisher
    return ""


def _parse_query_constraints(query: str) -> dict[str, Any]:
    text = str(query or "")
    normalized = text.lower()
    date_from = ""
    date_to = ""
    datetime_from = ""
    datetime_to = ""
    query_timezone = "Asia/Shanghai" if "北京时间" in text else "UTC"
    range_match = _EXPLICIT_DATE_RANGE.search(text)
    if range_match:
        try:
            date_from = date.fromisoformat(range_match.group("start")).isoformat()
            date_to = date.fromisoformat(range_match.group("end")).isoformat()
            if range_match.group("start_time") or range_match.group("end_time"):
                zone = ZoneInfo(query_timezone) if query_timezone != "UTC" else timezone.utc
                start_time = time.fromisoformat(range_match.group("start_time") or "00:00:00")
                end_time = time.fromisoformat(range_match.group("end_time") or "23:59:59")
                datetime_from = datetime.combine(date.fromisoformat(date_from), start_time, zone).isoformat()
                datetime_to = datetime.combine(date.fromisoformat(date_to), end_time, zone).isoformat()
        except ValueError:
            date_from = ""
            date_to = ""
            datetime_from = ""
            datetime_to = ""
    if not date_from:
        lookback_months = re.search(r"(?:过去|最近|近)\s*(\d{1,2})\s*(?:个)?月", text)
        lookback_days = re.search(r"(?:过去|最近|近)\s*(\d{1,3})\s*(?:天|日)", text)
        lookback_years = re.search(r"(?:过去|最近|近)\s*(\d{1,2})\s*年", text)
        if lookback_months:
            end = date.today()
            date_to = end.isoformat()
            date_from = _subtract_calendar_months(end, int(lookback_months.group(1))).isoformat()
        elif lookback_years:
            end = date.today()
            date_to = end.isoformat()
            date_from = _subtract_calendar_months(end, int(lookback_years.group(1)) * 12).isoformat()
        elif lookback_days:
            end = date.today()
            date_to = end.isoformat()
            date_from = (end - timedelta(days=int(lookback_days.group(1)))).isoformat()
        elif any(term in text for term in ("最近", "最新", "近期")):
            end = date.today()
            date_to = end.isoformat()
            date_from = (end - timedelta(days=30)).isoformat()

    count_match = re.search(r"(\d{1,2})\s*条", text)
    requested_count = max(1, min(int(count_match.group(1)), 10)) if count_match else 5
    include_web_news = _include_web_news_request(text, normalized)
    official_only = _explicit_primary_only_request(text, normalized) or (
        any(
            term in normalized
            for term in (
                "官方一手",
                "一手来源",
                "一手证据",
                "官方来源",
                "官方发布",
            )
        )
        or ("官方" in text and any(term in text for term in ("来源", "原文", "发布", "博客")))
    ) and not include_web_news
    requested_publishers = [
        publisher
        for publisher, config in _PUBLISHER_SOURCE_GROUPS.items()
        if any(alias in normalized for alias in config["aliases"])
    ]
    publisher_restriction = bool(requested_publishers) and (
        any(term in normalized for term in ("只限", "仅限", "只要", "发布主体", "发布方", "来自"))
        or official_only
    )
    publisher_owned_web_only = any(
        term in text
        for term in (
            "发布方自己的官网",
            "发布方自有官网",
            "自己的官网或官方研究博客",
            "自有官网或官方研究博客",
            "官网或官方研究博客",
        )
    )
    publisher_source_field = "owned_web_source_ids" if publisher_owned_web_only else "source_ids"
    allowed_publisher_source_ids = sorted(
        {
            source_id
            for publisher in requested_publishers
            for source_id in _PUBLISHER_SOURCE_GROUPS[publisher][publisher_source_field]
        }
    ) if publisher_restriction else []
    unavailable_publishers = [
        publisher
        for publisher in requested_publishers
        if publisher_restriction and not _PUBLISHER_SOURCE_GROUPS[publisher][publisher_source_field]
    ]
    return {
        "date_from": date_from,
        "date_to": date_to,
        "datetime_from": datetime_from,
        "datetime_to": datetime_to,
        "query_timezone": query_timezone,
        "requested_count": requested_count,
        "count_explicit": bool(count_match),
        "official_primary_only": official_only,
        "include_web_news": include_web_news,
        "allow_research_papers": "论文" in text or "paper" in normalized,
        "publisher_restriction": publisher_restriction,
        "publisher_owned_web_only": publisher_owned_web_only,
        "publisher_source_scope": (
            "publisher_owned_web_or_research_blog"
            if publisher_owned_web_only
            else "publisher_official_channels"
        ),
        "requested_publishers": requested_publishers if publisher_restriction else [],
        "allowed_publisher_source_ids": allowed_publisher_source_ids,
        "unavailable_publishers": unavailable_publishers,
    }


def _include_web_news_request(text: str, normalized: str) -> bool:
    if _explicit_primary_only_request(text, normalized):
        return False
    return any(
        term in text
        for term in (
            "全网",
            "新闻",
            "媒体",
            "报道",
            "舆情",
            "行业动态",
            "热门文章",
        )
    ) or any(term in normalized for term in ("web news", "google news", "bing news", "media coverage"))


def _explicit_primary_only_request(text: str, normalized: str) -> bool:
    strict_terms = (
        "必须给官方一手",
        "每条必须给官方",
        "只限官方",
        "仅限官方",
        "只看官方",
        "只要官方",
        "不要使用二手",
        "不使用二手",
        "排除二手",
    )
    return any(term in text for term in strict_terms) or any(
        term in normalized
        for term in (
            "official primary only",
            "primary sources only",
            "no secondary media",
        )
    )


def _subtract_calendar_months(value: date, months: int) -> date:
    months = max(0, int(months))
    month_index = value.year * 12 + value.month - 1 - months
    year = month_index // 12
    month = month_index % 12 + 1
    next_month_index = year * 12 + month
    next_year = next_month_index // 12
    next_month = next_month_index % 12 + 1
    last_day = (date(next_year, next_month, 1) - timedelta(days=1)).day
    return date(year, month, min(value.day, last_day))


def _apply_query_constraints(
    events: list[dict[str, Any]],
    constraints: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    start = date.fromisoformat(constraints["date_from"]) if constraints.get("date_from") else None
    end = date.fromisoformat(constraints["date_to"]) if constraints.get("date_to") else None
    start_at = _parse_aware_datetime(str(constraints.get("datetime_from") or ""))
    end_at = _parse_aware_datetime(str(constraints.get("datetime_to") or ""))
    diagnostics = {
        "input_count": len(events),
        "outside_date_window": 0,
        "outside_datetime_window": 0,
        "invalid_or_missing_date": 0,
        "non_primary_source": 0,
        "outside_requested_publishers": 0,
        "research_paper_excluded": 0,
        "output_count": 0,
    }
    selected: list[dict[str, Any]] = []
    for event in events:
        raw_published_at = str(event.get("published_at") or "")
        raw_date = raw_published_at[:10]
        published_at = _parse_aware_datetime(raw_published_at)
        if start_at or end_at:
            if published_at is None:
                diagnostics["invalid_or_missing_date"] += 1
                continue
            if (start_at and published_at < start_at) or (end_at and published_at > end_at):
                diagnostics["outside_datetime_window"] += 1
                continue
        published = None
        if not (start_at or end_at):
            try:
                published = date.fromisoformat(raw_date)
            except ValueError:
                if start or end:
                    diagnostics["invalid_or_missing_date"] += 1
                    continue
            if published and ((start and published < start) or (end and published > end)):
                diagnostics["outside_date_window"] += 1
                continue
        if constraints.get("official_primary_only"):
            source_id = str(event.get("source_id") or "")
            allowed = source_id in _PRIMARY_TECH_NEWS_SOURCES
            if constraints.get("allow_research_papers") and source_id == "arxiv_agent_papers":
                allowed = True
            if not allowed:
                diagnostics["non_primary_source"] += 1
                continue
        elif (
            str(event.get("source_id") or "") == "arxiv_agent_papers"
            and not constraints.get("allow_research_papers")
        ):
            diagnostics["research_paper_excluded"] += 1
            continue
        if constraints.get("publisher_restriction"):
            allowed_source_ids = set(constraints.get("allowed_publisher_source_ids") or [])
            if str(event.get("source_id") or "") not in allowed_source_ids:
                diagnostics["outside_requested_publishers"] += 1
                continue
        copied = dict(event)
        if published_at is not None and constraints.get("query_timezone"):
            zone_name = str(constraints.get("query_timezone") or "UTC")
            zone = ZoneInfo(zone_name) if zone_name != "UTC" else timezone.utc
            copied["published_at_query_timezone"] = published_at.astimezone(zone).isoformat()
        selected.append(copied)
    diagnostics["output_count"] = len(selected)
    return selected, diagnostics


def _parse_aware_datetime(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text or "T" not in text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


_TOPIC_GROUPS = {
    "agent_harness": {
        "query": ("agent", "智能体", "harness", "tool policy", "工具权限", "agent 平台"),
        "evidence": (
            "agent harness",
            "coding agent",
            "agent runtime",
            "agent loop",
            "subagent",
            "tool policy",
            "tool permission",
            "permission",
            "hook",
            "pretooluse",
            "trace",
            "runtime",
            "background agent",
            "agent task",
            "sandbox",
            "worktree",
            "session resume",
            "agent orchestration",
            "智能体运行时",
            "智能体编排",
            "工具权限",
        ),
    },
    "model_products": {
        "query": ("模型产品", "大模型", "模型", "model"),
        "evidence": (
            "model release",
            "model api",
            "model capability",
            "model pricing",
            "foundation model api",
            "模型发布",
            "模型更新",
            "模型接口",
            "模型价格",
        ),
    },
    "developer_tools": {
        "query": ("开发者工具", "developer tool", "sdk", "api", "codex", "claude", "ide"),
        "evidence": (
            "developer tool",
            "sdk",
            "cli",
            "ide",
            "codex",
            "claude code",
            "chatgpt",
            "mcp",
            "package install",
            "开发者工具",
            "开发工具",
        ),
    },
    "financial_ai": {
        "query": ("金融", "投研", "投资", "合规", "监管", "数据安全"),
        "evidence": ("金融", "投研", "投资", "合规", "监管", "数据安全"),
    },
}

_SIGNAL_GROUPS = {
    "inference_cost": {
        "query": ("推理成本", "推理效率", "token 成本", "token价格", "模型价格", "inference cost"),
        "evidence": (
            "inference cost",
            "inference pricing",
            "token cost",
            "token price",
            "token pricing",
            "price per token",
            "cost per token",
            "cost per request",
            "throughput",
            "tokens per second",
            "inference latency",
            "prompt cache",
        ),
    },
    "long_context": {
        "query": ("长上下文", "上下文窗口", "context window", "long context", "上下文管理"),
        "evidence": ("context window", "long context", "auto-compact", "context used", "max output tokens", "large responses"),
    },
    "tool_use": {
        "query": ("工具调用", "tool calling", "function calling", "agent 工具", "工具权限", "mcp"),
        "evidence": ("tool call", "agent tool", "subagent", "mcp", "hook", "permission", "sandbox", "function call"),
    },
}

_MODEL_VENDOR_UPDATE_SOURCES = {
    "anthropic_claude_code_releases",
    "openai_codex_releases",
}

_TOPIC_LABELS = {
    "inference_cost": "推理成本",
    "long_context": "长上下文",
    "tool_use": "Agent 工具调用",
    "agent_harness": "Agent Harness",
    "model_products": "模型产品",
    "developer_tools": "开发者工具",
    "financial_ai": "金融 AI",
}

_DEFAULT_IMPACT_TOPICS = ("agent_harness", "developer_tools", "model_products")

_IMPACT_LEVEL_LABELS = {
    "high": "高影响",
    "medium": "中影响",
    "conditional": "条件式影响",
    "low": "低影响",
}


def _topic_label(topic: str) -> str:
    return _TOPIC_LABELS.get(str(topic), str(topic))


def _impact_level_label(level: str) -> str:
    return _IMPACT_LEVEL_LABELS.get(str(level), str(level))


def _format_company_evidence(fact: dict[str, Any]) -> str:
    source_kind = str(fact.get("source_kind") or "other")
    source_label = {
        "public_disclosure": "公开披露",
        "analysis_inference": "分析推断",
        "project_design": "项目设计",
        "user_confirmed": "用户确认",
    }.get(source_kind, source_kind)
    fact_id = str(fact.get("fact_id") or "unknown")
    return f"[{source_label}; fact_id: {fact_id}] {str(fact.get('value') or '')}"


def _official_model_update_query(query: str) -> bool:
    normalized = str(query or "").lower()
    if _include_web_news_request(normalized, normalized):
        return False
    return _explicit_primary_only_request(normalized, normalized) or any(
        term in normalized for term in ("官方更新", "官方发布", "主流大模型")
    )


def _is_broad_impact_report_query(query: str) -> bool:
    text = str(query or "")
    normalized = text.lower()
    asks_report = any(term in text for term in ("报告", "日报", "周报", "分析", "影响"))
    asks_external_change = any(
        term in text
        for term in ("新闻", "外部变化", "技术发布", "行业动态", "最新", "近期", "最近")
    ) or any(term in normalized for term in ("news", "release", "market signal", "external change"))
    asks_company_impact = any(term in text for term in ("对我", "对当前", "对公司", "对企业", "对产品", "wiki", "画像", "影响"))
    return asks_report and asks_external_change and asks_company_impact


def _filter_events_for_query(
    query: str,
    events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, bool]]:
    normalized = str(query or "").lower()
    signal_topics = {
        topic: config
        for topic, config in _SIGNAL_GROUPS.items()
        if any(term in normalized for term in config["query"])
    }
    if signal_topics:
        if "主流大模型" in normalized:
            # Current connectors cover coding-agent product releases, not
            # foundation-model API, pricing, or capability announcements.
            return [], {topic: False for topic in signal_topics}
        official_only = _official_model_update_query(normalized)
        coverage = {topic: False for topic in signal_topics}
        matched: list[dict[str, Any]] = []
        for event in events:
            if official_only and str(event.get("source_id") or "") not in _MODEL_VENDOR_UPDATE_SOURCES:
                continue
            text = f"{event.get('title', '')} {event.get('summary', '')}".lower()
            event_topics = [topic for topic in signal_topics if _matches_signal_evidence(topic, text)]
            if not event_topics:
                continue
            copied = dict(event)
            copied["matched_query_topics"] = event_topics
            copied["query_match_score"] = 30 + 18 * len(event_topics)
            matched.append(copied)
            for topic in event_topics:
                coverage[topic] = True
        matched.sort(
            key=lambda item: (
                int(item.get("query_match_score") or 0),
                str(item.get("published_at") or ""),
            ),
            reverse=True,
        )
        return matched, coverage

    required_topics = {
        topic: config
        for topic, config in _TOPIC_GROUPS.items()
        if any(term in normalized for term in config["query"])
    }
    # "金融科技企业" describes the decision maker, not necessarily words
    # that must appear in a model or Agent vendor release.
    if "financial_ai" in required_topics and any(
        topic in required_topics for topic in ("agent_harness", "model_products", "developer_tools")
    ):
        required_topics.pop("financial_ai")
    if not required_topics:
        if not _is_broad_impact_report_query(query):
            return list(events), {}
        required_topics = {
            topic: _TOPIC_GROUPS[topic]
            for topic in _DEFAULT_IMPACT_TOPICS
        }

    coverage: dict[str, bool] = {}
    matched_ids: set[str] = set()
    for topic, config in required_topics.items():
        topic_matches = []
        for event in events:
            text = f"{event.get('title', '')} {event.get('summary', '')}".lower()
            if any(term in text for term in config["evidence"]):
                copied = dict(event)
                copied.setdefault("matched_query_topics", []).append(topic)
                copied["query_match_score"] = int(copied.get("query_match_score") or 0) + 18
                topic_matches.append(copied)
                matched_ids.add(str(event.get("event_id") or ""))
        coverage[topic] = bool(topic_matches)
    matched = []
    for event in events:
        if str(event.get("event_id") or "") not in matched_ids:
            continue
        text = f"{event.get('title', '')} {event.get('summary', '')}".lower()
        topics = [
            topic
            for topic, config in required_topics.items()
            if any(term in text for term in config["evidence"])
        ]
        copied = dict(event)
        copied["matched_query_topics"] = topics
        copied["query_match_score"] = 18 * len(topics)
        matched.append(copied)
    matched.sort(key=lambda item: int(item.get("query_match_score") or 0), reverse=True)
    return matched, coverage


def _matches_signal_evidence(topic: str, text: str) -> bool:
    """Require a topic-specific evidence phrase, not an isolated UI word.

    Release notes often mention a session "cost counter" or a model-picker
    "price" without changing inference economics. Those weak lexical matches
    must not satisfy an inference-cost request.
    """

    normalized = str(text or "").lower()
    terms = _SIGNAL_GROUPS.get(topic, {}).get("evidence", ())
    return any(term in normalized for term in terms)


def _structure_events(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events = []
    for item in items:
        text = f"{item['title']} {item['summary']}"
        category = "market_signal"
        if any(word in text for word in ("监管", "合规", "安全", "检查", "处罚")):
            category = "compliance_risk"
        elif any(word in text for word in ("补贴", "试点", "扶持", "采购", "招标", "数字化转型")):
            category = "opportunity"
        elif any(word in text for word in ("融资", "投资", "资本")):
            category = "capital_signal"
        events.append(
            {
                "event_id": f"event_{len(events) + 1:03d}",
                "title": item["title"],
                "category": category,
                "summary": item["summary"],
                "source": item["source"],
                "publisher": _publisher_for_source_id(str(item.get("source_id") or "")),
                "source_id": item.get("source_id", ""),
                "source_level": item.get("source_level", ""),
                "source_type": item.get("source_type", ""),
                "published_at": item["published_at"],
                "retrieved_at": item.get("retrieved_at", ""),
                "url": item["url"],
                "topics": item.get("topics", []),
                "tokens": tokenize(text),
            }
        )
    return events


def _analyze_company_impact(
    company_id: str,
    context_pack: dict[str, Any],
    events: list[dict[str, Any]],
    *,
    query: str = "",
) -> list[dict[str, Any]]:
    fact_chunks = []
    for fact in context_pack.get("pinned_facts", []) + context_pack.get("retrieved_company_facts", []):
        fact_id = str(fact.get("fact_id") or "")
        value = str(fact.get("value") or fact.get("text") or "")
        source_path = str(fact.get("source_path") or "")
        if (
            not fact_id
            or fact_id.startswith("preference.")
            or fact_id in {"risk.agent_harness", "risk.investment_advice"}
            or "preferences" in source_path.lower()
            or value.startswith(("用户希望", "报告应", "系统应"))
        ):
            continue
        fact_chunks.append(
            {
                "chunk_id": fact_id,
                "doc_id": source_path,
                "text": f"{value}\n{' '.join(str(x) for x in fact.get('policy_relevance', []))}",
                "metadata": fact,
            }
        )
    retriever = HybridRetriever(fact_chunks)
    analysis = []
    for event in events:
        hits = retriever.retrieve(f"{event['title']} {event['summary']}", top_k=5) if fact_chunks else []
        hits = _prioritize_product_facts(query, hits, fact_chunks)
        score = _news_score(event, hits)
        level = "conditional" if hits else "low"
        topics = list(event.get("matched_query_topics") or [])
        change_summary = _evidence_excerpt(str(event.get("summary") or ""), topics)
        analysis.append(
            {
                "event_id": event["event_id"],
                "title": event["title"],
                "category": event["category"],
                "impact_level": level,
                "impact_score": score,
                "reasoning": [
                    f"正文命中查询维度：{', '.join(topics) if topics else '通用主题'}",
                    "当前只证明外部组件变化，未证明示例产品或 iFinD 已使用该组件。",
                ],
                "company_evidence": [hit.get("metadata", {}) for hit in hits],
                "news_evidence": {**event, "summary": change_summary},
                "matched_query_topics": topics,
                "impact_chain": _impact_chain(
                    topics,
                    event_text=f"{event.get('title', '')} {change_summary}",
                ),
                "missing_company_facts": _missing_company_facts(topics),
                "verification_metrics": _verification_metrics(topics),
                "recommended_action": _news_action_for_topics(topics),
            }
        )
    analysis.sort(key=lambda item: item["impact_score"], reverse=True)
    return analysis


def _news_score(event: dict[str, Any], hits: list[dict[str, Any]]) -> int:
    category_weight = {
        "compliance_risk": 32,
        "opportunity": 28,
        "capital_signal": 18,
        "market_signal": 14,
    }.get(str(event.get("category")), 14)
    source_weight = {
        "L1": 8,
        "L2": 6,
        "L3": 3,
        "L4": 0,
        "L5": -10,
        "TEST": -40,
    }.get(str(event.get("source_level") or "L5"), -10)
    importance = max((int(hit.get("metadata", {}).get("importance", 3)) for hit in hits), default=2)
    confidence = max((float(hit.get("metadata", {}).get("confidence", 0.5)) for hit in hits), default=0.5)
    query_match = int(event.get("query_match_score") or 0)
    score = 8 + category_weight // 2 + source_weight + importance * 5 + min(len(hits) * 3, 10) + confidence * 4 + query_match
    return max(0, min(100, round(score)))


def _prioritize_product_facts(
    query: str,
    hits: list[dict[str, Any]],
    fact_chunks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized = str(query or "").lower()
    required_ids = []
    if "示例产品" in normalized:
        required_ids.append("ai_product.wencai")
    if "ifind" in normalized:
        required_ids.append("ai_product.ifind")
    if "示例公司" in normalized and any(term in normalized for term in ("产品", "研发", "影响", "决策")):
        required_ids.extend(("ai_product.wencai", "ai_product.ifind"))
    by_id = {str(item.get("chunk_id") or ""): item for item in fact_chunks}
    prioritized = [by_id[fact_id] for fact_id in required_ids if fact_id in by_id]
    prioritized.extend(hits)
    out = []
    seen = set()
    for item in prioritized:
        fact_id = str(item.get("metadata", {}).get("fact_id") or item.get("chunk_id") or "")
        if not fact_id or fact_id in seen:
            continue
        seen.add(fact_id)
        out.append(item)
    return out[:3]


def _evidence_excerpt(summary: str, topics: list[str]) -> str:
    text = " ".join(str(summary or "").split())
    if not text:
        return "官方来源未提供变更摘要。"
    terms = {
        term
        for topic in topics
        for term in (
            _SIGNAL_GROUPS.get(topic, {}).get("evidence", ())
            or _TOPIC_GROUPS.get(topic, {}).get("evidence", ())
        )
    }
    if not terms:
        terms = {
            "subagent",
            "permission",
            "worktree",
            "isolation",
            "process_wrapper",
            "corporate launcher",
            "stream-json",
            "sandbox",
            "hook",
            "trace",
        }
    release_items = [
        part.strip()
        for part in re.split(
            r"(?=\s(?:Added|Fixed|Improved|Changed|Updated|Removed|Deprecated)\s)",
            f" {text}",
            flags=re.IGNORECASE,
        )
        if part.strip()
    ]
    sentences = [part.strip() for part in re.split(r"(?<=[.!?。！？])\s+", text) if part.strip()]
    candidates = release_items if len(release_items) > 1 else sentences
    substantive_candidates = [
        candidate
        for candidate in candidates
        if len(candidate) >= 40
        and candidate.strip().lower() not in {"what's changed", "changes", "release notes"}
        and _looks_complete_excerpt(candidate)
    ]
    ranked_matches = sorted(
        (
            (sum(1 for term in terms if term in candidate.lower()), -index, candidate)
            for index, candidate in enumerate(substantive_candidates)
        ),
        reverse=True,
    )
    matched = [candidate for score, _, candidate in ranked_matches if score > 0]
    selected = matched[:2] or substantive_candidates[:2]
    return _join_complete_excerpts(selected, max_chars=1600)


def _join_complete_excerpts(parts: list[str], *, max_chars: int) -> str:
    selected: list[str] = []
    for raw in parts:
        part = " ".join(str(raw or "").split()).strip()
        if not part:
            continue
        candidate = " ".join([*selected, part])
        if len(candidate) <= max_chars:
            selected.append(part)
            continue
        if selected:
            break
        continue
    return " ".join(selected)


def _looks_complete_excerpt(value: str) -> bool:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return False
    final_word_match = re.search(r"([A-Za-z]+)[^A-Za-z]*$", text)
    final_word = final_word_match.group(1).lower() if final_word_match else ""
    return final_word not in {
        "a",
        "an",
        "and",
        "after",
        "before",
        "by",
        "for",
        "from",
        "in",
        "of",
        "on",
        "or",
        "the",
        "to",
        "was",
        "were",
        "when",
        "with",
    }


def _impact_chain(topics: list[str], *, event_text: str = "") -> list[str]:
    normalized = str(event_text or "").lower()
    if (
        "model override" in normalized
        or "parent model" in normalized
        or ("model" in normalized and any(term in normalized for term in ("resume", "follow-up", "followup")))
    ):
        return [
            "恢复或追问时的模型覆盖继承变化 -> 多轮会话可能落到非预期父模型 -> 回放 resume/follow-up 场景并核对每轮 model_id、成本和答案一致性",
        ]
    if "session resume" in normalized or (
        "resume" in normalized and any(term in normalized for term in ("background task", "orphaned task"))
    ):
        return [
            "会话恢复与孤儿后台任务清理变化 -> 长任务恢复时可能出现状态重复、丢失或错误提示 -> 回放 session resume、断线重连和后台任务终态一致性",
        ]
    if "worktree" in normalized or "isolation" in normalized:
        return [
            "子智能体代码隔离修复 -> 主仓误写风险可能下降 -> 回放 worktree 权限策略、git 变更审计和越权用例",
        ]
    if "process_wrapper" in normalized or "corporate launcher" in normalized:
        return [
            "企业进程包装器 -> 智能体自启动可纳入统一企业控制 -> 验证 wrapper 失败策略、环境注入和启动审计完整性",
        ]
    if "forward-subagent-text" in normalized or "stream-json" in normalized:
        return [
            "子智能体文本进入流式 Trace -> 可观测性增强但敏感内容暴露面扩大 -> 核验日志脱敏、权限继承和 Trace schema 兼容性",
        ]
    if "attach" in normalized and "daemon" in normalized:
        return [
            "会话 attach 与后台服务恢复改进 -> 长任务接管失败率可能下降 -> 回放断线重连、终端尺寸恢复和会话状态一致性",
        ]
    chains = {
        "inference_cost": "外部模型推理价格或吞吐变化 -> 单次问答成本可能变化 -> 需用同任务集核算真实单位成本",
        "long_context": "上下文窗口或压缩行为变化 -> 长研报与多轮问答的信息保留可能变化 -> 需回归长文准确率与截断率",
        "tool_use": "工具权限、Hook 或子智能体行为变化 -> Agent 工具可靠性与审计边界可能变化 -> 需回归成功率、越权率与 Trace 完整性",
        "agent_harness": "Agent Runtime 或 Harness 机制变化 -> 现有循环控制与可观测能力可能出现替代方案 -> 需对照当前 Trace、权限和恢复能力",
        "model_products": "模型产品能力或接口变化 -> 示例产品/iFinD 的候选模型边界可能变化 -> 需在同一业务任务集进行质量成本对比",
        "developer_tools": "SDK、协议或开发工具变化 -> 集成复杂度和运行稳定性可能变化 -> 需在隔离环境验证兼容性与故障恢复",
        "financial_ai": "金融 AI 能力变化 -> 可能影响投研问答或机构服务方案 -> 需先核验产品适配和合规边界",
    }
    return [chains[topic] for topic in topics if topic in chains]


def _missing_company_facts(topics: list[str]) -> list[str]:
    missing = ["示例产品或 iFinD 是否实际使用该模型、SDK 或同类组件"]
    if "inference_cost" in topics:
        missing.append("当前模型单次任务 token、延迟与成本基线")
    if "long_context" in topics:
        missing.append("当前长文档截断、压缩与答案质量基线")
    if "tool_use" in topics:
        missing.append("当前工具调用成功率、拒绝率和越权事件基线")
    if "agent_harness" in topics:
        missing.append("当前 Agent Loop、权限门控和恢复机制的可替代性")
    if "model_products" in topics:
        missing.append("示例产品或 iFinD 当前模型版本、调用量和质量成本基线")
    if "developer_tools" in topics:
        missing.append("当前生产依赖版本、兼容约束和回滚方案")
    return missing[:3]


def _verification_metrics(topics: list[str]) -> list[str]:
    metrics = {
        "inference_cost": "每成功任务成本、P95 延迟、tokens/任务",
        "long_context": "长文问答准确率、关键信息保留率、上下文截断率",
        "tool_use": "工具调用成功率、误拒率、越权率、Trace 完整率",
        "agent_harness": "端到端成功率、恢复成功率、越权率、Trace 完整率",
        "model_products": "业务准确率、每成功任务成本、P95 延迟、回退率",
        "developer_tools": "兼容用例通过率、错误率、回滚耗时、集成工时",
        "financial_ai": "业务命中率、事实错误率、人工复核率、合规拦截率",
    }
    return [metrics[topic] for topic in topics if topic in metrics]


def _news_action_for_topics(topics: list[str]) -> str:
    metrics = "；".join(_verification_metrics(topics)) or "任务成功率与错误率"
    return f"先确认生产栈是否受影响；若受影响，用影子流量对比 {metrics}，未验证前不进入生产升级。"


def _diversify_analysis(
    items: list[dict[str, Any]],
    *,
    max_per_source: int,
    max_total: int,
    ensure_secondary_web: bool = False,
) -> list[dict[str, Any]]:
    selected = []
    deferred = []
    source_counts: dict[str, int] = {}
    for item in items:
        source_id = str(item.get("news_evidence", {}).get("source_id") or "unknown")
        if source_counts.get(source_id, 0) >= max_per_source:
            deferred.append(item)
            continue
        selected.append(item)
        source_counts[source_id] = source_counts.get(source_id, 0) + 1
        if len(selected) >= max_total:
            break
    if len(selected) < max_total:
        selected_ids = {str(item.get("event_id") or "") for item in selected}
        for item in deferred:
            event_id = str(item.get("event_id") or "")
            if event_id in selected_ids:
                continue
            selected.append(item)
            selected_ids.add(event_id)
            if len(selected) >= max_total:
                break
    if ensure_secondary_web and max_total > 0:
        selected_ids = {str(item.get("event_id") or "") for item in selected}
        has_secondary = any(_is_secondary_web_item(item) for item in selected)
        candidate = next(
            (
                item
                for item in items
                if _is_secondary_web_item(item)
                and str(item.get("event_id") or "") not in selected_ids
            ),
            None,
        )
        if candidate and not has_secondary:
            if len(selected) < max_total:
                selected.append(candidate)
            else:
                selected[-1] = candidate
    return selected


def _is_secondary_web_item(item: dict[str, Any]) -> bool:
    evidence = item.get("news_evidence", {})
    return (
        str(evidence.get("source_id") or "") in _SECONDARY_WEB_NEWS_SOURCES
        or str(evidence.get("source_level") or "") == "L5"
    )


def _news_action(category: str, level: str) -> str:
    if category == "compliance_risk":
        return "由合规负责人确认是否触发数据安全、隐私或监管制度更新"
    if category == "opportunity":
        return "评估是否匹配公司当前产品能力、区域和资质，必要时加入政策/商机跟进清单"
    if level == "high":
        return "纳入本周管理层例会观察事项"
    return "低频观察，等待更多公开信息确认"


def _report_synthesis(analysis: list[dict[str, Any]]) -> dict[str, Any]:
    if not analysis:
        return {
            "overall_judgment": "当前数据源没有找到足以支撑影响判断的匹配证据。",
            "executive_summary": [
                "本轮没有可发布的匹配新闻证据，系统不生成推断性业务结论。",
                "建议补充官方发布、主流媒体报道或企业内部依赖信息后重新运行。",
            ],
            "impact_sections": [],
            "action_items": ["补充可审计数据源后重新生成报告。"],
            "source_level_counts": {},
            "topic_counts": {},
        }
    source_level_counts: dict[str, int] = {}
    topic_counts: dict[str, int] = {}
    for item in analysis:
        evidence = item.get("news_evidence", {})
        level = str(evidence.get("source_level") or "未标注")
        source_level_counts[level] = source_level_counts.get(level, 0) + 1
        for topic in item.get("matched_query_topics") or []:
            topic_counts[str(topic)] = topic_counts.get(str(topic), 0) + 1
    official_count = sum(source_level_counts.get(level, 0) for level in ("L1", "L2", "L3"))
    web_count = source_level_counts.get("L5", 0)
    topic_labels = [_topic_label(topic) for topic, _ in sorted(topic_counts.items(), key=lambda pair: pair[1], reverse=True)]
    dominant_topics = "、".join(topic_labels[:3]) or "通用技术变化"
    overall_judgment = (
        "本轮没有证据表明示例公司 AI 产品需要立即变更生产方案；"
        f"更合理的判断是：{dominant_topics} 正在形成工程能力对标压力，"
        "应进入依赖核验、影子评测和 Harness 能力补齐流程。"
    )
    executive_summary = [
        f"入选证据共 {len(analysis)} 条，其中官方/一手或准一手来源 {official_count} 条，全网新闻线索 {web_count} 条。",
        f"信息主要集中在 {dominant_topics}，影响性质以条件式影响为主：只有在示例产品、iFinD 或内部 Agent 平台使用相关组件时才会转化为实际风险或机会。",
        "当前最重要的不是直接替换模型或框架，而是建立可回归的工具权限、Trace、沙箱隔离、版本兼容和灰度验证指标。",
    ]
    impact_sections = _build_report_impact_sections(analysis)
    action_items = _ranked_report_actions(analysis)
    return {
        "overall_judgment": overall_judgment,
        "executive_summary": executive_summary,
        "impact_sections": impact_sections,
        "action_items": action_items,
        "source_level_counts": source_level_counts,
        "topic_counts": topic_counts,
    }


def _build_report_impact_sections(analysis: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sections = [
        {
            "key": "agent_harness",
            "title": "Agent Harness / Tool Policy 能力对标",
            "topics": {"agent_harness", "tool_use"},
            "judgment": "外部系统正在把权限边界、沙箱隔离、子智能体、Trace 和恢复能力产品化；如果示例公司内部 Agent 平台已有类似链路，需要重点比较可控性和可观测性。",
            "checks": "端到端成功率、越权率、Trace 完整率、失败恢复成功率。",
        },
        {
            "key": "developer_tools",
            "title": "开发者工具与协议兼容",
            "topics": {"developer_tools"},
            "judgment": "Claude Code、Codex、MCP、LangGraph 等工具链变化会影响内部 Agent 工程实践、插件接入方式和故障恢复策略，但需要先确认生产栈依赖。",
            "checks": "兼容用例通过率、集成错误率、回滚耗时、工具调用失败率。",
        },
        {
            "key": "model_products",
            "title": "模型产品与业务能力边界",
            "topics": {"model_products", "financial_ai"},
            "judgment": "模型和 AI 产品新闻可以作为候选方向线索，但不能直接证明示例产品或 iFinD 的质量、成本或合规边界发生变化。",
            "checks": "业务准确率、每成功任务成本、P95 延迟、人工复核率。",
        },
    ]
    output = []
    for section in sections:
        matched = [
            item
            for item in analysis
            if section["topics"].intersection(set(item.get("matched_query_topics") or []))
        ]
        if not matched:
            continue
        output.append(
            {
                "title": section["title"],
                "judgment": section["judgment"],
                "checks": section["checks"],
                "evidence_titles": [str(item.get("title") or "") for item in matched[:3]],
                "concrete_impacts": [_specific_impact_line(item) for item in matched[:3]],
            }
        )
    return output


def _ranked_report_actions(analysis: list[dict[str, Any]]) -> list[str]:
    actions = []
    all_topics = {
        str(topic)
        for item in analysis
        for topic in (item.get("matched_query_topics") or [])
    }
    if {"agent_harness", "tool_use"}.intersection(all_topics):
        actions.append("先盘点示例产品、iFinD 或内部 Agent 平台是否存在 Claude Code/Codex/MCP/LangGraph 同类依赖，并记录版本、调用边界和回滚方案。")
        actions.append("建立 Harness 回归集：覆盖工具权限、沙箱隔离、子智能体委派、Trace 完整性和失败恢复。")
    if "developer_tools" in all_topics:
        actions.append("用隔离环境跑兼容性验证，不在未完成影子流量和回滚演练前进入生产升级。")
    if {"model_products", "financial_ai"}.intersection(all_topics):
        actions.append("把模型/产品新闻转化为质量、成本、延迟和合规指标对比，不直接按媒体热度做选型。")
    actions.append("对 L5 全网新闻线索只做趋势提示；关键结论必须回链到官方源、原始公告或企业内部事实。")
    return actions[:5]


def _specific_impact_line(item: dict[str, Any]) -> str:
    chains = [str(value) for value in item.get("impact_chain") or [] if str(value).strip()]
    if chains:
        return chains[0]
    topics = [str(topic) for topic in item.get("matched_query_topics") or []]
    topic_text = "、".join(_topic_label(topic) for topic in topics) or "通用外部变化"
    if topics:
        return (
            f"{topic_text} 相关变化 -> 可能影响示例产品、iFinD 或内部 Agent 平台的技术选型、"
            "质量成本指标和上线治理要求 -> 需要先核验生产依赖与当前基线，不能直接得出替换结论"
        )
    return (
        "当前事件只证明外部生态出现变化，尚未命中明确技术维度；"
        "只能作为观察项，不能直接转化为产品改造结论"
    )


def _chinese_event_summary(item: dict[str, Any]) -> str:
    source = item.get("news_evidence", {})
    publisher = str(source.get("publisher") or source.get("source") or "外部来源")
    topics = "、".join(
        _topic_label(topic) for topic in item.get("matched_query_topics") or []
    ) or "外部生态"
    impact = _specific_impact_line(item)
    return (
        f"{publisher} 发布或报道了与 {topics} 相关的变化。"
        f"本系统将其解读为：{impact}。"
        "该判断仍是条件式影响，需要结合企业 Wiki 中的产品事实、生产依赖和指标基线进一步确认。"
    )


def _render_report_md(
    company_id: str,
    context_pack: dict[str, Any],
    analysis: list[dict[str, Any]],
    *,
    query: str,
    source_disclosure: str,
    gate_reason: str,
    source_manifest: dict[str, Any],
) -> str:
    profile = context_pack.get("profile", {})
    synthesis = _report_synthesis(analysis)
    source_statuses = list(source_manifest.get("source_statuses") or [])
    healthy_sources = [item for item in source_statuses if item.get("status") == "ok"]
    lines = [
        "# Agent 外部变化对示例公司 AI 产品的影响报告",
        "",
        f"- 企业：{profile.get('company_name', company_id)}",
        f"- 用户问题：{query or '未指定'}",
        f"- 相关性门控：{gate_reason}",
        f"- 入选证据：{len(analysis)} 条",
        "",
        "## 一、中文执行摘要",
        "",
        synthesis["overall_judgment"],
        "",
        "## 二、关键信息摘要",
        "",
    ]
    for item in synthesis["executive_summary"]:
        lines.append(f"- {item}")
    lines.extend(["", "| 事件 | 来源等级 | 时间 | 命中维度 | 影响判断 | 具体影响 |", "| --- | --- | --- | --- | --- | --- |"])
    for item in analysis:
        evidence = item["news_evidence"]
        topics = "、".join(_topic_label(topic) for topic in item.get("matched_query_topics") or []) or "通用主题"
        lines.append(
            "| "
            + " | ".join(
                [
                    str(item["title"]).replace("|", "/"),
                    str(evidence.get("source_level") or "未标注"),
                    str(evidence.get("published_at_query_timezone") or evidence.get("published_at") or "未记录"),
                    topics,
                    f"{_impact_level_label(str(item['impact_level']))} / {item['impact_score']}",
                    _specific_impact_line(item).replace("|", "/"),
                ]
            )
            + " |"
        )
    lines.extend(["", "## 三、具体影响分析", ""])
    if not synthesis["impact_sections"]:
        lines.append("- 当前没有足够证据形成业务影响判断。")
    for section in synthesis["impact_sections"]:
        lines.extend(
            [
                f"### {section['title']}",
                f"- 判断：{section['judgment']}",
                f"- 关联证据：{'; '.join(section['evidence_titles'])}",
                f"- 具体影响：{'; '.join(section.get('concrete_impacts') or [])}",
                f"- 验证指标：{section['checks']}",
                "",
            ]
        )
    lines.extend(["## 四、行动建议", ""])
    for index, action in enumerate(synthesis["action_items"], start=1):
        lines.append(f"{index}. {action}")
    lines.extend(["", "## 五、证据摘要", ""])
    if not analysis:
        lines.extend(["当前数据源没有可用于回答本轮问题的匹配证据。", ""])
    for item in analysis:
        lines.extend(
            [
                f"### {_impact_level_label(str(item['impact_level']))} / {item['impact_score']} - {item['title']}",
                f"- 类型：{item['category']}",
                f"- 发布主体：{item['news_evidence'].get('publisher', '') or '未标注'}",
                f"- 新闻来源：{item['news_evidence'].get('source', '')} / 证据等级 {item['news_evidence'].get('source_level', '')}",
                f"- 发布时间：{item['news_evidence'].get('published_at_query_timezone') or item['news_evidence'].get('published_at', '')}",
                f"- 原文：{item['news_evidence'].get('url', '')}",
                f"- 命中维度：{', '.join(_topic_label(topic) for topic in item.get('matched_query_topics') or []) or '通用主题'}",
                f"- 中文摘要：{_chinese_event_summary(item)}",
                f"- 原始来源摘要：{item['news_evidence'].get('summary', '')}",
                f"- 理由：{'；'.join(item['reasoning'])}",
                f"- 企业证据：{'; '.join(_format_company_evidence(x) for x in item['company_evidence']) or '无'}",
                f"- 具体影响链条：{'; '.join(item.get('impact_chain') or [_specific_impact_line(item)])}",
                f"- 待确认：{'; '.join(item.get('missing_company_facts') or []) or '无'}",
                f"- 验证指标：{'; '.join(item.get('verification_metrics') or []) or '未定义'}",
                f"- 建议动作：{item['recommended_action']}",
                "",
            ]
        )
    lines.extend(["## 附录 B：数据源与可信度", ""])
    lines.extend(
        [
            f"- 数据模式：{source_disclosure}",
            f"- 来源成功数：{len(healthy_sources)}/{len(source_statuses)}",
            f"- 快照路径：{source_manifest.get('snapshot_path') or '未记录'}",
            "- 可信度说明：L1/L2/L3 可作为主要证据；L5 仅作为全网新闻线索，关键结论必须回链到官方源或企业内部事实。",
            "",
        ]
    )
    if source_statuses:
        for item in source_statuses:
            lines.append(
                f"- {item.get('source_id') or item.get('name') or 'unknown'}："
                f"{item.get('status') or 'unknown'}"
                + (f"，等级 {item.get('level')}" if item.get("level") else "")
                + (f"，{item.get('item_count')} 条" if item.get("item_count") is not None else "")
                + (f"，{item.get('url')}" if item.get("url") else "")
            )
    else:
        lines.append("- 当前没有可用的实时来源状态，可能使用本地验收样例或录制快照。")
    return "\n".join(lines)


def _render_report_html(
    company_id: str,
    context_pack: dict[str, Any],
    analysis: list[dict[str, Any]],
    run_id: str,
    *,
    query: str,
    source_disclosure: str,
    gate_reason: str,
    source_manifest: dict[str, Any],
) -> str:
    profile = context_pack.get("profile", {})
    synthesis = _report_synthesis(analysis)
    source_statuses = list(source_manifest.get("source_statuses") or [])
    healthy_sources = [item for item in source_statuses if item.get("status") == "ok"]
    level_counts = " / ".join(
        f"{level}: {count}"
        for level, count in sorted((synthesis.get("source_level_counts") or {}).items())
    ) or "无"
    summary_rows = []
    for item in analysis:
        source = item.get("news_evidence", {})
        topics = " / ".join(_topic_label(topic) for topic in item.get("matched_query_topics") or [])
        summary_rows.append(
            "<tr>"
            f"<td><strong>{escape(str(item.get('title') or ''))}</strong><br>"
            f"<span>{escape(str(source.get('source') or '未知来源'))}</span></td>"
            f"<td>{escape(str(source.get('source_level') or '未标注'))}</td>"
            f"<td>{escape(str(source.get('published_at_query_timezone') or source.get('published_at') or '未记录'))}</td>"
            f"<td>{escape(topics or '通用主题')}</td>"
            f"<td><strong>{escape(_impact_level_label(str(item.get('impact_level') or '')))}</strong><br>{escape(str(item.get('impact_score') or ''))}</td>"
            f"<td>{escape(_specific_impact_line(item))}</td>"
            "</tr>"
        )
    impact_cards = []
    for section in synthesis.get("impact_sections") or []:
        impact_cards.append(
            "<article class='impact-card'>"
            f"<h3>{escape(str(section.get('title') or ''))}</h3>"
            f"<p>{escape(str(section.get('judgment') or ''))}</p>"
            f"<div><span>关联证据</span><strong>{escape('；'.join(section.get('evidence_titles') or []))}</strong></div>"
            f"<div><span>具体影响</span><strong>{escape('；'.join(section.get('concrete_impacts') or []))}</strong></div>"
            f"<div><span>验证指标</span><strong>{escape(str(section.get('checks') or ''))}</strong></div>"
            "</article>"
        )
    action_items = "".join(
        f"<li>{escape(str(action))}</li>" for action in synthesis.get("action_items") or []
    )
    source_rows = []
    for item in source_statuses:
        status = str(item.get("status") or "unknown")
        status_class = "ok" if status == "ok" else "warn"
        source_rows.append(
            "<tr>"
            f"<td>{escape(str(item.get('source_id') or item.get('name') or 'unknown'))}</td>"
            f"<td>{escape(str(item.get('level') or ''))}</td>"
            f"<td>{escape(str(item.get('type') or ''))}</td>"
            f"<td><span class='status {status_class}'>{escape(status)}</span></td>"
            f"<td>{escape(str(item.get('item_count') if item.get('item_count') is not None else ''))}</td>"
            f"<td>{escape(str(item.get('url') or item.get('snapshot_path') or ''))}</td>"
            "</tr>"
        )
    empty_source_row = "<tr><td colspan='6'>当前没有实时来源状态；请查看 RunArtifact。</td></tr>"
    source_overview = (
        "<section class='source-overview appendix'>"
        "<div class='source-head'>"
        "<div><span class='eyebrow'>附录</span><h2>数据源与可信度</h2></div>"
        f"<strong>{len(healthy_sources)}/{len(source_statuses)}</strong>"
        "</div>"
        "<div class='source-grid'>"
        f"<div><span>数据模式</span><strong>{escape(source_disclosure)}</strong></div>"
        f"<div><span>证据等级分布</span><strong>{escape(level_counts)}</strong></div>"
        f"<div><span>快照路径</span><strong>{escape(str(source_manifest.get('snapshot_path') or '未记录'))}</strong></div>"
        f"<div><span>采集时间</span><strong>{escape(str(source_manifest.get('collected_at') or '未记录'))}</strong></div>"
        "</div>"
        "<p class='note'>L1/L2/L3 可作为主要证据；L5 只作为全网新闻线索，关键结论必须回链到官方源、原始公告或企业内部事实。</p>"
        "<table><thead><tr><th>来源</th><th>等级</th><th>类型</th><th>状态</th><th>数量</th><th>URL / 快照</th></tr></thead>"
        f"<tbody>{''.join(source_rows) or empty_source_row}</tbody></table>"
        "</section>"
    )
    cards = []
    for item in analysis:
        evidence = "<br>".join(
            escape(_format_company_evidence(x)) for x in item.get("company_evidence", [])[:3]
        )
        source = item.get("news_evidence", {})
        topics = " / ".join(_topic_label(topic) for topic in item.get("matched_query_topics") or [])
        impact_chain = "<br>".join(
            escape(str(value)) for value in (item.get("impact_chain") or [_specific_impact_line(item)])
        )
        missing = "<br>".join(escape(str(value)) for value in item.get("missing_company_facts") or [])
        metrics = "<br>".join(escape(str(value)) for value in item.get("verification_metrics") or [])
        source_link = (
            f"<a href='{escape(str(source.get('url', '')), quote=True)}' target='_blank' rel='noopener'>"
            f"{escape(str(source.get('source', '原文')))}</a><br>"
            f"{escape(str(source.get('published_at_query_timezone') or source.get('published_at', '')))}"
        )
        cards.append(
            "<article class='event'>"
            f"<header><div><span class='level'>{escape(_impact_level_label(str(item['impact_level'])))}</span>"
            f"<h2>{escape(item['title'])}</h2></div><strong>{escape(str(item['impact_score']))}</strong></header>"
            f"<p class='source'>{source_link}</p>"
            "<dl>"
            f"<dt>发布主体</dt><dd>{escape(str(source.get('publisher') or '未标注'))}</dd>"
            f"<dt>命中维度</dt><dd>{escape(topics or '通用主题')}</dd>"
            f"<dt>证据等级</dt><dd>{escape(str(source.get('source_level') or '未标注'))}</dd>"
            f"<dt>中文摘要</dt><dd>{escape(_chinese_event_summary(item))}</dd>"
            f"<dt>原始来源摘要</dt><dd>{escape(str(source.get('summary', '来源未提供摘要')))}</dd>"
            f"<dt>企业证据</dt><dd>{evidence or '无可绑定企业事实'}</dd>"
            f"<dt>具体影响链条</dt><dd>{impact_chain}</dd>"
            f"<dt>待确认</dt><dd>{missing or '无'}</dd>"
            f"<dt>验证指标</dt><dd>{metrics or '未定义'}</dd>"
            f"<dt>建议动作</dt><dd>{escape(item['recommended_action'])}</dd>"
            "</dl></article>"
        )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>Agent 外部变化影响报告</title>
  <style>
    body {{
      margin: 0;
      background: #f3f5f7;
      color: #17202a;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
    }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 32px 24px 48px; }}
    h1 {{ margin: 0 0 10px; font-size: 34px; letter-spacing: 0; line-height: 1.18; }}
    h2 {{ margin: 0 0 14px; font-size: 22px; }}
    h3 {{ margin: 0 0 10px; font-size: 17px; }}
    .sub {{ color: #68717d; margin: 0 0 16px; line-height: 1.65; }}
    .eyebrow {{ color: #176b57; font-size: 12px; font-weight: 700; text-transform: uppercase; }}
    section {{ margin-top: 18px; }}
    .hero, .panel, .source-overview {{ background: #fff; border: 1px solid #dfe4ea; border-radius: 8px; padding: 22px; box-shadow: 0 1px 2px rgba(16, 24, 40, 0.04); }}
    .hero {{ border-top: 4px solid #176b57; }}
    .judgment {{ font-size: 19px; line-height: 1.75; margin: 14px 0 0; }}
    .meta {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }}
    .pill {{ border: 1px solid #d8e8e1; background: #eef8f4; color: #176b57; border-radius: 999px; padding: 6px 10px; font-size: 13px; font-weight: 650; }}
    .bullets {{ margin: 0; padding-left: 20px; line-height: 1.8; }}
    .impact-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }}
    .impact-card {{ border: 1px solid #e3e7eb; background: #fbfcfd; border-radius: 8px; padding: 16px; }}
    .impact-card p {{ color: #394653; line-height: 1.7; margin: 0 0 12px; }}
    .impact-card div {{ margin-top: 10px; }}
    .impact-card span {{ display: block; color: #68717d; font-size: 12px; margin-bottom: 4px; }}
    .impact-card strong {{ font-size: 13px; line-height: 1.55; }}
    .actions {{ margin: 0; padding-left: 22px; line-height: 1.85; }}
    .source-head {{ display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; margin-bottom: 16px; }}
    .source-head h2 {{ margin: 4px 0 0; font-size: 20px; }}
    .source-head > strong {{ font-size: 28px; color: #176b57; }}
    .source-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-bottom: 16px; }}
    .source-grid div {{ border: 1px solid #e8ebee; border-radius: 8px; padding: 10px 12px; background: #fbfcfd; min-width: 0; }}
    .source-grid span {{ display: block; color: #68717d; font-size: 12px; margin-bottom: 4px; }}
    .source-grid strong {{ display: block; font-size: 13px; line-height: 1.45; overflow-wrap: anywhere; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ text-align: left; border-top: 1px solid #e8ebee; padding: 10px 8px; vertical-align: top; overflow-wrap: anywhere; }}
    th {{ color: #68717d; font-weight: 650; }}
    .status {{ display: inline-block; border-radius: 999px; padding: 2px 8px; font-size: 12px; font-weight: 700; }}
    .status.ok {{ color: #176b57; background: #e8f5ef; }}
    .status.warn {{ color: #a15c00; background: #fff4df; }}
    .events {{ display: grid; gap: 16px; }}
    .event {{ background: #fff; border: 1px solid #dfe4ea; border-radius: 8px; padding: 20px; }}
    .event header {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }}
    .event h2 {{ margin: 6px 0 0; font-size: 19px; }}
    .event header > strong {{ font-size: 24px; color: #176b57; }}
    .level {{ color: #68717d; font-size: 12px; font-weight: 700; }}
    .source {{ color: #68717d; font-size: 13px; line-height: 1.55; }}
    a {{ color: #176b57; }}
    .note {{ color: #68717d; line-height: 1.65; margin: 0 0 12px; }}
    dl {{ display: grid; grid-template-columns: 132px minmax(0, 1fr); margin: 18px 0 0; border-top: 1px solid #e8ebee; }}
    dt, dd {{ margin: 0; padding: 12px 0; border-bottom: 1px solid #e8ebee; font-size: 14px; line-height: 1.65; }}
    dt {{ color: #68717d; padding-right: 16px; }}
    @media (max-width: 820px) {{ .impact-grid {{ grid-template-columns: 1fr; }} .source-grid {{ grid-template-columns: 1fr; }} dl {{ grid-template-columns: 1fr; }} dt {{ border-bottom: 0; padding-bottom: 0; }} }}
  </style>
</head>
<body>
<main>
  <section class="hero">
    <span class="eyebrow">中文执行摘要</span>
    <h1>Agent 外部变化对示例公司 AI 产品的影响报告</h1>
    <p class="sub">{escape(str(profile.get("company_name", company_id)))} · Run ID: {escape(run_id)}</p>
    <p class="sub">问题：{escape(query or "未指定")}</p>
    <p class="judgment">{escape(str(synthesis.get("overall_judgment") or ""))}</p>
    <div class="meta">
      <span class="pill">入选证据 {len(analysis)} 条</span>
      <span class="pill">来源成功 {len(healthy_sources)}/{len(source_statuses)}</span>
      <span class="pill">证据等级 {escape(level_counts)}</span>
      <span class="pill">门控通过</span>
    </div>
  </section>

  <section class="panel">
    <h2>一、中文执行摘要</h2>
    <ul class="bullets">{''.join(f"<li>{escape(str(item))}</li>" for item in synthesis.get("executive_summary") or [])}</ul>
  </section>

  <section class="panel">
    <h2>二、关键信息摘要</h2>
    <table><thead><tr><th>事件</th><th>等级</th><th>时间</th><th>命中维度</th><th>影响判断</th><th>具体影响</th></tr></thead>
    <tbody>{''.join(summary_rows) or '<tr><td colspan="6">当前没有与问题匹配且通过来源门控的事件。</td></tr>'}</tbody></table>
  </section>

  <section class="panel">
    <h2>三、具体影响分析</h2>
    <div class="impact-grid">{''.join(impact_cards) or '<p class="note">当前没有足够证据形成业务影响判断。</p>'}</div>
  </section>

  <section class="panel">
    <h2>四、行动建议</h2>
    <ol class="actions">{action_items}</ol>
  </section>

  <section>
    <span class="eyebrow">证据摘要</span>
    <h2>五、证据明细</h2>
    <div class="events">{''.join(cards) or '<p>当前没有与问题匹配且通过来源门控的事件。</p>'}</div>
  </section>

  {source_overview}
</main>
</body>
</html>"""
