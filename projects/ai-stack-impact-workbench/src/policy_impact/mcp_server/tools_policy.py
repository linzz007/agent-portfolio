"""Policy MCP-style tools."""

from __future__ import annotations

from policy_impact.policy_data.clause_extract import extract_policy_clauses
from policy_impact.policy_data.repository import build_policy_chunks, load_policy_documents
from policy_impact.rag.retriever import HybridRetriever


def policy_fetch_recent(
    company_id: str,
    region: list[str] | None = None,
    industry_keywords: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    refresh_live_sources: bool | None = None,
    allow_fixture_fallback: bool | None = None,
) -> list[dict]:
    return load_policy_documents(
        date_from=date_from,
        date_to=date_to,
        refresh_live_sources=refresh_live_sources,
        allow_fixture_fallback=allow_fixture_fallback,
    )


def policy_ingest(policy_documents: list[dict]) -> list[dict]:
    return build_policy_chunks(policy_documents)


def policy_retrieve(query: str, chunks: list[dict], top_k: int = 8) -> list[dict]:
    return HybridRetriever(chunks).retrieve(query, top_k=top_k)


def policy_clause_extract(evidence_hits: list[dict]) -> list[dict]:
    return extract_policy_clauses(evidence_hits)
