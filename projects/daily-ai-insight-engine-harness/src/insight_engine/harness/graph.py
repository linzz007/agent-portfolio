"""Harness Graph —— stage 状态机。

Graph 根据 State 决定 stage 之间的路由。它持有一个 StageHooks 实例，
在每个 before/after 生命周期节点触发已注册的监听器（linter、trace、
prompt 快照、state 快照），而 graph 本身不需要知道注册了什么。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
from typing import Literal

from insight_engine.harness.hooks.stage_hooks import StageHooks, build_default_hooks
from insight_engine.harness.state import InsightEngineState

StageName = Literal[
    "initialized",
    "collect_raw_items",
    "clean_items",
    "structure_events",
    "analyze_insights",
    "generate_report",
    "done",
    "failed",
]

StageHandler = Callable[[InsightEngineState], InsightEngineState]


@dataclass(frozen=True)
class GraphDecision:
    """Graph 每次路由判断的结果。"""

    next_stage: StageName
    reason: str


class InsightEngineGraph:
    """基于 hook 生命周期的 stage 状态机。

    用法::

        graph = InsightEngineGraph(handlers={...})
        state = graph.run(state)

    Graph 只调 StageHooks 的 fire_before / fire_after。
    所有副作用 —— linter 检查、trace 记录、prompt 快照、state 快照 ——
    都封装在已注册的监听器中，不在 graph 内部硬编码。
    """

    max_stage_retry_count = int(os.getenv("HARNESS_STAGE_MAX_RETRY", "1"))

    def __init__(
        self,
        handlers: dict[StageName, StageHandler],
        hooks: StageHooks | None = None,
    ) -> None:
        self.handlers = handlers
        self.hooks = hooks or build_default_hooks()

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def run(self, state: InsightEngineState) -> InsightEngineState:
        """从当前 state 的阶段开始执行，直到 done 或 failed。"""
        while state.current_stage not in {"done", "failed"}:
            decision = self.decide_next_stage(state)
            state.mark_stage(decision.next_stage)

            if decision.next_stage in {"done", "failed"}:
                break

            handler = self.handlers.get(decision.next_stage)
            if handler is None:
                state.add_error(
                    stage=decision.next_stage,
                    message="没有注册当前阶段的处理函数",
                    detail={"reason": decision.reason},
                )
                state.mark_stage("failed")
                break

            state = self._run_stage_with_gate(state, decision.next_stage, handler)
            if state.current_stage == "failed":
                break

        return state

    # ------------------------------------------------------------------
    # 路由
    # ------------------------------------------------------------------

    def decide_next_stage(self, state: InsightEngineState) -> GraphDecision:
        """根据当前 stage 和数据是否存在决定下一步路由。"""
        if state.current_stage == "initialized":
            return GraphDecision("collect_raw_items", "初始化完成，开始抓取原始信息")

        if state.current_stage == "collect_raw_items":
            if not state.global_raw_items and not state.ai_raw_items:
                return GraphDecision("failed", "没有抓取到原始信息")
            return GraphDecision("clean_items", "原始信息已存在，进入清洗阶段")

        if state.current_stage == "clean_items":
            if not state.global_cleaned_items and not state.ai_cleaned_items:
                return GraphDecision("failed", "清洗后没有可用信息")
            return GraphDecision("structure_events", "清洗结果已存在，进入结构化阶段")

        if state.current_stage == "structure_events":
            if not state.global_structured_events and not state.ai_structured_events:
                return GraphDecision("failed", "结构化记录为空")
            return GraphDecision("analyze_insights", "结构化记录已存在，进入分析阶段")

        if state.current_stage == "analyze_insights":
            if not state.analysis_result:
                return GraphDecision("failed", "分析结果为空")
            return GraphDecision("generate_report", "分析结果已存在，进入报告生成阶段")

        if state.current_stage == "generate_report":
            if not state.report_paths:
                return GraphDecision("failed", "报告路径为空")
            return GraphDecision("done", "报告已生成，stage gate 已通过，流程完成")

        return GraphDecision("failed", f"未知阶段：{state.current_stage}")

    # ------------------------------------------------------------------
    # 单 stage 执行器（含 hook 生命周期）
    # ------------------------------------------------------------------

    def _run_stage_with_gate(
        self,
        state: InsightEngineState,
        stage_name: StageName,
        handler: StageHandler,
    ) -> InsightEngineState:
        """在 hook 生命周期内运行单个 stage，含 gate 检查与重试。

        生命周期::

            fire_before  →  handler()  →  fire_after
                                             │
                             ┌─ linter 通过 ─→ 返回 state
                             │
                             └─ linter 失败 ─→ 重试或失败
        """
        while True:
            ctx = self.hooks.fire_before(state, stage_name)

            try:
                state = handler(state)
            except Exception as exc:  # noqa: BLE001
                self.hooks.fire_after(state, stage_name, ctx, error=repr(exc))
                state.add_error(
                    stage=stage_name,
                    message="阶段执行失败",
                    detail=repr(exc),
                )
                state.mark_stage("failed")
                return state

            results = self.hooks.fire_after(state, stage_name, ctx)

            # linter 监听器作为 after-hook 注册，其返回值即 gate 判决。
            gate_result = results.get("evaluate_linter", {})
            if not gate_result:
                return state

            if gate_result.get("passed"):
                return state

            current_retry_count = state.stage_retry_counts.get(stage_name, 0)
            can_retry = (
                bool(gate_result.get("retryable"))
                and current_retry_count < self.max_stage_retry_count
            )
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


def build_graph(
    handlers: dict[StageName, StageHandler],
    hooks: StageHooks | None = None,
) -> InsightEngineGraph:
    """创建 Graph 实例。

    ``daily_news_report_skill`` 调用此函数，传入真实 stage handler
    和默认 hook 集合（prompt 快照、linter、trace、state 快照）。
    """
    return InsightEngineGraph(handlers=handlers, hooks=hooks)
