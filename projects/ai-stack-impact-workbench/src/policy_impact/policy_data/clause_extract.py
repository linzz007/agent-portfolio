"""Policy clause extraction with deterministic fallback rules."""

from __future__ import annotations

from typing import Any


def infer_clause_type(text: str) -> str:
    if any(key in text for key in ("申报", "条件", "符合", "对象")):
        return "eligibility"
    if any(key in text for key in ("补贴", "奖励", "资助", "资金")):
        return "benefit"
    if any(key in text for key in ("截止", "期限", "日前")):
        return "deadline"
    if any(key in text for key in ("材料", "证明", "提交")):
        return "material"
    if any(key in text for key in ("不得", "应当", "合规", "安全", "监管")):
        return "compliance"
    return "support_direction"


def extract_policy_clauses(evidence_hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clauses = []
    for idx, hit in enumerate(evidence_hits, start=1):
        text = hit.get("compressed_text") or hit.get("text", "")
        clauses.append(
            {
                "clause_id": f"clause_{idx:03d}",
                "policy_id": hit.get("policy_id") or hit.get("doc_id"),
                "policy_title": hit.get("title", ""),
                "clause_type": infer_clause_type(text),
                "text": text,
                "source_url": hit.get("source_url", ""),
                "published_at": hit.get("published_at", ""),
                "evidence_chunk_id": hit.get("chunk_id"),
            }
        )
    return clauses
