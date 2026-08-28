"""Stage 4: ReAct 洞察分析。

这个阶段负责把 Stage 3 生成的 structured_events 分析成报告可直接使用的
analysis_result。它是一个受限 ReAct stage：LLM 不能自由调用外部工具，只能
在本文件暴露的白名单动作中选择下一步。

输入：

- state.global_structured_events: 全球热点结构化事件，用于判断今日公共舆情背景。
- state.ai_structured_events: AI 领域结构化事件，用于判断 AI 专业热点。

输出：

- state.analysis_result: 面向报告生成阶段的分析结果，包含 summary、Top 事件、
  趋势判断、风险或机会提示、理由字段和统计信息。
- data/processed/{run_id}/analysis_result.json: Stage 4 的可审计产物。
- data/react/{run_id}/analyze_insights/step_*.json: 每一步 ReAct prompt 和响应。

执行步骤：

1. 读取输入和准备 fallback：
   从 State 读取 global_structured_events / ai_structured_events，并先生成一份
   规则 fallback analysis_result。这样即使 LLM 不可用，流程也能继续运行。

2. 启动受限 ReAct loop：
   如果配置了 DEEPSEEK_API_KEY，则进入固定目标的 ReAct 计划；
   每一步都让 LLM 只输出 JSON action，而不是自由文本。

3. 执行白名单动作：
   LLM 只能选择 inspect_event_stats、select_top_events、build_trend_signals、
   finish 四类动作。当前计划强制按“统计 -> AI Top -> global Top -> 趋势 ->
   finish”执行。前三类动作由本地确定性函数执行，并把 observation 回填给
   下一轮 prompt；finish 必须给出完整 analysis_result，并为关键判断输出 reason。

4. 校验和落库：
   finish 输出会经过 validate_analysis_result 检查；通过后写入
   state.analysis_result 和 analysis_result.json。如果 LLM 超时、动作错误、
   超过最大步数或结果不合格，则使用 fallback，并在 warnings/react_trace 中留痕。
   后续轮次会拿到完整 ReAct 上下文，包括历史 action payload、observation 和
   linter feedback。

主要函数对应关系：

- analyze_insights(): Stage 4 入口，负责从 State 取输入、调用 ReAct 或 fallback、
  写回 state.analysis_result，并保存 analysis_result.json。
- run_analysis_react_loop(): 受限 ReAct 主循环，负责让 LLM 选择 action、执行动作、
  记录 react_trace，并在 finish 时返回合格分析结果。
- build_analysis_react_prompt(): 构造每一步 ReAct prompt，注入 skill、可用动作、
  当前事件摘要和已有 observations。
- ANALYSIS_RESULT_FIELD_SPEC: 从 state.analysis_result 字段合同导入，集中展示
  analysis_result 必须包含哪些字段、字段用途和嵌套子字段。
- ANALYSIS_FIELD_FILL_PLAN: 展示 ReAct 每一步为哪些 analysis_result 字段准备材料。
- inspect_event_stats(): 本地动作，统计事件数量、领域分布、来源分布和标签分布。
- select_top_events(): 本地动作，按 hotness_score 选择 global/ai/all Top 事件。
- build_trend_signals(): 本地动作，生成技术、应用、政策、资本四类趋势信号。
- validate_analysis_result(): Stage 4 的本地结果 linter，检查 analysis_result
  是否包含 Top 事件、趋势判断、风险机会提示和统计字段。
- build_fallback_analysis(): LLM 不可用或 ReAct 失败时的确定性兜底分析。
- write_react_artifact(): 保存每一步 ReAct prompt/响应，方便审计和复盘。
"""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from insight_engine.harness.artifacts import ensure_run_dir, write_json_artifact
from insight_engine.harness.hooks.after_llm_call import parse_json_output
from insight_engine.harness.llm_client import OpenAICompatibleChatClient
from insight_engine.harness.prompt_builder import build_retry_feedback
from insight_engine.harness.state import (
    ANALYSIS_EVENT_DISPLAY_REQUIRED_FIELDS,
    ANALYSIS_RESULT_FIELD_SPEC,
    REQUIRED_TREND_KEYS,
    STRUCTURED_EVENT_AI_AREAS,
    STRUCTURED_EVENT_GLOBAL_AREAS,
    InsightEngineState,
)


