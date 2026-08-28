---
last_updated: auto-generated
status: active
owner: coding-agent
source: src/insight_engine/harness/state.py
content_hash: 36b59c43c3d2d0d1
---

# State 字段合同

> 此文件由 `scripts/generate_contract_docs.py` 从 `state.py` 自动生成，勿手动编辑。
> 源码内容哈希：`36b59c43c3d2d0d1`
> 如果 CI 报告此文档过期，运行 `python scripts/generate_contract_docs.py` 重新生成。

## 概述

`InsightEngineState` 是一次日报生成任务的全局状态对象。它贯穿 5 个 Stage，
每个 Stage 在上面写入产物字段，下游 Stage 按合同消费这些字段。

5 个 Stage 分别产出 5 份字段合同：
1. RAW_ITEM_FIELD_SPEC — Stage 1 采集的原始数据
2. CLEANED_ITEM_FIELD_SPEC — Stage 2 清洗后的标准化数据
3. STRUCTURED_EVENT_FIELD_SPEC — Stage 3 LLM 抽取的结构化事件
4. ANALYSIS_RESULT_FIELD_SPEC — Stage 4 ReAct 分析结果
5. REPORT_PATHS_FIELD_SPEC — Stage 5 生成的报告和图表路径

数据流转方向：`raw_items → cleaned_items → structured_events → analysis_result → report_paths`

## 两条数据线

系统并行处理两条数据线：
- **global** 线：全球背景事件（政治、经济、气候等），用于报告的「全球热点背景」章节。
- **ai** 线：AI 行业事件（基础模型、AI 应用、政策、投资等），用于报告的主要分析内容。

每条数据线各自经过 Stage 1-3 的处理，在 Stage 4 合并分析。
State 中对应的字段对：
`global_raw_items` / `ai_raw_items` → `global_cleaned_items` / `ai_cleaned_items` → `global_structured_events` / `ai_structured_events`

---

## RAW_ITEM_FIELD_SPEC

**对应 Stage：** Stage 1 产出

| 字段名 | 类型 | 必填 | 用途 |
|--------|------|------|------|
| `source_id` | `str` | 是 | 数据源 ID。 |
| `source_scope` | `str` | 是 | global 或 ai 数据线。 |
| `source_type` | `str` | 是 | media、social、research 等来源类型。 |
| `title` | `str` | 是 | 原始新闻标题。 |
| `url` | `str` | 是 | 原始链接。 |
| `published_at` | `str` | 是 | 发布时间，保持来源原始格式或 ISO 格式。 |
| `author_or_org` | `str` | 否 | 作者或机构。 |
| `summary` | `str` | 否 | 来源摘要。 |
| `raw_content` | `str` | 否 | 原始正文或摘要内容。 |
| `retrieved_at` | `str` | 是 | 抓取时间。 |
| `metadata` | `object` | 否 | 来源特有字段。 |

共 11 个字段。

## CLEANED_ITEM_FIELD_SPEC

**对应 Stage：** Stage 2 产出

| 字段名 | 类型 | 必填 | 用途 |
|--------|------|------|------|
| `id` | `str` | 是 | 清洗后 item ID。 |
| `source_id` | `str` | 是 | 数据源 ID。 |
| `source_scope` | `str` | 是 | global 或 ai 数据线。 |
| `source_type` | `str` | 是 | 来源类型。 |
| `title` | `str` | 是 | 标准化标题。 |
| `url` | `str` | 是 | 标准化链接。 |
| `published_at` | `str` | 是 | 解析后的发布时间。 |
| `published_date` | `str|null` | 否 | 发布时间日期。 |
| `recency_days` | `int|null` | 否 | 距离 target_date 的天数。 |
| `is_recent` | `bool` | 是 | 是否在近期窗口内。 |
| `summary` | `str` | 是 | 清洗后的摘要。 |
| `clean_text` | `str` | 是 | 后续 LLM 使用的清洁文本。 |
| `domain` | `str` | 是 | 粗分类领域。 |
| `topic_tags` | `list[str]` | 是 | 主题标签。 |
| `is_ai_related` | `bool` | 是 | 是否 AI 相关。 |
| `ai_match_keywords` | `list[str]` | 是 | 命中的 AI 关键词。 |
| `quality_score` | `float` | 是 | 简单质量评分。 |
| `quality_reasons` | `list[str]` | 是 | 质量评分理由。 |
| `should_analyze_global` | `bool` | 是 | 是否进入 global 分析候选。 |
| `should_analyze_ai` | `bool` | 是 | 是否进入 AI 分析候选。 |
| `analysis_exclusion_reasons` | `list[str]` | 是 | 不进入分析的原因。 |
| `raw_ref` | `str` | 是 | 对应 raw item 位置。 |

共 22 个字段。

## STRUCTURED_EVENT_FIELD_SPEC

**对应 Stage：** Stage 3 产出

