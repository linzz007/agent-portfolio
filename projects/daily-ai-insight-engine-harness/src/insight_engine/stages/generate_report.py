"""Stage 5: 标题翻译与报告生成。

Stage 4 已经完成分析判断，Stage 5 只在标题翻译环节调用 LLM。
这个阶段只负责把 state.analysis_result 渲染成：

1. Markdown 报告：适合归档、复制和审阅。
2. 完整 HTML 报告：中文决策看板，首屏给结论、KPI、饼图和 Top 事件。
3. 图表 HTML：独立可视化页，保留原有 chart_html 产物。
4. chart_data.json 和 report_manifest.json：方便调试和审计。
5. Stage 5 会按 event.id 从 structured_events 回填展示字段；LLM 只负责选择和分析，
   不负责可靠复制 source、tags、hotness 等结构字段。

设计原则：

- 先给结论，再给证据，最后给结构化附录。
- 页面组件直接消费 chart_data，避免 HTML 模板里写太多业务判断。
- 饼图使用内联 SVG，不依赖外部 JS 或网络资源。
- 所有事件保留 id、来源、URL，满足 Harness 可追踪要求。
"""

from __future__ import annotations

import html
import json
from collections import Counter
from pathlib import Path
from typing import Any

from insight_engine.harness.artifacts import ensure_run_dir
from insight_engine.harness.llm_client import LLMClientError, OpenAICompatibleChatClient
from insight_engine.harness.state import (
    ANALYSIS_RESULT_FIELD_SPEC,
    CLEANED_ITEM_FIELD_SPEC,
    RAW_ITEM_FIELD_SPEC,
    REPORT_PATHS_FIELD_SPEC,
    REPORT_REQUIRED_HEADINGS as REQUIRED_REPORT_HEADINGS,
    STRUCTURED_EVENT_AI_AREAS,
    STRUCTURED_EVENT_FIELD_SPEC,
    STRUCTURED_EVENT_GLOBAL_AREAS,
    InsightEngineState,
)


PIE_COLORS = ["#155eef", "#0f766e", "#b54708", "#7a5af8", "#c01048", "#475467", "#0086c9", "#16b364"]

AREA_LABELS = {
    "foundation_model": "基础模型",
    "application": "AI 应用",
    "policy": "政策监管",
    "investment": "资本投资",
    "hardware": "硬件终端",
    "research": "研究论文",
    "security": "安全",
    "technology": "科技",
    "politics": "政治",
    "business": "商业",
    "market": "市场",
    "model": "模型",
    "ai_app": "AI 应用",
    "other": "其他",
    "climate": "气候",
    "unknown": "未知",
}

IMPORTANCE_LABELS = {
    "high": "高重要性",
    "medium": "中重要性",
    "low": "低重要性",
    "unknown": "未知",
}

SCOPE_LABELS = {
    "global": "全球热点",
    "ai": "AI 领域",
}


def generate_report(state: InsightEngineState) -> InsightEngineState:
    """执行确定性报告生成阶段。"""
    report_dir = ensure_run_dir("outputs/reports", state.run_id)
    chart_dir = ensure_run_dir("outputs/charts", state.run_id)
    all_events = state.global_structured_events + state.ai_structured_events
    report_analysis = hydrate_analysis_result_events(
        analysis_result=state.analysis_result,
        source_events=all_events,
    )
    title_translation = translate_event_titles(state=state, events=all_events)
    title_translations = title_translation["translations"]
    title_translation_path = report_dir / "title_translations.json"

    chart_data = build_chart_data(
        analysis_result=report_analysis,
        global_events=state.global_structured_events,
        ai_events=state.ai_structured_events,
        title_translations=title_translations,
        title_translation_status=title_translation["status"],
    )
    chart_data_path = chart_dir / "chart_data.json"
    chart_html_path = chart_dir / "charts.html"
    report_path = report_dir / "daily_ai_report.md"
    report_html_path = report_dir / "daily_ai_report.html"
    manifest_path = report_dir / "report_manifest.json"

    report_markdown = build_markdown_report(
        state=state,
        chart_path=str(chart_html_path),
        title_translations=title_translations,
        analysis_result=report_analysis,
    )
    report_errors = validate_report_markdown(report_markdown)
    if report_errors:
        state.add_warning("generate_report", "报告 markdown 未完全通过本地 linter", report_errors)

    write_text(chart_data_path, json.dumps(chart_data, ensure_ascii=False, indent=2))
    write_text(title_translation_path, json.dumps(title_translation, ensure_ascii=False, indent=2))
    write_text(chart_html_path, generate_chart_html(chart_data))
    write_text(report_path, report_markdown)
    write_text(
        report_html_path,
        generate_report_html(
            state=state,
            analysis_result=report_analysis,
            chart_data=chart_data,
            chart_html_path=str(chart_html_path),
            report_path=str(report_path),
            title_translations=title_translations,
        ),
    )

    state.report_paths = {
        "report": str(report_path),
        "report_html": str(report_html_path),
        "chart_html": str(chart_html_path),
        "chart_data": str(chart_data_path),
        "manifest": str(manifest_path),
        "title_translations": str(title_translation_path),
    }
    manifest = {
        "run_id": state.run_id,
        "target_date": state.target_date,
        "report": str(report_path),
        "report_html": str(report_html_path),
        "chart_html": str(chart_html_path),
        "chart_data": str(chart_data_path),
        "title_translations": str(title_translation_path),
        "title_translation_status": title_translation["status"],
        "structured_event_count": len(all_events),
        "render_mode": "llm_title_translation_then_dashboard_render",
        "design_notes": [
            "标题中文翻译由 LLM 逐条翻译，失败时保留英文原题并写 warning。",
            "正文按 AI 热点、全球背景、趋势判断、风险机会和附录展开。",
            "饼图由 chart_data 生成，HTML 不依赖外部图表库。",
        ],
    }
    write_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2))

    state.add_artifact("report", str(report_path))
    state.add_artifact("report_html", str(report_html_path))
    state.add_artifact("chart_html", str(chart_html_path))
    state.add_artifact("chart_data", str(chart_data_path))
    state.add_artifact("title_translations", str(title_translation_path))
    state.add_artifact("report_manifest", str(manifest_path))
    return state


