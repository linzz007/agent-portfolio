"""structure_events stage 的运行时产物检查。

这个文件检查 Stage 3 输出的 structured event 是否满足跨阶段字段合同。它是
LLM / fallback 结构化结果的硬闸门：只要数量不足、必填字段缺失、热度分非法，
graph 就不能继续进入 analyze_insights。
"""

from __future__ import annotations

import os
from typing import Any

from insight_engine.harness.state import STRUCTURED_EVENT_FIELD_SPEC, InsightEngineState
from insight_engine.linters.common import lint_result


STRUCTURED_EVENT_REQUIRED_FIELDS = [
    key for key, spec in STRUCTURED_EVENT_FIELD_SPEC.items() if spec.get("required")
]


def lint(state: InsightEngineState) -> dict[str, Any]:
    """检查 Stage 3 的 structured_events 是否满足 state.py 中的字段合同。

    这个函数合并 global / AI 两条结构化事件线，逐条检查：
    - 事件数量是否达标。
    - 是否包含 `STRUCTURED_EVENT_FIELD_SPEC` 标记为 required 的字段。
    - `hotness_score` 是否是 0-100 的整数。
    失败时 retryable=True，因为结构化阶段可能通过 LLM repair 或 fallback 重跑修复。
    """
    min_total = int(os.getenv("HARNESS_MIN_STRUCTURED_EVENTS", "10"))
    issues = []
    events = state.global_structured_events + state.ai_structured_events
    if not events:
        issues.append("结构化事件为空")
    elif len(events) < min_total:
        issues.append(f"结构化事件数量不足：{len(events)} < {min_total}")
    for index, event in enumerate(events):
        missing = [field for field in STRUCTURED_EVENT_REQUIRED_FIELDS if field not in event]
        if missing:
            issues.append(f"event[{index}] 缺少字段：{missing}")
        score = event.get("hotness_score")
        if not isinstance(score, int) or score < 0 or score > 100:
            issues.append(f"event[{index}].hotness_score 必须是 0-100 整数")
    return lint_result(
        "structure_events",
        not issues,
        issues[:20],
        {
            "global_structured_events": len(state.global_structured_events),
            "ai_structured_events": len(state.ai_structured_events),
            "total_structured_events": len(events),
            "min_total": min_total,
            "checked_events": len(events),
        },
        retryable=True,
    )
