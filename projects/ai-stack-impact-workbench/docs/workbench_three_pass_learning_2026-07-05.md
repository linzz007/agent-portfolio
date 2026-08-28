# Workbench 三遍学习链路：/research、general_chat、policy/news

本文档用于第一遍入门当前 Agent Workbench。它不从抽象概念讲起，而是按真实用户输入、真实 runtime step、真实 trace 字段来学。

本次验证时间：2026-07-05  
验证入口：`scripts/run_workbench_dialogue_acceptance.py`  
最新验收产物：`data/acceptance/latest-workbench-dialogue-acceptance.json`  
前端截图：`data/acceptance/workbench-dialogue-2026-07-05-1783265070.png`

## 总体结论

当前这三个学习步骤都有实现。

| 学习链路 | 当前状态 | 最适合学习什么 |
| --- | --- | --- |
| `/research` | 已完整跑通 | skill 路由、context manifest、system prompt、model_call、artifact、memory_write、trace |
| `general_chat` | 已完整跑通 | 为什么普通对话可以不调用模型、如何读 memory、如何查 company wiki、和 research 的差异 |
| `policy/news workflow` | 已跑通业务 workflow 汇总 trace | Workbench 如何包住固定业务流程，并统一留下 AgentRuntime trace |

最重要的判断：`/research` 是现在最适合作为第一遍学习入口的一条链路。它不是最复杂的业务 workflow，但它最完整地展示了 Harness 的核心结构。

## 建议测试问题

### 第一组：/research 从简单到难

1. `/research 帮我整理 Agent Workbench 的动态验收标准，需要覆盖 trace、memory、artifact 和模型选择。`
2. `/research 帮我生成一份面试讲解提纲：为什么 Harness 不是普通 Prompt 工程？`
3. `/research 对比 general_chat 和 research_report 的执行差异，重点看上下文、模型调用、产物和记忆写入。`
4. `/research 假设我要给面试官讲这个项目，请把 skill routing、context manifest、prompt contract、artifact、memory_write 串成一条链路。`

### 第二组：general_chat 从简单到难

1. `这个系统现在有哪些 trace、memory、model selection 证据？`
2. `为什么普通聊天不一定需要调用模型？`
3. `请根据长期记忆和企业知识库解释当前 Workbench 的核心能力。`
4. `刚才 /research 生成的结果有没有进入长期记忆？请用普通对话帮我查一下。`

### 第三组：policy/news workflow 从简单到难

1. `最近政策和合规要求会如何影响 Agent 平台审计、工具权限和运行留痕？`
2. `/policy 请生成本周政策影响分析，重点看 AI 应用平台的合规风险。`
3. `/auto 最新新闻里和 agent observability、tool policy 相关的内容有什么影响？`
4. `/news 请生成一份最近新闻影响报告，重点关注 Agent Runtime、Harness、工具安全和可观测性。`

## 第一遍：学透 /research

### 1. `/research 一个问题`

推荐输入：

```text
/research 帮我整理 Agent Workbench 的动态验收标准，需要覆盖 trace、memory、artifact 和模型选择。
```

前端会先解析斜杠命令。`/research` 本身不直接发给模型，而是被前端解释为“本轮使用 `research_report` skill”。

在动态验收中，这条用例是 D03，真实 step 序列是：

```text
intent_classification
-> skill_selection
-> context_build
-> memory_read
-> model_call
-> artifact_write
-> memory_write
-> final_answer
```

这条链路最像一个完整的 Harness loop：先决定意图和 skill，再构造上下文，再读取记忆，再调用模型生成结构，最后由 runtime 写 artifact 和 memory。

### 2. 为什么进入 `research_report` skill

有两层机制。

第一层是前端斜杠命令：

- `app.js` 中的 `slashCommands` 把 `/research` 映射到 `research_report`。
- `parseSlashCommand(raw)` 会把 `/research xxx` 拆成 `{ skillId: "research_report", message: "xxx" }`。
- 发送消息前，前端会先更新 session 的 `active_skill_id`。

第二层是后端 skill registry：

- `ChatWorkbenchService.send_message()` 读取 session 的 `active_skill_id`。
- `AgentRuntime.run_turn()` 调用 `SkillRegistry.resolve_skill(active_skill_id, mode, message)`。
- 如果 `active_skill_id` 存在，registry 会直接 `get_skill("research_report")`。

因此这不是“模型自己猜测要不要调研”，而是确定性的 Harness 路由。

### 3. 它拿到了哪些上下文

`AgentRuntime._build_context()` 会构造一个 `PolicyImpactState`，然后通过 `build_context_manifest()` 生成本轮上下文清单。

当前 `/research` 可见上下文主要是：

