"""collect_raw_items stage 的运行时产物检查。

这个文件检查 Stage 1 是否真正抓到了足够的 global / AI 原始数据。它不负责抓取
数据，也不关心报告质量；它只给 graph 一个明确判断：原始数据数量是否达到进入
clean_items stage 的最低门槛。
"""

from __future__ import annotations

import os
from typing import Any

from insight_engine.harness.state import InsightEngineState
from insight_engine.linters.common import lint_result


def lint(state: InsightEngineState) -> dict[str, Any]:
    """检查 Stage 1 的 raw item 数量是否满足最低要求。

    这个函数读取 `state.global_raw_items` 和 `state.ai_raw_items`，并使用环境变量
    `HARNESS_MIN_GLOBAL_RAW` / `HARNESS_MIN_AI_RAW` 作为可调阈值。失败时返回
    retryable=True，因为抓取阶段可能受网络、RSS 源波动等影响，允许 graph 重试。
    """
    min_global = int(os.getenv("HARNESS_MIN_GLOBAL_RAW", "5"))
    min_ai = int(os.getenv("HARNESS_MIN_AI_RAW", "3"))
    min_total = int(os.getenv("HARNESS_MIN_TOTAL_RAW", "10"))
    issues = []
    total = len(state.global_raw_items) + len(state.ai_raw_items)
    if len(state.global_raw_items) < min_global:
        issues.append(f"global_raw_items 数量不足：{len(state.global_raw_items)} < {min_global}")
    if len(state.ai_raw_items) < min_ai:
        issues.append(f"ai_raw_items 数量不足：{len(state.ai_raw_items)} < {min_ai}")
    if total < min_total:
        issues.append(f"raw_items 总量不足：{total} < {min_total}")
    return lint_result(
        "collect_raw_items",
        not issues,
        issues,
        {
            "global_raw_items": len(state.global_raw_items),
            "ai_raw_items": len(state.ai_raw_items),
            "total_raw_items": total,
            "min_global_raw": min_global,
            "min_ai_raw": min_ai,
            "min_total_raw": min_total,
        },
        retryable=True,
    )
