# Agent Workbench 逐用例逐 Step 深度审计

日期：2026-07-04

本次审计不是只看页面是否能打开，而是按真实用户路径逐轮对话，并把每一轮产生的 `AgentRun`、`AgentStep`、前端 trace 展示、prompt、context、memory、artifact 放在一起检查。

## 审计入口

运行命令：

```powershell
py -3 scripts\run_workbench_dialogue_acceptance.py
```

最新证据：

- 动态验收 JSON：`data/acceptance/latest-workbench-dialogue-acceptance.json`
- 前端截图：`data/acceptance/workbench-dialogue-2026-07-04-1783136622.png`
- 本地页面：`http://127.0.0.1:8501/`

本次还额外使用 Playwright 展开前端第 5 个 step，确认 `model_call` 的 details 里真实展示：

- `messages`
- system prompt
- `prompt_contract`
- `visible_keys`
- `loop_phase`
- `research_report_subagent`

自动验收脚本中的 F01 使用同一条 HTTP trace 校验 `prompt_contract`，使用 Chrome DOM 校验页面 shell、trace/details 容器和 `loop_phase`。这样避免 `chrome --dump-dom` 因 closed details 或渲染时序导致 prompt 字段误判。

## 总体结论

| 模块 | 结论 | 说明 |
| --- | --- | --- |
| API/对话闭环 | 通过 | 能创建 session、发消息、保存 user/assistant、挂载 run_id |
| 前端对话形态 | 通过 | 页面中心是对话，trace 在回答上方，侧栏承载模型/技能/tools |
| 模型选择 | 通过 | 自定义 OpenAI-compatible 模型能持久化，并进入 trace.model_id |
| Slash skills | 通过 | `/research`、`/wiki`、`/auto` 能改变 active skill 或恢复自动路由 |
| Trace 基础结构 | 通过 | 每步有 step_index、step_type、status、input/output、metadata |
| ReAct/loop 结构 | 通过 | 每步新增 `loop_phase`: think / act / observe / answer |
| System prompt | 通过 | `research_report` 的 `model_call` 现在记录 system prompt 和 prompt contract |
| Context 管理 | 通过 | 每轮都有 `context_manifest`，包含 visible keys、token budget、hidden fields/checksum |
| Memory 读写 | 通过 | `general_chat` 读 memory；`research_report` 写 skill_result memory 并可检索 |
| Artifact | 通过 | research/policy/news 产物路径存在；wiki 返回结构化 artifact |
| Tool/Gate Policy | 基本通过 | general/wiki 有 tool allow gate；policy/news 有 workflow gate 汇总 |
| Subagent 可观测 | 部分通过 | skill contract 有 subagents，policy workflow 有底层 tool/gate 汇总；但 Workbench 顶层尚未把每个 workflow stage 映射成独立前端 step |

## 本次修复

### 1. 补强 system prompt 和 prompt contract

新增：

- `src/policy_impact/harness/prompt_builder.py`

`research_report` 不再只用一句薄 prompt，而是构造：

- system prompt：说明它是 Agent Workbench 内的 Research Report subagent，不是自由聊天模型。
- user prompt：包含用户任务、visible context keys、memory 数量、token budget。
- prompt contract：包含 contract_id、role、loop boundary、required behavior、context controls。

前端展开 `model_call` 后可以看到这些字段。

### 2. 给每个 step 增加 loop phase

现在 trace 中每个 step 都有 `metadata.loop_phase`：

- `think`: intent、skill selection、context build、model call
- `observe`: memory read、gate check
- `act`: tool call、artifact write、memory write
- `answer`: final answer

这让前端 trace 不只是流水账，而能解释“思考-执行-观察-回答”的 loop 结构。

### 3. 修复真实 API 与本地代码不同步的验收盲点

审计过程中发现：直接跑 service 的动态验收已经有新字段，但 8501 上的 Uvicorn 进程仍是旧代码，前端展开 `model_call` 看不到 `prompt_contract`。

处理：

- 重启 8501 服务。
- 升级 F01 验收：真实 DOM 必须出现 `loop_phase` 和 trace/details 结构；同一条 HTTP trace 必须出现 `prompt_contract` 和 `research_report_subagent`。

这个点很重要：以后只跑 Python service 不够，涉及前端/API 时必须重启服务并检查真实页面。

## 用例逐 Step 审计

### D01：单轮普通对话

用户行为：询问 Agent Workbench 当前有哪些 trace、memory、model selection 证据。

期望 skill：`general_chat`

