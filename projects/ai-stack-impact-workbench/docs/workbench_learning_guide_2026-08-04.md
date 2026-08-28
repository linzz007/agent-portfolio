# Agent Workbench 学习与面试掌握指南

> 日期：2026-08-04，更新：2026-08-05  
> 目标：把 Workbench 学到可以写进简历、可以被面试追问的程度。  
> 核心判断：Workbench 不是新闻/政策/调研三个小工具的集合，而是一个面向外部变化影响分析场景的对话型 Agent Harness。
> 当前公开入口：默认对话、`/report`、Wiki 数据页。

## 0. 先怎么用

优先按 `readme.md` 启动页面。

如果你本地 `8501` 已经被旧进程占着，就把服务起到别的端口再看页面；本轮验收我用的是 `8765`。

你现在最值得先做的三件事：

1. 打开对话页，发一轮普通问题。
2. 输入 `/report`，看 Trace、source_summary 和 Artifact。
3. 切到 Wiki 页，理解“浏览资料”和“维护资料”为什么要分开。

当前最新验收结果：

- 全量 pytest：535 passed。
- Full capability acceptance：21 passed / 0 failed。
- 最新验收报告：`data/acceptance/latest_workbench_full_capability_acceptance.json`。

## 1. 项目一句话

Workbench 面向企业或个人项目的外部变化影响分析场景：用户在聊天入口提出“最近政策、新闻、行业事件、技术发布、开源更新对我有什么影响”“当前接入了哪些数据源”“把结果沉淀到 Wiki”等问题，系统根据 Company Wiki、长期 Memory、会话上下文和外部数据源，自动选择合适的内部路线，生成带运行证据的回答和产物。

面试中不要说：

> 我做了一个新闻分析系统。

应该说：

> 我做的是一个对话型 Agent Harness。外部变化影响分析是业务场景，Harness 负责把每轮对话变成可控制、可审计、可复盘、可回归的 AgentRun。

## 2. 当前公开能力

前端只展示少量用户入口：

| 入口 | 用途 |
|---|---|
| 普通输入 | 默认进入 `general_chat`，只结合 Company Wiki、长期 Memory 和当前会话上下文 |
| `/report` | 统一外部变化影响分析入口，内部按需进入政策、新闻、调研或数据源盘点路线 |
| Wiki 页面 | 独立浏览和编辑 Company Wiki 页面、结构化 facts 和来源信息，保存后同步 SQLite 索引 |

后端仍兼容 `/impact`、`/policy`、`/news`、`/research`、`/wiki`，但它们不再是主要公开入口。这样设计的原因是：新闻、政策、行业事件、技术发布和开源更新本质上都是“外部变化数据”，用户不应该先学习系统内部有哪些 workflow；同时重型报告需要显式授权，不能靠关键词自动触发。

## 3. 外部变化影响分析链路

`external_impact_report` 是公开 Skill。它接收 `/report` 触发的外部变化相关问题，然后选择内部路线：

```text
用户输入 /report
-> external_impact_report
-> impact router
   -> source_inventory：数据源盘点、快照状态、来源边界
   -> policy：政策/监管/合规影响分析
   -> news：新闻/行业事件/技术发布/开源更新影响分析
   -> research：补充调研和结构化报告
-> ContextManifest
-> ToolGateway / PermissionEngine
-> Gate / Source Boundary
-> Artifact
-> Trace
-> 最终回答
```

这条链路和以前的固定 `State -> Graph -> Stage Gate -> Artifact` 不冲突。区别是：

- 以前是固定 workflow，适合 Daily AI Insight Engine 这种批处理日报。
- 现在是对话型 Harness，先理解用户意图，再选择内部路线。
- 固定流程被折叠成 `/report` 的一个内部能力，而不是暴露给用户当多个按钮。

## 4. 四个核心 Harness 能力

### 4.1 可控

可控的意思是：模型能看什么、能用什么工具、最多跑几步、能不能写 Memory，都由代码契约控制。

重点文件：

- `src/policy_impact/skills/manifest.py`
- `src/policy_impact/skills/registry.py`
- `src/policy_impact/runtime/executor_registry.py`
- `src/policy_impact/harness/context_manifest.py`
- `src/policy_impact/harness/tool_gateway.py`
- `src/policy_impact/harness/permissions.py`

必须掌握：

- `SkillManifest` 的 `execution_mode`、`allowed_tools`、`allowed_subagents`、`context_policy_id`、`memory_policy_id`、`max_steps`、`max_model_calls`。
- `external_impact_report` 为什么拥有更大的工具白名单，但仍由内部路线和 ToolGateway 收口。
- `PermissionEngine` 的 `deny -> ask -> allow` 顺序。

面试表达：

