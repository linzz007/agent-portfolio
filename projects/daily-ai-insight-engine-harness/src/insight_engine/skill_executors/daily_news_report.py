"""今日新闻分析报告 Skill 执行器。

这个模块是页面对话层真正调用的能力入口：
对话 Agent 只需要判断用户想要日报，然后调用 `run_daily_news_report_skill`。
具体的数据获取、清洗、结构化、分析、报告生成、hook 和 gate 都由 graph/state 内部完成。
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from insight_engine.harness.artifacts import ensure_run_dir
from insight_engine.harness.env import load_project_env
from insight_engine.harness.graph import InsightEngineGraph, build_graph
from insight_engine.harness.state import InsightEngineState, utc_now_iso
from insight_engine.stages.analyze_insights import analyze_insights
from insight_engine.stages.clean_items import clean_items
from insight_engine.stages.collect_raw_items import collect_raw_items
from insight_engine.stages.generate_report import generate_report
from insight_engine.stages.structure_events import structure_events


@dataclass(frozen=True)
class DailyNewsReportResult:
    """daily_news_report_skill 的返回对象。"""

    state: InsightEngineState
    summary_paths: dict[str, str]

    @property
    def report_path(self) -> str | None:
        return self.state.artifacts.get("report")

    @property
    def report_html_path(self) -> str | None:
        return self.state.artifacts.get("report_html")

    @property
    def chart_path(self) -> str | None:
        return self.state.artifacts.get("chart_html")

    @property
    def pipeline_summary_path(self) -> str | None:
        return self.summary_paths.get("markdown")

    @property
    def run_artifact_path(self) -> str | None:
        return self.summary_paths.get("run_artifact_json")


def build_daily_news_report_graph() -> InsightEngineGraph:
    """注册日报 Skill 需要的完整 graph。"""
    return build_graph(
        {
            "collect_raw_items": collect_raw_items,
            "clean_items": clean_items,
            "structure_events": structure_events,
            "analyze_insights": analyze_insights,
            "generate_report": generate_report,
        }
    )


def run_daily_news_report_skill(state: InsightEngineState | None = None) -> DailyNewsReportResult:
    """运行完整的今日新闻分析报告能力。"""
    load_project_env()
    initial_state = state or InsightEngineState()
    graph = build_daily_news_report_graph()
    final_state = graph.run(initial_state)
    summary_paths = write_pipeline_summary(final_state)
    return DailyNewsReportResult(state=final_state, summary_paths=summary_paths)


def format_daily_news_report_result(
    result: DailyNewsReportResult,
    *,
    include_summary: bool = False,
) -> str:
    """把 Skill 结果格式化成页面或 CLI 可以直接展示的中文文本。"""
    state = result.state
    lines = [
        "今日新闻分析报告已生成。" if state.current_stage == "done" else "今日新闻分析报告生成未完成。",
        "",
        f"- run_id: `{state.run_id}`",
        f"- final_stage: `{state.current_stage}`",
        f"- report: `{result.report_path}`",
        f"- report_html: `{result.report_html_path}`",
        f"- chart_html: `{result.chart_path}`",
        f"- pipeline_summary: `{result.pipeline_summary_path}`",
        f"- run_artifact: `{result.run_artifact_path}`",
        "",
        "数据量：",
        f"- global_raw_items: {len(state.global_raw_items)}",
        f"- ai_raw_items: {len(state.ai_raw_items)}",
        f"- global_structured_events: {len(state.global_structured_events)}",
        f"- ai_structured_events: {len(state.ai_structured_events)}",
    ]

    if state.errors:
        lines.extend(["", "错误："])
        for error in state.errors[:5]:
            lines.append(f"- {error.get('stage')}: {error.get('message')}")

    if state.warnings:
        lines.extend(["", "警告："])
        for warning in state.warnings[:5]:
            lines.append(f"- {warning.get('stage')}: {warning.get('message')}")

    if include_summary and result.pipeline_summary_path:
        path = Path(result.pipeline_summary_path)
        if path.exists():
            lines.extend(["", "流程摘要：", path.read_text(encoding="utf-8")])

    return "\n".join(lines)


def write_pipeline_summary(state: InsightEngineState) -> dict[str, str]:
    """写出一次完整 Skill 运行的总览文件。"""
    output_dir = ensure_run_dir("outputs/pipeline", state.run_id)
    json_path = output_dir / "pipeline_summary.json"
    md_path = output_dir / "pipeline_summary.md"
    run_artifact_md_path = output_dir / "run_artifact.md"
    run_artifact_json_path = output_dir / "run_artifact.json"

    state.add_artifact("pipeline_summary", str(md_path))
    state.add_artifact("pipeline_summary_json", str(json_path))
    state.add_artifact("run_artifact_md", str(run_artifact_md_path))
    state.add_artifact("run_artifact", str(run_artifact_json_path))
    state.add_artifact("run_artifact_json", str(run_artifact_json_path))

    payload = build_summary_payload(state)

    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(build_summary_markdown(payload), encoding="utf-8")
    run_artifact_md_path.write_text(build_run_artifact_markdown(payload), encoding="utf-8")
    run_artifact_json = build_run_artifact_json_payload(
        state=state,
        summary_payload=payload,
        run_artifact_path=run_artifact_json_path,
    )
    run_artifact_json_path.write_text(json.dumps(run_artifact_json, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "markdown": str(md_path),
        "json": str(json_path),
        "run_artifact": str(run_artifact_json_path),
        "run_artifact_json": str(run_artifact_json_path),
        "run_artifact_md": str(run_artifact_md_path),
    }


def build_summary_payload(state: InsightEngineState) -> dict[str, Any]:
    """构造流程摘要 JSON，方便页面、测试和人工检查读取。"""
    analysis = state.analysis_result
    return {
        "run_id": state.run_id,
        "target_date": state.target_date,
        "final_stage": state.current_stage,
        "counts": {
            "global_raw_items": len(state.global_raw_items),
            "ai_raw_items": len(state.ai_raw_items),
            "global_cleaned_items": len(state.global_cleaned_items),
            "ai_cleaned_items": len(state.ai_cleaned_items),
            "global_structured_events": len(state.global_structured_events),
            "ai_structured_events": len(state.ai_structured_events),
        },
        "stage_trace": state.stage_trace,
        "stage_gate_results": state.stage_gate_results,
        "stage_retry_counts": state.stage_retry_counts,
        "artifacts": state.artifacts,
        "warnings": state.warnings,
        "errors": state.errors,
        "top_events": analysis.get("top_events", [])[:5] if isinstance(analysis, dict) else [],
        "global_top_events": analysis.get("global_top_events", [])[:5] if isinstance(analysis, dict) else [],
        "react_actions": {
            "analyze_insights": load_react_actions(state.artifacts.get("analysis_result")),
        },
    }


def build_summary_markdown(payload: dict[str, Any]) -> str:
    """把流程摘要 JSON 渲染成方便阅读的 Markdown。"""
    lines = [
        f"# Pipeline Summary - {payload['target_date']}",
        "",
        f"- run_id: `{payload['run_id']}`",
        f"- final_stage: `{payload['final_stage']}`",
        "",
        "## Counts",
        "",
    ]
    for key, value in payload["counts"].items():
        lines.append(f"- {key}: {value}")

    lines.extend(["", "## Key Artifacts", ""])
    for key, value in payload["artifacts"].items():
        lines.append(f"- {key}: `{value}`")

    lines.extend(["", "## AI Top Events", ""])
    for event in payload["top_events"]:
        lines.append(f"- {event.get('id')}: {event.get('title')}")

    lines.extend(["", "## Global Top Events", ""])
    for event in payload["global_top_events"]:
        lines.append(f"- {event.get('id')}: {event.get('title')}")

    lines.extend(["", "## Stage Trace", ""])
    for trace in payload["stage_trace"]:
        gate = trace.get("gate") or {}
        gate_status = "gate=pass" if gate.get("passed") else "gate=fail"
        lines.append(f"- {trace.get('stage')}: {trace.get('status')} / {trace.get('duration_ms')}ms / {gate_status}")

    lines.extend(["", "## Stage Gates", ""])
    for gate in payload.get("stage_gate_results", []):
        status = "pass" if gate.get("passed") else "fail"
        lines.append(f"- {gate.get('stage')}: {status}")
        for issue in gate.get("issues", [])[:5]:
            lines.append(f"  - {issue}")

    if payload.get("stage_retry_counts"):
        lines.extend(["", "## Stage Retries", ""])
        for stage, count in payload["stage_retry_counts"].items():
            lines.append(f"- {stage}: {count}")

    lines.extend(["", "## ReAct Actions", ""])
    for stage, actions in payload.get("react_actions", {}).items():
        if actions:
            lines.append(f"- {stage}: {' -> '.join(actions)}")
        else:
            lines.append(f"- {stage}: 无可展示 action")

    if payload.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        for warning in payload["warnings"]:
            lines.append(f"- {warning.get('stage')}: {warning.get('message')}")

    if payload.get("errors"):
        lines.extend(["", "## Errors", ""])
        for error in payload["errors"]:
            lines.append(f"- {error.get('stage')}: {error.get('message')}")

    return "\n".join(lines)


def build_run_artifact_markdown(payload: dict[str, Any]) -> str:
    """生成单文档 Run Artifact，按执行顺序展示完整 Harness 流程。"""
    lines = [
        f"# Run Artifact - {payload['target_date']}",
        "",
        "这个文件是一次日报生成任务的单文档总览。它不替代各 stage 的 JSON 原始证据，",
        "而是把 state、graph、stage gate、hook、ReAct 和 report 串成一条可读链路。",
        "",
        "## 运行结论",
        "",
        f"- run_id: `{payload['run_id']}`",
        f"- final_stage: `{payload['final_stage']}`",
        f"- warnings: {len(payload.get('warnings', []))}",
        f"- errors: {len(payload.get('errors', []))}",
        "",
        "## 流程总览",
        "",
        "```text",
        "collect_raw_items -> clean_items -> structure_events -> analyze_insights -> generate_report -> done",
        "```",
        "",
        "## 数据规模",
        "",
    ]
    for key, value in payload["counts"].items():
        lines.append(f"- {key}: {value}")

    lines.extend(["", "## Stage 执行明细", ""])
    for index, trace in enumerate(payload.get("stage_trace", []), start=1):
        stage = trace.get("stage")
        gate = trace.get("gate") or {}
        gate_status = "pass" if gate.get("passed") else "fail"
        artifact_keys = trace.get("artifact_keys") or []
        lines.extend(
            [
                f"### {index}. {stage}",
                "",
                f"- status: `{trace.get('status')}`",
                f"- duration_ms: `{trace.get('duration_ms')}`",
                f"- gate: `{gate_status}`",
                f"- started_at: `{trace.get('started_at')}`",
                f"- finished_at: `{trace.get('finished_at')}`",
            ]
        )
        if trace.get("error"):
            lines.append(f"- error: `{trace.get('error')}`")
        gate_issues = gate.get("issues") or []
        if gate_issues:
            lines.append("- gate_issues:")
            for issue in gate_issues[:8]:
                lines.append(f"  - {issue}")
        stage_artifacts = artifacts_for_stage(payload.get("artifacts", {}), str(stage), artifact_keys)
        if stage_artifacts:
            lines.append("- artifacts:")
            for key, value in stage_artifacts.items():
                lines.append(f"  - {key}: `{value}`")
        lines.append("")

    lines.extend(["## ReAct 轨迹", ""])
    react_actions = payload.get("react_actions", {})
    for stage, actions in react_actions.items():
        if actions:
            lines.append(f"- {stage}: {' -> '.join(actions)}")
        else:
            lines.append(f"- {stage}: 无 ReAct action")

    lines.extend(["", "## Stage Gate 结果", ""])
    for gate in payload.get("stage_gate_results", []):
        status = "pass" if gate.get("passed") else "fail"
        lines.append(f"- {gate.get('stage')}: `{status}`")
        for issue in gate.get("issues", [])[:8]:
            lines.append(f"  - {issue}")

    lines.extend(["", "## 关键业务输出", "", "### AI Top Events", ""])
    for event in payload.get("top_events", []):
        lines.append(
            f"- {event.get('id')}: {event.get('title')} "
            f"(hotness={event.get('hotness_score')}, area={event.get('industry_area')})"
        )

    lines.extend(["", "### Global Top Events", ""])
    for event in payload.get("global_top_events", []):
        lines.append(
            f"- {event.get('id')}: {event.get('title')} "
            f"(hotness={event.get('hotness_score')}, area={event.get('industry_area')})"
        )

    lines.extend(["", "## 输出文件", ""])
    for key in [
        "report",
        "report_html",
        "chart_html",
        "chart_data",
        "title_translations",
        "report_manifest",
        "pipeline_summary",
        "pipeline_summary_json",
    ]:
        value = payload.get("artifacts", {}).get(key)
        if value:
            lines.append(f"- {key}: `{value}`")

    if payload.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        for warning in payload["warnings"]:
            lines.append(f"- {warning.get('stage')}: {warning.get('message')}")

    if payload.get("errors"):
        lines.extend(["", "## Errors", ""])
        for error in payload["errors"]:
            lines.append(f"- {error.get('stage')}: {error.get('message')}")

    lines.extend(
        [
            "",
            "## Artifact 设计说明",
            "",
            "- 分散 JSON：每个 stage 的原始证据，适合机器校验、重放和定位问题。",
            "- run_artifact.md：一次运行的单文档视图，适合学习 Harness 流程和人工复盘。",
            "- pipeline_summary.md/json：轻量摘要，适合 CLI 和页面快速展示。",
            "",
        ]
    )
    return "\n".join(lines)


def build_run_artifact_json_payload(
    state: InsightEngineState,
    summary_payload: dict[str, Any],
    run_artifact_path: Path,
) -> dict[str, Any]:
    """生成自包含 JSON Run Artifact。

    这个 JSON 是主审计产物：它不只保存 artifact 路径，还会把已登记文件的内容嵌入进来。
    这样阅读者不需要再跳到 prompt、state snapshot、LLM response、ReAct step、报告文件里逐个看。
    """
    artifact_contents = load_artifact_contents(state.artifacts)
    return {
        "schema_version": "daily_ai_insight_run_artifact.v1",
        "artifact_type": "self_contained_run_artifact",
        "generated_at": utc_now_iso(),
        "self": {
            "path": str(run_artifact_path),
            "note": "这是主 Run Artifact JSON；除自身外，已登记 artifact 的内容会尽量嵌入 artifact_contents。",
        },
        "run": {
            "run_id": state.run_id,
            "target_date": state.target_date,
            "created_at": state.created_at,
            "final_stage": state.current_stage,
        },
        "workflow": [
            "collect_raw_items",
            "clean_items",
            "structure_events",
            "analyze_insights",
            "generate_report",
            "done",
        ],
        "counts": summary_payload.get("counts", {}),
        "timeline": build_run_timeline(summary_payload, artifact_contents),
        "stage_trace": state.stage_trace,
        "stage_gate_results": state.stage_gate_results,
        "stage_retry_counts": state.stage_retry_counts,
        "warnings": state.warnings,
        "errors": state.errors,
        "react_actions": summary_payload.get("react_actions", {}),
        "top_events": summary_payload.get("top_events", []),
        "global_top_events": summary_payload.get("global_top_events", []),
        "state": state.to_dict(),
        "artifact_manifest": build_artifact_manifest(state.artifacts, artifact_contents),
        "artifact_contents": artifact_contents,
        "reading_guide": {
            "timeline": "按 stage 顺序看每一步发生了什么、gate 是否通过、关联了哪些 artifact。",
            "artifact_contents": "所有已登记 artifact 的嵌入内容，JSON 文件会解析为 object，Markdown/HTML 会作为 text 保存。",
            "state": "最终 state 快照，包含所有核心字段、stage trace、gate、warnings、errors 和产物路径。",
        },
    }


def build_run_timeline(
    summary_payload: dict[str, Any],
    artifact_contents: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """按 stage 顺序整理发生过的事情，并把相关 artifact 内容挂进去。"""
    artifacts = summary_payload.get("artifacts", {})
    timeline: list[dict[str, Any]] = []
    for index, trace in enumerate(summary_payload.get("stage_trace", []), start=1):
        stage = str(trace.get("stage"))
        artifact_keys = trace.get("artifact_keys") or []
        stage_artifacts = artifacts_for_stage(artifacts, stage, artifact_keys)
        timeline.append(
            {
                "order": index,
                "stage": stage,
                "trace": trace,
                "gate": trace.get("gate"),
                "artifact_names": list(stage_artifacts.keys()),
                "artifacts": {
                    name: artifact_contents.get(name, {"embedded": False, "reason": "not_loaded"})
                    for name in stage_artifacts
                },
            }
        )
    return timeline


def build_artifact_manifest(
    artifacts: dict[str, str],
    artifact_contents: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """生成 artifact 清单，说明哪些内容已经嵌入到单 JSON 中。"""
    rows: list[dict[str, Any]] = []
    for name, path_value in artifacts.items():
        embedded = artifact_contents.get(name, {})
        path = Path(path_value)
        rows.append(
            {
                "name": name,
                "path": path_value,
                "exists": path.exists(),
                "embedded": bool(embedded.get("embedded")),
                "content_type": embedded.get("content_type"),
                "size_bytes": embedded.get("size_bytes"),
                "reason": embedded.get("reason"),
            }
        )
    return rows


def load_artifact_contents(artifacts: dict[str, str]) -> dict[str, dict[str, Any]]:
    """读取所有已登记 artifact 的实际内容，组成自包含 JSON。"""
    rows: dict[str, dict[str, Any]] = {}
    for name, path_value in artifacts.items():
        if name in {"run_artifact", "run_artifact_json"}:
            rows[name] = {
                "path": path_value,
                "embedded": False,
                "reason": "skip_self_reference",
            }
            continue
        rows[name] = read_artifact_file(path_value)
    return rows


def read_artifact_file(path_value: str) -> dict[str, Any]:
    """把单个 artifact 文件读成 JSON 友好的结构。"""
    path = Path(path_value)
    if not path.exists():
        return {
            "path": path_value,
            "embedded": False,
            "reason": "file_not_found",
        }
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {
            "path": path_value,
            "embedded": False,
            "reason": "non_utf8_file",
            "size_bytes": path.stat().st_size,
        }
    except OSError as exc:
        return {
            "path": path_value,
            "embedded": False,
            "reason": "read_error",
            "error": repr(exc),
        }

    payload: dict[str, Any] = {
        "path": path_value,
        "embedded": True,
        "size_bytes": path.stat().st_size,
    }
    if path.suffix.lower() == ".json":
        try:
            payload["content_type"] = "json"
            payload["content"] = json.loads(text)
        except json.JSONDecodeError:
            payload["content_type"] = "text"
            payload["content"] = text
            payload["reason"] = "json_decode_failed"
    else:
        payload["content_type"] = "text"
        payload["content"] = text
    return payload


def artifacts_for_stage(
    artifacts: dict[str, str],
    stage_name: str,
    artifact_keys: list[str],
) -> dict[str, str]:
    """按 stage 名称筛出最相关的 artifact 路径，避免单文档里堆满无关文件。"""
    stage_prefixes = {
        "collect_raw_items": ["raw_items", "state_snapshot:collect_raw_items"],
        "clean_items": ["cleaned_items", "global_cleaned_items", "ai_cleaned_items", "state_snapshot:clean_items"],
        "structure_events": [
            "prompt:structure_events",
            "structured_events",
            "global_structured_events",
            "ai_structured_events",
            "state_snapshot:structure_events",
        ],
        "analyze_insights": ["prompt:analyze_insights", "react:analyze_insights", "analysis_result", "state_snapshot:analyze_insights"],
        "generate_report": [
            "prompt:generate_report",
            "report",
            "report_html",
            "chart_html",
            "chart_data",
            "title_translations",
            "report_manifest",
            "state_snapshot:generate_report",
        ],
    }
    prefixes = stage_prefixes.get(stage_name, [stage_name])
    allowed_keys = set(artifact_keys)
    rows: dict[str, str] = {}
    for key, value in artifacts.items():
        if key in allowed_keys and any(key == prefix or key.startswith(f"{prefix}:") for prefix in prefixes):
            rows[key] = value
    return rows


def load_react_actions(path_value: str | None) -> list[str]:
    """从 React 产物中读取动作序列，用于流程摘要。"""
    if not path_value:
        return []
    path = Path(path_value)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    trace = payload.get("react_trace")
    if not isinstance(trace, list):
        return []
    return [str(step.get("action")) for step in trace if isinstance(step, dict) and step.get("action")]
