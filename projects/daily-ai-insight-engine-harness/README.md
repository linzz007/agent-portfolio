# Daily AI Insight Engine Harness | 新闻分析 Agent Harness

Daily AI Insight Engine Harness 是一个较小规模的 Harness 机制验证项目，用新闻日报生成场景验证 `State -> Graph -> Hook -> Gate -> Artifact` 的受控 workflow。它可以看作 Insight Workbench 中 `/report` 报告链路的早期实验形态。

## 待解决问题

- 新闻日报生成包含抓取、清洗、结构化、分析和报告生成，单次模型调用很难稳定覆盖完整链路。
- LLM 阶段容易出现跳步、漏字段、格式漂移和不可解释失败。
- AI Coding 迭代过程中，新增字段、stage、prompt 或工具时容易造成契约漂移。

## 关键动作

1. **Runtime Harness**
   用 State Contract、Stage Graph、Stage Runner、Hook、Gate 和 Artifact 约束运行时 Agent 的阶段输入输出。

2. **阶段化流水线**
   将日报生成拆成 `collect_raw_items -> clean_items -> structure_events -> analyze_insights -> generate_report`，每个阶段只处理明确输入和输出。

3. **上下文与工具边界**
   Context Router 按 stage 构造上下文，Tool Gateway 控制外部工具调用，避免把完整 state 无差别塞给模型。

4. **Coding Agent Harness**
   通过 `AGENTS.md`、`CLAUDE.md`、`feature_list.json`、`progress.json` 和 harness linter 约束 AI 编程助手的修改边界，降低工程结构漂移。

## 核心模块与代码入口

| 文件 | 作用 |
| --- | --- |
| `AGENTS.md` | AI 编程助手的项目地图和硬性修改规则。 |
| `CLAUDE.md` | Claude Code 风格的项目约束说明。 |
| `src/insight_engine/harness/state.py` | State Contract 和运行状态字段。 |
| `src/insight_engine/harness/graph.py` | Stage Graph 和阶段流转。 |
| `src/insight_engine/harness/stage_runner.py` | 阶段执行、hook、gate 的主调度。 |
| `src/insight_engine/harness/stage_gates.py` | 阶段质量门检查。 |
| `src/insight_engine/harness/context_router.py` | 阶段级上下文可见性控制。 |
| `src/insight_engine/harness/tool_gateway.py` | 工具白名单和调用边界。 |
| `scripts/harness_linter.py` | Harness 静态检查入口。 |

## 验证方式

```powershell
py -3 -m pytest
py -3 scripts/harness_linter.py
py -3 run_chat.py "生成今日 AI 新闻分析报告"
```

## 面试讲法

这个项目适合作为 Harness 基础机制的补充说明。它的价值不在“新闻分析本身”，而在于展示如何把一个容易漂移的 LLM workflow 变成有状态、有阶段、有质量门、有产物留痕的可调试系统。

## 脱敏说明

发布版已移除真实 `.env`、历史 `data/` 运行状态、`outputs/` 报告产物和外部参考原文，只保留核心代码、测试、配置模板和 Harness 设计文档。
