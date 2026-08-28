"""Harness graph for policy impact stages."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os

from policy_impact.harness.hooks.stage_hooks import StageHooks, build_default_hooks
from policy_impact.harness.state import PolicyImpactState, StageName

StageHandler = Callable[[PolicyImpactState], PolicyImpactState]


@dataclass(frozen=True)
class GraphDecision:
    next_stage: StageName
    reason: str


class PolicyImpactGraph:
    max_stage_retry_count = int(os.getenv("HARNESS_STAGE_MAX_RETRY", "1"))

    def __init__(self, handlers: dict[str, StageHandler], hooks: StageHooks | None = None) -> None:
        self.handlers = handlers
        self.hooks = hooks or build_default_hooks()

    def run(self, state: PolicyImpactState) -> PolicyImpactState:
        while state.current_stage not in {"done", "failed"}:
            decision = self.decide_next_stage(state)
            state.mark_stage(decision.next_stage)

            if decision.next_stage in {"done", "failed"}:
                break

            handler = self.handlers.get(decision.next_stage)
            if handler is None:
                state.add_error(decision.next_stage, "没有注册当前阶段的处理函数", {"reason": decision.reason})
                state.mark_stage("failed")
                break
            state = self._run_stage_with_gate(state, decision.next_stage, handler)
        return state

    def decide_next_stage(self, state: PolicyImpactState) -> GraphDecision:
        stage = state.current_stage
        if stage == "initialized":
            return GraphDecision("load_company_context", "初始化完成，读取企业知识库")
        if stage == "load_company_context":
            return GraphDecision("fetch_recent_policies" if state.company_context_pack else "failed", "企业上下文已构建")
        if stage == "fetch_recent_policies":
            if state.policy_documents:
                return GraphDecision("policy_ingest_and_index", "政策已获取")
            if state.source_manifest.get("no_updates"):
                return GraphDecision("generate_weekly_report", "来源抓取成功，本周没有新增政策")
            return GraphDecision("failed", "政策来源不可用")
        if stage == "policy_ingest_and_index":
            return GraphDecision("retrieve_relevant_clauses" if state.policy_chunks else "failed", "政策索引已构建")
        if stage == "retrieve_relevant_clauses":
            return GraphDecision("extract_policy_clauses" if state.evidence_hits else "failed", "候选证据已召回")
        if stage == "extract_policy_clauses":
            return GraphDecision("match_company_policy" if state.policy_clauses else "failed", "政策条款已抽取")
        if stage == "match_company_policy":
            return GraphDecision("analyze_policy_applicability", "候选匹配已生成，进入适用性判定")
        if stage == "analyze_policy_applicability":
            return GraphDecision(
                "score_policy_impact" if state.policy_applicability else "failed",
                "政策适用性已按直接、条件、不适用和证据不足分类",
            )
        if stage == "score_policy_impact":
            return GraphDecision("review_evidence_and_risk" if state.impact_assessments else "failed", "影响评分已生成")
        if stage == "review_evidence_and_risk":
            return GraphDecision("generate_weekly_report" if state.review_result else "failed", "复核已完成")
        if stage == "generate_weekly_report":
            return GraphDecision("done" if state.report_paths else "failed", "报告已生成")
        return GraphDecision("failed", f"未知阶段：{stage}")

    def _run_stage_with_gate(
        self,
        state: PolicyImpactState,
        stage_name: str,
        handler: StageHandler,
    ) -> PolicyImpactState:
        while True:
            ctx = self.hooks.fire_before(state, stage_name)
            try:
                state = handler(state)
            except Exception as exc:  # noqa: BLE001
                self.hooks.fire_after(state, stage_name, ctx, error=repr(exc))
                state.add_error(stage_name, "阶段执行失败", repr(exc))
                state.mark_stage("failed")
                return state

            results = self.hooks.fire_after(state, stage_name, ctx)
            gate = results.get("evaluate_linter", {})
            if not gate or gate.get("passed"):
                return state

            retry_count = state.stage_retry_counts.get(stage_name, 0)
            if not gate.get("retryable") or retry_count >= self.max_stage_retry_count:
                state.add_error(stage_name, "stage gate 未通过", gate)
                state.mark_stage("failed")
                return state

            state.increment_stage_retry(stage_name)
            state.add_warning(stage_name, "stage gate 未通过，重跑当前 stage", gate)


def build_graph(handlers: dict[str, StageHandler], hooks: StageHooks | None = None) -> PolicyImpactGraph:
    return PolicyImpactGraph(handlers=handlers, hooks=hooks)
