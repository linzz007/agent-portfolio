"""Sentence-level compression for RAG evidence snippets."""

from __future__ import annotations

import re

from policy_impact.rag.lexical import tokenize


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?\.])\s+|\n+", text or "")
    return [part.strip() for part in parts if part.strip()]


def compress_text(query: str, text: str, sent_topn: int = 2, sent_max_chars: int = 160) -> str:
    sentences = split_sentences(text)
    if not sentences:
        return (text or "")[:sent_max_chars]
    query_terms = set(tokenize(query))
    scored = []
    for sent in sentences:
        terms = set(tokenize(sent))
        overlap = len(query_terms & terms)
        scored.append((overlap, min(len(sent), sent_max_chars), sent))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    selected = [item[2][:sent_max_chars] for item in scored[: max(1, sent_topn)]]
    return " ".join(selected)
