---
last_updated: 2026-05-28
status: active
owner: coding-agent
---

# 待办事项

## 功能增强

| ID | 任务 | 优先级 | 说明 |
|----|------|--------|------|
| B01 | 新增数据源 | P1 | 添加更多 RSS/API 新闻源 |
| B02 | 增加 Reviewer Agent stage | P2 | LLM 审阅报告质量 |
| B03 | 增加邮件/钉钉通知 | P2 | 报告生成后推送通知 |
| B04 | 增加历史对比 | P2 | 与前一天报告对比趋势变化 |
| B05 | 增加中文新闻源 | P1 | 支持中文新闻抓取和分析 |

## 工程改进

| ID | 任务 | 优先级 | 说明 |
|----|------|--------|------|
| E01 | 加 pre-commit hook | P1 | 本地 commit 时运行 harness_linter |
| E02 | 增加代码覆盖率检查 | P1 | CI 中设置覆盖率阈值 |
| E03 | 增加文档新鲜度检查 | P2 | CI 中检查 docs/ 文件是否过期 |
| E04 | 增加 Git Worktree 隔离验证 | P2 | Agent 修改在 worktree 中验证后才合并 |
| E05 | 增加后台清理 Agent | P2 | 定时清理超长文件、缺失测试、过期 TODO |