| 字段 | 含义 |
| --- | --- |
| `company_context` | 本轮用户问题、已选择的 skill |
| `memory_updates` | 根据用户问题检索到的长期记忆 |

`context_manifest` 还会记录：

- `manifest_id`
- `stage_name=chat_turn`
- `agent_role=skill:research_report`
- `visible_keys`
- `token_budget`
- `token_estimates`
- `visible_content_checksums`
- `hidden_fields`

关键点：模型并不是拿到整个 state。它只拿到 `visible_keys` 允许暴露的字段。这个设计就是 Harness 里的上下文边界。

### 4. 读了哪些 memory

`/research` 会显式执行 `memory_read` step：

```text
step_type = memory_read
title = 为调研报告读取记忆
input_payload = { query: 用户问题 }
output_payload = { memory_count: 命中数量 }
loop_phase = observe
```

这一步调用的是 `PolicyMemoryStore.search_memory(message, top_k=5)`。

它的作用不是让模型“凭感觉回忆”，而是让 runtime 先做可审计的 memory 检索，然后把命中数量和上下文控制写进 trace。

### 5. system prompt 长什么样

`/research` 的 prompt 由 `build_research_report_prompt()` 生成。

核心 system prompt 是：

```text
You are the Research Report subagent inside Agent Workbench.
You operate under a harness contract, not as a free-form chatbot.
The runtime has already selected this skill, built a context manifest, and will persist artifacts and memory.
Your job is to produce a deterministic research outline for the requested report.
Use only the visible context and task. Never fabricate evidence, tool results, citations, or file paths.
If evidence is thin, say what is missing instead of overstating confidence.
Output must support sections: thesis, evidence, risks, recommendations, and next checks.
```

这里的设计重点是：模型只负责生成“报告结构/提纲”，不拥有工具调用、memory 写入、artifact 持久化权限。真正的边界控制在 runtime。

### 6. `model_call` 记录了什么

动态验收 D03 证明 `model_call` 已经记录了这些字段：

| 字段 | 说明 |
| --- | --- |
| `model_id` | 当前 session 选择的模型 |
| `schema_name` | `research_report.v1` |
| `messages` | system prompt + user prompt |
| `prompt_contract` | prompt 契约 |
| `output_payload.response_id` | 模型响应 ID |
| `output_payload.payload` | 模型输出结构 |
| `metadata.adapter` | 当前使用的模型适配器 |
| `metadata.loop_phase` | `think` |

`prompt_contract` 的关键内容包括：

```text
contract_id = research_report.v1
role = research_report_subagent
loop_boundary = model only proposes structure; runtime owns tools, memory writes, and artifacts
context_controls.visible_keys = company_context, memory_updates
context_controls.memory_count = 本轮命中记忆数量
context_controls.token_budget = 当前上下文预算
```

这就是面试里要讲的重点：你不是只存了模型输出，而是把“模型为什么被调用、能看什么、不能做什么、输出应该满足什么契约”都落进 trace。

### 7. 生成了什么 artifact

`/research` 会生成 Markdown 报告：

```text
data/companies/{company_id}/reports/{date}-research-report-{run_id}.md
```

动态验收 D03 中，artifact 类型是：

```text
artifacts.report = .../reports/2026-07-05-research-report-{run_id}.md
```

报告不是前端临时文本，而是由 runtime 写入文件系统，并被挂载到本轮 `AgentRun.artifacts`。

### 8. 写入了什么 memory

`/research` 会写入一条 `skill_result` memory：

```text
namespace = company:{company_id}:skill_results
memory_type = skill_result
content = 调研报告已生成，主题为“用户问题”：报告路径
metadata.skill_id = research_report
metadata.artifact_type = markdown_report
metadata.path = 报告路径
```

这一步会留下 `memory_write` step：

```text
step_type = memory_write
title = 保存调研报告记忆
input_payload = { memory_type: skill_result }
output_payload = { memory_id, namespace }
loop_phase = act
```

所以后续普通对话可以通过 memory 检索到“之前生成过某份调研报告”。

### 9. trace 如何展示

前端现在不是右侧单独 inspector，而是在 assistant 回答上方展示一张 `thinking-card`。

它展示：

- 本轮运行 ID
- skill 名称
- model 名称
- step 数
- tool 数
- gate 数
- artifact 数
- 每个 step 的 `step_type`、`title`、`loop_phase`
- 可展开的 `step-details` JSON

前端 F01 验收已经检查：

- `thinking-card trace above answer`
- `step-details JSON trace payload`
- `loop_phase visible in rendered trace JSON`
- `prompt_contract validated through the same HTTP trace`
- `old skill-dock/inspector removed`

你学习 `/research` 时，应该重点展开第 5 步 `model_call`，看里面的 `messages` 和 `prompt_contract`。

## 第二遍：再学 general_chat

### 1. 推荐输入

