"""Dry replay helpers for persisted policy impact checkpoints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from policy_impact.harness.state import utc_now_iso


def dry_replay_checkpoint(path: str | Path) -> dict[str, Any]:
    checkpoint_path = Path(path)
    payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    state = payload.get("state", {})
    tool_calls = payload.get("tool_calls", [])
    gate_results = payload.get("stage_gate_results", [])
    context_manifests = payload.get("context_manifests", [])
    model_calls = payload.get("model_calls", [])
    summary = {
        "schema_version": "checkpoint_replay.v1",
        "run_id": payload.get("run_id"),
        "company_id": payload.get("company_id"),
        "stage_name": payload.get("stage_name"),
        "checkpoint_created_at": payload.get("created_at"),
        "tool_call_count": len(tool_calls),
        "gate_count": len(gate_results),
        "context_manifest_count": len(context_manifests),
        "model_call_count": len(model_calls),
        "artifact_keys": sorted(state.get("artifacts", {}).keys()),
    }

    return {
        "schema_version": "checkpoint_replay.v1",
        "status": "replayed",
        "mode": "dry_replay",
        "stage_name": payload.get("stage_name"),
        "source_checkpoint": str(checkpoint_path),
        "replayed_at": utc_now_iso(),
        "state": state,
        "tool_call_count": len(tool_calls),
        "gate_count": len(gate_results),
        "summary": summary,
    }