ANALYSIS_OBJECTIVES = [
    "识别今日 AI 领域 Top 3-5 重要事件，并说明为什么重要。",
    "识别今日全球热点背景，判断它对 AI 舆情或产业环境是否有参考价值。",
    "从技术、应用、政策、资本四个方向给出趋势判断。",
    "识别风险或机会提示，并绑定 supporting_event_ids。",
    "为整体判断、Top 事件、趋势判断、风险机会提示输出 reason 字段，说明判断依据。",
    "输出可被报告阶段直接填入模板的 analysis_result。",
]
REACT_PLAN = [
    {
        "step": 1,
        "action": "inspect_event_stats",
        "args": {},
        "goal": "先看事件总量、领域分布、来源分布和标签分布。",
        "fills": ["stats"],
    },
    {
        "step": 2,
        "action": "select_top_events",
        "args": {"scope": "ai", "limit": 5},
        "goal": "选择今日 AI 领域 Top 事件，作为报告主线。",
        "fills": [
            "top_events[].id",
            "top_events[].title",
            "top_events[].selection_reason",
            "top_events[].source_name",
            "top_events[].source_type",
            "top_events[].published_at",
            "top_events[].industry_area",
            "top_events[].topic_tags",
            "top_events[].hotness_score",
            "top_events[].importance_level",
        ],
    },
    {
        "step": 3,
        "action": "select_top_events",
        "args": {"scope": "global", "limit": 5},
        "goal": "选择今日全球背景 Top 事件，作为舆情参照。",
        "fills": [
            "global_top_events[].id",
            "global_top_events[].title",
            "global_top_events[].selection_reason",
            "global_top_events[].source_name",
            "global_top_events[].source_type",
            "global_top_events[].published_at",
            "global_top_events[].industry_area",
            "global_top_events[].topic_tags",
            "global_top_events[].hotness_score",
            "global_top_events[].importance_level",
        ],
    },
    {
        "step": 4,
        "action": "build_trend_signals",
        "args": {},
        "goal": "聚合技术、应用、政策、资本四类趋势和风险机会提示。",
        "fills": [
            "trend_judgment",
            "trend_reasoning",
            "risk_or_opportunity_notes[].note",
            "risk_or_opportunity_notes[].reason",
            "risk_or_opportunity_notes[].supporting_event_ids",
        ],
    },
    {
        "step": 5,
        "action": "finish",
        "args": {},
        "goal": "综合前面 observations，输出完整 analysis_result。",
        "fills": ["summary", "summary_reason", "react_mode", "完整 analysis_result"],
    },
]

ANALYSIS_FIELD_FILL_PLAN = {
    "step_1_inspect_event_stats": {
        "action": "inspect_event_stats",
        "prepares_fields": ["stats"],
        "note": "统计结果必须原样或等价进入 analysis_result.stats。",
    },
    "step_2_select_ai_top_events": {
        "action": "select_top_events",
        "args": {"scope": "ai", "limit": 5},
        "prepares_fields": ["top_events"],
        "note": "AI Top 事件必须带 selection_reason，并保留 source、published_at、industry_area、topic_tags、hotness_score、importance_level 等展示字段。",
    },
    "step_3_select_global_top_events": {
        "action": "select_top_events",
        "args": {"scope": "global", "limit": 5},
        "prepares_fields": ["global_top_events"],
        "note": "global Top 事件用于舆情背景，必须带 selection_reason，并保留 source、published_at、industry_area、topic_tags、hotness_score、importance_level 等展示字段。",
    },
    "step_4_build_trend_signals": {
        "action": "build_trend_signals",
        "prepares_fields": ["trend_judgment", "trend_reasoning", "risk_or_opportunity_notes"],
        "note": "趋势和风险机会必须绑定 observation 或 supporting_event_ids。",
    },
    "step_5_finish": {
        "action": "finish",
        "prepares_fields": ["summary", "summary_reason", "react_mode"],
        "note": "finish 必须输出完整 analysis_result，随后立即由 validate_analysis_result 校验。",
    },
}


def analyze_insights(state: InsightEngineState) -> InsightEngineState:
    """执行 ReAct 洞察分析阶段。"""
    global_events = state.global_structured_events
    ai_events = state.ai_structured_events
    all_events = global_events + ai_events
    require_llm = os.getenv("ANALYZE_INSIGHTS_REQUIRE_LLM", "").lower() in {"1", "true", "yes"}
    use_llm = require_llm or os.getenv("ANALYZE_INSIGHTS_USE_LLM", "").lower() in {"1", "true", "yes"}
    client = OpenAICompatibleChatClient.from_deepseek_env() if use_llm else None

    fallback = build_fallback_analysis(
        global_events=global_events,
        ai_events=ai_events,
    )
    if not all_events:
        state.analysis_result = fallback
        return state

    react_trace: list[dict[str, Any]] = []
    if client is None and not use_llm:
        analysis_result = fallback
        react_trace.extend(build_fallback_analysis_trace(reason="fallback_llm_disabled"))
    elif client is None:
        state.add_warning("analyze_insights", "未配置 DEEPSEEK_API_KEY，analyze_insights 使用规则 fallback")
        analysis_result = fallback
        react_trace.extend(build_fallback_analysis_trace(reason="fallback_no_api_key"))
    else:
        try:
            analysis_result = run_analysis_react_loop(
                state=state,
                client=client,
                global_events=global_events,
                ai_events=ai_events,
                fallback=fallback,
                react_trace=react_trace,
            )
        except Exception as exc:  # noqa: BLE001
            if require_llm:
                raise
            state.add_warning(
                "analyze_insights",
                "LLM ReAct 分析失败，使用规则 fallback",
                detail=repr(exc),
            )
            analysis_result = fallback
            react_trace.extend(build_fallback_analysis_trace(reason="fallback_after_error", error=repr(exc)))

    state.analysis_result = analysis_result
    write_json_artifact(
        state=state,
        artifact_name="analysis_result",
        data={
            "run_id": state.run_id,
            "target_date": state.target_date,
            "input_counts": {
                "global_structured_events": len(global_events),
                "ai_structured_events": len(ai_events),
                "total_structured_events": len(all_events),
            },
            "llm_enabled": client is not None,
            "analysis_result_field_spec": ANALYSIS_RESULT_FIELD_SPEC,
            "analysis_field_fill_plan": ANALYSIS_FIELD_FILL_PLAN,
            "react_trace": react_trace,
            "analysis": analysis_result,
        },
        base_dir="data/processed",
        filename="analysis_result.json",
    )

    return state


