# Final Output Format

最终输出必须包含结构化数据、可读报告、可视化产物和质量评估结果。

## 结构化记录 Schema

每条新闻或信号都应该变成一条结构化记录：

```json
{
  "id": "string",
  "title": "string",
  "source_name": "string",
  "source_type": "media | research | official | social | aggregator | code_release",
  "url": "string",
  "published_at": "string",
  "industry_area": "foundation_model | ai_infra | ai_app | robotics | policy | investment | research | other",
  "topic_tags": ["string"],
  "hotness_score": 0,
  "importance_level": "low | medium | high",
  "summary": "string",
  "key_entities": ["string"],
  "impact_analysis": "string",
  "risk_or_opportunity": "string",
  "evidence": {
    "source_title": "string",
    "source_url": "string",
    "supporting_text": "string"
  },
  "raw_ref": "string"
}
```

## Schema 设计理由

这个 schema 不能只做摘要。它需要保留：

- 信息来自哪里
- 属于 AI 的哪个方向
- 热度和重要性如何
- 涉及哪些关键实体
- 可能带来什么影响
- 哪些来源证据支撑这个判断

## 日报章节

Markdown 报告必须包含：

1. 标题和生成元信息。
2. 数据源概览。
3. 今日 AI 领域主要热点：Top 3-5 事件。
4. 重要事件深度总结。
5. 趋势判断：
   - 技术
   - 应用
   - 政策
   - 资本
6. 风险和机会提示。
7. 结构化数据附录。
8. 可视化链接或图表引用。
9. 质量评估摘要。

## 可视化要求

V1 至少包含以下一种：

- 按数据源类型统计事件数量
- 按行业方向统计事件数量
- 热度排名图
- 风险/机会分布图

V1 推荐输出：

- `outputs/charts/{run_id}/charts.html`
- `outputs/charts/{run_id}/chart_data.json`

