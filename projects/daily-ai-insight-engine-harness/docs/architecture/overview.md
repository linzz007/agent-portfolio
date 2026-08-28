---
last_updated: 2026-05-28
status: active
owner: coding-agent
---

# 系统架构概览

## 一句话概述

Daily AI Insight Engine 是一个多阶段 AI 新闻分析流水线，采用 Harness Engineering 架构，
将 LLM 调用约束在可控的 stage 中，每个 stage 的输出都经过确定性 linter 校验。

## 分层结构

```text
src/insight_engine/
├── conversation/          # 对话层：意图路由，决定是否触发业务 Skill
├── skill_executors/       # 能力层：可被对话层调用的 Skill 执行器
├── harness/               # 控制层：Runtime Harness 基础设施
│   ├── state.py           # 一次运行的共享状态和字段合同
│   ├── graph.py           # 流程状态机（路由 + 重试控制）
│   ├── stage_gates.py     # Stage gate 调度器
│   ├── stage_runner.py    # 单 stage 调试运行器
│   ├── context_router.py  # 控制每个 stage 能看到的 state 字段和文档
│   ├── prompt_builder.py  # Prompt 构造、快照保存、retry feedback
│   ├── tool_gateway.py    # 运行时工具白名单控制
│   ├── llm_client.py      # OpenAI-compatible LLM 调用封装
│   ├── artifacts.py       # 产物保存工具
│   ├── env.py             # 环境变量加载
│   └── hooks/             # StageHooks 生命周期插槽系统
├── stages/                # 5 个 stage 的业务执行逻辑
├── linters/               # 每个 stage 的产物合同检查
├── agents/                # Agent stage 处理器（预留）
└── tools/                 # 运行时工具注册
```

## 依赖规则

- `conversation/` → 依赖 `skill_executors/` 和 `harness/llm_client.py`
- `skill_executors/` → 依赖 `harness/`（graph、state）和 `stages/`
- `harness/` → 依赖 `stages/`（通过 graph handler 注册）和 `linters/`（通过 stage_gates）
- `stages/` → 依赖 `harness/`（state、artifacts、llm_client）
- `linters/` → 依赖 `harness/state.py`（读取 FIELD_SPEC 合同）

## 核心设计理念

### 1. 两层 Harness 分离

```
Coding Agent Harness                    Runtime Agent Harness
─────────────────────                   ─────────────────────
约束 AI 编程助手如何修改仓库            约束运行时 Agent 如何分阶段处理数据
AGENTS.md + feature_list.json          state.py + graph.py + stage_gates.py
harness_linter.py + CI                 hooks/ + context_router.py + tool_gateway.py
```

### 2. Stage Gate + Hook 插槽系统

每个 stage 结束后不依赖 LLM 自评，而是用**确定性代码**检查产物：
```text
fire_before → handler() → fire_after
                              │
              ┌─ linter 通过 ─→ 进入下一 stage
              └─ linter 失败 ─→ 重试或终止
```

### 3. 确定性阶段与 LLM 阶段分层

| Stage | 类型 | LLM 调用 |
|-------|------|----------|
| collect_raw_items | 确定性 | 不允许 |
| clean_items | 确定性 | 不允许 |
| structure_events | LLM + Schema Linter + Fallback | 单次 Chat + repair |
| analyze_insights | 受限 ReAct + Fallback | ReAct loop |
| generate_report | 受限 ReAct + Fallback（标题翻译仅 LLM） | 标题翻译 |

### 4. 全链路可审计

每个 stage 自动保存产物到 `data/` 和 `outputs/` 目录，
pipeline_summary 和 run_artifact 提供完整运行轨迹。