def build_chart_data(
    analysis_result: dict[str, Any],
    global_events: list[dict[str, Any]],
    ai_events: list[dict[str, Any]],
    title_translations: dict[str, str],
    title_translation_status: str,
) -> dict[str, Any]:
    """构造 HTML 页面可直接使用的组件数据。"""
    all_events = global_events + ai_events
    top_events = analysis_result.get("top_events", [])[:5] if isinstance(analysis_result, dict) else []
    global_top_events = analysis_result.get("global_top_events", [])[:5] if isinstance(analysis_result, dict) else []
    risk_notes = analysis_result.get("risk_or_opportunity_notes", []) if isinstance(analysis_result, dict) else []
    source_counts = count_by_field(all_events, "source_name")
    area_counts = count_by_field(ai_events, "industry_area")
    top_tags = count_tags(all_events)
    importance_counts = count_by_field(all_events, "importance_level")

    return {
        "kpis": [
            {"label": "全球热点", "value": len(global_events), "hint": "未在抓取阶段过滤 AI"},
            {"label": "AI 事件", "value": len(ai_events), "hint": "AI 关键词或 AI 数据源命中"},
            {"label": "高重要性", "value": count_high_importance(all_events), "hint": "importance_level=high"},
            {"label": "风险/机会", "value": len(risk_notes), "hint": "Stage 4 生成"},
        ],
        "pies": {
            "scope": to_pie_slices({"global": len(global_events), "ai": len(ai_events)}, SCOPE_LABELS),
            "source": to_pie_slices(source_counts, {}),
            "industry_area": to_pie_slices(area_counts, AREA_LABELS),
            "importance": to_pie_slices(importance_counts, IMPORTANCE_LABELS),
        },
        "top_tags": to_pie_slices(top_tags, {}),
        "top_ai_events": summarize_events(top_events, title_translations),
        "global_context_events": summarize_events(global_top_events, title_translations),
        "trend_cards": build_trend_cards(analysis_result),
        "risk_opportunity_matrix": build_risk_matrix(risk_notes),
        "title_translation_status": title_translation_status,
    }


def hydrate_analysis_result_events(
    analysis_result: dict[str, Any],
    source_events: list[dict[str, Any]],
) -> dict[str, Any]:
    """用 structured_events 按 id 回填 analysis_result 中 Top 事件的展示字段。

    Stage 4 的 LLM 负责选择事件和给出分析理由，但它不应该成为结构字段复制器。
    这里用确定性逻辑补全 source、published_at、industry_area、topic_tags、hotness_score
    等下游报告展示需要的字段。
    """
    if not isinstance(analysis_result, dict):
        return {}
    source_by_id = {
        str(event.get("id")): event
        for event in source_events
        if isinstance(event, dict) and event.get("id")
    }
    hydrated = dict(analysis_result)
    hydrated["top_events"] = hydrate_events_by_id(
        hydrated.get("top_events", []),
        source_by_id=source_by_id,
    )
    hydrated["global_top_events"] = hydrate_events_by_id(
        hydrated.get("global_top_events", []),
        source_by_id=source_by_id,
    )
    return hydrated


