"""Evaluate CoursePilot RAG retrieval without calling the LLM runtime."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from time import perf_counter
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag.retrieve import Retriever  # noqa: E402
from rag.store_faiss import FAISSStore  # noqa: E402


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s:
                rows.append(json.loads(s))
    return rows


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _mean(values: Iterable[float]) -> float:
    xs = [float(v) for v in values]
    return sum(xs) / len(xs) if xs else 0.0


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    xs = sorted(float(v) for v in values)
    if len(xs) == 1:
        return xs[0]
    rank = (len(xs) - 1) * (p / 100.0)
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return xs[lo]
    weight = rank - lo
    return xs[lo] * (1.0 - weight) + xs[hi] * weight


def _safe_course_dir(data_dir: Path, course_name: str) -> Path:
    safe_name = os.path.basename(course_name.strip())
    if not safe_name or safe_name in {".", ".."}:
        raise ValueError(f"Invalid course name: {course_name!r}")
    return data_dir / safe_name


def _load_retriever(data_dir: Path, course_name: str) -> Retriever | None:
    index_path = _safe_course_dir(data_dir, course_name) / "index" / "faiss_index"
    if not Path(f"{index_path}.faiss").exists():
        return None
    store = FAISSStore()
    store.load(str(index_path))
    return Retriever(store)


def _chunk_doc_ids(chunks: Iterable[Any]) -> list[str]:
    out: list[str] = []
    for chunk in chunks:
        doc_id = getattr(chunk, "doc_id", None)
        if isinstance(doc_id, str) and doc_id:
            out.append(doc_id)
    return out


def _chunk_texts(chunks: Iterable[Any]) -> list[str]:
    out: list[str] = []
    for chunk in chunks:
        text = getattr(chunk, "text", None)
        if isinstance(text, str):
            out.append(text)
    return out


def _raw_chunk_texts(retriever: Retriever, chunks: Iterable[Any]) -> list[str]:
    chunk_map = {
        str(c.get("chunk_id")): str(c.get("text", ""))
        for c in getattr(retriever.store, "chunks", [])
        if c.get("chunk_id")
    }
    out: list[str] = []
    for chunk in chunks:
        chunk_id = getattr(chunk, "chunk_id", None)
        if isinstance(chunk_id, str) and chunk_id in chunk_map:
            out.append(chunk_map[chunk_id])
    return out


def _keyword_hit(texts: list[str], keywords: list[str]) -> float:
    if not keywords:
        return 0.0
    merged = "\n".join(texts).lower()
    hits = [1.0 for kw in keywords if str(kw).lower() in merged]
    return sum(hits) / len(keywords)


def _summarize(rows: list[dict[str, Any]]) -> dict[str, float]:
    ok_rows = [r for r in rows if not r.get("error")]
    gold_rows = [r for r in ok_rows if r.get("has_gold")]
    keyword_rows = [r for r in ok_rows if r.get("keyword_count", 0) > 0]
    retrieval_ms = [float(r.get("retrieval_ms", 0.0) or 0.0) for r in ok_rows]
    raw_chars = [float(r.get("raw_context_chars", 0.0) or 0.0) for r in ok_rows]
    compressed_chars = [float(r.get("compressed_context_chars", 0.0) or 0.0) for r in ok_rows]
    compression_rates = [float(r.get("context_compression_rate", 0.0) or 0.0) for r in ok_rows]
    return {
        "num_cases": float(len(rows)),
        "error_rate": _mean(1.0 if r.get("error") else 0.0 for r in rows),
        "avg_retrieval_ms": _mean(retrieval_ms),
        "p95_retrieval_ms": _percentile(retrieval_ms, 95),
        "hit_at_k": _mean(float(r.get("hit_at_k", 0.0) or 0.0) for r in gold_rows),
        "top1_acc": _mean(float(r.get("top1_acc", 0.0) or 0.0) for r in gold_rows),
        "precision_at_k": _mean(float(r.get("precision_at_k", 0.0) or 0.0) for r in gold_rows),
        "keyword_recall": _mean(float(r.get("keyword_recall", 0.0) or 0.0) for r in keyword_rows),
        "avg_raw_context_chars": _mean(raw_chars),
        "avg_compressed_context_chars": _mean(compressed_chars),
        "avg_context_compression_rate": _mean(compression_rates),
    }


def _write_summary_markdown(path: Path, summary: dict[str, float], raw_path: Path) -> None:
    lines = [
        "# RAG Retrieval Evaluation",
        "",
        f"- Raw rows: `{raw_path}`",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    for key in [
        "num_cases",
        "error_rate",
        "avg_retrieval_ms",
        "p95_retrieval_ms",
        "hit_at_k",
        "top1_acc",
        "precision_at_k",
        "keyword_recall",
        "avg_raw_context_chars",
        "avg_compressed_context_chars",
        "avg_context_compression_rate",
    ]:
        lines.append(f"| `{key}` | {summary.get(key, 0.0):.4f} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_eval(
    cases_path: Path,
    gold_path: Path,
    output_dir: Path,
    data_dir: Path,
    top_k: int,
    warmup: bool,
) -> dict[str, float]:
    cases = _load_jsonl(cases_path)
    gold_rows = _load_jsonl(gold_path)
    gold_map = {str(r.get("case_id", "")): r for r in gold_rows}

    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = output_dir / "rag_retrieval_raw.jsonl"
    if raw_path.exists():
        raw_path.unlink()

    retrievers: dict[str, Retriever | None] = {}
    warmed_courses: set[str] = set()
    raw_rows: list[dict[str, Any]] = []

    for case in cases:
        case_id = str(case.get("case_id", ""))
        course_name = str(case.get("course_name", ""))
        message = str(case.get("message", ""))
        gold = gold_map.get(case_id, {})
        gold_doc_ids = [str(x) for x in gold.get("gold_doc_ids", []) if str(x)]
        gold_keywords = [str(x) for x in gold.get("gold_keywords", []) if str(x)]

        if course_name not in retrievers:
            retrievers[course_name] = _load_retriever(data_dir, course_name)
        retriever = retrievers[course_name]
        if warmup and retriever is not None and course_name not in warmed_courses:
            retriever.retrieve("warmup retrieval query", top_k=1)
            warmed_courses.add(course_name)

        row: dict[str, Any] = {
            "case_id": case_id,
            "course_name": course_name,
            "top_k": top_k,
            "has_gold": bool(gold_doc_ids),
            "gold_doc_ids": gold_doc_ids,
            "keyword_count": len(gold_keywords),
        }
        if retriever is None:
            row["error"] = f"missing index for course: {course_name}"
            raw_rows.append(row)
            _append_jsonl(raw_path, row)
            continue

        t0 = perf_counter()
        try:
            chunks = retriever.retrieve(message, top_k=top_k)
            row["retrieval_ms"] = (perf_counter() - t0) * 1000.0
            doc_ids = _chunk_doc_ids(chunks)
            texts = _chunk_texts(chunks)
            raw_texts = _raw_chunk_texts(retriever, chunks)
            raw_chars = sum(len(t) for t in raw_texts)
            compressed_chars = sum(len(t) for t in texts)
            row["retrieved_doc_ids"] = doc_ids
            row["raw_context_chars"] = raw_chars
            row["compressed_context_chars"] = compressed_chars
            row["context_compression_rate"] = (
                1.0 - (compressed_chars / raw_chars) if raw_chars > 0 else 0.0
            )
            row["hit_at_k"] = 1.0 if set(doc_ids).intersection(gold_doc_ids) else 0.0
            row["top1_acc"] = 1.0 if doc_ids and doc_ids[0] in gold_doc_ids else 0.0
            row["precision_at_k"] = (
                sum(1.0 for doc_id in doc_ids if doc_id in gold_doc_ids) / len(doc_ids)
                if doc_ids and gold_doc_ids
                else 0.0
            )
            row["keyword_recall"] = _keyword_hit(texts, gold_keywords)
        except Exception as ex:
            row["retrieval_ms"] = (perf_counter() - t0) * 1000.0
            row["error"] = f"{type(ex).__name__}: {ex}"

        raw_rows.append(row)
        _append_jsonl(raw_path, row)

    summary = _summarize(raw_rows)
    (output_dir / "rag_retrieval_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_summary_markdown(output_dir / "rag_retrieval_summary.md", summary, raw_path)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate CoursePilot RAG retrieval only.")
    parser.add_argument("--cases", required=True)
    parser.add_argument("--gold", required=True)
    parser.add_argument("--output-dir", default=str(ROOT / "data" / "perf_runs" / "rag_retrieval"))
    parser.add_argument("--data-dir", default=str(ROOT / "data" / "workspaces"))
    parser.add_argument("--top-k", type=int, default=4)
    parser.add_argument("--retrieval-mode", default="", choices=["", "dense", "bm25", "hybrid"])
    parser.add_argument("--embedding-model", default="")
    parser.add_argument("--no-warmup", action="store_true", help="Include embedding cold start in latency metrics.")
    args = parser.parse_args()

    if args.retrieval_mode:
        os.environ["RETRIEVAL_MODE"] = args.retrieval_mode
    if args.embedding_model:
        os.environ["EMBEDDING_MODEL"] = args.embedding_model

    summary = run_eval(
        cases_path=Path(args.cases).resolve(),
        gold_path=Path(args.gold).resolve(),
        output_dir=Path(args.output_dir).resolve(),
        data_dir=Path(args.data_dir).resolve(),
        top_k=max(1, int(args.top_k)),
        warmup=not args.no_warmup,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
