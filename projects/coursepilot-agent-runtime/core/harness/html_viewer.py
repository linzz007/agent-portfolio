"""Static HTML rendering for CoursePilot RunArtifact files."""

from __future__ import annotations

import json
import re
from html import escape
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional


_INTERNAL_META_RE = re.compile(
    r"<!--\s*(?:QUIZ_META|EXAM_META)\b[\s\S]*?-->",
    flags=re.IGNORECASE,
)


def _as_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _strip_hidden_metadata(text: str) -> str:
    return _INTERNAL_META_RE.sub("", str(text or "")).strip()


def _safe_text(value: Any) -> str:
    return escape(str(value if value is not None else ""), quote=True)


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _json_pre(value: Any, *, max_chars: Optional[int] = None) -> str:
    text = _json_dump(value)
    if max_chars is not None and len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n..."
    return f"<pre>{_safe_text(text)}</pre>"


def _status_class(status: Any) -> str:
    s = str(status or "unknown").strip().lower()
    if s in {"ok", "passed", "succeeded", "success"}:
        return "ok"
    if s in {"warning", "warn"}:
        return "warning"
    if s in {"error", "failed", "fail"}:
        return "error"
    return "unknown"


def _summary_card(label: str, value: Any) -> str:
    return (
        '<div class="summary-card">'
        f'<div class="summary-label">{_safe_text(label)}</div>'
        f'<div class="summary-value">{_safe_text(value)}</div>'
        "</div>"
    )


def _render_diagnostics(items: Iterable[Mapping[str, Any]]) -> str:
    rows = []
    for item in items:
        status = item.get("status", "unknown")
        check = item.get("check") or item.get("name") or "diagnostic"
        message = item.get("message") or item.get("reason") or ""
        rows.append(
            f'<div class="diagnostic {_status_class(status)}">'
            f'<span class="pill">{_safe_text(status)}</span>'
            f'<strong>{_safe_text(check)}</strong>'
            f'<p>{_safe_text(message)}</p>'
            "</div>"
        )
    if not rows:
        rows.append('<p class="muted">No diagnostics.</p>')
    return "\n".join(rows)