| 字段名 | 类型 | 必填 | 用途 |
|--------|------|------|------|
| `id` | `str` | 是 | 结构化事件 ID。 |
| `source_scope` | `str` | 是 | global 或 ai 数据线。 |
| `title` | `str` | 是 | 事件标题。 |
| `source_name` | `str` | 是 | 来源名称，通常等于 source_id。 |
| `source_type` | `str` | 是 | 来源类型。 |
| `url` | `str` | 是 | 来源 URL，不允许 LLM 编造。 |
| `published_at` | `str` | 是 | 发布时间，不允许 LLM 编造。 |
| `industry_area` | `str` | 是 | 行业/议题方向。 |
| `topic_tags` | `list[str]` | 是 | 主题标签。 |
| `hotness_score` | `int` | 是 | 0-100 热度分。 |
| `importance_level` | `str` | 是 | high、medium 或 low。 |
| `summary` | `str` | 是 | 一句话事实摘要。 |
| `key_entities` | `list[str]` | 是 | 关键实体。 |
| `impact_analysis` | `str` | 是 | 影响分析。 |
| `risk_or_opportunity` | `str` | 是 | 风险或机会判断。 |
| `evidence` | `object` | 是 | 支撑证据，必须绑定来源。 |
| `raw_ref` | `str` | 是 | 对应 cleaned item ID。 |

共 17 个字段。

## ANALYSIS_RESULT_FIELD_SPEC

**对应 Stage：** Stage 4 产出

| 字段名 | 类型 | 必填 | 用途 |
|--------|------|------|------|
| `summary` | `str` | 是 | 今日整体判断，给报告开头使用。 |
| `summary_reason` | `str` | 是 | 整体判断的理由，用于提高 LLM 推理质量和后续审计。 |
| `global_top_events` | `list[object]` | 是 | 今日全球背景 Top 事件，给报告的全球热点背景章节使用。必须保留展示字段，避免下游报告丢失来源、方向、标签和热度。 |
| `top_events` | `list[object]` | 是 | 今日 AI 领域 Top 3-5 事件，给报告主热点和深度总结使用。必须保留展示字段，避免下游报告丢失来源、方向、标签和热度。 |
| `trend_judgment` | `object` | 是 | 技术、应用、政策、资本四个方向的趋势判断。 |
| `trend_reasoning` | `object` | 是 | 四个趋势判断分别对应的理由，不一定直接给报告展示，但用于提高准确率和审计。 |
| `risk_or_opportunity_notes` | `list[object]` | 是 | 风险或机会提示，必须绑定支撑事件。 |
| `stats` | `object` | 是 | 统计数据，给图表和报告数据源概览使用。 |
| `react_mode` | `str` | 否 | 标记分析来自 llm_react 还是 fallback_rules。 |

共 9 个字段。

## REPORT_PATHS_FIELD_SPEC

**对应 Stage：** Stage 5 产出

| 字段名 | 类型 | 必填 | 用途 |
|--------|------|------|------|
| `report` | `str` | 是 | Markdown 报告路径。 |
| `report_html` | `str` | 是 | 完整 HTML 报告路径。 |
| `chart_html` | `str` | 是 | HTML 图表路径。 |
| `chart_data` | `str` | 是 | 图表数据 JSON 路径。 |
| `manifest` | `str` | 是 | 报告生成 manifest 路径。 |

共 5 个字段。

---

## 枚举值和约束常量

### STRUCTURED_EVENT_AI_AREAS（AI 事件行业领域）

- `ai_app`
- `ai_infra`
- `foundation_model`
- `investment`
- `other`
- `policy`
- `research`
- `robotics`
- `security`

### STRUCTURED_EVENT_GLOBAL_AREAS（全球事件领域）

- `ai`
- `business`
- `climate`
- `culture`
- `health`
- `other`
- `politics`
- `security`
- `tech`

### REQUIRED_TREND_KEYS（趋势判断维度）

- `technology`
- `application`
- `policy`
- `capital`

### ANALYSIS_EVENT_DISPLAY_REQUIRED_FIELDS（报告展示必选字段）

- `id`
- `title`
- `url`
- `summary`
- `selection_reason`
- `source_name`
- `source_type`
- `published_at`
- `industry_area`
- `topic_tags`
- `hotness_score`
- `importance_level`

### REPORT_REQUIRED_HEADINGS（报告必含章节标题）

- ## 数据源概览
- ## 全球热点背景
- ## 今日 AI 领域主要热点
- ## 重要事件深度总结
- ## 趋势判断
- ## 风险和机会提示
- ## 结构化数据附录
- ## 质量评估摘要
- ## 数据字段合同（Schema）

---

## 合同的使用方式

1. **Stage 负责生成**：每个 Stage handler 负责产出合同的字段值。
2. **Linter 负责校验**：每个 Stage 的 linter 从 `state.py` import FIELD_SPEC，逐字段检查类型和必填。
3. **下游按合同消费**：下游 Stage 假定上游产物的字段合同已通过校验，直接取值使用。
4. **Graph 读 Linter 结果**：Graph 在 `fire_after` 后读取 `evaluate_linter` 返回的 `passed` 字段，决定继续、重试或失败。
