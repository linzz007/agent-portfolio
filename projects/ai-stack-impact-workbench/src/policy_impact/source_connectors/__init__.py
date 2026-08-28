"""Primary-source ingestion for news and policy evidence."""

from policy_impact.source_connectors.ingestion import (
    collect_news_sources,
    collect_policy_sources,
    load_latest_news_snapshot,
    load_latest_policy_snapshot,
)

__all__ = [
    "collect_news_sources",
    "collect_policy_sources",
    "load_latest_news_snapshot",
    "load_latest_policy_snapshot",
]
