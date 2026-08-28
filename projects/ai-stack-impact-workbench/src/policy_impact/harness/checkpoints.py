"""Checkpoint persistence for policy impact harness runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from policy_impact.harness.artifacts import ensure_run_dir
from policy_impact.harness.state import PolicyImpactState, utc_now_iso


def _checkpoint_path(run_dir: Path, stage_name: str, created_at: str) -> Path:
    path = run_dir / f"{stage_name}.checkpoint.json"
    if not path.exists():
        return path

    timestamp = (
        created_at.replace("-", "")
        .replace(":", "")
        .replace(".", "")
        .replace("+0000", "Z")
        .replace("+00:00", "Z")
    )
    path = run_dir / f"{stage_name}.{timestamp}.checkpoint.json"
    suffix = 2
    while path.exists():
        path = run_dir / f"{stage_name}.{timestamp}.{suffix}.checkpoint.json"
        suffix += 1
    return path


def write_checkpoint(
    state: PolicyImpactState,
    stage_name: str,
    base_dir: str | Path = "data/checkpoints",
) -> dict[str, Any]:
    """Persist a stage checkpoint and register it on the active state."""

    created_at = utc_now_iso()
    run_dir = ensure_run_dir(base_dir, state.run_id)
    path = _checkpoint_path(run_dir, stage_name, created_at)
    payload = {
        "run_id": state.run_id,
        "company_id": state.company_id,
        "stage_name": stage_name,
        "created_at": created_at,
        "state": state.to_dict(),
        "tool_calls": state.tool_calls,
        "stage_gate_results": state.stage_gate_results,
        "context_manifests": state.context_manifests,
        "model_calls": state.model_calls,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    checkpoint = {
        "stage_name": stage_name,
        "path": str(path),
        "created_at": created_at,
    }
    state.add_checkpoint(checkpoint)
    return checkpoint
