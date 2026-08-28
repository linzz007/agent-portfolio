"""Report and memory tools."""

from __future__ import annotations

from pathlib import Path

from policy_impact.company_wiki.loader import company_dir
from policy_impact.memory.store import PolicyMemoryStore


def report_write(company_id: str, filename: str, content: str) -> dict:
    report_dir = company_dir(company_id) / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / filename
    path.write_text(content, encoding="utf-8")
    return {"path": str(path), "filename": filename}


def memory_save(company_id: str, namespace: str, memory_type: str, content: str, importance: float = 0.5, metadata: dict | None = None) -> dict:
    store = PolicyMemoryStore(company_id)
    item_id = store.save_memory(namespace, memory_type, content, importance=importance, metadata=metadata)
    return {"id": item_id}


def memory_search(company_id: str, query: str, top_k: int = 5) -> list[dict]:
    return PolicyMemoryStore(company_id).search_memory(query=query, top_k=top_k)
