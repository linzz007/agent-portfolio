"""Lightweight BM25 lexical retrieval, adapted from the CoursePilot pattern."""

from __future__ import annotations

from collections import Counter
import math
import re
from typing import Any


def tokenize(text: str) -> list[str]:
    if not text:
        return []
    tokens: list[str] = []
    for part in re.findall("[a-z0-9_]+|[\u4e00-\u9fff]+", text.lower()):
        if not re.fullmatch("[\u4e00-\u9fff]+", part):
            tokens.append(part)
            continue
        if len(part) == 1:
            tokens.append(part)
            continue
        tokens.extend(part[index : index + 2] for index in range(len(part) - 1))
        if len(part) >= 3:
            tokens.extend(part[index : index + 3] for index in range(len(part) - 2))
    return tokens


class BM25Index:
    def __init__(self, chunks: list[dict[str, Any]], k1: float = 1.5, b: float = 0.75) -> None:
        self.chunks = chunks
        self.k1 = k1
        self.b = b
        self._doc_tfs: list[dict[str, int]] = []
        self._doc_lens: list[int] = []
        self._idf: dict[str, float] = {}
        self._avgdl = 0.0
        self._build()

    def _build(self) -> None:
        if not self.chunks:
            return
        df: Counter[str] = Counter()
        for chunk in self.chunks:
            tf = Counter(tokenize(str(chunk.get("text", ""))))
            self._doc_tfs.append(dict(tf))
            self._doc_lens.append(sum(tf.values()))
            for term in tf:
                df[term] += 1
        n = len(self.chunks)
        self._avgdl = sum(self._doc_lens) / n if n else 0.0
        for term, count in df.items():
            self._idf[term] = math.log1p((n - count + 0.5) / (count + 0.5))

    def search(self, query: str, top_k: int = 5) -> list[tuple[dict[str, Any], float]]:
        terms = Counter(tokenize(query))
        if not terms:
            return []
        avgdl = self._avgdl or 1.0
        scores = []
        for idx, tf_map in enumerate(self._doc_tfs):
            dl = self._doc_lens[idx] or 1
            score = 0.0
            for term, qtf in terms.items():
                tf = tf_map.get(term, 0)
                if tf <= 0:
                    continue
                denom = tf + self.k1 * (1.0 - self.b + self.b * (dl / avgdl))
                score += self._idf.get(term, 0.0) * ((tf * (self.k1 + 1.0)) / denom) * qtf
            if score > 0:
                scores.append((idx, score))
        scores.sort(key=lambda item: item[1], reverse=True)
        return [(self.chunks[idx], float(score)) for idx, score in scores[: max(1, top_k)]]