| Step | 类型 | Phase | 输入 | 输出 | 审计结论 |
| --- | --- | --- | --- | --- | --- |
| 1 | `intent_classification` | think | message, mode | resolved_skill_id | 通过，确定性路由能解释为什么进入 general_chat |
| 2 | `skill_selection` | think | active_skill_id | skill | 通过，skill contract 进入 trace |
| 3 | `context_build` | think | message, skill_id | context_manifest | 通过，有 visible keys/token budget/hidden fields |
| 4 | `memory_read` | observe | query, top_k | memory_count | 通过，长期记忆是可审计读取，不是隐式拼 prompt |
| 5 | `tool_call` | act | tools | fact_count, memory_count | 通过，调用 memory_search/company_wiki_search，且有 gate allow |
| 6 | `final_answer` | answer | assistant_message_id | answer_preview, artifact_keys | 通过，assistant message 挂 run_id |

判断：符合普通对话 harness 要求。它不调用模型，是当前 deterministic local demo 的合理取舍。

### D02：模型选择后对话

用户行为：添加 `dialogue-compatible` 模型并使用当前模型提问。

期望 skill：`general_chat`

Step 结构与 D01 一致。额外检查：

- 自定义模型进入 model config。
- session.model_id 更新为 `dialogue-compatible`。
- trace.model_id 保留 `dialogue-compatible`。

判断：模型选择链路通过。当前没有真实远程模型调用，这是 demo/runtime 适配层的边界，不应在简历里说“已接入真实模型供应商调用”。

### D03：`/research` 调研报告

用户行为：输入 `/research ...`，要求整理动态验收标准。

期望 skill：`research_report`

| Step | 类型 | Phase | 输入 | 输出 | 审计结论 |
| --- | --- | --- | --- | --- | --- |
| 1 | `intent_classification` | think | message, mode | resolved_skill_id | 通过，slash command 剥离后进入 runtime |
| 2 | `skill_selection` | think | active_skill_id | skill | 通过，explicit skill 来源可解释 |
| 3 | `context_build` | think | message, skill_id | context_manifest | 通过，控制模型可见上下文 |
| 4 | `memory_read` | observe | query | memory_count | 通过，先读 memory 再生成报告 |
| 5 | `model_call` | think | messages, model_id, prompt_contract, schema_name | response_id, payload, created_at | 通过，system prompt 和 prompt contract 已可审计 |
| 6 | `artifact_write` | act | - | report | 通过，Markdown 报告路径存在 |
| 7 | `memory_write` | act | memory_type | memory_id, namespace | 通过，skill_result memory 可被后续检索 |
| 8 | `final_answer` | answer | assistant_message_id | answer_preview, artifact_keys | 通过，回答挂 artifact |

判断：这是目前最能体现 Harness 能力的用例。面试时可以重点讲：slash skill -> context manifest -> prompt contract -> model call -> artifact -> memory write -> trace。

### D04：自动政策影响 workflow

用户行为：直接询问政策/合规对 Agent 平台审计、工具权限、运行留痕的影响。

期望 skill：`policy_weekly_impact`

| Step | 类型 | Phase | 输入 | 输出 | 审计结论 |
| --- | --- | --- | --- | --- | --- |
| 1 | `intent_classification` | think | message, mode | resolved_skill_id | 通过，政策/合规关键词路由正确 |
| 2 | `skill_selection` | think | active_skill_id | skill | 通过 |
| 3 | `context_build` | think | message, skill_id | context_manifest | 通过 |
| 4 | `tool_call` | act | - | tool_call_count, workflow_run_id | 通过，底层 workflow 产生 11 个 tool calls |
| 5 | `gate_check` | observe | - | gate_count, workflow_status | 通过，有 workflow gate 汇总 |
| 6 | `artifact_write` | act | - | artifacts | 通过，Markdown/HTML/run_artifact 路径存在 |
| 7 | `final_answer` | answer | assistant_message_id | answer_preview, artifact_keys | 通过 |

判断：作为 workflow 汇总视图是合格的；如果要宣传“完整 subagent 可观测”，还需要把底层 stage/subagent 映射成 Workbench 顶层 step。

### D05：`/wiki` 企业知识库规划

用户行为：输入 `/wiki ...`，检查企业知识库缺哪些文件以及下一步追问。

期望 skill：`company_wiki_blueprint`

| Step | 类型 | Phase | 输入 | 输出 | 审计结论 |
| --- | --- | --- | --- | --- | --- |
| 1 | `intent_classification` | think | message, mode | resolved_skill_id | 通过 |
| 2 | `skill_selection` | think | active_skill_id | skill | 通过 |
| 3 | `context_build` | think | message, skill_id | context_manifest | 通过 |
| 4 | `tool_call` | act | - | missing_file_count, question_count | 通过，工具调用有 gate allow |
| 5 | `artifact_write` | act | - | artifact_type | 通过，返回结构化 wiki_blueprint |
| 6 | `final_answer` | answer | assistant_message_id | answer_preview, artifact_keys | 通过 |

判断：符合知识库规划 skill 的最小闭环。它没有写 memory，当前合理，因为这是规划结果，不一定应该自动沉淀为长期记忆。

### D06：多轮 skill 切换

用户行为：