def run_analysis_react_loop(
    state: InsightEngineState,
    client: OpenAICompatibleChatClient,
    global_events: list[dict[str, Any]],
    ai_events: list[dict[str, Any]],
    fallback: dict[str, Any],
    react_trace: list[dict[str, Any]],
) -> dict[str, Any]:
    """运行受限 ReAct loop。"""
    max_steps = max(int(os.getenv("ANALYZE_REACT_MAX_STEPS", "7")), len(REACT_PLAN) + 2)
    observations: list[dict[str, Any]] = []
    react_context: list[dict[str, Any]] = []
    tool_state: dict[str, Any] = {}

    for step_index in range(1, max_steps + 1):
        expected_step = expected_react_step(step_index)
        prompt = build_analysis_react_prompt(
            global_events=global_events,
            ai_events=ai_events,
            observations=observations,
            react_context=react_context,
            step_index=step_index,
            expected_step=expected_step,
        )
        # 重试反馈：告诉 LLM 上一次为什么被 linter 拦截
        retry_note = ""
        if step_index == 1:
            retry_note = build_retry_feedback(state, "analyze_insights")

        response = client.chat_json(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是受 Harness 约束的分析 Agent。"
                        "你必须用 JSON 选择动作，不能调用未列出的工具，不能编造证据。"
                        + retry_note
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )
        response_path = write_react_artifact(
            state=state,
            stage_name="analyze_insights",
            step_index=step_index,
            data={
                "prompt": prompt,
                "model": response.model,
                "content": response.content,
                "raw_response": response.raw_response,
            },
        )
        action_payload = parse_react_action(response.content)
        action = action_payload.get("action")
        thought = action_payload.get("thought", "")
        args = action_payload.get("args") or {}
        order_errors = validate_react_action_order(
            action=action,
            args=args,
            expected_step=expected_step,
        )
        if order_errors:
            observation = {"action": "linter", "observation": {"errors": order_errors}}
            observations.append(observation)
            react_context.append(
                {
                    "step": step_index,
                    "expected": expected_step,
                    "action_payload": action_payload,
                    "response_artifact": str(response_path),
                    "observation": observation,
                }
            )
            react_trace.append(
                {
                    "step": step_index,
                    "thought": thought,
                    "action": action,
                    "args": args,
                    "expected": expected_step,
                    "response_artifact": str(response_path),
                    "observation": observation,
                }
            )
            continue

        if action == "inspect_event_stats":
            observation = inspect_event_stats(global_events=global_events, ai_events=ai_events)
            observations.append({"action": action, "args": args, "observation": observation})
            tool_state["event_stats"] = observation
        elif action == "select_top_events":
            args = dict(expected_step.get("args") or args)
            observation = select_top_events(
                global_events=global_events,
                ai_events=ai_events,
                scope=str(args.get("scope", "ai")),
                limit=int(args.get("limit", 5)),
            )
            observations.append({"action": action, "args": args, "observation": observation})
            tool_state[f"top_events_{args.get('scope', 'ai')}"] = observation
        elif action == "build_trend_signals":
            observation = build_trend_signals(global_events=global_events, ai_events=ai_events)
            observations.append({"action": action, "args": args, "observation": observation})
            tool_state["trend_signals"] = observation
        elif action == "finish":
            analysis_result = action_payload.get("analysis_result")
            errors = validate_analysis_result(analysis_result, observations=observations)
            react_context.append(
                {
                    "step": step_index,
                    "expected": expected_step,
                    "action_payload": action_payload,
                    "response_artifact": str(response_path),
                    "validation_errors": errors,
                }
            )
            react_trace.append(
                {
                    "step": step_index,
                    "thought": thought,
                    "action": action,
                    "expected": expected_step,
                    "response_artifact": str(response_path),
                    "validation_errors": errors,
                }
            )
            if not errors:
                return normalize_analysis_result(analysis_result)
            observations.append({"action": "linter", "observation": {"errors": errors}})
        else:
            observations.append({"action": "linter", "observation": {"errors": [f"未知 action: {action}"]}})

        if action != "finish":
            react_context.append(
                {
                    "step": step_index,
                    "expected": expected_step,
                    "action_payload": action_payload,
                    "response_artifact": str(response_path),
                    "observation": observations[-1] if observations else None,
                }
            )

        react_trace.append(
            {
                "step": step_index,
                "thought": thought,
                "action": action,
                "args": args,
                "response_artifact": str(response_path),
                "observation": observations[-1] if observations else None,
            }
        )

    react_trace.append({"status": "fallback_after_max_steps", "tool_state_keys": sorted(tool_state.keys())})
    return fallback


