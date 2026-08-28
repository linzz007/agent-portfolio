"""对话 Agent 的意图路由。

使用 LLM 根据 Skill 描述判断用户意图，决定是否调用 daily_news_report_skill。
LLM 不可用时自动降级为关键词匹配。

这是真正的 Skill 调用模式：LLM 读取 SKILL.md 描述 → 理解能力范围 →
判断用户请求是否匹配 → 决定是否触发。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from insight_engine.harness.artifacts import project_root
from insight_engine.harness.llm_client import LLMClientError, OpenAICompatibleChatClient
from insight_engine.skill_executors.daily_news_report import (
    DailyNewsReportResult,
    format_daily_news_report_result,
    run_daily_news_report_skill,
)


IntentName = Literal["daily_news_report", "chat"]

# 关键词兜底 —— LLM 不可用时的降级方案
DAILY_REPORT_KEYWORDS = [
    "今日新闻分析报告",
    "新闻分析报告",
    "今日新闻",
    "新闻日报",
    "ai日报",
    "AI日报",
    "生成日报",
    "生成报告",
    "日报",
    "新闻报告",
]


@dataclass(frozen=True)
class ConversationResponse:
    """对话层统一返回对象。"""

    intent: IntentName
    message: str
    reason: str = ""
    artifacts: dict[str, str | None] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    skill_result: DailyNewsReportResult | None = None


# ---------------------------------------------------------------------------
# Skill 描述加载
# ---------------------------------------------------------------------------

def load_skill_description() -> str:
    """加载 daily_news_report Skill 的完整描述，供 LLM 做意图判断。"""
    skill_path = project_root() / "skills" / "daily_news_report" / "SKILL.md"
    if not skill_path.exists():
        return ""
    return skill_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 意图检测（LLM 优先，关键词兜底）
# ---------------------------------------------------------------------------

def detect_intent(message: str) -> tuple[IntentName, str]:
    """判断用户消息意图。

    优先使用 LLM 读取 Skill 描述后判断；LLM 不可用时降级为关键词匹配。
    返回 (intent, reason)。
    """
    skill_desc = load_skill_description()
    llm_result = _classify_with_llm(message, skill_desc)
    if llm_result is not None:
        return llm_result

    # LLM 不可用，降级为关键词匹配
    normalized = message.strip().lower()
    if any(keyword.lower() in normalized for keyword in DAILY_REPORT_KEYWORDS):
        return "daily_news_report", "关键词匹配（LLM 不可用）"
    return "chat", "关键词匹配（LLM 不可用）"


def _classify_with_llm(
    message: str, skill_desc: str
) -> tuple[IntentName, str] | None:
    """用 LLM 判断用户意图。失败时返回 None。"""
    if not skill_desc:
        return None

    client = OpenAICompatibleChatClient.from_deepseek_env()
    if client is None:
        return None

    system_prompt = (
        "你是一个对话意图分类器。下面的 SKILL.md 描述了一个可用的 Skill 能力。"
        "请判断用户的消息是否在请求触发这个 Skill。\n\n"
        "## 可用 Skill\n\n"
        f"{skill_desc}\n\n"
        "## 输出要求\n\n"
        "只输出一个 JSON 对象：\n"
        '{"intent": "daily_news_report" | "chat", "reason": "用中文简短说明判断理由"}\n'
        '如果用户明确要求生成日报、新闻分析报告等，intent 为 "daily_news_report"。\n'
        '如果是普通闲聊、询问项目信息、或与日报无关的请求，intent 为 "chat"。'
    )

    try:
        response = client.chat_json(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message},
            ],
            temperature=0.0,
        )
        payload = json.loads(response.content)
        intent = payload.get("intent", "chat")
        reason = payload.get("reason", "LLM 分类")
        if intent in ("daily_news_report", "chat"):
            return intent, reason
        return "chat", reason
    except (LLMClientError, json.JSONDecodeError, KeyError):
        return None


# ---------------------------------------------------------------------------
# 消息处理入口
# ---------------------------------------------------------------------------

def handle_message(message: str, *, show_summary: bool = False) -> ConversationResponse:
    """处理一条页面对话消息。"""
    intent, reason = detect_intent(message)
    if intent == "daily_news_report":
        result = run_daily_news_report_skill()
        state = result.state
        return ConversationResponse(
            intent=intent,
            reason=reason,
            message=format_daily_news_report_result(result, include_summary=show_summary),
            artifacts={
                "report": result.report_path,
                "report_html": result.report_html_path,
                "chart_html": result.chart_path,
                "pipeline_summary": result.pipeline_summary_path,
                "pipeline_summary_json": result.summary_paths.get("json"),
                "run_artifact": result.run_artifact_path,
            },
            summary={
                "run_id": state.run_id,
                "final_stage": state.current_stage,
                "global_raw_items": len(state.global_raw_items),
                "ai_raw_items": len(state.ai_raw_items),
                "global_structured_events": len(state.global_structured_events),
                "ai_structured_events": len(state.ai_structured_events),
                "errors": state.errors,
                "warnings": state.warnings,
            },
            skill_result=result,
        )

    return ConversationResponse(
        intent="chat",
        reason=reason,
        message=(
            "这是 Daily AI Insight Engine 的对话入口。"
            "普通问题会在这里正常回答；如果你说「生成今日新闻分析报告」，"
            "系统会调用 daily_news_report_skill 并返回报告、图表和流程摘要路径。"
        ),
    )
