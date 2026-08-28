"""Build company context packs."""

from __future__ import annotations

from typing import Any

from policy_impact.company_wiki.loader import load_company_knowledge
from policy_impact.rag.retriever import HybridRetriever


def build_company_context_pack(company_id: str, task: str = "weekly_policy_impact") -> dict[str, Any]:
    knowledge = load_company_knowledge(company_id)
    facts = sorted(
        knowledge["facts"],
        key=lambda fact: (int(fact.get("importance", 0)), float(fact.get("confidence", 0.0))),
        reverse=True,
    )
    pinned = [fact for fact in facts if int(fact.get("importance", 0)) >= 5]
    chunks = [
        {
            "chunk_id": fact["fact_id"],
            "doc_id": fact["source_path"],
            "text": f"{fact.get('value', '')}\n{fact.get('text', '')}",
            "metadata": fact,
        }
        for fact in facts
    ]
    retriever = HybridRetriever(chunks)
    query = " ".join(str(x) for x in knowledge["profile"].get("business_keywords", []))
    retrieved = retriever.retrieve(query or task, top_k=8) if chunks else []
    return {
        "company_id": company_id,
        "task": task,
        "profile": knowledge["profile"],
        "pinned_facts": pinned,
        "retrieved_company_facts": [hit.get("metadata", {}) for hit in retrieved],
        "open_questions": [fact for fact in facts if fact.get("confidence", 1.0) < 0.7],
    }