def build_analysis_react_prompt(
    global_events: list[dict[str, Any]],
    ai_events: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    react_context: list[dict[str, Any]],
    step_index: int,
    expected_step: dict[str, Any],
) -> str:
    """构造分析 ReAct prompt。"""
    return "\n".join(
        [
            f"# analyze_insights ReAct Step {step_index}",
            "",
            "你要分析今日 global 与 AI 结构化事件，最终输出 analysis_result。",
            "每一步只能选择一个 action，并且必须严格执行本轮指定动作。",
            "",
            "## 分析目标",
            json.dumps(ANALYSIS_OBJECTIVES, ensure_ascii=False, indent=2),
            "",
            "## 固定 ReAct 计划",
            json.dumps(REACT_PLAN, ensure_ascii=False, indent=2),
            "",
            "## 字段填充计划",
            "每一步不一定直接写 analysis_result，但必须为对应字段准备充分材料。",
            "finish 时要把这些材料组装成完整 analysis_result；字段缺失会被 linter 拦截。",
            json.dumps(ANALYSIS_FIELD_FILL_PLAN, ensure_ascii=False, indent=2),
            "",
            "## 本轮必须执行",
            json.dumps(expected_step, ensure_ascii=False, indent=2),
            "",
            "## analysis_result 字段合同",
            "finish 输出必须满足这个字段合同；字段缺失或理由字段太短会被本地 linter 拦截。",
            json.dumps(ANALYSIS_RESULT_FIELD_SPEC, ensure_ascii=False, indent=2),
            "",
            "## 可用 actions",
            json.dumps(
                {
                    "inspect_event_stats": {"args": {}},
                    "select_top_events": {"args": {"scope": "global|ai|all", "limit": 3}},
                    "build_trend_signals": {"args": {}},
                    "finish": {"args": {}, "requires": "analysis_result"},
                },
                ensure_ascii=False,
                indent=2,
            ),
            "",
            "## 输出格式",
            "只能输出 JSON object：",
            json.dumps(
                {
                    "thought": "简短说明本轮如何完成指定目标",
                    "action": expected_step["action"],
                    "args": expected_step.get("args", {}),
                },
                ensure_ascii=False,
                indent=2,
            ),
            "",
            "finish 时必须输出：",
            json.dumps({"thought": "...", "action": "finish", "analysis_result": analysis_schema_example()}, ensure_ascii=False, indent=2),
            "",
            "## 当前事件摘要",
            json.dumps(
                {
                    "global_events": compact_events(global_events, limit=len(global_events)),
                    "ai_events": compact_events(ai_events, limit=len(ai_events)),
                },
                ensure_ascii=False,
                indent=2,
            ),
            "",
            "## 完整 ReAct 上下文",
            "下面是本阶段此前所有 LLM action payload、observation 和 linter feedback。",
            "你必须基于这些上下文继续，不要忽略前面已经得到的结果。",
            json.dumps(react_context, ensure_ascii=False, indent=2),
            "",
            "## 已有 observations",
            json.dumps(observations, ensure_ascii=False, indent=2),
        ]
    )


def expected_react_step(step_index: int) -> dict[str, Any]:
    """返回当前 ReAct 步骤必须执行的动作；计划外步骤只能继续 finish 修复。"""
    if step_index <= len(REACT_PLAN):
        return dict(REACT_PLAN[step_index - 1])
    return {
        "step": step_index,
        "action": "finish",
        "args": {},
        "goal": "根据 linter feedback 修复 analysis_result，直到满足所有分析目标。",
    }


def validate_react_action_order(
    action: Any,
    args: dict[str, Any],
    expected_step: dict[str, Any],
) -> list[str]:
    """检查 LLM 是否严格执行固定 ReAct 计划。"""
    errors: list[str] = []
    expected_action = expected_step.get("action")
    if action != expected_action:
        errors.append(f"本轮必须执行 action={expected_action}，实际为 {action}")
        return errors

    expected_args = expected_step.get("args") or {}
    for key, expected_value in expected_args.items():
        actual_value = args.get(key)
        if str(actual_value) != str(expected_value):
            errors.append(f"本轮 action 参数 {key} 必须为 {expected_value!r}，实际为 {actual_value!r}")
    return errors