> 可控不是在 prompt 里要求模型守规矩，而是通过 SkillManifest、ContextManifest、ToolGateway 和 PermissionEngine 把模型的上下文、工具和副作用限制在明确边界内。

### 4.2 可审计

可审计的意思是：一个回答必须能追溯到一次运行和一组步骤。

重点文件：

- `src/policy_impact/memory/store.py`
- `src/policy_impact/harness/agent_runtime.py`
- `src/policy_impact/app/chat_workbench_service.py`
- `src/policy_impact/app/static/workbench/app.js`

必须掌握：

- `agent_runs` 表记录一次完整回合。
- `agent_steps` 表记录 intent、skill、context、memory、tool、gate、artifact、final answer。
- 前端把 Trace 放在回答上方，用户展开后可以看到输入、输出、工具和 Gate。
- 数据源盘点路线会额外展示 `source_summary`，让用户看到当前分析依赖哪些来源和快照。

面试表达：

> 每轮对话都会持久化为 AgentRun，每个关键阶段写入 AgentStep。排查问题时可以根据 run_id 查看本轮选择了哪个 Skill、内部路线是什么、使用了哪些上下文、调用了哪些工具、Gate 为什么通过或阻断、最终生成了哪些 Artifact。

### 4.3 可复盘

可复盘的意思是：当结果错了，你能解释“错在哪里”，而不是只说模型幻觉。

准备三个 bad case：

1. 路由错：外部变化问题没有进入 `external_impact_report`。
2. 上下文错：Company Wiki 或长期 Memory 命中不相关内容，污染回答。
3. 证据错：数据源快照为空，但系统仍把结果写成实时结论。

复盘路径：

```text
run_id
-> intent_classification
-> skill_selection
-> context_build
-> memory_read
-> tool_call / source_summary
-> gate_check
-> artifact_write
-> final_answer
```

面试表达：

> 我把复盘对象拆成 skill、internal_route、context、memory、tool、gate 和 artifact。一个 bad case 不是简单归因给模型，而是沿 AgentRun 的步骤定位：是路由错、上下文错、工具错、记忆错，还是 Gate 没拦住。

### 4.4 可回归

可回归的意思是：修改 Runtime、Skill 或 Prompt 后，可以自动验证旧链路没有坏。

必须会跑：

```powershell
cd D:\AAAcode\code-code\agent+\harness\policy_impact
py -3 -m compileall -q src scripts tests
py -3 scripts\harness_linter.py
py -3 -m pytest -q
node --check src\policy_impact\app\static\workbench\app.js
node --check scripts\run_workbench_ui_cdp_check.mjs
py -3 scripts\run_workbench_dialogue_acceptance.py --skip-ui
py -3 scripts\run_workbench_full_capability_acceptance.py
```

如果你当前页面不是跑在 `8501`，先指定验收地址：

```powershell
$env:WORKBENCH_BASE_URL='http://127.0.0.1:8765'
py -3 scripts\run_workbench_full_capability_acceptance.py
```

面试表达：

> 我没有只靠手动点页面验证 Agent，而是把路由、上下文、Memory、权限、Gate、Trace、Wiki 写回和多轮语义沉淀成 pytest、harness_linter 和 acceptance 脚本。这样每次修改 Runtime 或 Skill 后都能做回归验证。

## 5. 必学模块顺序

### 第 1 层：跑起来和演示

学习文件：

- `readme.md`
- `run_policy_api.py`
- `src/policy_impact/app/api.py`
- `src/policy_impact/app/chat_workbench_service.py`
- `src/policy_impact/app/static/workbench/app.js`

验收问题：

- 页面入口是什么？
- 如何新建会话？
- 为什么公开命令只保留 `/report`？
- 用户不输入命令时，为什么必须保持普通对话？
- Wiki 为什么是独立数据页，而不是一个 slash skill？
- Trace 在页面哪里展示？

### 第 2 层：Turn 生命周期

学习文件：

- `src/policy_impact/app/chat_workbench_service.py`
- `src/policy_impact/harness/agent_runtime.py`
- `src/policy_impact/runtime/turn_coordinator.py`
- `src/policy_impact/memory/store.py`

验收问题：

- 用户消息在哪里保存？
- AgentRun 在哪里创建？
- AgentStep 是怎么追加的？
- assistant message 和 run_id 如何关联？
- 一轮失败时状态怎么记录？

### 第 3 层：Skill 与内部路线

学习文件：

- `src/policy_impact/skills/manifest.py`
- `src/policy_impact/skills/registry.py`
- `src/policy_impact/runtime/execution_mode.py`
- `src/policy_impact/runtime/executor_registry.py`
- `src/policy_impact/skills/executors/current_skills.py`

验收问题：

