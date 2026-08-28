"""单阶段运行器，用于调试。

通过和完整 graph 相同的 StageHooks 生命周期调用单个 stage handler，
确保 linter / trace / 快照仍然触发。
"""

from __future__ import annotations

import os

from insight_engine.harness.graph import StageHandler, StageName
from insight_engine.harness.hooks.stage_hooks import StageHooks, build_default_hooks
from insight_engine.harness.state import InsightEngineState


def run_single_stage(
    state: InsightEngineState,
    stage_name: StageName,
    handler: StageHandler,
    hooks: StageHooks | None = None,
) -> InsightEngineState:
    """运行单个 stage，走完整的 hook 生命周期（linter、trace、快照）。"""
    _hooks = hooks or build_default_hooks()
    max_retry = int(os.getenv("HARNESS_STAGE_MAX_RETRY", "1"))

    state.mark_stage(stage_name)

    while True:
        ctx = _hooks.fire_before(state, stage_name)
        try:
            state = handler(state)
        except Exception as exc:  # noqa: BLE001
            _hooks.fire_after(state, stage_name, ctx, error=repr(exc))
            state.add_error(
                stage=stage_name,
                message="单阶段执行失败",
                detail=repr(exc),
            )
            state.mark_stage("failed")
            return state

        results = _hooks.fire_after(state, stage_name, ctx)
        gate_result = results.get("evaluate_linter", {})
        if not gate_result or gate_result.get("passed"):
            return state

        current_retry = state.stage_retry_counts.get(stage_name, 0)
        can_retry = bool(gate_result.get("retryable")) and current_retry < max_retry
        if not can_retry:
            state.add_error(
                stage=stage_name,
                message="stage gate 未通过",
                detail=gate_result,
            )
            state.mark_stage("failed")
            return state

        retry_count = state.increment_stage_retry(stage_name)
        state.add_warning(
            stage=stage_name,
            message=f"stage gate 未通过，重跑当前 stage：第 {retry_count} 次",
            detail=gate_result,
        )
