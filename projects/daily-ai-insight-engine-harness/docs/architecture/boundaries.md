---
last_updated: 2026-05-28
status: active
owner: coding-agent
---

# 模块边界和依赖规则

## 依赖方向图

```text
                    ┌─────────────────┐
                    │  conversation/   │  ← 对话入口，判断意图
                    │  router.py       │
                    └────────┬────────┘
                             │ 调用
                    ┌────────▼────────┐
                    │ skill_executors/ │  ← 业务能力入口
                    │ daily_news_report│
                    └────────┬────────┘
                             │ 组装 graph
         ┌───────────────────┼───────────────────┐
         │                   │                   │
  ┌──────▼──────┐   ┌───────▼───────┐   ┌───────▼──────┐
  │  harness/   │   │   stages/     │   │  linters/    │
  │  graph.py   │──▶│ collect_raw   │──▶│ collect_raw  │
  │  state.py   │   │ clean_items   │   │ clean_items  │
  │  stage_gates│   │ structure_ev  │   │ structure_ev │
  │  hooks/     │   │ analyze_ins   │   │ analyze_ins  │
  │  context_rt │   │ generate_rpt  │   │ generate_rpt │
  └─────────────┘   └───────────────┘   └──────────────┘
```

## 各层职责边界

### conversation/（对话层）
- **职责**：判断用户意图，决定是否触发业务 Skill
- **允许依赖**：skill_executors/、harness/llm_client.py
- **禁止**：直接操作 state、graph、stage
- **对外接口**：`handle_message(message) -> ConversationResponse`

### skill_executors/（能力层）
- **职责**：组装 graph，启动流水线，格式化返回结果
- **允许依赖**：harness/graph.py、harness/state.py、stages/
- **禁止**：写业务逻辑（业务逻辑在 stages/ 里）
- **对外接口**：`run_daily_news_report_skill() -> DailyNewsReportResult`

### harness/（控制层）
- **职责**：提供 Runtime Harness 基础设施——状态管理、流程路由、Hook 系统、LLM 客户端
- **允许依赖**：无（仅标准库，stages/ 通过 graph handler 注册的方式注入）
- **禁止**：写业务逻辑

### stages/（业务层）
- **职责**：执行具体业务逻辑（抓取、清洗、结构化、分析、报告生成）
- **允许依赖**：harness/state.py、harness/artifacts.py、harness/llm_client.py
- **禁止**：直接调用 graph 路由（那是 graph 的职责）、跨 stage 直接调用

### linters/（校验层）
- **职责**：检查 stage 产物是否满足字段合同，返回 pass/fail 判决
- **允许依赖**：harness/state.py（读取 FIELD_SPEC）
- **禁止**：写业务逻辑、修改 state

## 横切关注点规则

横切关注点（auth、log、telemetry、hook、linter）只能通过 harness/ 的注册机制注入：

1. **Hook**：通过 `StageHooks.on_before()` / `StageHooks.on_after()` 注册监听器
2. **Linter**：通过 `stage_gates.LINTERS` 字典注册，由 `evaluate_linter` hook 调用
3. **Tool**：通过 `tool_gateway.TOOL_REGISTRY` 注册，通过 `STAGE_ALLOWED_TOOLS` 授权

## 禁止的跨层调用

- ❌ stages/ 不能直接调用 conversation/ 的函数
- ❌ stages/ 不能直接调用其他 stage 的函数（必须通过 graph 路由）
- ❌ linters/ 不能修改 state（只能读取和判断）
- ❌ conversation/ 不能直接操作 state 数据（必须通过 skill_executors/）
- ❌ 确定性 stage 不能调用 LLMClient
