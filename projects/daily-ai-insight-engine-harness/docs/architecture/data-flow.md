---
last_updated: 2026-05-28
status: active
owner: coding-agent
---

# 数据流转图

## 完整数据链路

```text
┌─────────────────────────────────────────────────────────────────────┐
│                        STAGE 1: collect_raw_items                    │
│                                                                     │
│  config/sources.json → RSS/API 抓取 → raw_items                      │
│                                                                     │
│  输入：数据源配置（URL、关键词、max_age_days）                          │
│  输出：global_raw_items + ai_raw_items                               │
│  产物：data/raw/{run_id}/raw_items.json                              │
│  LLM：不允许                                                        │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        STAGE 2: clean_items                          │
│                                                                     │
│  raw_items → 清洗/去重/打标签 → cleaned_items                        │
│                                                                     │
│  输入：global_raw_items + ai_raw_items                               │
│  处理：标准化字段、去重、打标签（is_ai_related、should_analyze_*）       │
│  输出：global_cleaned_items + ai_cleaned_items                       │
│  产物：data/processed/{run_id}/cleaned_items.json                    │
│  LLM：不允许                                                        │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     STAGE 3: structure_events                        │
│                                                                     │
│  cleaned_items → LLM JSON 抽取 → structured_events                   │
│                                                                     │
│  输入：should_analyze=True 的 cleaned_items                          │
│  处理：LLM 批量 JSON 抽取 → schema linter → repair/falback            │
│  输出：global_structured_events + ai_structured_events               │
│  产物：data/processed/{run_id}/structured_events.json                │
│       data/llm/{run_id}/structure_events/                           │
│  LLM：单次 Chat Completions + repair                                │
│  Gate：STRUCTURED_EVENT_FIELD_SPEC 合同检查                           │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     STAGE 4: analyze_insights                        │
│                                                                     │
│  structured_events → ReAct loop → analysis_result                   │
│                                                                     │
│  输入：global_structured_events + ai_structured_events               │
│  处理：受限 ReAct（统计→Top→趋势→finish）                              │
│  输出：analysis_result（summary、top_events、trend_judgment 等）      │
│  产物：data/processed/{run_id}/analysis_result.json                  │
│       data/react/{run_id}/analyze_insights/                         │
│  LLM：受限 ReAct loop（5 步固定计划）                                  │
│  Gate：ANALYSIS_RESULT_FIELD_SPEC 合同检查                            │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     STAGE 5: generate_report                         │
│                                                                     │
│  analysis_result → LLM 标题翻译 → 确定性模板渲染 → 报告产物             │
│                                                                     │
│  输入：analysis_result + structured_events                           │
│  处理：LLM 翻译标题 → 补全展示字段 → 渲染 Markdown/HTML/图表            │
│  输出：report_paths（markdown、html、chart、chart_data、manifest）     │
│  产物：outputs/reports/{run_id}/*                                    │
│       outputs/charts/{run_id}/*                                     │
│       outputs/pipeline/{run_id}/*                                   │
│  LLM：仅标题翻译（逐条 Chat Completions）                              │
│  Gate：REPORT_REQUIRED_HEADINGS + chart_data 完整性检查               │
└─────────────────────────────────────────────────────────────────────┘
```

## State 数据结构流

```text
InsightEngineState（贯穿全流程）

    run_id ────────────────────────────────▶ 出现在所有产物路径中
    target_date ───────────────────────────▶ 报告标题和过滤时间窗口
    current_stage ─────────────────────────▶ Graph 路由决策依据

    ┌─ Stage 1 写入 ─────────────────────────┐
    │ global_raw_items: list[RawItem]       │
    │ ai_raw_items: list[RawItem]           │
    └───────────────────────────────────────┘
              │ Stage 2 读取
              ▼
    ┌─ Stage 2 写入 ─────────────────────────┐
    │ global_cleaned_items: list[CleanedItem]│
    │ ai_cleaned_items: list[CleanedItem]   │
    └───────────────────────────────────────┘
              │ Stage 3 读取
              ▼
    ┌─ Stage 3 写入 ─────────────────────────┐
    │ global_structured_events: list[Event]  │
    │ ai_structured_events: list[Event]     │
    └───────────────────────────────────────┘
              │ Stage 4 读取
              ▼
    ┌─ Stage 4 写入 ─────────────────────────┐
    │ analysis_result: AnalysisResult       │
    └───────────────────────────────────────┘
              │ Stage 5 读取
              ▼
    ┌─ Stage 5 写入 ─────────────────────────┐
    │ report_paths: ReportPaths             │
    └───────────────────────────────────────┘

    artifacts: dict[str, str] ─────────────▶ 所有产物的路径注册表
    errors: list[Error] ───────────────────▶ 运行错误（致命）
    warnings: list[Warning] ───────────────▶ 运行警告（非致命）
    stage_trace: list[Trace] ──────────────▶ 每个 stage 的执行轨迹
    stage_gate_results: list[GateResult] ──▶ Linter 检查结果
```

## Context Router 的可见性控制

不同 stage 通过 `context_router.py` 只能看到其需要的 state 字段：

| Stage | 可见 State 字段 |
|-------|----------------|
| collect_raw_items | run_id, target_date, sources, errors, warnings |
| clean_items | 以上 + global_raw_items, ai_raw_items, artifacts |
| structure_events | run_id, target_date, global_cleaned_items, ai_cleaned_items, artifacts |
| analyze_insights | run_id, target_date, global_structured_events, ai_structured_events, artifacts |
| generate_report | 以上 + analysis_result |
