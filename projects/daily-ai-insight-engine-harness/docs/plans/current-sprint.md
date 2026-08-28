---
last_updated: 2026-05-28
status: active
owner: coding-agent
---

# 当前迭代

## 迭代目标
完成 insight_engine_claude 的 Coding Agent Harness 重构，使项目可以被 Claude Code 等 AI 编程助手自主维护。

## 当前任务

| 优先级 | 任务 | 状态 |
|--------|------|------|
| P0 | 创建 docs/ 结构化知识库 | ✅ 完成 |
| P0 | 编写 AGENTS.md 地图模式 | ✅ 完成 |
| P0 | 编写架构、边界、数据流文档 | ✅ 完成 |
| P0 | 编写编码规范文档 | ✅ 完成 |
| P0 | 编写设计文档和迭代计划 | ✅ 完成 |
| P0 | 复制全部源代码 | ✅ 完成 |
| P0 | 配置 harness_linter 和 CI | 进行中 |
| P1 | 写 harness-engineering 从零搭建指南 | 进行中 |

## 下一步
1. 运行 `python scripts/harness_linter.py` 验证所有检查通过
2. 运行 `python -m pytest` 验证所有测试通过
3. 用 `run_chat.py "帮我生成今日新闻分析报告"` 端到端验证
