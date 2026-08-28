# daily_news_report_skill

## 触发条件

当用户在页面聊天里表达“生成今日新闻分析报告”“今日新闻日报”“AI 日报”等意图时，对话 Agent 调用这个 Skill。

普通闲聊不调用本 Skill。

## 执行方式

本 Skill 不让 LLM 自由猜流程，而是启动 `src/insight_engine/skill_executors/daily_news_report.py` 中的 graph：

```text
collect_raw_items
-> clean_items
-> structure_events
-> analyze_insights
-> generate_report
-> done/failed
```

graph 使用 `InsightEngineState` 保存每一步的数据，每个 stage 的 gate linter 自动检查产物质量。

## 输出

必须返回：

- `report`：Markdown 报告路径。
- `report_html`：完整 HTML 报告路径。
- `chart_html`：HTML 可视化路径。
- `pipeline_summary`：完整流程摘要路径。
- `run_id`：本次运行 ID。
- `stage_gate_results`：各 stage gate linter 的检查结果。