def build_fallback_analysis(
    global_events: list[dict[str, Any]],
    ai_events: list[dict[str, Any]],
) -> dict[str, Any]:
    """规则 fallback 分析。"""
    all_events = global_events + ai_events
    ai_top = sort_events(ai_events)[:5]
    global_top = sort_events(global_events)[:5]
    all_top = sort_events(all_events)[:5]
    stats = inspect_event_stats(global_events=global_events, ai_events=ai_events)
    trend_signals = build_trend_signals(global_events=global_events, ai_events=ai_events)

    return {
        "summary": (
            f"本次共整理 {len(all_events)} 条结构化事件，其中 global {len(global_events)} 条、"
            f"AI {len(ai_events)} 条。AI 热点主要用于专业洞察，global 热点用于舆情背景参照。"
        ),
        "summary_reason": (
            "该整体判断来自结构化事件数量、AI/global 两条数据线的分布，以及 Top 事件和趋势信号的聚合结果。"
        ),
        "global_top_events": [summarize_event(event) for event in global_top],
        "top_events": [summarize_event(event) for event in ai_top or all_top],
        "trend_judgment": trend_signals["trend_judgment"],
        "trend_reasoning": build_trend_reasoning(trend_signals=trend_signals, ai_events=ai_events),
        "risk_or_opportunity_notes": trend_signals["risk_or_opportunity_notes"],
        "stats": stats,
        "react_mode": "fallback_rules",
    }


def build_fallback_analysis_trace(reason: str, error: str | None = None) -> list[dict[str, Any]]:
    """让无 LLM 路径也保留可读的 ReAct 轨迹。"""
    trace: list[dict[str, Any]] = [
        {
            "step": 1,
            "mode": "fallback",
            "thought": "先观察事件分布，决定报告需要哪些统计口径。",
            "action": "inspect_event_stats",
            "observation": "stats",
        },
        {
            "step": 2,
            "mode": "fallback",
            "thought": "根据热度、重要性和来源选择 AI Top 事件。",
            "action": "select_top_events",
            "args": {"scope": "ai", "limit": 5},
            "observation": "ai_top_events",
        },
        {
            "step": 3,
            "mode": "fallback",
            "thought": "根据热度、重要性和来源选择 global Top 事件。",
            "action": "select_top_events",
            "args": {"scope": "global", "limit": 5},
            "observation": "global_top_events",
        },
        {
            "step": 4,
            "mode": "fallback",
            "thought": "把事件领域聚合成技术、应用、政策、资本四类趋势信号。",
            "action": "build_trend_signals",
            "observation": "trend_signals",
        },
        {
            "step": 5,
            "mode": "fallback",
            "thought": "本地规则已经得到满足 schema 的 analysis_result。",
            "action": "finish",
            "status": reason,
        },
    ]
    if error:
        trace[-1]["error"] = error
    return trace


def inspect_event_stats(
    global_events: list[dict[str, Any]],
    ai_events: list[dict[str, Any]],
) -> dict[str, Any]:
    """统计事件分布：按行业、来源、标签维度汇总 global 和 AI 事件。"""
    all_events = global_events + ai_events
    area_counts = Counter(str(event.get("industry_area", "other")) for event in all_events)
    ai_area_counts = Counter(str(event.get("industry_area", "other")) for event in ai_events)
    global_area_counts = Counter(str(event.get("industry_area", "other")) for event in global_events)
    source_counts = Counter(str(event.get("source_type", "unknown")) for event in all_events)
    tag_counts = Counter(tag for event in all_events for tag in event.get("topic_tags", []))
    return {
        "total_events": len(all_events),
        "global_events": len(global_events),
        "ai_events": len(ai_events),
        "area_counts": dict(area_counts),
        "ai_area_counts": dict(ai_area_counts),
        "global_area_counts": dict(global_area_counts),
        "source_counts": dict(source_counts),
        "top_tags": dict(tag_counts.most_common(10)),
    }


def select_top_events(
    global_events: list[dict[str, Any]],
    ai_events: list[dict[str, Any]],
    scope: str,
    limit: int,
) -> list[dict[str, Any]]:
    """按 scope 和热度选择 Top 事件，返回精简后的事件摘要列表。"""
    if scope == "global":
        source = global_events
    elif scope == "all":
        source = global_events + ai_events
    else:
        source = ai_events
    return [summarize_event(event) for event in sort_events(source)[:limit]]