1. 普通提问。
2. `/research` 生成面试展示提纲。
3. `/auto 最新新闻...` 回到自动路由。

审计结果：

- Turn 1 进入 `general_chat`，6 个 step，与 D01 一致。
- Turn 2 进入 `research_report`，8 个 step，与 D03 一致，写入 memory。
- Turn 3 进入 `recent_news_report`，7 个 step。
- 同一 session 中消息顺序为 user/assistant/user/assistant/user/assistant。
- `latest_trace` 指向第三轮，不会展示旧 trace。
- 之前发现的路由问题已修复：`/auto 最新新闻... tool policy...` 不再误进 `policy_weekly_impact`。

判断：多轮会话和 skill 切换通过。这个用例适合用来演示“Workbench 不是固定工作流页面，而是对话入口 + skill runtime”。

### D07：非法 skill 拦截

用户行为：尝试设置不存在的 `missing_skill`。

审计结果：

- `update_session` 抛出清晰 KeyError。
- 不进入 runtime。
- 不写 chat message。
- 不创建 agent run。

判断：通过。这个点很适合讲 Tool/Skill Policy 的确定性控制：不是靠 prompt 让模型别乱来，而是在 harness 层拒绝。

### F01：真实前端/API 验收

用户行为：通过 HTTP API 创建真实 UI 会话，发送调研消息，然后用浏览器渲染页面。

检查结果：

- 页面有 `Agent Workbench` shell。
- 有模型选择和模型管理。
- 有 slash command palette。
- trace 在回答上方。
- 没有旧版 `skill-dock` 和右侧 `inspector`。
- DOM 内有 `loop_phase` 和 step details 结构。
- 同一条 HTTP trace 中有 `prompt_contract=research_report.v1`、`prompt_role=research_report_subagent`。
- Playwright 展开 `model_call` 后，能看到 system prompt、visible context keys、memory count、token budget。

判断：真实前端展示通过。

## System Prompt 审计

当前最完整的 prompt 是 `research_report.v1`：

- 角色清晰：Research Report subagent inside Agent Workbench。
- 边界清晰：模型只生成结构，runtime 负责工具、memory、artifact。
- 约束清晰：不编造证据、工具结果、引用或文件路径。
- 上下文清晰：传入 visible context keys、memory 数量、token budget。
- 输出契约清晰：支持 thesis/evidence/risks/recommendations/next checks。

判断：作为毕业生项目里的 harness prompt contract，已经足够讲清楚。后续如果接真实模型，可以继续把 schema validation 和 retry repair 加到 `model_call` 后面。

## Memory 审计

当前 memory 行为：

- `general_chat`：读长期 memory，用于回答。
- `research_report`：读 memory 后生成报告，再把报告结果写入 `company:{company_id}:skill_results`。
- `D06` 验证了第二轮写入的 research memory 能被后续检索。

判断：符合“memory 由 runtime 控制读写”的设计要求。当前还不是完整个人画像系统：没有 memory 合并、过期、冲突解决、用户确认写入。面试时可以说“已有 memory store 和 skill_result 记忆闭环，画像治理是下一阶段”。

## Context 审计

每轮都有 `context_build` step，输出 `context_manifest`：

- `visible_keys`: 当前 skill 可见字段。
- `token_budget`: system/task/profile/evidence/memory/tool 预算。
- `token_estimates`: 可见字段 token 估算。
- `visible_content_checksums`: 可见内容 checksum。
- `hidden_fields`: 不暴露给该 stage 的字段。

判断：符合上下文管理的核心要求。当前 context compression 还是 deterministic manifest + token estimate，并不是复杂摘要压缩；不要把它包装成 Claude Code 级别上下文压缩。

## 当前不足

1. policy/news workflow 在 Workbench 顶层还是汇总 trace，没有把每个底层 stage/subagent 变成前端独立 step。
2. `general_chat` 当前是工具检索式 deterministic 回答，没有真实 model call。
3. memory 还缺少用户确认写入、画像合并、冲突解决和遗忘策略。
4. model gateway 仍是 scripted/local adapter，真实 OpenAI-compatible 调用还没实现。
5. 中文源码在 PowerShell/Python 某些输出中存在编码显示混乱，浏览器展示正常，但长期最好统一文件编码和终端读取方式。

## 最终验收标准

以后每次改 Workbench，至少跑：

```powershell
py -3 scripts\run_workbench_dialogue_acceptance.py
py -3 -m pytest -q
py -3 scripts\harness_linter.py
node --check src\policy_impact\app\static\workbench\app.js
py -3 -m compileall -q src scripts tests
```

并人工确认：

- 打开 `http://127.0.0.1:8501/`
- 最新会话能看到“执行过程”在回答上方。
- 展开 `model_call`，能看到 system prompt 和 prompt contract。
- 发送 `/research xxx` 后，报告路径和 memory 写入能出现在 trace。