- `external_impact_report` 的 execution mode、allowed tools、allowed subagents 是什么？
- `policy_weekly_impact`、`recent_news_report`、`research_report` 为什么还保留为内部兼容能力？
- `source_inventory` 为什么不需要模型调用？
- 为什么新增业务能力不应该到处写 if/else？

### 第 4 层：Context 和 Memory

学习文件：

- `src/policy_impact/harness/context_manifest.py`
- `src/policy_impact/harness/chat_context.py`
- `src/policy_impact/harness/prompt_builder.py`
- `src/policy_impact/memory/store.py`
- `src/policy_impact/company_wiki/loader.py`
- `data/companies/company_001/wiki/**/*.md`

验收问题：

- ContextManifest 记录了什么？
- visible_keys 和 hidden_fields 有什么意义？
- token_budget 怎么分配？
- 聊天历史、长期 Memory、Company Wiki 有什么区别？
- 为什么模型不能随便把推断写进长期 Memory？
- 为什么 Wiki 源资料保留 markdown，而运行时检索使用 SQLite 索引？

### 第 5 层：Tool Policy、Gate 和数据源边界

学习文件：

- `src/policy_impact/harness/tool_gateway.py`
- `src/policy_impact/harness/permissions.py`
- `src/policy_impact/harness/gate_engine.py`
- `src/policy_impact/runtime/gates.py`
- `src/policy_impact/runtime/gate_catalog.py`
- `config/sources.json`
- `src/policy_impact/source_connectors/ingestion.py`

验收问题：

- deny、ask、allow 的顺序是什么？
- 未知工具为什么默认 deny？
- 数据源 `source_levels` 如何表达证据强度？
- 快照为空时，Gate 应该允许什么、禁止什么？
- `source_summary` 在 Trace 里如何体现？

### 第 6 层：Subagent

学习文件：

- `src/policy_impact/harness/subagents.py`
- `src/policy_impact/runtime/subagent_roles.py`
- `src/policy_impact/runtime/task_brief.py`
- `src/policy_impact/runtime/subagent_outputs.py`
- `tests/test_subagents.py`

验收问题：

- Subagent 能看到主 Agent 的全部历史吗？
- TaskBrief 包含什么？
- allowed_subagents 在哪里定义？
- verifier / skeptic 的价值是什么？
- Subagent 输出如何回到主流程？

### 第 7 层：Eval 和验收

学习文件：

- `scripts/harness_linter.py`
- `scripts/run_workbench_dialogue_acceptance.py`
- `scripts/run_workbench_multiturn_acceptance.py`
- `scripts/run_business_semantic_acceptance.py`
- `docs/acceptance/`

验收问题：

- pytest 覆盖什么？
- harness_linter 防什么？
- dialogue acceptance 和 multiturn acceptance 区别是什么？
- 业务语义验收为什么比普通单元测试更重要？
- 哪些能力当前还不能宣称已完成？

## 6. 两周学习计划

### Day 1：跑起来

任务：

- 按 README 启动服务。
- 打开页面。
- 试三轮：普通聊天、外部变化影响分析、数据源盘点。

输出：

- 写一段 200 字说明：这个系统打开后能做什么。

### Day 2：读主链路

任务：

- 阅读 `ChatWorkbenchService`。
- 阅读 `AgentRuntime.run_turn()` 和 `TurnCoordinator`。
- 找到保存 user message、创建 run、保存 assistant message 的位置。

输出：

- 画出主链路文本图。

### Day 3：AgentRun / AgentStep

任务：

- 阅读 `PolicyMemoryStore` 里 `agent_runs`、`agent_steps` 表结构。
- 找到 `create_agent_run()`、`add_agent_step()`、`list_agent_steps()`。

输出：

- 解释 AgentRun 和 AgentStep 的区别。

### Day 4：SkillManifest

任务：

- 阅读 `SkillManifest`。
- 阅读 `registry.py` 中 `external_impact_report` 和 `general_chat`。
- 只把 `company_wiki_blueprint` 当兼容能力了解，不作为公开入口学习重点。

输出：

- 做一张表：公开 Skill、ExecutionMode、allowed_tools、allowed_subagents、max_steps。

### Day 5：外部变化内部路线

任务：

- 阅读 `_run_external_impact_report()`。
- 阅读 `_impact_internal_route()`。
- 阅读 `_run_impact_source_inventory()`。

输出：

- 解释 source_inventory、policy、news、research 四条内部路线分别解决什么问题。

### Day 6：ContextManifest

任务：

- 阅读 `context_manifest.py`。
- 阅读 `chat_context.py`。
- 找一次真实运行里的 `context_build` step。

输出：

- 解释 visible_keys、hidden_fields、token_budget、checksum。

### Day 7：Memory 和 Wiki 数据面

任务：

