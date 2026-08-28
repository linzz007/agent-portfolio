"""generate_report stage 的运行时产物检查。

这个文件检查 Stage 5 是否真的生成了可交付报告和图表产物。它同时检查文件是否存在、
Markdown 章节是否齐全、HTML 看板结构是否存在、chart_data 是否能支撑页面展示，
以及标题翻译是否达到当前严格要求。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from insight_engine.harness.state import REPORT_REQUIRED_HEADINGS, InsightEngineState
from insight_engine.linters.common import lint_result


def lint(state: InsightEngineState) -> dict[str, Any]:
    """检查 Stage 5 的报告、HTML、图表和 chart_data 是否可交付。

    这个函数读取 `state.report_paths` / `state.artifacts` 中登记的产物路径，并验证：
    - Markdown、HTML 报告、charts.html、chart_data.json 是否存在。
    - Markdown 是否包含 `REPORT_REQUIRED_HEADINGS` 的全部章节。
    - HTML 是否包含中文标题、侧边导航、暗色样式和至少 4 个饼图。
    - chart_data 是否包含 KPI、饼图、Top AI 事件和有效标题翻译。
    """
    issues = []
    report_path = state.report_paths.get("report") or state.artifacts.get("report")
    html_path = state.report_paths.get("report_html") or state.artifacts.get("report_html")
    chart_path = state.report_paths.get("chart_html") or state.artifacts.get("chart_html")
    chart_data_path = state.report_paths.get("chart_data") or state.artifacts.get("chart_data")

    if not report_path or not Path(report_path).exists():
        issues.append("报告 Markdown 文件不存在")
        report_text = ""
    else:
        report_text = Path(report_path).read_text(encoding="utf-8")

    if not html_path or not Path(html_path).exists():
        issues.append("报告 HTML 文件不存在")
        html_text = ""
    else:
        html_text = Path(html_path).read_text(encoding="utf-8")

    if not chart_path or not Path(chart_path).exists():
        issues.append("可视化 HTML 文件不存在")

    if not chart_data_path or not Path(chart_data_path).exists():
        issues.append("chart_data.json 文件不存在")
        chart_data: dict[str, Any] = {}
    else:
        try:
            chart_data = json.loads(Path(chart_data_path).read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            issues.append("chart_data.json 不是合法 JSON")
            chart_data = {}

    missing_headings = [heading for heading in REPORT_REQUIRED_HEADINGS if heading not in report_text]
    if missing_headings:
        issues.append(f"报告缺少章节：{missing_headings}")

    # Schema 章节内容检查
    missing_schema_tables = _check_schema_tables(report_text)
    if missing_schema_tables:
        issues.append(f"报告 Schema 章节缺失字段合同表格：{missing_schema_tables}")

    if "schema-table" not in html_text:
        issues.append("HTML 报告缺少 Schema 字段合同表格")
    if 'href="#schema"' not in html_text:
        issues.append("HTML 报告侧边导航缺少字段合同链接")

    pie_count = html_text.count('data-chart-type="pie"')
    if "今日 AI 与全球热点洞察" not in html_text:
        issues.append("HTML 报告缺少中文主标题")
    if 'class="sidebar"' not in html_text or 'href="#ai-hotspots"' not in html_text:
        issues.append("HTML 报告缺少左侧侧边导航")
    if "--surface: #070b12" not in html_text:
        issues.append("HTML 报告缺少暗色看板样式")
    if "英文原题：" not in html_text:
        issues.append("HTML 报告缺少英文原题展示")
    if pie_count < 4:
        issues.append("HTML 报告饼状图少于 4 个")

    kpis = chart_data.get("kpis", []) if isinstance(chart_data, dict) else []
    pies = chart_data.get("pies", {}) if isinstance(chart_data, dict) else {}
    top_ai_events = chart_data.get("top_ai_events", []) if isinstance(chart_data, dict) else []
    title_translation_status = chart_data.get("title_translation_status") if isinstance(chart_data, dict) else None
    if title_translation_status not in {"ok", "empty"}:
        issues.append(f"标题中文翻译未完全通过：{title_translation_status}")
    if len(kpis) < 4:
        issues.append("chart_data 中 KPI 少于 4 个")
    if len([value for value in pies.values() if value]) < 3:
        issues.append("chart_data 中有效饼图数据少于 3 组")
    if not top_ai_events:
        issues.append("chart_data 中 Top AI 事件为空")
    else:
        for index, event in enumerate(top_ai_events[:5], start=1):
            if not event.get("title_zh"):
                issues.append(f"Top AI 事件 {index} 缺少中文标题")
            elif _looks_english(str(event.get("title", ""))) and not _has_cjk(str(event.get("title_zh", ""))):
                issues.append(f"Top AI 事件 {index} 中文标题不是有效中文翻译")
            elif str(event.get("title_zh", "")).strip() == str(event.get("title", "")).strip() and _looks_english(str(event.get("title", ""))):
                issues.append(f"Top AI 事件 {index} 中文标题仍然等于英文原题")
            if not event.get("title"):
                issues.append(f"Top AI 事件 {index} 缺少英文原题")
            if not event.get("keywords"):
                issues.append(f"Top AI 事件 {index} 缺少关键词")
            elif any(str(keyword).strip() in {"未知", "unknown"} for keyword in event.get("keywords", [])):
                issues.append(f"Top AI 事件 {index} 关键词包含未知占位")
            if str(event.get("area_label", "")).strip() in {"", "未知", "unknown"}:
                issues.append(f"Top AI 事件 {index} 缺少有效方向标签")
            hotness = event.get("hotness_score")
            if not isinstance(hotness, int) or not 0 <= hotness <= 100:
                issues.append(f"Top AI 事件 {index} 缺少有效热度分")
            if not event.get("source_name") or not event.get("source_type"):
                issues.append(f"Top AI 事件 {index} 缺少来源字段")

    return lint_result(
        "generate_report",
        not issues,
        issues,
        {
            "report": report_path,
            "report_html": html_path,
            "chart_html": chart_path,
            "chart_data": chart_data_path,
            "missing_heading_count": len(missing_headings),
            "pie_count": pie_count,
            "kpi_count": len(kpis),
            "top_ai_event_count": len(top_ai_events),
            "sidebar_present": 'class="sidebar"' in html_text,
            "title_translation_status": title_translation_status,
        },
        retryable=True,
    )


def _has_cjk(value: str) -> bool:
    """判断字符串是否包含中文字符，用于验证中文标题翻译。"""
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def _looks_english(value: str) -> bool:
    """粗略判断标题是否仍是英文，用于发现“中文标题等于英文原题”的情况。"""
    return bool(value) and not _has_cjk(value)


def _check_schema_tables(report_text: str) -> list[str]:
    """检查报告是否包含全部 5 个字段合同表格。

    通过关键字段名确认每个合同表格都存在于 Markdown 报告中。
    返回缺失的合同名称列表。
    """
    checks = {
        "Raw Item": ["source_id", "source_scope", "retrieved_at"],
        "Cleaned Item": ["clean_text", "quality_score", "should_analyze_ai"],
        "Structured Event": ["hotness_score", "industry_area", "key_entities"],
        "Analysis Result": ["trend_judgment", "risk_or_opportunity_notes", "summary_reason"],
        "Report Paths": ["report_html", "chart_data", "manifest"],
    }
    missing = []
    for name, keywords in checks.items():
        if not all(kw in report_text for kw in keywords):
            missing.append(name)
    return missing
