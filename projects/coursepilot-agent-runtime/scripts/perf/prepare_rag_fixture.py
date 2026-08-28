"""Prepare a small local CoursePilot workspace for RAG benchmark smoke tests."""

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


def _parse_text_fixture(path: Path) -> list[dict[str, object]]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    return [{"text": text, "page": None, "doc_id": path.name}]


def prepare_fixture(course_name: str, data_dir: Path, fixture_path: Path) -> Path:
    workspace = _workspace_path(data_dir, course_name)
    uploads_dir = workspace / "uploads"
    index_dir = workspace / "index"
    for subdir in ("uploads", "index", "notes", "mistakes", "exams", "practices"):
        (workspace / subdir).mkdir(parents=True, exist_ok=True)

    target_doc = uploads_dir / fixture_path.name
    shutil.copyfile(fixture_path, target_doc)

    pages = _parse_text_fixture(target_doc)
    if not pages:
        raise RuntimeError(f"No text parsed from fixture: {target_doc}")

    chunks = chunk_documents(pages)
    if not chunks:
        raise RuntimeError(f"No chunks generated from fixture: {target_doc}")

    store = build_index(chunks)
    index_path = index_dir / "faiss_index"
    store.save(str(index_path))
    return workspace


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare matrix_fixture workspace for RAG benchmark.")
    parser.add_argument("--course-name", default="matrix_fixture")
    parser.add_argument("--data-dir", default=str(ROOT / "data" / "workspaces"))
    parser.add_argument("--fixture", default=str(ROOT / "benchmarks" / "fixtures" / "matrix_fixture.txt"))
    parser.add_argument(
        "--embedding-model",
        default="",
        help="Optional EMBEDDING_MODEL override, for example BAAI/bge-small-zh-v1.5.",
    )
    args = parser.parse_args()

    if args.embedding_model:
        os.environ["EMBEDDING_MODEL"] = args.embedding_model

    data_dir = Path(args.data_dir).resolve()
    fixture_path = Path(args.fixture).resolve()
    workspace = prepare_fixture(args.course_name, data_dir, fixture_path)
    print(f"[fixture] workspace={workspace}")
    print(f"[fixture] upload={(workspace / 'uploads' / fixture_path.name)}")
    print(f"[fixture] index={(workspace / 'index' / 'faiss_index')}.faiss")
    print("[fixture] retrieval-only command:")
    print(
        "python scripts/perf/eval_rag_retrieval.py "
        "--cases benchmarks/cases_fixture.jsonl "
        "--gold benchmarks/rag_gold_fixture.jsonl "
        "--output-dir data/perf_runs/rag_fixture_retrieval "
        "--retrieval-mode hybrid "
        "--top-k 4"
    )
    print("[fixture] full runtime command:")
    print(
        "python scripts/perf/bench_runner.py "
        "--cases benchmarks/cases_fixture.jsonl "
        "--gold benchmarks/rag_gold_fixture.jsonl "
        "--output-dir data/perf_runs/fixture_smoke "
        "--profile fixture_smoke "
        "--repeats 1"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
