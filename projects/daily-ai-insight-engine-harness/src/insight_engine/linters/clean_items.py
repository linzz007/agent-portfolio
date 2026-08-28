"""clean_items stage 的运行时产物检查。

这个文件检查 Stage 2 的清洗结果是否能被结构化阶段消费。它不重新清洗数据，
只确认 cleaned item 数量达标、核心标题没有缺失、URL 没有重复到会破坏后续
证据追踪。
"""

from __future__ import annotations

import os
from typing import Any

from insight_engine.harness.state import InsightEngineState
from insight_engine.linters.common import lint_result


def lint(state: InsightEngineState) -> dict[str, Any]:
    """检查 Stage 2 的 cleaned item 是否满足进入 structure_events 的最低条件。

    这个函数合并 global 和 AI 两条清洗数据线后检查：
    1. 清洗结果不能为空，且总量达标。
    2. 每条可用记录要有 title，避免结构化阶段无法生成事件标题。
    3. URL 不应重复，避免同一来源在后续分析和报告中被重复计数。
    """
    min_total = int(os.getenv("HARNESS_MIN_CLEANED_ITEMS", "10"))
    issues = []
    all_items = state.global_cleaned_items + state.ai_cleaned_items
    if not all_items:
        issues.append("清洗后没有任何可用数据")
    elif len(all_items) < min_total:
        issues.append(f"清洗后数据量不足：{len(all_items)} < {min_total}")

    missing_title = [item.get("id") for item in all_items if not item.get("title")]
    if missing_title:
        issues.append(f"存在缺少 title 的 cleaned item：{missing_title[:5]}")

    urls = [str(item.get("url")) for item in all_items if item.get("url")]
    duplicate_urls = sorted({url for url in urls if urls.count(url) > 1})
    if duplicate_urls:
        issues.append(f"存在重复 URL：{duplicate_urls[:5]}")

    return lint_result(
        "clean_items",
        not issues,
        issues,
        {
            "global_cleaned_items": len(state.global_cleaned_items),
            "ai_cleaned_items": len(state.ai_cleaned_items),
            "total_cleaned_items": len(all_items),
            "min_total": min_total,
            "duplicate_url_count": len(duplicate_urls),
        },
        retryable=False,
    )
