"""MCP-style adapters for deterministic recent-news stages."""

from __future__ import annotations

from typing import Any

from policy_impact.company_wiki.indexer import build_company_context_pack
from policy_impact.skill_executors.recent_news_report import (
    _analyze_company_impact,
    _clean_news_items,
    _load_news_items,
    _structure_events,
)


def news_load_items(*, refresh_live_sources: bool = True) -> dict[str, Any]:
    """Load recent-news items and return their source manifest."""
    raw_items, source_manifest = _load_news_items(
        refresh_live_sources=refresh_live_sources
    )
    return {"raw_items": raw_items, "source_manifest": source_manifest}


def news_structure_events(raw_items: list[dict[str, Any]]) -> dict[str, Any]:
    """Clean raw news items and deterministically structure unique events."""
    cleaned_items = _clean_news_items(raw_items)
    return {
        "cleaned_items": cleaned_items,
        "structured_events": _structure_events(cleaned_items),
    }


def news_analyze_company_impact(
    company_id: str,
    structured_events: list[dict[str, Any]],
    *,
    query: str = "",
) -> dict[str, Any]:
    """Build company context and analyze structured news events against it."""
    context_pack = build_company_context_pack(company_id, task="recent_news_report")
    return {
        "context_pack": context_pack,
        "analysis": _analyze_company_impact(
            company_id,
            context_pack,
            structured_events,
            query=query,
        ),
    }
