"""Prompt Builder。

即使 V1 的主体逻辑是规则代码，也保留 prompt 组装能力。
这样后续替换为 LLM Agent 时，不需要重写 Harness。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from insight_engine.harness.context_router import build_context_package, read_context_file
from insight_engine.harness.state import InsightEngineState

PROMPTLESS_STAGES = {"collect_raw_items", "clean_items"}


@dataclass(frozen=True)
class PromptPackage:
    """某个 stage 的完整 prompt 包。

    stage_name          当前 stage 名称
    prompt_text         最终拼好的完整 prompt
    context_paths       本次用了哪些文档
    visible_state_keys  本次暴露了哪些 State 字段
    """

    stage_name: str
    prompt_text: str
    context_paths: list[str]
    visible_state_keys: list[str]


def build_prompt_package(stage_name: str, state: InsightEngineState) -> PromptPackage:
    """组装 stage prompt 快照。

    从 context_router 获取该 stage 的 agent prompt、runtime docs 和可见 state 字段，
    拼成完整的 prompt 文本，用于事后审计和复现。
    """
    context = build_context_package(stage_name=stage_name, state=state)

    sections: list[str] = [
        f"# Stage Prompt: {stage_name}",
        "",
        "## Agent Role",
        read_context_file(context.agent_prompt_path),
        "",
        "## Runtime Documents",
    ]

    for doc_path in context.runtime_doc_paths:
        sections.extend([f"### {doc_path}", read_context_file(doc_path), ""])

    sections.extend(
        [
            "## Visible State",
            "```json",
            json.dumps(context.visible_state, ensure_ascii=False, indent=2, default=str),
            "```",
            "",
            "## Output Requirement",
            "请严格产出当前 stage 对应的结构化结果，并保留可追踪证据。",
            "",
        ]
    )

    return PromptPackage(
        stage_name=stage_name,
        prompt_text="\n".join(sections),
        context_paths=[context.agent_prompt_path, *context.runtime_doc_paths],
        visible_state_keys=list(context.visible_state.keys()),
    )


def should_build_prompt(stage_name: str) -> bool:
    """判断当前 stage 是否需要生成 LLM prompt 快照。

    确定性 stage（collect_raw_items、clean_items）不调 LLM，因此不需要快照。
    """
    return stage_name not in PROMPTLESS_STAGES


def build_retry_feedback(state: InsightEngineState, stage_name: str) -> str:
    """构建重试反馈文本，告诉 LLM 上一次为什么失败。

    从 state.stage_gate_results 中提取该 stage 最近一次失败的 linter 问题，
    格式化为"上次尝试的失败原因"，注入到 LLM prompt 中。
    如果没有历史失败记录，返回空字符串。
    """
    retry_count = state.stage_retry_counts.get(stage_name, 0)
    if retry_count == 0:
        return ""

    # 找到该 stage 最近一次失败的 gate 结果
    last_failure = None
    for gate in reversed(state.stage_gate_results):
        if gate.get("stage") == stage_name and not gate.get("passed"):
            last_failure = gate
            break

    if last_failure is None:
        return ""

    issues = last_failure.get("issues", [])
    if not issues:
        return ""

    lines: list[str] = [
        "",
        "## 重试反馈 —— 上一次尝试失败的原因",
        "",
        f"这是第 {retry_count} 次重试。上一次你产出的结果没有通过质量检查。",
        "请在本次尝试中特别关注以下问题并修复：",
        "",
    ]
    for issue in issues:
        lines.append(f"- {issue}")
    lines.append("")

    return "\n".join(lines)


def get_latest_gate_issues(state: InsightEngineState, stage_name: str) -> list[str]:
    """获取该 stage 最近一次 gate 检查的问题列表，供 stage handler 读取。"""
    for gate in reversed(state.stage_gate_results):
        if gate.get("stage") == stage_name:
            return gate.get("issues", [])
    return []
