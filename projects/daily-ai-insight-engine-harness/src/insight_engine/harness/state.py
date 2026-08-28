"""Harness State 定义。

State 用来记录一次日报生成任务的完整运行状态。
后续 graph、context_router、hook 都会围绕这个对象工作。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any
from uuid import uuid4

# 预设默认数据源列表，方便后续扩展和修改。
DEFAULT_SOURCES = ["arxiv", "hacker_news", "techcrunch_rss", "the_verge_rss"]

# 跨阶段数据字段合同。
# 这些值本身不是运行时数据，而是 State 中各个核心字段应该满足的结构说明。
# stage 负责生成字段值，gate/linter 负责校验字段值，下游 stage 按合同消费字段值。
RAW_ITEM_FIELD_SPEC = {
    "source_id": {"type": "str", "required": True, "purpose": "数据源 ID。"},
    "source_scope": {"type": "str", "required": True, "purpose": "global 或 ai 数据线。"},
    "source_type": {"type": "str", "required": True, "purpose": "media、social、research 等来源类型。"},
    "title": {"type": "str", "required": True, "purpose": "原始新闻标题。"},
    "url": {"type": "str", "required": True, "purpose": "原始链接。"},
    "published_at": {"type": "str", "required": True, "purpose": "发布时间，保持来源原始格式或 ISO 格式。"},
    "author_or_org": {"type": "str", "required": False, "purpose": "作者或机构。"},
    "summary": {"type": "str", "required": False, "purpose": "来源摘要。"},
    "raw_content": {"type": "str", "required": False, "purpose": "原始正文或摘要内容。"},
    "retrieved_at": {"type": "str", "required": True, "purpose": "抓取时间。"},
    "metadata": {"type": "object", "required": False, "purpose": "来源特有字段。"},
}

CLEANED_ITEM_FIELD_SPEC = {
    "id": {"type": "str", "required": True, "purpose": "清洗后 item ID。"},
    "source_id": {"type": "str", "required": True, "purpose": "数据源 ID。"},
    "source_scope": {"type": "str", "required": True, "purpose": "global 或 ai 数据线。"},
    "source_type": {"type": "str", "required": True, "purpose": "来源类型。"},
    "title": {"type": "str", "required": True, "purpose": "标准化标题。"},
    "url": {"type": "str", "required": True, "purpose": "标准化链接。"},
    "published_at": {"type": "str", "required": True, "purpose": "解析后的发布时间。"},
    "published_date": {"type": "str|null", "required": False, "purpose": "发布时间日期。"},
    "recency_days": {"type": "int|null", "required": False, "purpose": "距离 target_date 的天数。"},
    "is_recent": {"type": "bool", "required": True, "purpose": "是否在近期窗口内。"},
    "summary": {"type": "str", "required": True, "purpose": "清洗后的摘要。"},
    "clean_text": {"type": "str", "required": True, "purpose": "后续 LLM 使用的清洁文本。"},
    "domain": {"type": "str", "required": True, "purpose": "粗分类领域。"},
    "topic_tags": {"type": "list[str]", "required": True, "purpose": "主题标签。"},
    "is_ai_related": {"type": "bool", "required": True, "purpose": "是否 AI 相关。"},
    "ai_match_keywords": {"type": "list[str]", "required": True, "purpose": "命中的 AI 关键词。"},
    "quality_score": {"type": "float", "required": True, "purpose": "简单质量评分。"},
    "quality_reasons": {"type": "list[str]", "required": True, "purpose": "质量评分理由。"},
    "should_analyze_global": {"type": "bool", "required": True, "purpose": "是否进入 global 分析候选。"},
    "should_analyze_ai": {"type": "bool", "required": True, "purpose": "是否进入 AI 分析候选。"},
    "analysis_exclusion_reasons": {"type": "list[str]", "required": True, "purpose": "不进入分析的原因。"},
    "raw_ref": {"type": "str", "required": True, "purpose": "对应 raw item 位置。"},
}
# 结构化信息的字段
STRUCTURED_EVENT_FIELD_SPEC = {
    "id": {"type": "str", "required": True, "purpose": "结构化事件 ID。"},
    "source_scope": {"type": "str", "required": True, "purpose": "global 或 ai 数据线。"},
    "title": {"type": "str", "required": True, "purpose": "事件标题。"},
    "source_name": {"type": "str", "required": True, "purpose": "来源名称，通常等于 source_id。"},
    "source_type": {"type": "str", "required": True, "purpose": "来源类型。"},
    "url": {"type": "str", "required": True, "purpose": "来源 URL，不允许 LLM 编造。"},
    "published_at": {"type": "str", "required": True, "purpose": "发布时间，不允许 LLM 编造。"},
    "industry_area": {"type": "str", "required": True, "purpose": "行业/议题方向。"},
    "topic_tags": {"type": "list[str]", "required": True, "purpose": "主题标签。"},
    "hotness_score": {"type": "int", "required": True, "purpose": "0-100 热度分。"},
    "importance_level": {"type": "str", "required": True, "purpose": "high、medium 或 low。"},
    "summary": {"type": "str", "required": True, "purpose": "一句话事实摘要。"},
    "key_entities": {"type": "list[str]", "required": True, "purpose": "关键实体。"},
    "impact_analysis": {"type": "str", "required": True, "purpose": "影响分析。"},
    "risk_or_opportunity": {"type": "str", "required": True, "purpose": "风险或机会判断。"},
    "evidence": {"type": "object", "required": True, "purpose": "支撑证据，必须绑定来源。"},
    "raw_ref": {"type": "str", "required": True, "purpose": "对应 cleaned item ID。"},
}
# 结构化事件的行业/议题方向预设值，供 LLM 选择使用，避免过于宽泛或过于离散。
STRUCTURED_EVENT_AI_AREAS = {
    "foundation_model",
    "ai_infra",
    "ai_app",
    "robotics",
    "policy",
    "investment",
    "research",
    "security",
    "other",
}
# 这个分类既要覆盖 AI 相关事件的主要方向，也要兼顾全球背景事件的常见领域，供 LLM 选择使用。
STRUCTURED_EVENT_GLOBAL_AREAS = {
    "politics",
    "business",
    "health",
    "security",
    "climate",
    "culture",
    "tech",
    "ai",
    "other",
}

REQUIRED_TREND_KEYS = ["technology", "application", "policy", "capital"]
# 分析结果中需要展示在报告里的事件字段，供 gate 校验和后续报告生成使用，确保关键信息不丢失。
ANALYSIS_EVENT_DISPLAY_REQUIRED_FIELDS = [
    "id",
    "title",
    "url",
    "summary",
    "selection_reason",
    "source_name",
    "source_type",
    "published_at",
    "industry_area",
    "topic_tags",
    "hotness_score",
    "importance_level",
]
# 分析结果字段合同，定义了分析结果中各个字段的类型、是否必填，以及它们在报告中的用途说明。
ANALYSIS_RESULT_FIELD_SPEC = {
    "summary": {
        "type": "str",
        "required": True,
        "purpose": "今日整体判断，给报告开头使用。",
    },
    "summary_reason": {
        "type": "str",
        "required": True,
        "purpose": "整体判断的理由，用于提高 LLM 推理质量和后续审计。",
    },
    "global_top_events": {
        "type": "list[object]",
        "required": True,
        "item_required_fields": [
            *ANALYSIS_EVENT_DISPLAY_REQUIRED_FIELDS,
            "impact_analysis 或 risk_or_opportunity",
        ],
        "purpose": "今日全球背景 Top 事件，给报告的全球热点背景章节使用。必须保留展示字段，避免下游报告丢失来源、方向、标签和热度。",
    },
    "top_events": {
        "type": "list[object]",
        "required": True,
        "item_required_fields": [
            *ANALYSIS_EVENT_DISPLAY_REQUIRED_FIELDS,
            "impact_analysis 或 risk_or_opportunity",
        ],
        "purpose": "今日 AI 领域 Top 3-5 事件，给报告主热点和深度总结使用。必须保留展示字段，避免下游报告丢失来源、方向、标签和热度。",
    },
    "trend_judgment": {
        "type": "object",
        "required": True,
        "required_fields": REQUIRED_TREND_KEYS,
        "purpose": "技术、应用、政策、资本四个方向的趋势判断。",
    },
    "trend_reasoning": {
        "type": "object",
        "required": True,
        "required_fields": REQUIRED_TREND_KEYS,
        "purpose": "四个趋势判断分别对应的理由，不一定直接给报告展示，但用于提高准确率和审计。",
    },
    "risk_or_opportunity_notes": {
        "type": "list[object]",
        "required": True,
        "item_required_fields": ["area", "note", "reason", "supporting_event_ids"],
        "purpose": "风险或机会提示，必须绑定支撑事件。",
    },
    "stats": {
        "type": "object",
        "required": True,
        "required_fields": ["total_events", "global_events", "ai_events"],
        "purpose": "统计数据，给图表和报告数据源概览使用。",
    },
    "react_mode": {
        "type": "str",
        "required": False,
        "purpose": "标记分析来自 llm_react 还是 fallback_rules。",
    },
}

REPORT_PATHS_FIELD_SPEC = {
    "report": {"type": "str", "required": True, "purpose": "Markdown 报告路径。"},
    "report_html": {"type": "str", "required": True, "purpose": "完整 HTML 报告路径。"},
    "chart_html": {"type": "str", "required": True, "purpose": "HTML 图表路径。"},
    "chart_data": {"type": "str", "required": True, "purpose": "图表数据 JSON 路径。"},
    "manifest": {"type": "str", "required": True, "purpose": "报告生成 manifest 路径。"},
}

REPORT_REQUIRED_HEADINGS = [
    "## 数据源概览",
    "## 全球热点背景",
    "## 今日 AI 领域主要热点",
    "## 重要事件深度总结",
    "## 趋势判断",
    "## 风险和机会提示",
    "## 结构化数据附录",
    "## 质量评估摘要",
    "## 数据字段合同（Schema）",
]

def utc_now_iso() -> str:
    """返回 UTC ISO 时间字符串，方便不同运行环境保持一致。"""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class InsightEngineState:
    """一次 AI 日报任务的共享状态。"""
    # run_id：一次运行的唯一 ID。所有 artifact 都按它分目录。
    # target_date：这次日报对应哪一天。
    # created_at：任务创建时间。
    # current_stage：当前流程跑到哪一步。
    # stage_retry_counts：每个 stage 因 gate 未通过而重试的次数。

    # raw_items            原始抓取数据
    # cleaned_items        清洗后数据
    # structured_events    结构化事件
    # analysis_result      分析结果
    # report_paths         报告和图表路径
    # artifacts：每一步输出文件在哪里。
    # errors：致命错误。
    # warnings：非致命问题，比如某个数据源超时。
    # stage_trace：每个 stage 的运行轨迹，包括耗时、状态、产物。

    run_id: str = field(default_factory=lambda: uuid4().hex)
    target_date: str = field(default_factory=lambda: date.today().isoformat())
    created_at: str = field(default_factory=utc_now_iso)
    sources: list[str] = field(default_factory=lambda: list(DEFAULT_SOURCES))

    # 世界热点数据线：用于观察大盘舆情、社会关注点和非 AI 背景噪声。
    global_raw_items: list[dict[str, Any]] = field(
        default_factory=list,
        metadata={"item_contract": RAW_ITEM_FIELD_SPEC, "description": "Stage 1 global 原始抓取数据。"},
    )
    global_cleaned_items: list[dict[str, Any]] = field(
        default_factory=list,
        metadata={"item_contract": CLEANED_ITEM_FIELD_SPEC, "description": "Stage 2 global 清洗后数据。"},
    )
    global_structured_events: list[dict[str, Any]] = field(
        default_factory=list,
        metadata={"item_contract": STRUCTURED_EVENT_FIELD_SPEC, "description": "Stage 3 global 结构化事件。"},
    )

    # AI 专业数据线：用于观察 AI 行业、研究、产品和社区热点。
    ai_raw_items: list[dict[str, Any]] = field(
        default_factory=list,
        metadata={"item_contract": RAW_ITEM_FIELD_SPEC, "description": "Stage 1 AI 原始抓取数据。"},
    )
    ai_cleaned_items: list[dict[str, Any]] = field(
        default_factory=list,
        metadata={"item_contract": CLEANED_ITEM_FIELD_SPEC, "description": "Stage 2 AI 清洗后数据。"},
    )
    ai_structured_events: list[dict[str, Any]] = field(
        default_factory=list,
        metadata={"item_contract": STRUCTURED_EVENT_FIELD_SPEC, "description": "Stage 3 AI 结构化事件。"},
    )

    analysis_result: dict[str, Any] = field(
        default_factory=dict,
        metadata={
            "contract": ANALYSIS_RESULT_FIELD_SPEC,
            "description": "Stage 4 生成、Stage 5 消费的分析结果字段合同。",
        },
    )
    report_paths: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "contract": REPORT_PATHS_FIELD_SPEC,
            "required_headings": REPORT_REQUIRED_HEADINGS,
            "description": "Stage 5 生成的报告、图表和 manifest 路径。",
        },
    )
    artifacts: dict[str, str] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    stage_trace: list[dict[str, Any]] = field(default_factory=list)
    stage_gate_results: list[dict[str, Any]] = field(default_factory=list)
    stage_retry_counts: dict[str, int] = field(default_factory=dict)
    current_stage: str = "initialized"

    @property
    def raw_items(self) -> list[dict[str, Any]]:
        """兼容旧流程：默认返回 AI 专业原始数据。"""
        return self.ai_raw_items

    @raw_items.setter
    def raw_items(self, value: list[dict[str, Any]]) -> None:
        self.ai_raw_items = value

    @property
    def cleaned_items(self) -> list[dict[str, Any]]:
        """兼容旧流程：默认返回 AI 专业清洗数据。"""
        return self.ai_cleaned_items

    @cleaned_items.setter
    def cleaned_items(self, value: list[dict[str, Any]]) -> None:
        self.ai_cleaned_items = value

    @property
    def structured_events(self) -> list[dict[str, Any]]:
        """兼容旧流程：默认返回 AI 专业结构化事件。"""
        return self.ai_structured_events

    @structured_events.setter
    def structured_events(self, value: list[dict[str, Any]]) -> None:
        self.ai_structured_events = value

    def add_artifact(self, name: str, path: str) -> None:
        """记录某个阶段产物的保存路径。"""
        self.artifacts[name] = path

    def add_error(self, stage: str, message: str, detail: Any | None = None) -> None:
        """记录运行错误，但不中断整个 State 对象。"""
        self.errors.append(
            {
                "stage": stage,
                "message": message,
                "detail": detail,
                "created_at": utc_now_iso(),
            }
        )

    def add_warning(self, stage: str, message: str, detail: Any | None = None) -> None:
        """记录非致命告警。"""
        self.warnings.append(
            {
                "stage": stage,
                "message": message,
                "detail": detail,
                "created_at": utc_now_iso(),
            }
        )

    def mark_stage(self, stage: str) -> None:
        """更新当前阶段，供 graph 和日志使用。"""
        self.current_stage = stage

    def add_stage_trace(self, trace: dict[str, Any]) -> None:
        """记录阶段执行轨迹。"""
        self.stage_trace.append(trace)

    def add_stage_gate_result(self, result: dict[str, Any]) -> None:
        """记录某个 stage 的规则检查结果。"""
        self.stage_gate_results.append(result)

    def increment_stage_retry(self, stage: str) -> int:
        """记录某个 stage 因 gate 未通过而重跑的次数。"""
        next_count = self.stage_retry_counts.get(stage, 0) + 1
        self.stage_retry_counts[stage] = next_count
        return next_count

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化 dict，后续用于保存运行快照。"""
        return {
            "run_id": self.run_id,
            "target_date": self.target_date,
            "created_at": self.created_at,
            "sources": self.sources,
            "contracts": {
                "raw_item": RAW_ITEM_FIELD_SPEC,
                "cleaned_item": CLEANED_ITEM_FIELD_SPEC,
                "structured_event": STRUCTURED_EVENT_FIELD_SPEC,
                "analysis_result": ANALYSIS_RESULT_FIELD_SPEC,
                "report_paths": REPORT_PATHS_FIELD_SPEC,
                "report_required_headings": REPORT_REQUIRED_HEADINGS,
            },
            "global_raw_items": self.global_raw_items,
            "global_cleaned_items": self.global_cleaned_items,
            "global_structured_events": self.global_structured_events,
            "ai_raw_items": self.ai_raw_items,
            "ai_cleaned_items": self.ai_cleaned_items,
            "ai_structured_events": self.ai_structured_events,
            # 兼容旧 key：当前流程默认仍使用 AI 专业数据线。
            "raw_items": self.raw_items,
            "cleaned_items": self.cleaned_items,
            "structured_events": self.structured_events,
            "analysis_result": self.analysis_result,
            "report_paths": self.report_paths,
            "artifacts": self.artifacts,
            "errors": self.errors,
            "warnings": self.warnings,
            "stage_trace": self.stage_trace,
            "stage_gate_results": self.stage_gate_results,
            "stage_retry_counts": self.stage_retry_counts,
            "current_stage": self.current_stage,
        }
