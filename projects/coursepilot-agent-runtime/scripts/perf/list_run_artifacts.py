"""List or inspect Agent Harness RunArtifact JSON files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _trim(value: Any, max_chars: int = 72) -> str:
    text = str(value or "").strip().replace("\r", " ").replace("\n", " ")
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def _items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _compact_json(value: Any, max_chars: int = 220) -> str:
    if value in (None, {}, []):
        return ""
    try:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except TypeError:
        text = str(value)
    return _trim(text, max_chars)


def _status_label(status: Any) -> str:
    text = str(status or "unknown").lower()
    if text == "ok":
        return "OK"
    if text == "warning":
        return "WARN"
    if text == "error":
        return "ERROR"
    return text.upper()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_artifacts(base_dir: Path) -> list[Path]:
    if not base_dir.exists():
        return []
    return sorted(
        base_dir.rglob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def _session(data: dict[str, Any]) -> dict[str, Any]:
    session = data.get("session")
    return session if isinstance(session, dict) else {}


def _matches(data: dict[str, Any], path: Path, contains: str, run_id: str) -> bool:
    session = _session(data)
    if run_id and run_id not in str(data.get("run_id", "")):
        return False
    if contains:
        haystack = " ".join(
            [
                str(path),
                str(data.get("run_id", "")),
                str(session.get("course_name", "")),
                str(session.get("mode", "")),
                str(session.get("user_message", "")),
                str(session.get("request_id", "")),
            ]
        ).lower()
        if contains.lower() not in haystack:
            return False
    return True


def _print_list(rows: list[tuple[Path, dict[str, Any]]]) -> None:
    if not rows:
        print("No RunArtifact files found.")
        return
    print("| # | started_at | status | mode | course | run_id | message | path |")
    print("|---:|---|---|---|---|---|---|---|")
    for idx, (path, data) in enumerate(rows, start=1):
        session = _session(data)
        print(
            "| {idx} | {started} | {status} | {mode} | {course} | {run_id} | {message} | {path} |".format(
                idx=idx,
                started=_trim(session.get("started_at"), 20),
                status=_trim(session.get("status"), 12),
                mode=_trim(session.get("mode"), 10),
                course=_trim(session.get("course_name"), 24),
                run_id=_trim(data.get("run_id"), 32),
                message=_trim(session.get("user_message"), 54),
                path=path,
            )
        )


def _print_detail(path: Path, data: dict[str, Any], *, full_io: bool = False) -> None:
    session = _session(data)
    metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
    trace_events = metrics.get("trace_events") if isinstance(metrics.get("trace_events"), list) else []
    retrieval = data.get("retrieval") if isinstance(data.get("retrieval"), list) else []
    tool_calls = data.get("tool_calls") if isinstance(data.get("tool_calls"), list) else []
    context_budget = data.get("context_budget") if isinstance(data.get("context_budget"), dict) else {}
    diagnostics = _items(data.get("diagnostics"))
    timeline = _items(data.get("timeline"))
    try:
        from core.harness.observability import build_run_diagnostics

        diagnostics = build_run_diagnostics(
            session=session,
            retrieval=retrieval,
            context_budget=context_budget,
            tool_calls=tool_calls,
            tool_decisions=_items(data.get("tool_decisions")),
            output=data.get("output") if isinstance(data.get("output"), dict) else {},
            error=data.get("error") if isinstance(data.get("error"), dict) else None,
        )
    except Exception:
        pass

    print(f"# RunArtifact: {data.get('run_id')}")
    print(f"- path: `{path}`")
    print(f"- schema_version: `{data.get('schema_version', '')}`")
    print(f"- started_at: `{session.get('started_at', '')}`")
    print(f"- status: `{session.get('status', '')}`")
    print(f"- course: `{session.get('course_name', '')}`")
    print(f"- mode: `{session.get('mode', '')}`")
    print(f"- request_id: `{session.get('request_id', '')}`")
    print(f"- trace_id: `{session.get('trace_id', '')}`")
    print(f"- user_message: {_trim(session.get('user_message'), 160)}")
    print("")
    print("## Diagnostics")
    if diagnostics:
        for item in diagnostics:
            print(
                f"- [{_status_label(item.get('status'))}] `{item.get('check')}`: {_trim(item.get('message'), 180)}"
            )
            details = _compact_json(item.get("details"))
            if details:
                print(f"  details: `{details}`")
    else:
        print("- none")
    print("")
    print("## Timeline")
    if timeline:
        for item in timeline[:24]:
            print(
                f"{item.get('step', '-')}. [{_status_label(item.get('status'))}] {item.get('title') or item.get('type')} - {_trim(item.get('summary'), 180)}"
            )
            io_max_chars = 200000 if full_io and item.get("type") == "llm_call" else 220
            input_text = _compact_json(item.get("input"), max_chars=io_max_chars)
            output_text = _compact_json(item.get("output"), max_chars=io_max_chars)
            if input_text:
                print(f"   input: `{input_text}`")
            if output_text:
                print(f"   output: `{output_text}`")
    else:
        print("- none")
    print("")
    print("## Context Budget")
    if context_budget:
        for key in [
            "history_tokens_est",
            "rag_tokens_est",
            "memory_tokens_est",
            "final_tokens_est",
            "budget_tokens_est",
            "context_pressure_ratio",
            "history_summary_source",
            "history_llm_compress_applied",
            "hard_truncated",
        ]:
            print(f"- {key}: `{context_budget.get(key)}`")
    else:
        print("- none")
    print("")
    print("## Retrieval")
    if retrieval:
        for item in retrieval[:8]:
            print(
                f"- `{item.get('doc_id')}` `{item.get('chunk_id')}` score=`{item.get('score')}` text={_trim(item.get('text'), 120)}"
            )
    else:
        print("- none")
    print("")
    print("## Tool Calls")
    if tool_calls:
        for item in tool_calls[:12]:
            print(
                f"- source=`{item.get('source')}` type=`{item.get('type')}` tool=`{item.get('tool_name')}` success=`{item.get('success', item.get('tool_success'))}`"
            )
    else:
        print("- none")
    print("")
    print("## Trace Events")
    if trace_events:
        for item in trace_events[:16]:
            print(f"- seq=`{item.get('seq')}` type=`{item.get('type')}`")
    else:
        print("- none")


def main() -> int:
    _configure_stdout()
    parser = argparse.ArgumentParser(description="List or inspect Agent Harness RunArtifact files.")
    parser.add_argument("--dir", default=str(ROOT / "data" / "runs"))
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--contains", default="", help="Filter by user message, course, request_id, run_id, or path.")
    parser.add_argument("--run-id", default="", help="Show runs whose run_id contains this value.")
    parser.add_argument("--detail", action="store_true", help="Print a readable detail view for the first matched run.")
    parser.add_argument("--full-io", action="store_true", help="When printing detail, do not truncate LLM input/output messages.")
    args = parser.parse_args()

    base_dir = Path(args.dir)
    rows: list[tuple[Path, dict[str, Any]]] = []
    for path in _iter_artifacts(base_dir):
        try:
            data = _load_json(path)
        except Exception as exc:
            print(f"[skip] {path}: {exc}", file=sys.stderr)
            continue
        if _matches(data, path, args.contains, args.run_id):
            rows.append((path, data))
        if not args.detail and len(rows) >= max(1, args.limit):
            break

    if args.detail:
        if not rows:
            print("No matching RunArtifact files found.")
            return 1
        _print_detail(rows[0][0], rows[0][1], full_io=bool(args.full_io))
        return 0

    _print_list(rows[: max(1, args.limit)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