```text
这个系统现在有哪些 trace、memory、model selection 证据？
```

动态验收 D01 的真实 step 序列是：

```text
intent_classification
-> skill_selection
-> context_build
-> memory_read
-> tool_call
-> final_answer
```

### 2. 普通聊天为什么不一定调用模型

当前 `general_chat` 是一个“检索式普通回答”。

它的执行语义是：

- 不运行重型业务 workflow。
- 不生成 artifact。
- 不写入新的 skill_result memory。
- 只读取长期 memory 和 company wiki。
- 用检索到的事实拼出可解释回答。

这不是缺陷，而是一个设计选择：普通聊天在这个 Workbench 里承担“低成本、可解释、读上下文”的能力，而不是每句话都交给模型生成。

如果未来接入真实模型，合理做法不是把这条链路推翻，而是在 `memory_read` 和 `tool_call` 后增加一个可审计 `model_call`，并保留当前的 context/tool/gate 结构。

### 3. 它怎么读 memory

`general_chat` 调用：

```text
ToolGateway.call("main_agent", "memory_search", company_id, query, top_k=5)
```

然后写入 step：

```text
step_type = memory_read
title = 读取长期记忆
input_payload = { query, top_k }
output_payload = { memory_count }
loop_phase = observe
```

这说明 memory 是由工具网关和 store 读取的，不是 prompt 里随便塞一段历史记录。

### 4. 它怎么查 company wiki

`general_chat` 接着调用：

```text
company_wiki_search(company_id, query, top_k=5)
```

然后把 `memory_search` 和 `company_wiki_search` 两次工具调用汇总成一个 `tool_call` step。

该 step 带有 gate 结果：

```text
decision = allow
reason = main_agent allowlist permits memory_search and company_wiki_search
```

这体现的是 Tool Policy：普通聊天不是能任意调用工具，只能调用 allowlist 允许的检索工具。

### 5. 它怎么生成回答

当前回答由 runtime 根据检索结果拼装：

- 前 3 条 company wiki fact
- 前 3 条 memory item
- 对应 citations

如果没有命中，它会提示先补充企业知识库或生成知识库规划。

### 6. 它和 `/research` 的差异

| 对比项 | general_chat | research_report |
| --- | --- | --- |
| 入口 | 自动路由或 `/chat` | `/research` 显式路由 |
| 核心用途 | 低成本问答、读上下文 | 结构化调研、生成报告 |
| 是否 model_call | 当前不调用 | 调用 `ScriptedModelAdapter` |
| 是否 artifact | 不生成 | 生成 Markdown report |
| 是否 memory_write | 不写 skill_result | 写入 `company:{id}:skill_results` |
| tools | `memory_search`、`company_wiki_search` | 当前模型只生成结构，artifact/memory 由 runtime 写 |
| trace 长度 | 6 步 | 8 步 |

你第二遍学习的目标是理解：不同 skill 的执行语义不同。不是所有用户问题都应该走同一个 Agent loop。

## 第三遍：再学 policy/news workflow

### 1. 推荐输入

政策 workflow：

```text
最近政策和合规要求会如何影响 Agent 平台审计、工具权限和运行留痕？
```

news workflow：

```text
/auto 最新新闻里和 agent observability、tool policy 相关的内容有什么影响？
```

### 2. 它是什么

`policy_weekly_impact` 和 `recent_news_report` 不是普通聊天，也不是 `/research` 这种轻量调研技能。

它们是已经存在的业务 workflow，被 Workbench 包装成 skill：

- 下层：固定 workflow / stage / gate / artifact。
- 上层：统一走 `AgentRuntime.run_turn()`。
- trace：统一写入 `agent_runs` 和 `agent_steps`。
- 前端：用同一个 thinking-card 展示。

### 3. policy workflow 的 trace

动态验收 D04 的真实 step 序列是：

```text
intent_classification
-> skill_selection
-> context_build
-> tool_call
-> gate_check
-> artifact_write
-> final_answer
```

其中：

- `tool_call` 运行政策分析工作流，并记录底层 `workflow_run_id` 和 `tool_call_count`。
- `gate_check` 汇总底层 workflow 的 gate 数量和状态。
- `artifact_write` 挂载 Markdown、HTML、run artifact。

### 4. news workflow 的 trace

动态验收 D06 第三轮证明：

```text
/auto 最新新闻里和 agent observability、tool policy 相关的内容有什么影响？
```

会路由到：

```text
recent_news_report
```

真实 step 序列是：

```text
intent_classification
-> skill_selection
-> context_build
-> tool_call
-> gate_check
-> artifact_write
-> final_answer
```

它会生成：

- Markdown 新闻影响报告
- HTML 新闻影响报告
- run artifact JSON

### 5. 这部分和 `/research` 的差异

