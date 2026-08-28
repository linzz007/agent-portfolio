# Daily AI Insight Engine Harness | AI 舆情日报生成 Harness

这是一个面向 AI 新闻与舆情日报生成场景的 **Agent Harness 机制验证项目**。系统从 RSS/API 等公开来源抓取新闻，经过数据清洗、事件结构化、洞察分析和报告生成，输出 Markdown / HTML 报告，并记录每个阶段的运行证据。

## 面试官先看

- **业务问题**：日报生成不是“总结几条新闻”这么简单，LLM 可能跳过清洗、漏掉字段、输出格式漂移，出了错也很难复盘。
- **技术重点**：用 State Contract、Stage Graph、Hook、Gate、Tool Gateway 和 Artifact 把固定流程做成可检查、可失败、可复盘的 Harness。
- **可追问点**：为什么要有 Stage Gate、Hook 和 Linter；如何约束 AI Coding 修改边界；如何把确定性阶段和 LLM 阶段分开；失败时如何定位。

## 核心设计

1. **Coding Agent Harness**：通过 `AGENTS.md`、`CLAUDE.md`、`feature_list.json`、`progress.json` 和 harness linter 约束 AI 编程助手的修改边界。
2. **Runtime Agent Harness**：用 State Contract、Stage Graph、Stage Runner、Hook、Gate 和 Artifact 约束运行时 Agent 的阶段输入输出。
3. **阶段化流水线**：collect_raw_items -> clean_items -> structure_events -> analyze_insights -> generate_report。
4. **工具白名单**：Tool Gateway 控制外部工具调用，避免 LLM 访问未声明能力。
5. **上下文可见性**：Context Router 按 stage 构造上下文，减少把完整 state 无差别塞给模型。
6. **产物校验与兜底**：每个阶段通过 linter / gate 检查字段、格式和报告产物，失败时记录错误并进入可解释兜底。

## 面试官可看的代码入口

| 文件 | 看点 |
| --- | --- |
| `AGENTS.md` | 给 AI 编程助手的项目地图和硬性修改规则。 |
| `CLAUDE.md` | Claude Code 风格的项目约束说明。 |
| `src/insight_engine/harness/state.py` | State Contract 和运行状态字段。 |
| `src/insight_engine/harness/graph.py` | Stage Graph 和阶段流转。 |
| `src/insight_engine/harness/stage_runner.py` | 阶段执行、hook、gate 的主调度。 |
| `src/insight_engine/harness/stage_gates.py` | 阶段质量门检查。 |
| `src/insight_engine/harness/context_router.py` | 阶段级上下文可见性控制。 |
| `src/insight_engine/harness/tool_gateway.py` | 工具白名单和调用边界。 |
| `scripts/harness_linter.py` | Harness 静态检查入口。 |
| `docs/reference/state-contracts.md` | 状态合同文档。 |

## 验证方式

```powershell
py -3 -m pytest
py -3 scripts/harness_linter.py
py -3 run_chat.py "帮我生成今日 AI 新闻分析报告"
```

## 项目定位

这个项目适合在面试中解释 **Harness 从 0 到 1 的基础机制**。它比 Workbench 更小，更适合讲清楚 State、Graph、Hook、Gate、Artifact 这些概念为什么存在、解决什么问题。

## 脱敏说明

发布版已移除真实 `.env`、历史 `data/` 运行状态、`outputs/` 报告产物和外部参考原文，只保留核心代码、测试、配置模板和 Harness 设计文档。
