# Daily AI Insight Engine Harness | AI 舆情日报生成 Harness

Daily AI Insight Engine Harness 面向 AI 新闻与舆情日报生成场景，从 RSS/API 等公开来源抓取新闻，经过数据清洗、事件结构化、洞察分析和报告生成，输出 Markdown / HTML 报告，并记录每个阶段的运行证据。

## 核心问题

- 日报生成流程包含抓取、清洗、结构化、分析和报告生成，单次模型调用难以覆盖完整链路。
- LLM 阶段可能出现跳步、漏字段、格式漂移和不可解释失败。
- AI Coding 迭代过程中，新增字段、stage、prompt 或工具时容易造成契约漂移。

## 核心设计

1. **Coding Agent Harness**：通过 `AGENTS.md`、`CLAUDE.md`、`feature_list.json`、`progress.json` 和 harness linter 约束 AI 编程助手的修改边界。
2. **Runtime Agent Harness**：用 State Contract、Stage Graph、Stage Runner、Hook、Gate 和 Artifact 约束运行时 Agent 的阶段输入输出。
3. **阶段化流水线**：collect_raw_items -> clean_items -> structure_events -> analyze_insights -> generate_report。
4. **工具白名单**：Tool Gateway 控制外部工具调用，避免 LLM 访问未声明能力。
5. **上下文可见性**：Context Router 按 stage 构造上下文，减少把完整 state 无差别塞给模型。
6. **产物校验与兜底**：每个阶段通过 linter / gate 检查字段、格式和报告产物，失败时记录错误并进入可解释兜底。

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
| `docs/reference/state-contracts.md` | 状态合同文档。 |

## 验证方式

```powershell
py -3 -m pytest
py -3 scripts/harness_linter.py
py -3 run_chat.py "生成今日 AI 新闻分析报告"
```

## 工程价值

项目以较小规模验证 State、Graph、Hook、Gate、Artifact 等 Harness 基础机制。它将确定性阶段与 LLM 阶段分离，并通过阶段质量门和 linter 降低流程跳步、契约漂移和错误难定位的问题。

## 脱敏说明

发布版已移除真实 `.env`、历史 `data/` 运行状态、`outputs/` 报告产物和外部参考原文，只保留核心代码、测试、配置模板和 Harness 设计文档。
