---
last_updated: 2026-05-28
status: active
owner: coding-agent
---

# 错误码

## 系统错误码

| 错误码 | 含义 | 来源 | 处理方式 |
|--------|------|------|----------|
| E001 | 数据源全部抓取失败 | collect_raw_items | graph 走到 failed |
| E002 | 清洗后无可用数据 | clean_items | graph 走到 failed |
| E003 | 结构化事件为空 | structure_events | graph 走到 failed |
| E004 | 分析结果为空 | analyze_insights | graph 走到 failed |
| E005 | 报告路径为空 | generate_report | graph 走到 failed |
| E006 | Stage gate 未通过（不可重试） | stage_gates | graph 走到 failed |
| E007 | Stage handler 未注册 | graph | graph 走到 failed |
| E008 | 未知 stage | graph | graph 走到 failed |

## Warning 码

| Warning 码 | 含义 | 来源 | 处理方式 |
|------------|------|------|----------|
| W001 | 单个数据源抓取失败 | collect_raw_items | 继续处理其他源 |
| W002 | 数据源返回数量不足 | collect_raw_items | 记录后继续 |
| W003 | LLM 不可用，使用 fallback | structure_events/analyze_insights | 降级到规则 fallback |
| W004 | LLM repair 失败，使用 fallback | structure_events | 降级到规则 fallback |
| W005 | 标题翻译失败 | generate_report | 保留英文原题 |
| W006 | 报告章节不完整 | generate_report | 继续生成但记录 warning |
| W007 | 部分标题未翻译 | generate_report | 保留英文原题 |

## Permission 错误

| 错误 | 含义 | 来源 |
|------|------|------|
| PermissionError | stage 不在工具白名单中 | tool_gateway |
| KeyError | 工具未在 REGISTRY 中注册 | tool_gateway |