def _render_timeline(items: Iterable[Mapping[str, Any]]) -> str:
    rows = []
    for idx, item in enumerate(items, start=1):
        typ = item.get("type", "event")
        name = item.get("name") or item.get("model") or item.get("tool_name") or ""
        status = item.get("status") or item.get("mode") or ""
        rows.append(
            "<tr>"
            f"<td>{idx}</td>"
            f"<td><code>{_safe_text(typ)}</code></td>"
            f"<td>{_safe_text(name)}</td>"
            f"<td>{_safe_text(status)}</td>"
            f"<td>{_json_pre(item, max_chars=2500)}</td>"
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="5" class="muted">No timeline events.</td></tr>')
    return (
        "<table>"
        "<thead><tr><th>#</th><th>Type</th><th>Name</th><th>Status</th><th>Payload</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def _llm_events(artifact: Mapping[str, Any]) -> List[Dict[str, Any]]:
    metrics = _as_dict(artifact.get("metrics"))
    events = _as_list(metrics.get("trace_events"))
    return [event for event in events if event.get("type") == "llm_call"]


def _render_llm_calls(events: Iterable[Mapping[str, Any]]) -> str:
    blocks = []
    for idx, event in enumerate(events, start=1):
        model = event.get("model") or "unknown-model"
        stream = event.get("stream", False)
        with_tools = bool(event.get("with_tools") or event.get("stream_tools"))
        meta = {
            "model": model,
            "provider": event.get("provider"),
            "prompt_tokens": event.get("prompt_tokens"),
            "completion_tokens": event.get("completion_tokens"),
            "prompt_tokens_est": event.get("prompt_tokens_est"),
            "llm_ms": event.get("llm_ms"),
            "stream": stream,
            "with_tools": with_tools,
            "success": event.get("success"),
        }
        blocks.append(
            "<details open>"
            f"<summary>LLM #{idx} · {_safe_text(model)}</summary>"
            "<h4>Metadata</h4>"
            f"{_json_pre(meta)}"
            "<h4>Input Messages</h4>"
            f"{_json_pre(event.get('input_messages', []))}"
            "<h4>Output Message</h4>"
            f"{_json_pre(event.get('output_message', {}))}"
            "</details>"
        )
    if not blocks:
        blocks.append('<p class="muted">No llm_call events.</p>')
    return "\n".join(blocks)


def render_run_artifact_html(artifact: Mapping[str, Any]) -> str:
    """Return a standalone HTML document for one RunArtifact dictionary."""

    artifact = _as_dict(artifact)
    session = _as_dict(artifact.get("session"))
    output = _as_dict(artifact.get("output"))
    raw_output = str(output.get("content") or "")
    visible_output = _strip_hidden_metadata(raw_output)
    run_id = artifact.get("run_id") or "unknown-run"
    title = f"RunArtifact {run_id}"

    summary = "\n".join(
        [
            _summary_card("Run ID", run_id),
            _summary_card("Schema", artifact.get("schema_version", "")),
            _summary_card("Mode", session.get("mode", "")),
            _summary_card("Skill", session.get("skill_id", "")),
            _summary_card("Status", session.get("status", "")),
            _summary_card("Trace ID", session.get("trace_id", "")),
            _summary_card("User Message", session.get("user_message", "")),
        ]
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{_safe_text(title)}</title>
  <style>
    :root {{ color-scheme: light; --border: #d7dde8; --text: #172033; --muted: #657087; }}
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: var(--text); background: #f6f8fb; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px; }}
    h1 {{ margin: 0 0 18px; font-size: 28px; }}
    h2 {{ margin: 28px 0 12px; font-size: 20px; }}
    h4 {{ margin: 14px 0 8px; }}
    .summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 10px; }}
    .summary-card, section, details {{ background: #fff; border: 1px solid var(--border); border-radius: 8px; }}
    .summary-card {{ padding: 12px; }}
    .summary-label {{ font-size: 12px; color: var(--muted); }}
    .summary-value {{ margin-top: 6px; font-weight: 600; word-break: break-word; }}
    section {{ padding: 16px; margin-top: 14px; }}
    details {{ padding: 12px 14px; margin: 10px 0; }}
    summary {{ cursor: pointer; font-weight: 700; }}
    pre {{ white-space: pre-wrap; word-break: break-word; background: #f3f5f8; padding: 12px; border-radius: 6px; overflow: auto; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ border-top: 1px solid var(--border); padding: 8px; vertical-align: top; text-align: left; }}
    th {{ color: var(--muted); font-size: 12px; }}
    code {{ background: #edf1f7; padding: 2px 5px; border-radius: 4px; }}
    .diagnostic {{ border-left: 5px solid #8792a5; padding: 10px 12px; margin: 8px 0; background: #fff; }}
    .diagnostic.ok {{ border-color: #1f9d55; }}
    .diagnostic.warning {{ border-color: #d99a16; }}
    .diagnostic.error {{ border-color: #cf2e2e; }}
    .diagnostic p {{ margin: 6px 0 0; color: var(--muted); }}
    .pill {{ display: inline-block; min-width: 58px; margin-right: 8px; padding: 2px 8px; border-radius: 999px; background: #edf1f7; font-size: 12px; text-align: center; }}
    .muted {{ color: var(--muted); }}
  </style>
</head>
<body>
<main>
  <h1>{_safe_text(title)}</h1>
  <div class="summary">{summary}</div>

  <h2>Diagnostics</h2>
  <section>{_render_diagnostics(_as_list(artifact.get("diagnostics")))}</section>

  <h2>Timeline</h2>
  <section>{_render_timeline(_as_list(artifact.get("timeline")))}</section>

  <h2>Visible Output</h2>
  <section>{_json_pre(visible_output)}</section>

  <h2>LLM Calls</h2>
  <section>{_render_llm_calls(_llm_events(artifact))}</section>

  <h2>Retrieval</h2>
  <section>{_json_pre(artifact.get("retrieval", []))}</section>

  <h2>Tool Calls</h2>
  <section>{_json_pre(artifact.get("tool_calls", []))}</section>

  <h2>Raw Artifact</h2>
  <section><details><summary>Show full JSON</summary>{_json_pre(artifact)}</details></section>
</main>
</body>
</html>
"""


def render_run_artifact_file(input_path: str | Path, output_path: str | Path | None = None) -> Path:
    """Render one RunArtifact JSON file to HTML and return the output path."""

    src = Path(input_path)
    with src.open("r", encoding="utf-8") as f:
        artifact = json.load(f)
    dst = Path(output_path) if output_path else src.with_suffix(".html")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(render_run_artifact_html(artifact), encoding="utf-8")
    return dst