def hydrate_events_by_id(
    events: Any,
    source_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """把分析事件和完整结构化事件合并；分析字段优先，空值不覆盖上游结构字段。"""
    if not isinstance(events, list):
        return []
    rows: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        source_event = source_by_id.get(str(event.get("id")), {})
        merged = dict(source_event)
        for key, value in event.items():
            if not is_empty_value(value):
                merged[key] = value
        rows.append(merged)
    return rows


def is_empty_value(value: Any) -> bool:
    """判断分析字段是否为空；空值不允许覆盖 structured_events 中的原始字段。"""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return not value
    if isinstance(value, dict):
        return not value
    return False


def build_schema_section() -> str:
    """生成数据字段合同（Schema）章节。

    展示本系统全部 5 个跨阶段字段合同：raw_item → cleaned_item →
    structured_event → analysis_result → report_paths。
    每个字段附带类型、是否必填、中文用途说明，以及受控枚举值（如有）。
    """
    lines: list[str] = [
        "## 数据字段合同（Schema）",
        "",
        "本系统通过 5 个跨阶段字段合同约束数据质量。每个 stage 负责产出对应字段，",
        "gate/linter 负责校验字段，下游 stage 按合同消费字段。",
        "",
    ]

    # ── Raw Item ──
    lines.extend(_format_field_spec_table(
        title="### Raw Item 字段合同（Stage 1: collect_raw_items 产出）",
        description="原始数据抓取后每个 item 必须满足的结构。global 和 AI 两条数据线共用此合同。",
        spec=RAW_ITEM_FIELD_SPEC,
        enums=None,
    ))

    # ── Cleaned Item ──
    lines.extend(_format_field_spec_table(
        title="### Cleaned Item 字段合同（Stage 2: clean_items 产出）",
        description="清洗去重后每个 item 的字段结构。新增标签、质量分、分析候选标记。",
        spec=CLEANED_ITEM_FIELD_SPEC,
        enums=None,
    ))

    # ── Structured Event ──
    lines.extend(_format_field_spec_table(
        title="### Structured Event 字段合同（Stage 3: structure_events 产出）",
        description="LLM 或 fallback 生成的每条结构化事件字段。URL 和发布时间不允许 LLM 编造。",
        spec=STRUCTURED_EVENT_FIELD_SPEC,
        enums={
            "industry_area（global 数据线）": sorted(STRUCTURED_EVENT_GLOBAL_AREAS),
            "industry_area（AI 数据线）": sorted(STRUCTURED_EVENT_AI_AREAS),
            "importance_level": ["high", "medium", "low"],
            "hotness_score": "0 ~ 100 整数",
        },
    ))

    # ── Analysis Result ──
    lines.extend(_format_analysis_result_spec())

    # ── Report Paths ──
    lines.extend(_format_field_spec_table(
        title="### Report Paths 字段合同（Stage 5: generate_report 产出）",
        description="报告生成后登记的全部产物路径。下游（页面/CLI/audit）按此合同读取文件。",
        spec=REPORT_PATHS_FIELD_SPEC,
        enums=None,
    ))

    # ── Report Required Headings ──
    lines.extend([
        "### Report 必须章节",
        "",
        "Markdown 报告必须包含以下全部章节标题，由 `generate_report` stage gate linter 自动检查：",
        "",
    ])
    for heading in REQUIRED_REPORT_HEADINGS:
        lines.append(f"- `{heading}`")
    lines.append("")

    return "\n".join(lines)


def _format_field_spec_table(
    title: str,
    description: str,
    spec: dict[str, dict],
    enums: dict[str, Any] | None,
) -> list[str]:
    """把一个 FIELD_SPEC 渲染为 Markdown 表格 + 说明。"""
    lines = [title, "", description, ""]
    # 表头
    lines.append("| 字段名 | 类型 | 必填 | 用途说明 |")
    lines.append("|--------|------|------|----------|")
    for field_name, meta in spec.items():
        req = "**是**" if meta.get("required") else "否"
        ftype = str(meta.get("type", ""))
        purpose = str(meta.get("purpose", ""))
        # 嵌套字段提示
        extra = meta.get("required_fields") or meta.get("item_required_fields")
        if extra:
            sub = "、".join(str(x) for x in extra[:8])
            purpose += f"（子字段：{sub}）"
        lines.append(f"| `{field_name}` | {ftype} | {req} | {purpose} |")
    lines.append("")

    if enums:
        lines.append("**受控枚举值：**")
        for label, values in enums.items():
            if isinstance(values, str):
                lines.append(f"- {label}：{values}")
            else:
                lines.append(f"- {label}：{' / '.join(str(v) for v in values)}")
        lines.append("")

    return lines


def _format_analysis_result_spec() -> list[str]:
    """单独渲染 analysis_result 字段合同（结构嵌套较深）。"""
    lines = [
        "### Analysis Result 字段合同（Stage 4: analyze_insights 产出）",
        "",
        "分析阶段的输出是下游报告的输入。每个子字段有独立的类型和必填子字段约束。",
        "",
        "| 字段名 | 类型 | 必填 | 用途说明 |",
        "|--------|------|------|----------|",
    ]
    for field_name, meta in ANALYSIS_RESULT_FIELD_SPEC.items():
        req = "**是**" if meta.get("required") else "否"
        ftype = str(meta.get("type", ""))
        purpose = str(meta.get("purpose", ""))
        sub = meta.get("required_fields") or meta.get("item_required_fields")
        if sub:
            sub_str = "、".join(str(x) for x in sub[:12])
            purpose += f"（子字段：{sub_str}）"
        lines.append(f"| `{field_name}` | {ftype} | {req} | {purpose} |")
    lines.append("")

    # trend_judgment 四方向约束
    lines.extend([
        "**`trend_judgment` / `trend_reasoning` 必须包含四个方向：**",
        "- `technology`（技术方向）",
        "- `application`（应用方向）",
        "- `policy`（政策方向）",
        "- `capital`（资本方向）",
        "",
        "**`risk_or_opportunity_notes` 每条必须包含：**",
        "- `area` — 风险/机会领域",
        "- `note` — 具体提示",
        "- `reason` — 支撑理由",
        "- `supporting_event_ids` — 绑定的支撑事件 ID 列表",
        "",
    ])
    return lines


def build_markdown_report(
    state: InsightEngineState,
    chart_path: str,
    title_translations: dict[str, str],
    analysis_result: dict[str, Any] | None = None,
) -> str:
    """用模板拼装含全部必需章节的 Markdown 日报。"""
    analysis = analysis_result or state.analysis_result
    all_events = state.global_structured_events + state.ai_structured_events
    stats = analysis.get("stats", {})
    lines: list[str] = [
        f"# 今日 AI 与全球热点洞察 - {state.target_date}",
        "",
        f"- run_id: `{state.run_id}`",
        f"- 全球结构化事件数: {len(state.global_structured_events)}",
        f"- AI 结构化事件数: {len(state.ai_structured_events)}",
        f"- 可视化文件: `{chart_path}`",
        "",
        str(analysis.get("summary", "")),
        "",
        f"> 判断依据：{analysis.get('summary_reason', '')}",
        "",
        "## 数据源概览",
        "",
    ]
    for source_type, count in (stats.get("source_counts") or {}).items():
        lines.append(f"- {source_type}: {count} 条")
    if not (stats.get("source_counts") or {}):
        lines.append("- 暂无来源统计")

    lines.extend(["", "## 全球热点背景", ""])
    for index, event in enumerate(analysis.get("global_top_events", [])[:5], start=1):
        title_zh = translated_title(event, title_translations)
        lines.extend(
            [
                f"### {index}. {title_zh}",
                "",
                f"- 英文原题: {event.get('title', '')}",
                f"- 链接: {event.get('url', '')}",
                f"- 入选理由: {event.get('selection_reason', '')}",
                "",
                str(event.get("summary", "")),
                "",
            ]
        )
    if not analysis.get("global_top_events"):
        lines.append("本次没有可展示的 global Top 事件。")

    lines.extend(["", "## 今日 AI 领域主要热点", ""])
    for index, event in enumerate(analysis.get("top_events", [])[:5], start=1):
        title_zh = translated_title(event, title_translations)
        lines.extend(
            [
                f"### {index}. {title_zh}",
                "",
                f"- 英文原题: {event.get('title', '')}",
                f"- 来源: {event.get('source_name', '')} / {event.get('source_type', '')}",
                f"- 方向: {translate_area(event.get('industry_area', ''))}",
                f"- 热度分: {event.get('hotness_score', '')}",
                f"- 链接: {event.get('url', '')}",
                f"- 入选理由: {event.get('selection_reason', '')}",
                "",
                str(event.get("summary", "")),
                "",
            ]
        )

    lines.extend(["## 重要事件深度总结", ""])
    for event in analysis.get("top_events", [])[:5]:
        title_zh = translated_title(event, title_translations)
        lines.extend(
            [
                f"### {event.get('id', '')}: {title_zh}",
                "",
                f"英文原题：{event.get('title', '')}",
                "",
                f"影响分析：{event.get('impact_analysis', '')}",
                "",
                f"风险或机会：{event.get('risk_or_opportunity', '')}",
                "",
            ]
        )

    trend = analysis.get("trend_judgment", {})
    trend_reasoning = analysis.get("trend_reasoning", {})
    lines.extend(["## 趋势判断", ""])
    for label, key in [("技术方向", "technology"), ("应用方向", "application"), ("政策方向", "policy"), ("资本方向", "capital")]:
        lines.append(f"- {label}：{trend.get(key, '')}")
        lines.append(f"  - 理由：{trend_reasoning.get(key, '')}")
    lines.append("")

    lines.extend(["## 风险和机会提示", ""])
    for note in analysis.get("risk_or_opportunity_notes", []):
        ids = ", ".join(str(item) for item in note.get("supporting_event_ids", []))
        lines.append(f"- {note.get('area', '')}: {note.get('note', '')} 支撑事件：{ids}")
        lines.append(f"  - 理由：{note.get('reason', '')}")

    lines.extend(["", "## 结构化数据附录", ""])
    for event in all_events:
        title_zh = translated_title(event, title_translations)
        lines.extend(
            [
                f"### {event.get('id', '')}: {title_zh}",
                "",
                f"- 英文原题: {event.get('title', '')}",
                f"- scope: {event.get('source_scope', '')}",
                f"- source: {event.get('source_name', '')}",
                f"- area: {event.get('industry_area', '')}",
                f"- tags: {', '.join(event.get('topic_tags', []))}",
                f"- url: {event.get('url', '')}",
                "",
            ]
        )

    lines.extend(["## 质量评估摘要", "", "最终质量检查由 `generate_report` stage gate 自动完成。", ""])

    # 追加数据字段合同章节
    lines.append(build_schema_section())

    return "\n".join(lines)


def generate_report_html(
    state: InsightEngineState,
    analysis_result: dict[str, Any],
    chart_data: dict[str, Any],
    chart_html_path: str,
    report_path: str,
    title_translations: dict[str, str],
) -> str:
    """生成左侧导航 + 纵向内容流的暗色中文报告页面。"""
    analysis = analysis_result
    pies = chart_data.get("pies", {})
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>今日 AI 与全球热点洞察 - {escape(state.target_date)}</title>
  <style>
    :root {{
      color-scheme: dark;
      --ink: #f8fafc;
      --muted: #98a2b3;
      --line: rgba(148, 163, 184, .2);
      --panel: #111827;
      --panel-2: #0f172a;
      --surface: #070b12;
      --surface-2: #0b111c;
      --accent: #38bdf8;
      --accent-2: #22c55e;
      --risk: #fb7185;
      --opportunity: #34d399;
      --warm: #f59e0b;
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top right, rgba(56, 189, 248, .12), transparent 34%),
        linear-gradient(180deg, var(--surface), #020617 72%);
      line-height: 1.58;
    }}
    a {{ color: var(--accent); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .app-shell {{
      display: grid;
      grid-template-columns: 246px minmax(0, 1fr);
      min-height: 100vh;
    }}
    .sidebar {{
      position: sticky;
      top: 0;
      height: 100vh;
      padding: 22px 18px;
      border-right: 1px solid var(--line);
      background: rgba(7, 11, 18, .86);
      backdrop-filter: blur(18px);
    }}
    .brand {{ font-size: 14px; color: var(--muted); margin-bottom: 18px; }}
    .brand strong {{ display: block; color: var(--ink); font-size: 18px; margin-bottom: 4px; }}
    .nav {{ display: grid; gap: 8px; margin-top: 18px; }}
    .nav a {{
      display: block;
      padding: 9px 10px;
      border-radius: 8px;
      color: #cbd5e1;
      border: 1px solid transparent;
      font-size: 13px;
    }}
    .nav a:hover {{ background: rgba(148, 163, 184, .1); border-color: var(--line); text-decoration: none; }}
    .side-meta {{ color: var(--muted); font-size: 12px; margin-top: 18px; word-break: break-all; }}
    .content {{ width: min(1120px, calc(100vw - 246px)); margin: 0 auto; padding: 28px; }}
    .hero {{
      padding: 30px 0 22px;
      border-bottom: 1px solid var(--line);
    }}
    .eyebrow {{ color: var(--accent-2); font-weight: 750; font-size: 13px; }}
    h1 {{ margin: 8px 0 12px; font-size: 38px; line-height: 1.12; letter-spacing: 0; }}
    h2 {{ margin: 0 0 14px; font-size: 20px; letter-spacing: 0; }}
    h3 {{ margin: 0 0 8px; font-size: 16px; letter-spacing: 0; }}
    .lead {{ margin: 0; color: #dbeafe; font-size: 17px; max-width: 850px; }}
    .reason {{ border-left: 3px solid var(--accent-2); padding-left: 10px; color: #cbd5e1; font-size: 13px; margin-top: 10px; }}
    .meta {{ color: var(--muted); font-size: 13px; word-break: break-all; }}
    .toolbar {{ display: flex; gap: 10px; flex-wrap: wrap; margin-top: 18px; }}
    .button {{ border: 1px solid var(--line); background: #0b1220; border-radius: 8px; padding: 9px 12px; font-size: 13px; color: var(--ink); }}
    .kpis {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: 22px 0; }}
    .kpi {{ background: linear-gradient(180deg, #111827, #0b1220); border: 1px solid var(--line); border-radius: 8px; padding: 14px; min-height: 104px; }}
    .kpi .value {{ font-size: 30px; font-weight: 760; }}
    .kpi .label {{ color: var(--ink); font-size: 14px; font-weight: 700; }}
    .kpi .hint {{ color: var(--muted); font-size: 12px; margin-top: 6px; }}
    .section {{ background: rgba(17, 24, 39, .9); border: 1px solid var(--line); border-radius: 8px; padding: 18px; margin-bottom: 18px; scroll-margin-top: 24px; }}
    .pie-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; }}
    .pie-card {{ border: 1px solid var(--line); border-radius: 8px; padding: 14px; background: #0b1220; }}
    .pie-card svg {{ width: 100%; max-width: 190px; display: block; margin: 0 auto; }}
    .legend {{ display: grid; gap: 6px; margin-top: 10px; font-size: 12px; }}
    .legend-row {{ display: grid; grid-template-columns: 10px 1fr auto; gap: 7px; align-items: center; }}
    .swatch {{ width: 10px; height: 10px; border-radius: 2px; }}
    .event {{ border-top: 1px solid var(--line); padding: 14px 0; }}
    .event:first-of-type {{ border-top: 0; padding-top: 0; }}
    .event-meta {{ display: flex; gap: 8px; flex-wrap: wrap; color: var(--muted); font-size: 12px; margin: 8px 0; }}
    .pill {{ border: 1px solid var(--line); border-radius: 999px; padding: 2px 8px; background: #0b1220; }}
    .title-zh {{ font-size: 18px; color: #f8fafc; }}
    .title-en {{ color: #94a3b8; font-size: 13px; margin: 6px 0 0; }}
    .keyword-row {{ display: flex; gap: 6px; flex-wrap: wrap; margin: 8px 0; }}
    .keyword {{ color: #bae6fd; background: rgba(56, 189, 248, .09); border-color: rgba(56, 189, 248, .24); }}
    .rank {{ color: var(--accent); font-weight: 800; margin-right: 6px; }}
    .trend-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }}
    .trend {{ border: 1px solid var(--line); border-radius: 8px; padding: 12px; background: #0b1220; min-height: 128px; }}
    .matrix {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }}
    .matrix-cell {{ border: 1px solid var(--line); border-radius: 8px; padding: 12px; background: #0b1220; min-height: 130px; }}
    .matrix-cell.risk {{ border-left: 4px solid var(--risk); }}
    .matrix-cell.opportunity {{ border-left: 4px solid var(--opportunity); }}
    .note {{ color: #cbd5e1; font-size: 13px; margin: 8px 0 0; }}
    .appendix {{ font-size: 13px; color: #cbd5e1; }}
    .appendix-row {{ border-top: 1px solid var(--line); padding: 8px 0; }}
    .schema-table {{ margin-bottom: 22px; }}
    .schema-table h3 {{ color: var(--accent-2); font-size: 15px; margin: 0 0 6px; }}
    .schema-table p {{ color: var(--muted); font-size: 13px; margin: 0 0 10px; }}
    .schema-table table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
    .schema-table th, .schema-table td {{ border: 1px solid var(--line); padding: 7px 8px; text-align: left; }}
    .schema-table th {{ background: #0b1220; color: #e2e8f0; font-weight: 700; }}
    .schema-table td code {{ color: var(--accent); background: rgba(56, 189, 248, .08); padding: 1px 5px; border-radius: 3px; }}
    @media (max-width: 960px) {{
      .app-shell {{ grid-template-columns: 1fr; }}
      .sidebar {{ position: static; height: auto; border-right: 0; border-bottom: 1px solid var(--line); }}
      .content {{ width: 100%; padding: 18px; }}
      .kpis, .pie-grid, .trend-grid, .matrix {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 30px; }}
    }}
  </style>
</head>
<body>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <strong>AI Insight</strong>
        中文新闻洞察报告
      </div>
      <nav class="nav" aria-label="报告导航">
        <a href="#overview">总览结论</a>
        <a href="#kpis">关键指标</a>
        <a href="#distribution">数据分布</a>
        <a href="#ai-hotspots">AI 热点</a>
        <a href="#global-context">全球背景</a>
        <a href="#trends">趋势判断</a>
        <a href="#risk-opportunity">风险机会</a>
        <a href="#appendix">结构化附录</a>
        <a href="#schema">字段合同</a>
      </nav>
      <div class="side-meta">run_id<br>{escape(state.run_id)}</div>
    </aside>
    <main class="content">
      <header class="hero" id="overview">
        <div class="eyebrow">中文新闻洞察看板</div>
        <h1>今日 AI 与全球热点洞察 · {escape(state.target_date)}</h1>
        <p class="lead">{escape(analysis.get("summary", ""))}</p>
        <p class="reason">判断依据：{escape(analysis.get("summary_reason", ""))}</p>
        <div class="toolbar">
          <a class="button" href="{escape(Path(report_path).name)}">Markdown 报告</a>
          <a class="button" href="{escape(Path(chart_html_path).resolve().as_uri())}">独立图表页</a>
        </div>
      </header>

    <section class="kpis" id="kpis" data-section="kpi">
      {kpi_cards(chart_data.get("kpis", []))}
    </section>

    <section class="section" id="distribution" data-section="pie-overview">
      <h2>数据分布总览</h2>
      <div class="pie-grid">
        {pie_chart("全球 / AI 占比", pies.get("scope", []))}
        {pie_chart("新闻来源占比", pies.get("source", []))}
        {pie_chart("AI 事件领域占比", pies.get("industry_area", []))}
        {pie_chart("重要性等级占比", pies.get("importance", []))}
      </div>
    </section>

    <section class="section" id="ai-hotspots">
      <h2>今日 AI 领域主要热点</h2>
      {event_cards(chart_data.get("top_ai_events", []), show_rank=True)}
    </section>
    <section class="section" id="global-context">
      <h2>全球热点背景</h2>
      {event_cards(chart_data.get("global_context_events", []), show_rank=False)}
    </section>
    <section class="section" id="trends">
      <h2>趋势判断</h2>
      <div class="trend-grid">{trend_cards(chart_data.get("trend_cards", []))}</div>
    </section>
    <section class="section" id="risk-opportunity">
      <h2>风险与机会矩阵</h2>
      <div class="matrix">{risk_matrix(chart_data.get("risk_opportunity_matrix", []))}</div>
    </section>
    <section class="section" id="deep-dive">
      <h2>重要事件深度总结</h2>
      {deep_dive(chart_data.get("top_ai_events", []))}
    </section>
    <section class="section" id="appendix">
      <h2>结构化数据附录</h2>
      <div class="appendix">{appendix_rows(state.global_structured_events + state.ai_structured_events, title_translations=title_translations)}</div>
    </section>
    <section class="section" id="quality">
      <h2>质量评估摘要</h2>
      <p class="note">质量检查由 <code>generate_report</code> stage gate 自动完成。Stage 5 只在标题翻译环节调用 LLM，页面渲染仍使用确定性模板。</p>
    </section>
    <section class="section" id="schema">
      <h2>数据字段合同（Schema）</h2>
      <p class="note">本系统通过 5 个跨阶段字段合同约束数据质量。每个 stage 负责产出对应字段，gate/linter 负责校验字段，下游 stage 按合同消费字段。</p>
      {schema_html_tables()}
    </section>
    </main>
  </div>
</body>
</html>
"""


def generate_chart_html(chart_data: dict[str, Any]) -> str:
    """生成独立 HTML 可视化页面。"""
    pies = chart_data.get("pies", {})
    data_json = json.dumps(chart_data, ensure_ascii=False, indent=2)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AI 日报图表页</title>
  <style>
    body {{ margin: 0; font-family: Arial, sans-serif; color: #f8fafc; background: #070b12; }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 28px; }}
    h1, h2, h3 {{ letter-spacing: 0; }}
    .grid {{ display: grid; grid-template-columns: 1fr; gap: 16px; }}
    .card {{ background: #111827; border: 1px solid rgba(148, 163, 184, .2); border-radius: 8px; padding: 16px; }}
    svg {{ width: 100%; max-width: 220px; display: block; margin: 0 auto; }}
    .legend {{ display: grid; gap: 6px; margin-top: 10px; font-size: 13px; }}
    .legend-row {{ display: grid; grid-template-columns: 10px 1fr auto; gap: 7px; align-items: center; }}
    .swatch {{ width: 10px; height: 10px; border-radius: 2px; }}
    pre {{ background: #111827; border: 1px solid rgba(148, 163, 184, .2); border-radius: 8px; padding: 16px; overflow: auto; }}
    @media (max-width: 760px) {{ .grid {{ grid-template-columns: 1fr; }} main {{ padding: 18px; }} }}
  </style>
</head>
<body>
  <main>
    <h1>AI 日报图表页</h1>
    <div class="grid">
      {pie_chart("全球 / AI 占比", pies.get("scope", []))}
      {pie_chart("新闻来源占比", pies.get("source", []))}
      {pie_chart("AI 事件领域占比", pies.get("industry_area", []))}
      {pie_chart("重要性等级占比", pies.get("importance", []))}
    </div>
    <h2>原始图表数据</h2>
    <pre>{escape(data_json)}</pre>
  </main>
</body>
</html>
"""


def kpi_cards(items: list[dict[str, Any]]) -> str:
    """渲染 KPI 卡片。"""
    rows = []
    for item in items:
        rows.append(
            f"""<div class="kpi">
  <div class="label">{escape(item.get("label", ""))}</div>
  <div class="value">{escape(item.get("value", 0))}</div>
  <div class="hint">{escape(item.get("hint", ""))}</div>
</div>"""
        )
    return "\n".join(rows)


def pie_chart(title: str, slices: list[dict[str, Any]]) -> str:
    """用内联 SVG 渲染环形饼图。"""
    if not slices:
        return f"""<div class="pie-card card"><h3>{escape(title)}</h3><p>暂无数据</p></div>"""
    total = sum(max(0, int(item.get("value", 0))) for item in slices) or 1
    radius = 70
    circumference = 2 * 3.<redacted-phone>9793 * radius
    offset = 0.0
    circles = []
    legend_rows = []
    for item in slices:
        value = max(0, int(item.get("value", 0)))
        color = str(item.get("color") or PIE_COLORS[len(circles) % len(PIE_COLORS)])
        dash = value / total * circumference
        gap = max(0.0, circumference - dash)
        percent = value / total * 100
        circles.append(
            f"""<circle cx="100" cy="100" r="{radius}" fill="none" stroke="{escape(color)}" stroke-width="58"
    stroke-dasharray="{dash:.2f} {gap:.2f}" stroke-dashoffset="-{offset:.2f}" transform="rotate(-90 100 100)" />"""
        )
        legend_rows.append(
            f"""<div class="legend-row">
  <span class="swatch" style="background:{escape(color)}"></span>
  <span>{escape(item.get("label", ""))}</span>
  <strong>{value} / {percent:.0f}%</strong>
</div>"""
        )
        offset += dash

    return f"""<div class="pie-card card" data-chart-type="pie">
  <h3>{escape(title)}</h3>
  <svg viewBox="0 0 200 200" role="img" aria-label="{escape(title)}">
    <circle cx="100" cy="100" r="{radius}" fill="none" stroke="#edf1f7" stroke-width="58" />
    {''.join(circles)}
    <circle cx="100" cy="100" r="42" fill="#0b1220" />
    <text x="100" y="96" text-anchor="middle" font-size="20" font-weight="700" fill="#f8fafc">{total}</text>
    <text x="100" y="116" text-anchor="middle" font-size="11" fill="#94a3b8">总计</text>
  </svg>
  <div class="legend">{''.join(legend_rows)}</div>
</div>"""


def event_cards(events: list[dict[str, Any]], *, show_rank: bool) -> str:
    """渲染事件卡片。"""
    if not events:
        return "<p>暂无事件。</p>"
    rows = []
    for index, event in enumerate(events, start=1):
        rank = f'<span class="rank">#{index}</span>' if show_rank else ""
        keywords = event.get("keywords", [])
        rows.append(
            f"""<article class="event">
  <h3 class="title-zh">{rank}{escape(event.get("title_zh", ""))}</h3>
  <p class="title-en">英文原题：{escape(event.get("title", ""))}</p>
  <div class="event-meta">
    <span class="pill">{escape(event.get("id", ""))}</span>
    <span class="pill">{escape(event.get("area_label", ""))}</span>
    <span class="pill">热度 {escape(event.get("hotness_score", ""))}</span>
  </div>
  <div class="keyword-row">{keyword_pills(keywords)}</div>
  <p>{escape(event.get("summary", ""))}</p>
  <p class="reason">{escape(event.get("selection_reason", ""))}</p>
  <p><a href="{escape(event.get("url", ""))}">查看来源</a></p>
</article>"""
        )
    return "\n".join(rows)


def deep_dive(events: list[dict[str, Any]]) -> str:
    """渲染 Top AI 事件深度总结。"""
    if not events:
        return "<p>暂无深度总结。</p>"
    rows = []
    for event in events:
        rows.append(
            f"""<article class="event">
  <h3 class="title-zh">{escape(event.get("id", ""))}: {escape(event.get("title_zh", ""))}</h3>
  <p class="title-en">英文原题：{escape(event.get("title", ""))}</p>
  <p><strong>为什么重要：</strong>{escape(event.get("selection_reason", ""))}</p>
  <p><strong>影响分析：</strong>{escape(event.get("impact_analysis", ""))}</p>
  <p><strong>风险或机会：</strong>{escape(event.get("risk_or_opportunity", ""))}</p>
</article>"""
        )
    return "\n".join(rows)


def trend_cards(cards: list[dict[str, Any]]) -> str:
    """渲染趋势判断卡片。"""
    rows = []
    for card in cards:
        rows.append(
            f"""<div class="trend">
  <h3>{escape(card.get("label", ""))}</h3>
  <p>{escape(card.get("judgment", ""))}</p>
  <p class="reason">{escape(card.get("reason", ""))}</p>
</div>"""
        )
    return "\n".join(rows)


def risk_matrix(items: list[dict[str, Any]]) -> str:
    """渲染风险与机会矩阵。"""
    if not items:
        return "<p>暂无风险或机会提示。</p>"
    rows = []
    for item in items:
        kind = "opportunity" if item.get("type") == "opportunity" else "risk"
        title = f"{item.get('horizon_label', '')} · {item.get('type_label', '')}"
        rows.append(
            f"""<div class="matrix-cell {kind}">
  <h3>{escape(title)}</h3>
  <p>{escape(item.get("note", ""))}</p>
  <p class="note">理由：{escape(item.get("reason", ""))}</p>
  <p class="meta">支撑事件：{escape(', '.join(item.get("supporting_event_ids", [])))}</p>
</div>"""
        )
    return "\n".join(rows)


def appendix_rows(events: list[dict[str, Any]], title_translations: dict[str, str]) -> str:
    """渲染结构化事件附录。"""
    if not events:
        return "<p>暂无结构化事件。</p>"
    rows = []
    for event in events:
        rows.append(
            f"""<div class="appendix-row">
  <strong>{escape(event.get("id", ""))}</strong> · {escape(translated_title(event, title_translations))}
  <br><span>英文原题：{escape(event.get("title", ""))}</span>
  <br><span>{escape(event.get("source_scope", ""))} / {escape(event.get("source_name", ""))} / {escape(translate_area(event.get("industry_area", "")))}</span>
</div>"""
        )
    return "\n".join(rows)


def summarize_events(events: list[dict[str, Any]], title_translations: dict[str, str]) -> list[dict[str, Any]]:
    """把原始事件整理为页面事件卡片字段。"""
    rows = []
    for event in events:
        rows.append(
            {
                "id": event.get("id", ""),
                "title_zh": translated_title(event, title_translations),
                "title": event.get("title", ""),
                "url": event.get("url", ""),
                "source_name": event.get("source_name", ""),
                "source_type": event.get("source_type", ""),
                "area_label": translate_area(event.get("industry_area", "")),
                "keywords": extract_keywords(event),
                "hotness_score": event.get("hotness_score", ""),
                "summary": event.get("summary", ""),
                "selection_reason": event.get("selection_reason", ""),
                "impact_analysis": event.get("impact_analysis", ""),
                "risk_or_opportunity": event.get("risk_or_opportunity", ""),
            }
        )
    return rows


def keyword_pills(keywords: list[Any]) -> str:
    """渲染事件关键词。"""
    if not keywords:
        return '<span class="pill keyword">暂无关键词</span>'
    return "".join(f'<span class="pill keyword">{escape(keyword)}</span>' for keyword in keywords[:8])


def translate_event_titles(state: InsightEngineState, events: list[dict[str, Any]]) -> dict[str, Any]:
    """把事件英文标题逐条翻译成中文。

    这是 Stage 5 唯一允许调用 LLM 的地方。它不做分析、不改写事件事实，只做标题翻译。
    如果没有配置 API key 或 LLM 失败，返回英文原题作为兜底，并在 state.warning 中记录，
    避免再次用规则拼接的中文短语冒充翻译。
    """
    unique_titles = []
    for event in events:
        title = str(event.get("title", "")).strip()
        if title and title not in unique_titles:
            unique_titles.append(title)

    translations = {title: title for title in unique_titles}
    if not unique_titles:
        return {"status": "empty", "translations": translations, "missing_titles": []}

    client = OpenAICompatibleChatClient.from_deepseek_env()
    if client is None:
        state.add_warning(
            "generate_report",
            "未配置 DEEPSEEK_API_KEY，标题中文翻译未执行，HTML 将保留英文原题",
        )
        return {
            "status": "fallback_no_api_key",
            "translations": translations,
            "missing_titles": unique_titles,
        }

    title_items = [{"id": f"t{index + 1}", "title": title} for index, title in enumerate(unique_titles)]
    messages = [
        {
            "role": "system",
            "content": (
                "你是新闻标题翻译器。你的唯一任务是把英文新闻标题忠实翻译成中文。"
                "不得摘要、不得改写、不得添加事实、不得省略原题中的主体、动作和限制条件。"
                "公司名、人名、产品名可以保留英文或采用通用中文译名。"
            ),
        },
        {
            "role": "user",
            "content": (
                "请把下面标题逐条翻译成中文，返回 JSON object，格式严格为："
                '{"translations":{"t1":"中文标题","t2":"中文标题"}}。\n\n'
                f"{json.dumps({'titles': title_items}, ensure_ascii=False, indent=2)}"
            ),
        },
    ]

    try:
        response = client.chat_json(messages=messages, temperature=0.0)
        payload = json.loads(response.content)
        raw_translations = payload.get("translations", {})
    except (LLMClientError, json.JSONDecodeError, TypeError) as exc:
        state.add_warning(
            "generate_report",
            "标题中文翻译失败，HTML 将保留英文原题",
            repr(exc),
        )
        return {
            "status": "fallback_after_error",
            "translations": translations,
            "missing_titles": unique_titles,
            "error": repr(exc),
        }

    missing_titles: list[str] = []
    for item in title_items:
        source_title = item["title"]
        translated = str(raw_translations.get(item["id"], "")).strip()
        if translated and has_cjk(translated):
            translations[source_title] = translated
        else:
            missing_titles.append(source_title)

    status = "ok" if not missing_titles else "partial"
    if missing_titles:
        state.add_warning(
            "generate_report",
            "部分标题未得到有效中文翻译，已保留英文原题",
            {"missing_count": len(missing_titles)},
        )
    return {
        "status": status,
        "model": response.model,
        "translations": translations,
        "missing_titles": missing_titles,
    }


def translated_title(event: dict[str, Any], title_translations: dict[str, str]) -> str:
    """读取事件的 LLM 中文标题翻译。"""
    title = str(event.get("title", "")).strip()
    explicit_title = event.get("title_zh") or event.get("chinese_title")
    if explicit_title and has_cjk(str(explicit_title)):
        return str(explicit_title)
    return title_translations.get(title, title)


def extract_keywords(event: dict[str, Any]) -> list[str]:
    """整理事件关键词，优先使用 topic_tags，再补充来源和方向。"""
    keywords: list[str] = []
    for tag in event.get("topic_tags", []):
        value = str(tag).strip()
        if value and value not in keywords:
            keywords.append(value)
    area = translate_area(event.get("industry_area", ""))
    if area and area not in keywords:
        keywords.insert(0, area)
    source = str(event.get("source_name", "")).strip()
    if source and source not in keywords:
        keywords.append(source)
    return keywords[:8]


def has_cjk(value: str) -> bool:
    """判断字符串是否包含中文字符。"""
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def build_trend_cards(analysis_result: dict[str, Any]) -> list[dict[str, str]]:
    """整理趋势判断四象限。"""
    trend = analysis_result.get("trend_judgment", {}) if isinstance(analysis_result, dict) else {}
    reasoning = analysis_result.get("trend_reasoning", {}) if isinstance(analysis_result, dict) else {}
    return [
        {"label": "技术方向", "judgment": str(trend.get("technology", "")), "reason": str(reasoning.get("technology", ""))},
        {"label": "应用方向", "judgment": str(trend.get("application", "")), "reason": str(reasoning.get("application", ""))},
        {"label": "政策方向", "judgment": str(trend.get("policy", "")), "reason": str(reasoning.get("policy", ""))},
        {"label": "资本方向", "judgment": str(trend.get("capital", "")), "reason": str(reasoning.get("capital", ""))},
    ]


def build_risk_matrix(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 Stage 4 的风险机会提示整理成矩阵卡片。"""
    rows = []
    for index, note in enumerate(notes):
        text = f"{note.get('area', '')} {note.get('note', '')} {note.get('reason', '')}"
        is_opportunity = any(word in text for word in ["机会", "增长", "加速", "推动", "窗口"])
        is_short = index < 2 or any(word in text for word in ["短期", "今日", "近期"])
        rows.append(
            {
                "type": "opportunity" if is_opportunity else "risk",
                "type_label": "机会" if is_opportunity else "风险",
                "horizon": "short" if is_short else "medium",
                "horizon_label": "短期" if is_short else "中期",
                "area": str(note.get("area", "")),
                "note": str(note.get("note", "")),
                "reason": str(note.get("reason", "")),
                "supporting_event_ids": [str(item) for item in note.get("supporting_event_ids", [])],
            }
        )
    return rows


def to_pie_slices(counts: dict[str, Any], label_map: dict[str, str]) -> list[dict[str, Any]]:
    """把计数字典转换成饼图切片。"""
    rows = []
    sorted_items = sorted(counts.items(), key=lambda item: int(item[1] or 0), reverse=True)[:8]
    for index, (key, value) in enumerate(sorted_items):
        normalized_key = str(key or "unknown")
        rows.append(
            {
                "key": normalized_key,
                "label": label_map.get(normalized_key, normalized_key),
                "value": int(value or 0),
                "color": PIE_COLORS[index % len(PIE_COLORS)],
            }
        )
    return [item for item in rows if item["value"] > 0]


def validate_report_markdown(markdown: str) -> list[str]:
    """检查报告是否包含全部必需章节标题。"""
    errors: list[str] = []
    if not markdown.strip():
        return ["报告内容为空"]
    for heading in REQUIRED_REPORT_HEADINGS:
        if heading not in markdown:
            errors.append(f"缺少章节：{heading}")
    return errors


def count_by_field(events: list[dict[str, Any]], field: str) -> dict[str, int]:
    """按指定字段统计事件计数值分布。"""
    return dict(Counter(str(event.get(field, "unknown") or "unknown") for event in events))


def count_tags(events: list[dict[str, Any]]) -> dict[str, int]:
    """统计所有事件的 topic_tags 出现频次，返回 Top 10。"""
    counter = Counter(tag for event in events for tag in event.get("topic_tags", []))
    return dict(counter.most_common(10))


def count_high_importance(events: list[dict[str, Any]]) -> int:
    """统计高重要性事件数。"""
    return sum(1 for event in events if str(event.get("importance_level", "")).lower() == "high")


def translate_area(value: Any) -> str:
    """把内部行业字段转换为中文标签。"""
    key = str(value or "unknown")
    return AREA_LABELS.get(key, key)


def escape(value: Any) -> str:
    """HTML 转义。"""
    return html.escape(str(value or ""), quote=True)


def schema_html_tables() -> str:
    """将全部 FIELD_SPEC 渲染为 HTML 表格，嵌入报告页面。"""
    rows: list[str] = []

    rows.append(_html_spec_table(
        title="Raw Item 字段合同（Stage 1 产出）",
        desc="原始数据抓取后每个 item 必须满足的结构。global 和 AI 两条数据线共用此合同。",
        spec=RAW_ITEM_FIELD_SPEC,
    ))

    rows.append(_html_spec_table(
        title="Cleaned Item 字段合同（Stage 2 产出）",
        desc="清洗去重后每个 item 的字段结构。新增标签、质量分、分析候选标记。",
        spec=CLEANED_ITEM_FIELD_SPEC,
    ))

    rows.append(_html_spec_table(
        title="Structured Event 字段合同（Stage 3 产出）",
        desc="LLM 或 fallback 生成的每条结构化事件字段。URL 和发布时间不允许 LLM 编造。",
        spec=STRUCTURED_EVENT_FIELD_SPEC,
    ))

    rows.append(_html_spec_table(
        title="Analysis Result 字段合同（Stage 4 产出）",
        desc="分析阶段的输出是下游报告的输入。每个子字段有独立的类型和必填子字段约束。",
        spec=ANALYSIS_RESULT_FIELD_SPEC,
    ))

    rows.append(_html_spec_table(
        title="Report Paths 字段合同（Stage 5 产出）",
        desc="报告生成后登记的全部产物路径。下游（页面/CLI/audit）按此合同读取文件。",
        spec=REPORT_PATHS_FIELD_SPEC,
    ))

    return "\n".join(rows)


def _html_spec_table(title: str, desc: str, spec: dict[str, dict]) -> str:
    """把一个 FIELD_SPEC 渲染为 HTML 表格。"""
    header = "".join(
        f"<th>{escape(col)}</th>"
        for col in ["字段名", "类型", "必填", "用途说明"]
    )
    body_rows: list[str] = []
    for field_name, meta in spec.items():
        req = "<strong>是</strong>" if meta.get("required") else "否"
        ftype = escape(meta.get("type", ""))
        purpose = escape(meta.get("purpose", ""))
        extra = meta.get("required_fields") or meta.get("item_required_fields")
        if extra:
            sub = "、".join(str(x) for x in extra[:8])
            purpose += f"（子字段：{sub}）"
        body_rows.append(
            f"<tr><td><code>{escape(field_name)}</code></td>"
            f"<td>{ftype}</td><td>{req}</td><td>{purpose}</td></tr>"
        )
    return (
        f'<div class="schema-table">'
        f"<h3>{escape(title)}</h3>"
        f"<p>{escape(desc)}</p>"
        f'<table><thead><tr>{header}</tr></thead>'
        f"<tbody>{''.join(body_rows)}</tbody></table>"
        f"</div>"
    )
    """HTML 转义。"""
    return html.escape(str(value or ""), quote=True)


def write_text(path: Path, content: str) -> None:
    """创建父目录并写入文本文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
