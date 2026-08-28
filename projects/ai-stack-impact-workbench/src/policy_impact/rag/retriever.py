"""Hybrid retrieval over in-memory chunks."""

from __future__ import annotations

from typing import Any

from policy_impact.rag.lexical import BM25Index, tokenize
from policy_impact.rag.sentence_compressor import compress_text


class HybridRetriever:
    """BM25 plus lightweight semantic overlap.

    This keeps the runtime dependency-light while preserving the same extension point
    as CoursePilot's dense + BM25 + RRF retrieval.
    """

    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self.chunks = chunks
        self.bm25 = BM25Index(chunks)

    def retrieve(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        lexical = self.bm25.search(query, top_k=max(top_k * 3, top_k))
        semantic = self._semantic_overlap(query, top_k=max(top_k * 3, top_k))
        fused = self._rrf(lexical, semantic, top_k=top_k)
        out = []
        for chunk, score in fused:
            item = dict(chunk)
            item["score"] = float(score)
            item["compressed_text"] = compress_text(query, str(chunk.get("text", "")))
            out.append(item)
        return out

    def _semantic_overlap(self, query: str, top_k: int) -> list[tuple[dict[str, Any], float]]:
        q_terms = set(tokenize(query))
        if not q_terms:
            return []
        scored = []
        for idx, chunk in enumerate(self.chunks):
            terms = set(tokenize(str(chunk.get("text", ""))))
            if not terms:
                continue
            score = len(q_terms & terms) / max(1, len(q_terms | terms))
            if score > 0:
                scored.append((idx, score))
        scored.sort(key=lambda item: item[1], reverse=True)
        return [(self.chunks[idx], float(score)) for idx, score in scored[:top_k]]

    @staticmethod
    def _chunk_key(chunk: dict[str, Any]) -> str:
        return str(chunk.get("chunk_id") or f"{chunk.get('doc_id')}:{hash(chunk.get('text', ''))}")

    def _rrf(
        self,
        lexical: list[tuple[dict[str, Any], float]],
        semantic: list[tuple[dict[str, Any], float]],
        top_k: int,
    ) -> list[tuple[dict[str, Any], float]]:
        scores: dict[str, float] = {}
        refs: dict[str, dict[str, Any]] = {}
        for rank, (chunk, _) in enumerate(lexical, start=1):
            key = self._chunk_key(chunk)
            scores[key] = scores.get(key, 0.0) + 1.0 / (60 + rank)
            refs[key] = chunk
        for rank, (chunk, _) in enumerate(semantic, start=1):
            key = self._chunk_key(chunk)
            scores[key] = scores.get(key, 0.0) + 1.0 / (60 + rank)
            refs[key] = chunk
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        return [(refs[key], score) for key, score in ranked[: max(1, top_k)]]
