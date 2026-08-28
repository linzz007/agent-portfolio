"""Policy repository backed by live official sources and recorded snapshots."""

from __future__ import annotations

from contextvars import ContextVar
import os
from pathlib import Path
from typing import Any

from policy_impact.harness.artifacts import project_root
from policy_impact.company_wiki.loader import parse_frontmatter
from policy_impact.source_connectors import collect_policy_sources, load_latest_policy_snapshot


_LAST_SOURCE_MANIFEST: ContextVar[dict[str, Any]] = ContextVar(
    "policy_source_manifest",
    default={},
)


def policy_raw_dir() -> Path:
    return project_root() / "data" / "policies" / "raw"


def get_policy_source_manifest() -> dict[str, Any]:
    return dict(_LAST_SOURCE_MANIFEST.get())


def load_policy_documents(
    date_from: str | None = None,
    date_to: str | None = None,
    *,
    refresh_live_sources: bool | None = None,
    allow_fixture_fallback: bool | None = None,
) -> list[dict[str, Any]]:
    refresh_live_sources = (
        _env_bool("POLICY_IMPACT_LIVE_SOURCES", True)
        if refresh_live_sources is None
        else refresh_live_sources
    )
    allow_fixture_fallback = (
        _env_bool("POLICY_IMPACT_ALLOW_FIXTURES", False)
        if allow_fixture_fallback is None
        else allow_fixture_fallback
    )

    if refresh_live_sources:
        live = collect_policy_sources(date_from=date_from, date_to=date_to)
        documents = list(live.get("documents") or [])
        manifest = {
            "mode": "live",
            "collection_succeeded": bool(live.get("collection_succeeded")),
            "source_statuses": live.get("source_statuses") or [],
            "snapshot_path": live.get("snapshot_path") or "",
            "no_updates": not documents and bool(live.get("collection_succeeded")),
        }
        _LAST_SOURCE_MANIFEST.set(manifest)
        if documents or manifest["no_updates"]:
            return documents

    snapshot = load_latest_policy_snapshot()
    snapshot_docs = [
        item
        for item in snapshot.get("items") or []
        if _date_in_range(str(item.get("published_at") or ""), date_from, date_to)
    ]
    if snapshot_docs:
        _LAST_SOURCE_MANIFEST.set(
            {
                "mode": "source_snapshot",
                "collection_succeeded": True,
                "source_statuses": snapshot.get("source_statuses") or [],
                "snapshot_path": snapshot.get("snapshot_path") or "",
                "captured_at": snapshot.get("captured_at"),
                "no_updates": False,
            }
        )
        return snapshot_docs

    if allow_fixture_fallback:
        fixtures = _load_local_documents(date_from=date_from, date_to=date_to)
        _LAST_SOURCE_MANIFEST.set(
            {
                "mode": "fixture",
                "collection_succeeded": bool(fixtures),
                "source_statuses": [],
                "snapshot_path": "",
                "no_updates": False,
            }
        )
        return fixtures

    current = get_policy_source_manifest()
    current.setdefault("mode", "unavailable")
    current.setdefault("collection_succeeded", False)
    current.setdefault("source_statuses", [])
    current.setdefault("snapshot_path", "")
    current.setdefault("no_updates", False)
    _LAST_SOURCE_MANIFEST.set(current)
    return []


def _load_local_documents(
    *,
    date_from: str | None,
    date_to: str | None,
) -> list[dict[str, Any]]:
    docs = []
    for path in sorted(policy_raw_dir().glob("*.md")):
        text = path.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)
        published_at = str(meta.get("published_at") or "")
        if not _date_in_range(published_at, date_from, date_to):
            continue
        policy_id = str(meta.get("policy_id") or path.stem)
        docs.append(
            {
                "policy_id": policy_id,
                "title": meta.get("title", path.stem),
                "issuer": meta.get("issuer", ""),
                "region_scope": meta.get("region_scope", []),
                "published_at": published_at,
                "retrieved_at": "",
                "source_url": meta.get("source_url", str(path)),
                "source_id": "local_policy_archive",
                "source_level": meta.get("source_level", "TEST"),
                "source_type": "local_markdown",
                "data_mode": meta.get("data_mode", "fixture"),
                "is_fixture": bool(meta.get("is_fixture", True)),
                "policy_type": meta.get("policy_type", []),
                "raw_text_path": str(path),
                "text": body,
            }
        )
    return docs


def build_policy_chunks(policy_documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chunks = []
    for doc in policy_documents:
        paragraphs = [p.strip() for p in doc.get("text", "").split("\n\n") if p.strip()]
        for idx, paragraph in enumerate(paragraphs):
            chunks.append(
                {
                    "chunk_id": f"{doc['policy_id']}_chunk_{idx}",
                    "doc_id": doc["policy_id"],
                    "policy_id": doc["policy_id"],
                    "title": doc["title"],
                    "issuer": doc.get("issuer", ""),
                    "published_at": doc.get("published_at", ""),
                    "source_url": doc.get("source_url", ""),
                    "source_level": doc.get("source_level", "L4"),
                    "data_mode": doc.get("data_mode", "source_snapshot"),
                    "is_fixture": bool(doc.get("is_fixture", False)),
                    "text": paragraph,
                }
            )
    return chunks


def _date_in_range(value: str, date_from: str | None, date_to: str | None) -> bool:
    if not value:
        return not date_from and not date_to
    day = value[:10]
    if date_from and day < date_from[:10]:
        return False
    if date_to and day > date_to[:10]:
        return False
    return True


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}
