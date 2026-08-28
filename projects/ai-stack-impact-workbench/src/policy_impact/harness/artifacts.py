"""Artifact helpers for policy impact harness runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def ensure_run_dir(base_dir: str | Path, run_id: str) -> Path:
    base = Path(base_dir)
    if not base.is_absolute():
        base = project_root() / base
    run_dir = base / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def write_json_artifact(state: Any, artifact_name: str, data: Any, base_dir: str | Path, filename: str) -> Path:
    run_dir = ensure_run_dir(base_dir, state.run_id)
    path = run_dir / filename
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    state.add_artifact(artifact_name, str(path))
    return path


def write_text_artifact(state: Any, artifact_name: str, text: str, base_dir: str | Path, filename: str) -> Path:
    run_dir = ensure_run_dir(base_dir, state.run_id)
    path = run_dir / filename
    path.write_text(text, encoding="utf-8")
    state.add_artifact(artifact_name, str(path))
    return path
