"""Structured run artifact persistence."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def _env_flag(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "1" if default else "0")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump())
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


@dataclass
class RunArtifact:
    """Evidence produced by one Agent Harness execution."""

    run_id: str
    session: Dict[str, Any]
    plan: Optional[Dict[str, Any]] = None
    user_goal: Optional[Dict[str, Any]] = None
    retrieval: List[Dict[str, Any]] = field(default_factory=list)
    context_budget: Optional[Dict[str, Any]] = None
    memory_trace: Dict[str, Any] = field(default_factory=dict)
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    tool_decisions: List[Dict[str, Any]] = field(default_factory=list)
    risk_decisions: List[Dict[str, Any]] = field(default_factory=list)
    timeline: List[Dict[str, Any]] = field(default_factory=list)
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    output: Optional[Dict[str, Any]] = None
    eval_result: Optional[Dict[str, Any]] = None
    metrics: Dict[str, Any] = field(default_factory=dict)
    error: Optional[Dict[str, Any]] = None
    lifecycle_events: List[Dict[str, Any]] = field(default_factory=list)
    schema_version: str = "harness.run_artifact.v3"

    def to_dict(self) -> Dict[str, Any]:
        return _jsonable(asdict(self))


class ArtifactStore:
    """Append-only JSON artifact store under data/runs by default."""

    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = Path(base_dir or os.getenv("HARNESS_ARTIFACT_DIR", "./data/runs"))

    def _path_for(self, artifact: RunArtifact) -> Path:
        started_at = str((artifact.session or {}).get("started_at") or "")
        day = started_at[:10] if len(started_at) >= 10 else datetime.now().strftime("%Y-%m-%d")
        return self.base_dir / day / f"{artifact.run_id}.json"

    def write(self, artifact: RunArtifact) -> str:
        path = self._path_for(artifact)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".json.tmp")
        payload = artifact.to_dict()
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp_path, path)
        if _env_flag("HARNESS_RENDER_HTML"):
            from core.harness.html_viewer import render_run_artifact_html

            html_path = path.with_suffix(".html")
            tmp_html_path = html_path.with_suffix(".html.tmp")
            tmp_html_path.write_text(render_run_artifact_html(payload), encoding="utf-8")
            os.replace(tmp_html_path, html_path)
        return str(path)

    @staticmethod
    def read(path: str) -> Dict[str, Any]:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
