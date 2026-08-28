"""Prepare a multi-document linear algebra workspace for RAG evaluation."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag.chunk import chunk_documents  # noqa: E402
from rag.store_faiss import build_index  # noqa: E402


def _workspace_path(data_dir: Path, course_name: str) -> Path:
    safe_name = os.path.basename(course_name.strip())
    if not safe_name or safe_name in {".", ".."}:
        raise ValueError(f"Invalid course name: {course_name!r}")
    return data_dir / safe_name


def _parse_text_files(fixture_dir: Path) -> list[dict[str, object]]:
    pages: list[dict[str, object]] = []
    for path in sorted(fixture_dir.glob("*.txt")):
        text = path.read_text(encoding="utf-8").strip()
        if text:
            pages.append({"text": text, "page": None, "doc_id": path.name})
    return pages


def prepare_fixture(course_name: str, data_dir: Path, fixture_dir: Path) -> Path:
    workspace = _workspace_path(data_dir, course_name)
    uploads_dir = workspace / "uploads"
    index_dir = workspace / "index"
    for subdir in ("uploads", "index", "notes", "mistakes", "exams", "practices"):
        (workspace / subdir).mkdir(parents=True, exist_ok=True)

    for source in sorted(fixture_dir.glob("*.txt")):
        shutil.copyfile(source, uploads_dir / source.name)

    pages = _parse_text_files(uploads_dir)
    if not pages:
        raise RuntimeError(f"No text parsed from fixture dir: {uploads_dir}")

    chunks = chunk_documents(pages)
    if not chunks:
        raise RuntimeError(f"No chunks generated from fixture dir: {uploads_dir}")

    store = build_index(chunks)
    index_path = index_dir / "faiss_index"
    store.save(str(index_path))
    return workspace


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare linear_algebra_eval workspace for RAG evaluation.")
    parser.add_argument("--course-name", default="linear_algebra_eval")
    parser.add_argument("--data-dir", default=str(ROOT / "data" / "workspaces"))
    parser.add_argument(
        "--fixture-dir",
        default=str(ROOT / "benchmarks" / "fixtures" / "linear_algebra_course"),
    )
    parser.add_argument(
        "--embedding-model",
        default="",
        help="Optional EMBEDDING_MODEL override, for example BAAI/bge-small-zh-v1.5.",
    )
    args = parser.parse_args()

    if args.embedding_model:
        os.environ["EMBEDDING_MODEL"] = args.embedding_model

    data_dir = Path(args.data_dir).resolve()
    fixture_dir = Path(args.fixture_dir).resolve()
    workspace = prepare_fixture(args.course_name, data_dir, fixture_dir)
    print(f"[fixture] workspace={workspace}")
    print(f"[fixture] uploads={workspace / 'uploads'}")
    print(f"[fixture] index={(workspace / 'index' / 'faiss_index')}.faiss")
    print("[fixture] eval command:")
    print(
        "python scripts/perf/eval_rag_retrieval.py "
        "--cases benchmarks/cases_linear_algebra.jsonl "
        "--gold benchmarks/rag_gold_linear_algebra.jsonl "
        "--output-dir data/perf_runs/rag_linear_algebra_hybrid "
        "--retrieval-mode hybrid "
        "--top-k 4"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
