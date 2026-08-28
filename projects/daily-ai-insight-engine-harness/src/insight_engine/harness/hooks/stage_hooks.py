"""Stage Hooks —— 可插拔的生命周期监听器系统。

Hook（钩子）= 注册在 stage 生命周期节点（before / after）上的监听器。
它负责观察、记录、快照，不负责评判。

Linter（检查器）= 一种特殊的 after-stage 监听器，检查产物并返回
pass/fail 判决。Graph 读取这个判决来决定下一步流向。

本文件同时提供 StageHooks 插槽系统和默认注册的监听器函数。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from insight_engine.harness.artifacts import ensure_run_dir, write_json_artifact
from insight_engine.harness.prompt_builder import build_prompt_package, should_build_prompt
from insight_engine.harness.state import InsightEngineState, utc_now_iso

# ---------------------------------------------------------------------------
# 监听器函数签名
# ---------------------------------------------------------------------------

BeforeHook = Callable[["InsightEngineState", str], Any]
AfterHook = Callable[["InsightEngineState", str, dict[str, Any], str | None], Any]


# ---------------------------------------------------------------------------
# 插槽系统
# ---------------------------------------------------------------------------

class StageHooks:
    """可注册的 before/after 生命周期插槽。

    用法::

        hooks = StageHooks()
        hooks.on_before(snapshot_prompt)
        hooks.on_before(record_start_time)
        hooks.on_after(evaluate_linter)       # linter 只是另一个 after 监听器
        hooks.on_after(record_trace)
        hooks.on_after(snapshot_state)

        ctx     = hooks.fire_before(state, stage_name)
        state   = handler(state)
        results = hooks.fire_after(state, stage_name, ctx)

        gate = results.get("evaluate_linter", {})
    """

    def __init__(self) -> None:
        self._before: list[BeforeHook] = []
        self._after: list[AfterHook] = []

    def on_before(self, fn: BeforeHook) -> None:
        """注册一个在每个 stage 执行前触发的监听器。"""
        self._before.append(fn)

    def on_after(self, fn: AfterHook) -> None:
        """注册一个在每个 stage 执行后触发的监听器。

        注册顺序很重要：linter 监听器必须最先注册，这样 trace 监听器
        才能在 state 中读到 linter 写入的 gate_result。
        """
        self._after.append(fn)

    def fire_before(
        self, state: InsightEngineState, stage_name: str
    ) -> dict[str, Any]:
        """触发所有 before 监听器，返回以函数名为键的上下文字典。"""
        context: dict[str, Any] = {}
        for fn in self._before:
            result = fn(state, stage_name)
            if result is not None:
                context[fn.__name__] = result
        return context

    def fire_after(
        self,
        state: InsightEngineState,
        stage_name: str,
        context: dict[str, Any],
        error: str | None = None,
    ) -> dict[str, Any]:
        """触发所有 after 监听器，返回以函数名为键的结果字典。

        linter 监听器的返回值会被 graph 读取以决定流程走向。
        """
        results: dict[str, Any] = {}
        for fn in self._after:
            result = fn(state, stage_name, context, error)
            if result is not None:
                results[fn.__name__] = result
        return results


# ---------------------------------------------------------------------------
# 默认 before-stage 监听器
# ---------------------------------------------------------------------------

def snapshot_prompt(state: InsightEngineState, stage_name: str) -> None:
    """生成 prompt 快照，以便事后复现该 stage 当时的上下文。"""
    if should_build_prompt(stage_name):
        prompt_package = build_prompt_package(stage_name=stage_name, state=state)
        prompt_dir = ensure_run_dir("data/prompts", state.run_id)
        prompt_path = prompt_dir / f"{stage_name}.md"
        prompt_path.write_text(prompt_package.prompt_text, encoding="utf-8")
        state.add_artifact(f"prompt:{stage_name}", str(prompt_path))


def record_start_time(state: InsightEngineState, stage_name: str) -> dict[str, Any]:
    """记录挂钟时间和性能计数器起始值，供 trace 监听器计算耗时。"""
    return {"started_at": utc_now_iso(), "start_time": time.perf_counter()}


# ---------------------------------------------------------------------------
# 默认 after-stage 监听器
# ---------------------------------------------------------------------------

def evaluate_linter(
    state: InsightEngineState,
    stage_name: str,
    context: dict[str, Any],
    error: str | None = None,
) -> dict[str, Any]:
    """调用该 stage 注册的 linter 并返回 gate 判决。

    这是 hook 插槽系统和 linter 框架之间的桥梁。
    如果 stage 抛异常，直接返回硬失败；否则由 linter 检查产物。
    """
    if error:
        return {
            "stage": stage_name,
            "passed": False,
            "issues": [error],
            "metrics": {},
            "retryable": False,
            "checked_at": utc_now_iso(),
        }

    from insight_engine.harness.stage_gates import evaluate_stage_gate

    gate_result = evaluate_stage_gate(state, stage_name)
    state.add_stage_gate_result(gate_result)
    return gate_result


def record_trace(
    state: InsightEngineState,
    stage_name: str,
    context: dict[str, Any],
    error: str | None = None,
) -> None:
    """将 stage 执行轨迹写入 state。

    从 state 中读取最新 gate 结果（由 evaluate_linter 写入，
    因此 evaluate_linter 必须在本监听器之前注册）。
    """
    time_ctx = context.get("record_start_time", {})
    started_at = time_ctx.get("started_at", utc_now_iso())
    start_time = time_ctx.get("start_time", time.perf_counter())
    finished_at = utc_now_iso()
    duration_ms = int((time.perf_counter() - start_time) * 1000)

    trace: dict[str, Any] = {
        "stage": stage_name,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_ms": duration_ms,
        "status": "failed" if error else "ok",
        "error": error,
        "artifact_keys": sorted(state.artifacts.keys()),
    }

    # 附上该 stage 最新的 gate 结果
    for entry in reversed(state.stage_gate_results):
        if entry.get("stage") == stage_name:
            trace["gate"] = entry
            break

    state.add_stage_trace(trace)


def snapshot_state(
    state: InsightEngineState,
    stage_name: str,
    context: dict[str, Any],
    error: str | None = None,
) -> None:
    """保存完整 state 快照到磁盘，用于事后排查。"""
    write_json_artifact(
        state=state,
        artifact_name=f"state_snapshot:{stage_name}",
        data=state.to_dict(),
        base_dir="data/state",
        filename=f"{stage_name}.json",
    )


# ---------------------------------------------------------------------------
# 快捷构造器
# ---------------------------------------------------------------------------

def build_default_hooks() -> StageHooks:
    """创建预装默认监听器的 StageHooks 实例。

    注册顺序是刻意设计的：
    - after 监听器：linter 最先运行（将 gate 结果写入 state），
      然后是 trace 记录器（从 state 读取 gate 结果），
      最后是 state 快照。
    """
    hooks = StageHooks()
    hooks.on_before(snapshot_prompt)
    hooks.on_before(record_start_time)
    hooks.on_after(evaluate_linter)
    hooks.on_after(record_trace)
    hooks.on_after(snapshot_state)
    return hooks