def build_trend_signals(
    global_events: list[dict[str, Any]],
    ai_events: list[dict[str, Any]],
) -> dict[str, Any]:
    """按技术/应用/政策/资本四维度聚合趋势信号和风险提示。"""
    ai_counts = Counter(str(event.get("industry_area", "other")) for event in ai_events)
    global_counts = Counter(str(event.get("industry_area", "other")) for event in global_events)
    risk_notes = build_risk_or_opportunity_notes(ai_events)
    return {
        "trend_judgment": {
            "technology": trend_text(ai_counts, ["foundation_model", "ai_infra", "research"], "技术侧"),
            "application": trend_text(ai_counts, ["ai_app", "robotics"], "应用侧"),
            "policy": trend_text(ai_counts, ["policy", "security"], "政策与安全侧"),
            "capital": trend_text(ai_counts, ["investment"], "资本侧"),
        },
        "global_context": {
            "dominant_global_areas": dict(global_counts.most_common(5)),
            "summary": "global 事件用于判断今日公共议题背景，不直接等同于 AI 专业热点。",
        },
        "risk_or_opportunity_notes": risk_notes,
    }


def trend_text(area_counts: Counter[str], areas: list[str], label: str) -> str:
    """根据领域计数生成趋势描述文本。"""
    count = sum(area_counts.get(area, 0) for area in areas)
    if count:
        return f"{label}出现 {count} 条相关信号，需要结合 Top 事件继续观察。"
    return f"{label}本次没有明显集中信号，暂不做强判断。"


