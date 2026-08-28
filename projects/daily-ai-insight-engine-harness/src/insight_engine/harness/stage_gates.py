"""Stage gate dispatcher.

Stage gate 是运行时 linter 的调度层：Graph 在每个 stage 跑完后调用这里，
这里再分派到 `src/insight_engine/linters/{stage_name}.py`。

职责边界：

- stage 负责生成产物。
- linter 负责检查产物是否满足字段合同和进入下一阶段的最低要求。
- stage_gates 只负责按 stage_name 找到对应 linter。
- graph 根据 linter 结果决定继续、重试或失败。
"""

from __future__ import annotations

from typing import Any

from insight_engine.harness.state import InsightEngineState
from insight_engine.linters import (
    analyze_insights,
    clean_items,
    collect_raw_items,
    generate_report,
    structure_events,
)
from insight_engine.linters.common import lint_result


LINTERS = {
    "collect_raw_items": collect_raw_items.lint,
    "clean_items": clean_items.lint,
    "structure_events": structure_events.lint,
    "analyze_insights": analyze_insights.lint,
    "generate_report": generate_report.lint,
}


def evaluate_stage_gate(state: InsightEngineState, stage_name: str) -> dict[str, Any]:
    """检查某个 stage 的产物是否满足最低要求。"""
    checker = LINTERS.get(stage_name)
    if checker is None:
        return lint_result(stage_name, True, [], {"note": "no linter configured"}, retryable=False)
    return checker(state)
