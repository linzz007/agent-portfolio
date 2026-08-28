"""analyze_insights stage 的运行时产物检查。

这个文件检查 Stage 4 产出的 `analysis_result` 是否足够完整，能不能被报告生成阶段
直接消费。它重点检查分析字段、Top 事件展示字段、趋势理由和风险/机会证据，
防止 LLM 或 fallback 只产出空泛总结。
"""

from __future__ import annotations

from typing import Any

from insight_engine.harness.state import (
    ANALYSIS_EVENT_DISPLAY_REQUIRED_FIELDS,
    ANALYSIS_RESULT_FIELD_SPEC,
    REQUIRED_TREND_KEYS,
    STRUCTURED_EVENT_AI_AREAS,
    STRUCTURED_EVENT_GLOBAL_AREAS,
    InsightEngineState,
)
from insight_engine.linters.common import lint_result


ANALYSIS_REQUIRED_KEYS = [
    key for key, spec in ANALYSIS_RESULT_FIELD_SPEC.items() if spec.get("required")
]


def lint(state: InsightEngineState) -> dict[str, Any]:
    """检查 Stage 4 的 analysis_result 是否符合报告阶段的输入合同。

    这个函数不是重新做分析，而是检查分析结果能不能支撑最终报告：
    - 顶层必填字段要齐全。
    - summary / summary_reason 要有可读内容。
    - AI Top 事件和 global Top 事件数量要合理。
    - Top 事件必须保留来源、时间、方向、标签、热度等展示字段。
    - 趋势判断和风险/机会提示必须带理由和 supporting_event_ids。
    """
    analysis = state.analysis_result
    issues = []
    missing = [key for key in ANALYSIS_REQUIRED_KEYS if key not in analysis]
    if missing:
        issues.append(f"analysis_result 缺少字段：{missing}")

    summary = analysis.get("summary")
    if not isinstance(summary, str) or len(summary.strip()) < 30:
        issues.append("analysis_result.summary 必须包含可读的整体判断")

    summary_reason = analysis.get("summary_reason")
    if not isinstance(summary_reason, str) or len(summary_reason.strip()) < 20:
        issues.append("analysis_result.summary_reason 必须说明整体判断依据")

    top_events = analysis.get("top_events")
    if not isinstance(top_events, list) or not 3 <= len(top_events) <= 5:
        issues.append("analysis_result.top_events 必须包含 3-5 个 AI 重点事件")
    else:
        missing_reason = [
            index
            for index, event in enumerate(top_events)
            if not isinstance(event, dict) or not event.get("selection_reason")
        ]
        if missing_reason:
            issues.append(f"analysis_result.top_events 缺少 selection_reason：{missing_reason[:5]}")
        issues.extend(
            validate_event_display_fields(
                events=top_events,
                field_name="analysis_result.top_events",
                allowed_areas=STRUCTURED_EVENT_AI_AREAS,
            )
        )

    global_top_events = analysis.get("global_top_events")
    if not isinstance(global_top_events, list) or not 1 <= len(global_top_events) <= 5:
        issues.append("analysis_result.global_top_events 必须包含 1-5 个全球背景事件")
    else:
        missing_reason = [
            index
            for index, event in enumerate(global_top_events)
            if not isinstance(event, dict) or not event.get("selection_reason")
        ]
        if missing_reason:
            issues.append(f"analysis_result.global_top_events 缺少 selection_reason：{missing_reason[:5]}")
        issues.extend(
            validate_event_display_fields(
                events=global_top_events,
                field_name="analysis_result.global_top_events",
                allowed_areas=STRUCTURED_EVENT_GLOBAL_AREAS,
            )
        )

    trend = analysis.get("trend_judgment", {})
    for key in REQUIRED_TREND_KEYS:
        value = trend.get(key) if isinstance(trend, dict) else None
        if not isinstance(value, str) or len(value.strip()) < 12:
            issues.append(f"trend_judgment 缺少有效 {key}")

    trend_reasoning = analysis.get("trend_reasoning", {})
    for key in REQUIRED_TREND_KEYS:
        reason = trend_reasoning.get(key) if isinstance(trend_reasoning, dict) else None
        if not isinstance(reason, str) or len(reason.strip()) < 15:
            issues.append(f"trend_reasoning 缺少有效 {key}")

    risk_notes = analysis.get("risk_or_opportunity_notes")
    if not isinstance(risk_notes, list) or not risk_notes:
        issues.append("risk_or_opportunity_notes 不能为空")
    else:
        missing_support = [
            index
            for index, note in enumerate(risk_notes)
            if not isinstance(note, dict) or not note.get("supporting_event_ids") or not note.get("reason")
        ]
        if missing_support:
            issues.append(f"risk_or_opportunity_notes 缺少 supporting_event_ids 或 reason：{missing_support[:5]}")

    return lint_result(
        "analyze_insights",
        not issues,
        issues,
        {
            "analysis_keys": sorted(analysis.keys()),
            "top_event_count": len(analysis.get("top_events", [])),
        },
        retryable=True,
    )


def validate_event_display_fields(
    events: list[Any],
    field_name: str,
    allowed_areas: set[str],
) -> list[str]:
    """检查 Top 事件是否保留 Stage 5 报告和图表需要的展示字段。

    Stage 5 不应该再猜 source、published_at、industry_area、topic_tags、
    hotness_score 等结构字段，所以这里提前检查 Stage 4 是否把这些字段传下来了。
    """
    issues: list[str] = []
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            issues.append(f"{field_name}[{index}] 必须是 object")
            continue

        missing = [
            key
            for key in ANALYSIS_EVENT_DISPLAY_REQUIRED_FIELDS
            if _is_missing_display_value(event.get(key))
        ]
        if missing:
            issues.append(f"{field_name}[{index}] 缺少展示字段：{missing}")

        area = str(event.get("industry_area", "")).strip()
        if area and area not in allowed_areas:
            issues.append(f"{field_name}[{index}].industry_area 不在允许范围：{area}")

        hotness = event.get("hotness_score")
        if not isinstance(hotness, int) or not 0 <= hotness <= 100:
            issues.append(f"{field_name}[{index}].hotness_score 必须是 0-100 整数")

        tags = event.get("topic_tags")
        if not isinstance(tags, list) or not any(str(tag).strip() for tag in tags):
            issues.append(f"{field_name}[{index}].topic_tags 必须是非空 list")
    return issues


def _is_missing_display_value(value: Any) -> bool:
    """判断一个展示字段是否等价于缺失。

    字符串空白、空 list 和 None 都会被视为缺失；数字、布尔值等非空标量会保留。
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return not value
    return False