- 阅读 `save_memory()`、`save_memory_once()`、`search_memory()`。
- 做一次“记住……”对话，再开新会话验证是否能召回。
- 阅读 `company_wiki/loader.py`、`ChatWorkbenchService.wiki_tree()`、`ChatWorkbenchService.update_wiki_page()`。
- 打开 Wiki 页面，理解页面正文、FACT 区块、SQLite facts 索引之间的关系。

输出：

- 解释聊天历史、conversation_summary、长期 Memory、Company Wiki、SQLite facts 索引的区别。
- 解释为什么 Wiki 保存不调用模型，但会影响后续聊天和 `/report`。

### Day 8：数据源和 Tool Policy

任务：

- 阅读 `config/sources.json`。
- 阅读 `source_connectors/ingestion.py`。
- 阅读 `tool_gateway.py` 和 `permissions.py`。

输出：

- 解释数据源等级、快照、工具白名单和 Gate 的关系。

### Day 9：Gate

任务：

- 阅读 `gate_engine.py`、`runtime/gates.py`、`runtime/gate_catalog.py`。
- 重点看 claim_schema、evidence_binding、publication。

输出：

- 准备一个“高风险结论为什么要过 Gate”的面试回答。

### Day 10：Subagent

任务：

- 阅读 `subagents.py`、`task_brief.py`、`subagent_roles.py`。
- 阅读 `tests/test_subagents.py`。

输出：

- 解释 Subagent 如何隔离上下文和权限。

### Day 11：Trace 前端

任务：

- 阅读 `app.js` 中 Trace 展示逻辑。
- 在页面上跑一轮并展开 Trace。

输出：

- 准备一次演示话术：从用户问题到 Trace。

### Day 12：验收脚本

任务：

- 跑 pytest、harness_linter、dialogue acceptance。
- 记录通过结果。

输出：

- 写清楚每类测试验证什么。

### Day 13：准备 bad case

任务：

- 构造三个 bad case：路由错、Memory 污染、证据边界不足。
- 对照 Trace 找定位路径。

输出：

- 每个 bad case 写 5 行复盘。

### Day 14：决定能否投简历

任务：

- 随机选择一次外部变化分析运行。
- 现场讲清主链路。
- 现场打开代码解释 3 个核心文件。
- 现场打开 Trace 解释一次 bad case。

如果做不到，就不要把 Workbench 放主简历，先用 Daily 作为保底版本。

## 7. 投简历前的最低掌握标准

- 能独立启动项目并完成一次真实对话。
- 能解释 `AgentRuntime -> Skill -> ContextManifest -> ToolGateway/Gate -> AgentStep` 主链路。
- 能说清 `external_impact_report` 和内部 policy/news/research/source_inventory 的关系。
- 能打开 Trace 说明一次 AgentRun 发生了什么。
- 能解释一个 bad case 是怎么定位的。
- 能如实说明哪些能力当前未完整实现，例如完整 Replay、审批暂停恢复、四层版本化 Memory。

不要在简历或面试里宣称：

- 完整生产级多租户 Harness。
- 完整 Recorded/Fork/Fresh Replay。
- 完整四层版本化 Memory。
- 显著优于 Codex/Claude。
- 业务指标提升，除非有真实 Artifact 支撑。

## 8. 最终简历表述建议

建议写：

> 面向企业外部变化影响分析场景，构建单用户 Agent Workbench，普通输入默认进入 `general_chat` 并读取 Company Wiki 与长期 Memory；用户显式输入 `/report` 时进入 `external_impact_report`，内部按需选择数据源盘点、政策、新闻或调研路线；同时提供独立 Wiki 数据页展示企业资料和结构化 facts。系统将每轮请求持久化为 AgentRun 与有序 AgentStep，并通过 SkillManifest、ContextManifest、ToolGateway、PermissionEngine、Gate、Trace 和 ArtifactStore 约束模型上下文、工具调用、记忆读写和产物生成过程，支撑业务 Agent 的可控执行、运行审计、问题复盘和回归验证。

不要写：

> 实现了完整企业级 Agent Infra 平台。

因为当前项目还不是生产级平台，这样写会被面试官抓住。

## 9. 你要形成的最终认知

Workbench 的价值不是“我做了一个更聪明的 Agent”，而是：

```text
普通 Agent 应用：
用户问题 -> 模型回答

Workbench Harness：
用户问题
-> Skill 契约
-> 内部路线选择
-> Context 边界
-> Tool 权限
-> Memory 策略
-> Gate 校验
-> Trace 留痕
-> Artifact 沉淀
-> Eval 回归
-> 可解释回答
```

面试官真正想听的是：你是否理解业务 Agent 从 demo 走向可维护系统时，为什么必须有控制面、证据链和回归体系。
