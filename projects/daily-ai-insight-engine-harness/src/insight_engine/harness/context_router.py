"""Context Router。

Context Router 决定每个 stage 能看到哪些 State 字段和运行时文档。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from insight_engine.harness.artifacts import project_root
from insight_engine.harness.state import InsightEngineState


@dataclass(frozen=True)
class ContextPackage:
    """某个 stage 的上下文包。"""

    stage_name: str
    agent_prompt_path: str
    runtime_doc_paths: list[str]
    visible_state: dict[str, Any]

# 每一个agent的prompt
STAGE_AGENT_PROMPTS = {
    "structure_events": "prompts/agents/structuring_agent.md",
    "analyze_insights": "prompts/agents/analysis_agent.md",
    "generate_report": "prompts/agents/report_agent.md",
}

# 可以使用的state数据
STAGE_VISIBLE_FIELDS = {
    "collect_raw_items": ["run_id", "target_date", "sources", "errors", "warnings"],
    "clean_items": [
        "run_id",
        "target_date",
        "global_raw_items",
        "ai_raw_items",
        "artifacts",
        "errors",
        "warnings",
    ],
    "structure_events": [
        "run_id",
        "target_date",
        "global_cleaned_items",
        "ai_cleaned_items",
        "artifacts",
        "errors",
        "warnings",
    ],
    "analyze_insights": [
        "run_id",
        "target_date",
        "global_structured_events",
        "ai_structured_events",
        "artifacts",
        "errors",
        "warnings",
    ],
    "generate_report": [
        "run_id",
        "target_date",
        "global_structured_events",
        "ai_structured_events",
        "analysis_result",
        "artifacts",
        "errors",
        "warnings",
    ],
}

# 每个阶段需要的运行时文档
RUNTIME_DOCS = {
    "structure_events": [
        "docs/runtime/global_rules.md",
        "docs/runtime/final_output_format.md",
    ],
    "analyze_insights": [
        "docs/runtime/global_rules.md",
        "docs/runtime/final_output_format.md",
    ],
    "generate_report": [
        "docs/runtime/global_rules.md",
        "docs/runtime/final_output_format.md",
    ],
}


def build_context_package(stage_name: str, state: InsightEngineState) -> ContextPackage:
    """构建当前 stage 可见上下文。"""
    state_dict = state.to_dict()
    visible_fields = STAGE_VISIBLE_FIELDS.get(stage_name, [])
    visible_state = {
        field: compact_for_prompt(field, state_dict.get(field), state.artifacts)
        for field in visible_fields
    }

    return ContextPackage(
        stage_name=stage_name,
        agent_prompt_path=STAGE_AGENT_PROMPTS.get(stage_name, ""),
        runtime_doc_paths=RUNTIME_DOCS.get(stage_name, []),
        visible_state=visible_state,
    )


def read_context_file(relative_path: str) -> str:
    """读取上下文文档。文件不存在时返回空字符串。"""
    if not relative_path:
        return ""
    path = project_root() / relative_path
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def context_file_exists(relative_path: str) -> bool:
    """判断上下文文件是否存在。"""
    return bool(relative_path) and (project_root() / relative_path).exists()


def compact_for_prompt(field: str, value: Any, artifacts: dict[str, str]) -> Any:
    """把 State 字段压缩成适合 prompt 快照的形式。

    完整数据保存在 artifact 文件中，prompt 里只放计数、样例和路径，避免上下文过大。
    """
    if isinstance(value, list):
        return {
            "count": len(value),
            "sample": value[:3],
            "artifact_hint": _artifact_hint_for_field(field, artifacts),
        }
    if isinstance(value, dict):
        if field == "artifacts":
            return value
        return {
            "keys": list(value.keys()),
            "sample": dict(list(value.items())[:8]),
            "artifact_hint": _artifact_hint_for_field(field, artifacts),
        }
    return value


def _artifact_hint_for_field(field: str, artifacts: dict[str, str]) -> str | None:
    mapping = {
        "raw_items": "raw_items",
        "cleaned_items": "cleaned_items",
        "structured_events": "structured_events",
        "global_raw_items": "global_raw_items",
        "global_cleaned_items": "global_cleaned_items",
        "global_structured_events": "global_structured_events",
        "ai_raw_items": "raw_items",
        "ai_cleaned_items": "cleaned_items",
        "ai_structured_events": "structured_events",
        "analysis_result": "analysis_result",
        "report_paths": "report_manifest",
    }
    artifact_key = mapping.get(field)
    if artifact_key:
        return artifacts.get(artifact_key)
    return None
