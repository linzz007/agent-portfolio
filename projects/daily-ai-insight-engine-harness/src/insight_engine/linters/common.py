"""所有 stage linter 共用的返回格式工具。

这个文件不检查任何具体业务字段，只负责把各个 linter 的检查结果整理成
`graph.py` 和 `stage_hooks.py` 都能理解的统一结构。这样每个 stage linter
只需要关心自己的产物是否合格，不需要重复拼装 passed、issues、metrics 等字段。
"""

from __future__ import annotations

from typing import Any

from insight_engine.harness.state import utc_now_iso


def lint_result(
    stage_name: str,
    passed: bool,
    issues: list[str],
    metrics: dict[str, Any],
    retryable: bool,
) -> dict[str, Any]:
    """生成统一的 stage gate / linter 结果对象。

    参数含义：
    - stage_name：当前被检查的 stage 名称。
    - passed：本次检查是否通过。
    - issues：失败原因列表，给 graph、pipeline_summary 和人工排查使用。
    - metrics：辅助观察指标，不一定代表失败。
    - retryable：失败后 graph 是否允许重跑当前 stage。
    """
    return {
        "stage": stage_name,
        "passed": passed,
        "issues": issues,
        "metrics": metrics,
        "retryable": retryable,
        "checked_at": utc_now_iso(),
    }