def build_risk_or_opportunity_notes(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按行业方向聚合事件并生成风险或机会注释。"""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        grouped[str(event.get("industry_area", "other"))].append(event)

    notes: list[dict[str, Any]] = []
    for area, area_events in grouped.items():
        if area == "policy":
            note = "监管、安全和合规事件可能提高 AI 产品部署门槛。"
        elif area == "ai_infra":
            note = "AI 基础设施事件可能影响算力成本、推理效率或供应链。"
        elif area == "foundation_model":
            note = "基础模型事件可能影响模型竞争格局和产品能力边界。"
        elif area == "investment":
            note = "资本事件可能提示市场对某类 AI 方向的短期偏好。"
        else:
            note = "该方向需要结合更多来源继续观察。"
        notes.append(
            {
                "area": area,
                "note": note,
                "reason": f"{area} 方向出现 {len(area_events)} 条相关事件，因此需要在报告中提示对应风险或机会。",
                "supporting_event_ids": [event.get("id") for event in area_events[:5]],
            }
        )
    return notes


def build_trend_reasoning(
    trend_signals: dict[str, Any],
    ai_events: list[dict[str, Any]],
) -> dict[str, str]:
    """为四类趋势判断生成可审计理由，帮助 LLM/fallback 避免空泛结论。"""
    ai_counts = Counter(str(event.get("industry_area", "other")) for event in ai_events)
    return {
        "technology": (
            "依据 foundation_model、ai_infra、research 等技术相关事件数量判断；"
            f"当前相关分布为 {dict(ai_counts)}。"
        ),
        "application": (
            "依据 ai_app、robotics 等应用相关事件数量判断；"
            f"当前相关分布为 {dict(ai_counts)}。"
        ),
        "policy": (
            "依据 policy、security 等监管与安全相关事件数量判断；"
            f"当前相关分布为 {dict(ai_counts)}。"
        ),
        "capital": (
            "依据 investment 等融资和资本市场相关事件数量判断；"
            f"当前相关分布为 {dict(ai_counts)}。"
        ),
    }


def validate_analysis_result(
    payload: Any,
    observations: list[dict[str, Any]] | None = None,
) -> list[str]:
    """校验 analysis_result 是否完成分析目标，而不只是字段存在。"""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["analysis_result 必须是 object"]

    summary = payload.get("summary")
    if not isinstance(summary, str) or len(summary.strip()) < 30:
        errors.append("summary 必须是至少 30 字符的整体判断")
    summary_reason = payload.get("summary_reason")
    if not isinstance(summary_reason, str) or len(summary_reason.strip()) < 20:
        errors.append("summary_reason 必须说明整体判断依据")

    top_events = payload.get("top_events")
    if not isinstance(top_events, list):
        errors.append("top_events 必须是 list")
    elif not 3 <= len(top_events) <= 5:
        errors.append("top_events 必须包含 3-5 个 AI 重点事件")
    else:
        errors.extend(
            validate_event_summaries(
                top_events,
                "top_events",
                allowed_areas=STRUCTURED_EVENT_AI_AREAS,
            )
        )

    global_top_events = payload.get("global_top_events")
    if not isinstance(global_top_events, list):
        errors.append("global_top_events 必须是 list")
    elif not 1 <= len(global_top_events) <= 5:
        errors.append("global_top_events 必须包含 1-5 个全球背景事件")
    else:
        errors.extend(
            validate_event_summaries(
                global_top_events,
                "global_top_events",
                allowed_areas=STRUCTURED_EVENT_GLOBAL_AREAS,
            )
        )

    if not isinstance(payload.get("trend_judgment"), dict):
        errors.append("缺少 trend_judgment object")
    else:
        missing = [key for key in REQUIRED_TREND_KEYS if key not in payload["trend_judgment"]]
        if missing:
            errors.append(f"trend_judgment 缺少字段：{missing}")
        for key in REQUIRED_TREND_KEYS:
            value = payload["trend_judgment"].get(key)
            if not isinstance(value, str) or len(value.strip()) < 12:
                errors.append(f"trend_judgment.{key} 必须是有内容的趋势判断")

    trend_reasoning = payload.get("trend_reasoning")
    if not isinstance(trend_reasoning, dict):
        errors.append("trend_reasoning 必须是 object")
    else:
        for key in REQUIRED_TREND_KEYS:
            reason = trend_reasoning.get(key)
            if not isinstance(reason, str) or len(reason.strip()) < 15:
                errors.append(f"trend_reasoning.{key} 必须说明趋势判断依据")

    if not isinstance(payload.get("risk_or_opportunity_notes"), list):
        errors.append("risk_or_opportunity_notes 必须是 list")
    elif not payload["risk_or_opportunity_notes"]:
        errors.append("risk_or_opportunity_notes 不能为空")
    else:
        errors.extend(validate_risk_notes(payload["risk_or_opportunity_notes"]))

    if not isinstance(payload.get("stats"), dict):
        errors.append("stats 必须是 object")
    elif "total_events" not in payload["stats"]:
        errors.append("stats 必须包含 total_events")

    if observations is not None:
        errors.extend(validate_required_observations(observations))

    return errors


def validate_event_summaries(
    events: list[Any],
    field_name: str,
    allowed_areas: set[str],
) -> list[str]:
    """检查 Top 事件是否能被报告阶段直接引用。"""
    errors: list[str] = []
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            errors.append(f"{field_name}[{index}] 必须是 object")
            continue
        missing = [
            key
            for key in ANALYSIS_EVENT_DISPLAY_REQUIRED_FIELDS
            if is_missing_display_value(event.get(key))
        ]
        if missing:
            errors.append(f"{field_name}[{index}] 缺少报告展示字段：{missing}")
        area = str(event.get("industry_area", "")).strip()
        if area and area not in allowed_areas:
            errors.append(f"{field_name}[{index}].industry_area 不在允许范围：{area}")
        hotness = event.get("hotness_score")
        if not isinstance(hotness, int) or not 0 <= hotness <= 100:
            errors.append(f"{field_name}[{index}].hotness_score 必须是 0-100 整数")
        tags = event.get("topic_tags")
        if not isinstance(tags, list) or not any(str(tag).strip() for tag in tags):
            errors.append(f"{field_name}[{index}].topic_tags 必须是非空 list")
        if not event.get("impact_analysis") and not event.get("risk_or_opportunity"):
            errors.append(f"{field_name}[{index}] 必须包含 impact_analysis 或 risk_or_opportunity")
    return errors


def is_missing_display_value(value: Any) -> bool:
    """判断展示字段是否缺失。"""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return not value
    return False


def validate_risk_notes(notes: list[Any]) -> list[str]:
    """检查风险机会提示是否绑定支撑事件。"""
    errors: list[str] = []
    for index, note in enumerate(notes):
        if not isinstance(note, dict):
            errors.append(f"risk_or_opportunity_notes[{index}] 必须是 object")
            continue
        if not note.get("area"):
            errors.append(f"risk_or_opportunity_notes[{index}] 缺少 area")
        if not isinstance(note.get("note"), str) or len(note.get("note", "").strip()) < 12:
            errors.append(f"risk_or_opportunity_notes[{index}].note 太短")
        if not isinstance(note.get("reason"), str) or len(note.get("reason", "").strip()) < 15:
            errors.append(f"risk_or_opportunity_notes[{index}].reason 必须说明判断依据")
        supporting_ids = note.get("supporting_event_ids")
        if not isinstance(supporting_ids, list) or not supporting_ids:
            errors.append(f"risk_or_opportunity_notes[{index}] 必须包含 supporting_event_ids")
    return errors


def validate_required_observations(observations: list[dict[str, Any]]) -> list[str]:
    """检查 finish 前是否已经完成固定 ReAct 计划中的观察动作。"""
    observed_actions = [item.get("action") for item in observations]
    if "inspect_event_stats" not in observed_actions:
        return ["finish 前必须先执行 inspect_event_stats"]

    selected_scopes = {
        str((item.get("args") or {}).get("scope"))
        for item in observations
        if item.get("action") == "select_top_events"
    }
    errors: list[str] = []
    if "ai" not in selected_scopes:
        errors.append("finish 前必须选择 AI Top 事件")
    if "global" not in selected_scopes:
        errors.append("finish 前必须选择 global Top 事件")
    if "build_trend_signals" not in observed_actions:
        errors.append("finish 前必须执行 build_trend_signals")
    return errors


def normalize_analysis_result(payload: Any) -> dict[str, Any]:
    """补全分析结果中的默认字段，确保下游依赖键存在。"""
    assert isinstance(payload, dict)
    payload.setdefault("summary", "本次分析由 ReAct loop 生成。")
    payload.setdefault("summary_reason", "该整体判断基于已执行的 ReAct observations、Top 事件和趋势信号。")
    payload.setdefault("global_top_events", [])
    payload.setdefault("trend_reasoning", {})
    payload.setdefault("react_mode", "llm_react")
    return payload


def parse_react_action(text: str) -> dict[str, Any]:
    """把 LLM 文本解析为 ReAct action JSON object。"""
    payload = parse_json_output(text)
    if not isinstance(payload, dict):
        raise ValueError("ReAct 输出必须是 JSON object")
    return payload


def analysis_schema_example() -> dict[str, Any]:
    """返回 analysis_result 的 schema 示例，用于给 LLM 提供输出格式参考。"""
    return {
        "summary": "一句完整的整体判断，说明今日 AI 热点主线、全球背景和主要风险机会。",
        "summary_reason": "说明为什么得出这个整体判断，例如引用 stats、Top 事件和趋势信号。",
        "global_top_events": [
            {
                "id": "event_global_1",
                "title": "全球背景事件标题",
                "source_name": "global_source",
                "source_type": "media",
                "url": "https://example.com/global",
                "published_at": "2026-05-19T09:00:00+00:00",
                "industry_area": "politics",
                "topic_tags": ["politics"],
                "hotness_score": 80,
                "importance_level": "high",
                "summary": "全球背景事件摘要",
                "selection_reason": "说明为什么这个事件可以代表今日全球背景。",
                "impact_analysis": "说明这个全球背景事件为什么会影响今日舆情或产业环境。",
            }
        ],
        "top_events": [
            {
                "id": "event_ai_1",
                "title": "AI 重点事件标题",
                "source_name": "ai_source",
                "source_type": "media",
                "url": "https://example.com/ai",
                "published_at": "2026-05-19T09:00:00+00:00",
                "industry_area": "foundation_model",
                "topic_tags": ["ai", "model"],
                "hotness_score": 90,
                "importance_level": "high",
                "summary": "AI 重点事件摘要",
                "selection_reason": "说明为什么这个事件应该进入今日 AI Top 事件。",
                "impact_analysis": "说明这个事件为什么重要，以及它影响哪个方向。",
            }
        ],
        "trend_judgment": {
            "technology": "技术方向的具体趋势判断，必须绑定输入事件。",
            "application": "应用方向的具体趋势判断，必须绑定输入事件。",
            "policy": "政策方向的具体趋势判断，必须绑定输入事件。",
            "capital": "资本方向的具体趋势判断，必须绑定输入事件。",
        },
        "trend_reasoning": {
            "technology": "说明技术趋势判断依据，引用观察结果或事件 id。",
            "application": "说明应用趋势判断依据，引用观察结果或事件 id。",
            "policy": "说明政策趋势判断依据，引用观察结果或事件 id。",
            "capital": "说明资本趋势判断依据，引用观察结果或事件 id。",
        },
        "risk_or_opportunity_notes": [
            {
                "area": "policy",
                "note": "风险或机会提示，不能只写空泛判断。",
                "reason": "说明为什么这是风险或机会，例如来自哪些事件或统计信号。",
                "supporting_event_ids": ["event_ai_1"],
            }
        ],
        "stats": {"total_events": 0, "global_events": 0, "ai_events": 0},
    }


def compact_events(events: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """按热度排序后取 Top N 并精简字段，减少 prompt 体积。"""
    return [summarize_event(event) for event in sort_events(events)[:limit]]


def summarize_event(event: dict[str, Any]) -> dict[str, Any]:
    """把完整事件精简为核心展示字段。"""
    return {
        "id": event.get("id"),
        "source_scope": event.get("source_scope"),
        "title": event.get("title"),
        "source_name": event.get("source_name"),
        "source_type": event.get("source_type"),
        "url": event.get("url"),
        "published_at": event.get("published_at"),
        "industry_area": event.get("industry_area"),
        "topic_tags": event.get("topic_tags", []),
        "hotness_score": event.get("hotness_score"),
        "importance_level": event.get("importance_level"),
        "summary": event.get("summary"),
        "selection_reason": (
            f"该事件 hotness_score={event.get('hotness_score')}，importance_level={event.get('importance_level')}，"
            "并且包含可追踪来源和摘要，适合作为本轮分析候选。"
        ),
        "impact_analysis": event.get("impact_analysis"),
        "risk_or_opportunity": event.get("risk_or_opportunity"),
    }


def sort_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 hotness_score 降序排列事件。"""
    return sorted(events, key=lambda event: int(event.get("hotness_score") or 0), reverse=True)


def write_react_artifact(
    state: InsightEngineState,
    stage_name: str,
    step_index: int,
    data: dict[str, Any],
) -> Path:
    """保存 ReAct 每一步的 prompt/响应产物到 data/react/{run_id}/ 目录。"""
    output_dir = ensure_run_dir("data/react", state.run_id) / stage_name
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"step_{step_index}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    state.add_artifact(f"react:{stage_name}:step_{step_index}", str(path))
    return path
