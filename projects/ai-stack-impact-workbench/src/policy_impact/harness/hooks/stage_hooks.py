"""Lifecycle hooks for policy impact stages."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from policy_impact.harness.artifacts import write_json_artifact
from policy_impact.harness.checkpoints import write_checkpoint
from policy_impact.harness.context_router import build_stage_context_manifest
from policy_impact.harness.state import PolicyImpactState, utc_now_iso
from policy_impact.harness.tool_gateway import get_tool_audit_log

BeforeHook = Callable[[PolicyImpactState, str], Any]
AfterHook = Callable[[PolicyImpactState, str, dict[str, Any], str | None], Any]


class StageHooks:
    def __init__(self) -> None:
        self._before: list[BeforeHook] = []
        self._after: list[AfterHook] = []

    def on_before(self, fn: BeforeHook) -> None:
        self._before.append(fn)

    def on_after(self, fn: AfterHook) -> None:
        self._after.append(fn)

    def fire_before(self, state: PolicyImpactState, stage_name: str) -> dict[str, Any]:
        context: dict[str, Any] = {}
        for fn in self._before:
            value = fn(state, stage_name)
            if value is not None:
                context[fn.__name__] = value
        return context

    def fire_after(
        self,
        state: PolicyImpactState,
        stage_name: str,
        context: dict[str, Any],
        error: str | None = None,
    ) -> dict[str, Any]:
        results: dict[str, Any] = {}
        for fn in self._after:
            value = fn(state, stage_name, context, error)
            if value is not None:
                results[fn.__name__] = value
        return results


def record_start_time(state: PolicyImpactState, stage_name: str) -> dict[str, Any]:
    return {"started_at": utc_now_iso(), "start_time": time.perf_counter()}


def record_context_manifest(state: PolicyImpactState, stage_name: str) -> dict[str, Any] | None:
    manifest_sequence = _next_manifest_sequence(state, stage_name)
    try:
        manifest = build_stage_context_manifest(
            stage_name,
            state,
            manifest_sequence=manifest_sequence,
        ).to_dict()
    except Exception as exc:
        state.add_warning(stage_name, "context_manifest_failed", repr(exc))
        return None
    state.add_context_manifest(manifest)
    return manifest


def _next_manifest_sequence(state: PolicyImpactState, stage_name: str) -> int:
    return (
        sum(
            1
            for manifest in state.context_manifests
            if manifest.get("metadata", {}).get("stage_name") == stage_name
        )
        + 1
    )


def evaluate_linter(
    state: PolicyImpactState,
    stage_name: str,
    context: dict[str, Any],
    error: str | None = None,
) -> dict[str, Any]:
    if error:
        result = {
            "stage": stage_name,
            "passed": False,
            "issues": [error],
            "metrics": {},
            "retryable": False,
            "checked_at": utc_now_iso(),
        }
    else:
        from policy_impact.harness.stage_gates import evaluate_stage_gate

        result = evaluate_stage_gate(state, stage_name)
    state.add_stage_gate_result(result)
    return result


def sync_tool_audit(
    state: PolicyImpactState,
    stage_name: str,
    context: dict[str, Any],
    error: str | None = None,
) -> dict[str, Any]:
    state.tool_calls = get_tool_audit_log()
    return {"tool_call_count": len(state.tool_calls)}


def record_trace(
    state: PolicyImpactState,
    stage_name: str,
    context: dict[str, Any],
    error: str | None = None,
) -> None:
    timing = context.get("record_start_time", {})
    started_at = timing.get("started_at", utc_now_iso())
    start_time = timing.get("start_time", time.perf_counter())
    trace = {
        "stage": stage_name,
        "started_at": started_at,
        "finished_at": utc_now_iso(),
        "duration_ms": int((time.perf_counter() - start_time) * 1000),
        "status": "failed" if error else "ok",
        "error": error,
        "artifact_keys": sorted(state.artifacts.keys()),
    }
    for gate in reversed(state.stage_gate_results):
        if gate.get("stage") == stage_name:
            trace["gate"] = gate
            break
    state.add_stage_trace(trace)


def snapshot_state(
    state: PolicyImpactState,
    stage_name: str,
    context: dict[str, Any],
    error: str | None = None,
) -> None:
    # Full replay state is already persisted by checkpoint_stage.  This global
    # snapshot is a lightweight index for inspection, avoiding a second copy of
    # the growing prompts, evidence, and trace after every stage.
    write_json_artifact(
        state,
        f"state_snapshot:{stage_name}",
        {
            "schema_version": "state_snapshot.v2",
            "run_id": state.run_id,
            "company_id": state.company_id,
            "stage_name": stage_name,
            "current_stage": state.current_stage,
            "error": error,
            "artifact_keys": sorted(state.artifacts),
            "tool_call_count": len(state.tool_calls),
            "gate_count": len(state.stage_gate_results),
            "context_manifest_count": len(state.context_manifests),
            "model_call_count": len(state.model_calls),
            "last_trace": state.stage_trace[-1] if state.stage_trace else {},
        },
        "data/state",
        f"{stage_name}.json",
    )


def checkpoint_stage(
    state: PolicyImpactState,
    stage_name: str,
    context: dict[str, Any],
    error: str | None = None,
) -> dict[str, Any] | None:
    try:
        return write_checkpoint(state, stage_name)
    except Exception as exc:
        state.add_warning(stage_name, "checkpoint_failed", repr(exc))
        return None


def build_default_hooks() -> StageHooks:
    hooks = StageHooks()
    hooks.on_before(record_context_manifest)
    hooks.on_before(record_start_time)
    hooks.on_after(sync_tool_audit)
    hooks.on_after(evaluate_linter)
    hooks.on_after(record_trace)
    hooks.on_after(snapshot_state)
    hooks.on_after(checkpoint_stage)
    return hooks