| 对比项 | research_report | policy/news workflow |
| --- | --- | --- |
| 类型 | 轻量报告 skill | 被 Workbench 包起来的业务 skill |
| 内部结构 | runtime 直接编排 memory/model/artifact | 底层 workflow 已经有 stage/gate/artifact |
| 顶层 trace | 细到 model_call、memory_write | 目前是 workflow 汇总 trace |
| 适合学习阶段 | 第一遍 | 第三遍 |
| 面试讲法 | 展示 Harness loop 的完整链路 | 展示 Workbench 如何统一托管业务 workflow |

当前不足也要诚实说清楚：policy/news 在 Workbench 顶层还没有把每个底层 stage/subagent 映射成独立前端 step。现在顶层看到的是汇总的 `tool_call`、`gate_check`、`artifact_write`。如果后续要继续强化，这是最值得补的一点。

## 你应该怎么按三遍学习

### 第一遍只看 `/research`

目标：能完整复述这条链路。

你要能讲清楚：

```text
/research 一个问题
-> 前端 parseSlashCommand 把命令映射到 research_report
-> session.active_skill_id 被设置为 research_report
-> AgentRuntime 通过 SkillRegistry 选择 skill
-> context_build 只暴露 company_context 和 memory_updates
-> memory_read 检索长期记忆
-> prompt_builder 生成 system prompt 和 prompt_contract
-> model_call 记录 messages、schema、prompt_contract、模型输出
-> artifact_write 写 Markdown 报告
-> memory_write 写 skill_result memory
-> final_answer 保存最终回答
-> 前端 thinking-card 展示所有 step 和 details JSON
```

如果这条能讲顺，Workbench 就入门了。

### 第二遍再看 general_chat

目标：理解普通对话和技能任务不是一种执行语义。

你要能讲清楚：

```text
general_chat
-> 自动路由或 /chat
-> context_build
-> memory_read
-> tool_call: memory_search + company_wiki_search
-> gate_result: allowlist 允许
-> runtime 根据 evidence 生成回答
-> 不生成 artifact
-> 不写 skill_result memory
-> 当前不需要 model_call
```

这能解释为什么 Workbench 不是一个“所有问题都丢给模型”的壳。

### 第三遍再看 policy/news

目标：理解 Workbench 如何统一托管业务 workflow。

你要能讲清楚：

```text
policy/news
-> AgentRuntime 仍然负责 intent、skill、context、trace
-> 业务 executor 负责固定 workflow
-> 底层 workflow 产生 tool calls、gate results、artifacts
-> 顶层 AgentStep 汇总 tool_call、gate_check、artifact_write
-> 前端仍然用同一套 trace UI 展示
```

这能解释为什么这个项目不是只有一个聊天框，而是有 Harness 能力的 Agent Workbench。

## 本次验收结果

已通过：

```powershell
py -3 scripts\run_workbench_dialogue_acceptance.py --skip-ui
py -3 scripts\run_workbench_dialogue_acceptance.py
```

完整验收结论：

| 用例 | 状态 | 说明 |
| --- | --- | --- |
| D01 | passed | 单轮 `general_chat`，读取企业知识库和长期记忆 |
| D02 | passed | 自定义 OpenAI-compatible 模型后对话，model_id 进入 trace |
| D03 | passed | `/research` 触发 `research_report`，生成 artifact 并写 memory |
| D04 | passed | 政策/合规问题自动路由到 `policy_weekly_impact` |
| D05 | passed | `/wiki` 触发企业知识库规划 |
| D06 | passed | 多轮对话：普通对话 -> `/research` -> `/auto` 新闻路由 |
| D07 | passed | 非法 skill 被确定性拒绝 |
| F01 | passed | 真实前端渲染、模型配置、斜杠命令、trace details 展示 |

前端 F01 额外确认：

- Agent Workbench shell 存在。
- 模型管理弹窗存在。
- 斜杠命令 palette 存在。
- trace 在回答上方。
- step details 能展示 JSON trace payload。
- `loop_phase` 在前端可见。
- `/research` 的 `prompt_contract` 在同一条 HTTP trace 中可验证。

## 当前不需要改代码的判断

这次对照三条学习链路，没有发现必须修复的代码问题。

原因：

- `/research` 已经具备完整 Harness 链路。
- `general_chat` 的“当前不一定调用模型”是设计语义，不是 bug。
- `policy/news` 已经能作为被 Workbench 包装的业务 skill 留下统一 trace。
- 前端已经通过 F01 动态验收，trace 展示位置和 details JSON 符合之前设计。

下一步如果继续增强，优先级最高的是：把 policy/news 底层 workflow 的每个 stage/subagent 展开成 Workbench 顶层独立 step。这个不是当前学习入口的必要条件，但会让第三遍学习更有杀伤力。

