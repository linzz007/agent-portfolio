---
last_updated: 2026-05-28
status: "✅ Implemented"
owner: coding-agent
---

# Feature: 今日新闻分析报告

## 目标
从每日新闻信息中提取结构化洞察，生成包含 Markdown 报告、HTML 可视化、
图表和完整流水线摘要的日报。

## 非目标
- 不做实时推送或定时任务
- 不做用户账户系统
- 不做多语言翻译（仅英文→中文标题翻译）

## 技术方案

### 涉及的模块
- types/: 定义在 state.py 中的 FIELD_SPEC 合同
- harness/: graph、state、stage_gates、hooks、context_router、tool_gateway
- stages/: collect_raw_items、clean_items、structure_events、analyze_insights、generate_report
- linters/: 每个 stage 的产物质量检查
- conversation/: 对话意图路由

### 数据模型变更
5 个跨阶段字段合同：
1. RAW_ITEM_FIELD_SPEC（Stage 1 产出）
2. CLEANED_ITEM_FIELD_SPEC（Stage 2 产出）
3. STRUCTURED_EVENT_FIELD_SPEC（Stage 3 产出）
4. ANALYSIS_RESULT_FIELD_SPEC（Stage 4 产出）
5. REPORT_PATHS_FIELD_SPEC（Stage 5 产出）

### API 变更
无外部 API。内部入口：
- `run_chat.py "消息"` — 对话入口
- `run_full_pipeline.py --show` — 流水线入口

### 验收标准
- 能从 5+ 个数据源抓取新闻
- 生成带标题、来源、热度分的结构化事件
- 生成包含 KPI、饼图、事件卡片、趋势判断的 HTML 看板
- 全流程可审计（每个 stage 中间产物落盘）
- 测试通过，harness_linter 通过
