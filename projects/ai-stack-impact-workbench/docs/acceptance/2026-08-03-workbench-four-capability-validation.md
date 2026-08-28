# Agent Workbench 四能力验收与学习路线

生成时间：2026-08-03

## 1. 总体结论

Agent Workbench 的重点不是新闻、政策或报告本身，而是把业务 Agent 放进统一 Harness 中，使每轮运行具备：

1. 可控：Skill、Context、Tool、Permission、Stop Hook 控制模型能做什么、看什么、怎么停。
2. 可审计：AgentRun、AgentStep、ToolCall、GateDecision、Artifact 都有结构化记录。
3. 可复盘：根据 run_id 可以还原 skill 路由、上下文、工具、Gate、Memory 与最终回答。
4. 可回归：用单元测试、动态对话验收、UI CDP 检查防止行为漂移。

本次验收发现并修复了一个真实观测缺口：`gate_decision` 作为 AgentStep 持久化时缺少 `metadata.loop_phase`，导致 Trace 无法按 loop 阶段完整聚合。现在统一补为 `observe`。

## 2. 本次验证结果

已通过的验证：

- `py -3 scripts\harness_linter.py`
- `py -3 -m pytest -q`
- `py -3 scripts\run_workbench_acceptance.py`
- `py -3 scripts\run_workbench_dialogue_acceptance.py --skip-ui`
- `node --check src\policy_impact\app\static\workbench\app.js`
- `node --check scripts\run_workbench_ui_cdp_check.mjs`
- `node scripts\run_workbench_ui_cdp_check.mjs`

关键结果：

- Harness Linter：HK001-HK009 passed
- 单元测试：526 passed
- 静态/动态 Workbench 验收：passed
- 前端 CDP 验收：passed
- 当前本地页面：`http://127.0.0.1:8501/`
- 当前模型状态：未配置密钥时显示“无可用模型 / 不可用”，不会偷偷使用假模型。

最新验收产物：

- `data/acceptance/latest-workbench-acceptance.json`
- `data/acceptance/latest-workbench-dialogue-acceptance.json`
- `data/acceptance/latest_workbench_ui_cdp_check.json`
- `data/acceptance/screenshots/workbench-ui-check-20260803031047.png`

## 3. 真实模型启动方式

不要把 API Key 写进仓库，也不要写进可提交配置。需要真实调用 deepseek-v4-flash 时，在同一个 PowerShell 窗口执行：

```powershell
$env:ANTHROPIC_BASE_URL="https://api.deepseek.com/anthropic"
$env:ANTHROPIC_MODEL="deepseek-v4-flash"
$env:ANTHROPIC_AUTH_TOKEN="<redacted>"
py -3 run_policy_api.py
```

然后打开：

```text
http://127.0.0.1:8501/
```

如果没有配置 `ANTHROPIC_AUTH_TOKEN`，页面应该显示不可用，并且发送入口不能正常执行。这是正确行为。

## 4. 学习时先看哪条链路

每次测试都按这个顺序看：

```text
用户输入
-> Skill 路由
-> ContextManifest
-> Memory Read/Write
-> ToolGateway / Permission
-> Model Call / Subagent
-> Gate
-> Artifact
-> final_answer
-> run_stopped
```

注意：`final_answer` 不是终点，`run_stopped` 才是终点。因为 Stop Hook 要在候选回答发布前做最终审计。

## 5. 从简单到复杂的测试 query

### T01 普通对话

输入：

```text
请用普通对话解释 Agent Workbench 能做什么，重点说明 trace、memory、固定模型策略。
```

预期：

- `selected_skill_id = general_chat`
- 有 `memory_read`
- 有 `tool_call`
- 有 `context_build`
- 有 `model_call`
- 无 artifact
- `final_answer -> run_stopped`

学习重点：普通聊天不是裸 LLM，而是也经过 Runtime、Context、Memory、Tool 和 Stop Hook。

### T02 上下文边界

输入：

```text
/chat 请只基于当前知识库和记忆，解释这个项目和 CoursePilot 的区别；证据不足就明确说不足。
```

预期：

- `selected_skill_id = general_chat`
- 如果知识库没有 CoursePilot 事实，回答应说明证据不足
- ContextManifest 不应该把无关企业事实强塞给模型

学习重点：Context 管理不只是压缩 token，更重要的是控制“哪些证据允许进入本轮回答”。

### T03 调研报告 Skill

输入：

```text
/research 帮我整理 Agent Workbench 的设计重点，按可控、可审计、可复盘、可回归组织。
```

预期：

- `selected_skill_id = research_report`
- 有 `memory_read`
- 有 `context_build`
- 有 `model_call`
- 有 `artifact_write`
- 默认不写长期 memory

学习重点：Skill 是业务能力包，Harness 负责统一接入、上下文、产物和审计。

### T04 企业知识库规划

输入：

```text
/wiki 帮我检查企业知识库还缺哪些资料，下一步应该追问用户什么？
```

预期：

- `selected_skill_id = company_wiki_blueprint`
- 有 tool call
- 有 wiki blueprint artifact
- 回答包含缺失文件和下一步采集问题

学习重点：这类 Skill 偏确定性业务工具，仍然必须通过同一套 AgentStep 和 Artifact 记录。

### T05 政策影响 Workflow

输入：

```text
/policy 请分析近120天政策监管对 AI Agent 平台工具权限、审计留痕和企业合规的影响。
```

预期：

- `selected_skill_id = policy_weekly_impact`
- 有 `gate_decision`
- 有 `tool_call`
- 有 `workflow_stage`
- 有 `gate_check`
- 有 `artifact_write`
- `gate_decision.metadata.loop_phase = observe`

学习重点：Workflow 不等于低级。关键是每个阶段都留下 Gate、Tool、Artifact 和 loop phase。

### T06 新闻报告 Workflow

输入：

```text
/news 请生成最近新闻对 AI Agent 平台、模型产品和开发者工具方向的影响报告。
```

预期：

- `selected_skill_id = recent_news_report`
- 有数据源抓取或样例数据说明
- 有 gate 检查
- 有 Markdown/HTML/run artifact

学习重点：新闻类任务要讲清楚数据源边界、主题过滤、证据门控和不确定性。

### T07 多轮 Skill 切换

输入顺序：

```text
我先问一个普通问题：这个系统的核心运行证据有哪些？
/research 再把刚才的运行证据整理成一份面试展示提纲。
/auto 最新新闻里和 agent observability、tool policy 相关的内容有什么影响？
```

预期：

- 同一 session 中消息顺序是 user/assistant 重复
- 第一轮 `general_chat`
- 第二轮 `/research` 切到 `research_report`
- 第三轮 `/auto` 重新走自动路由
- `latest_trace` 指向最后一轮

学习重点：这是 Workbench 最像真实产品的地方，用户不是单轮调用 API，而是在一个会话里切换业务能力。

### T08 模型不可用边界

操作：

```text
不配置 ANTHROPIC_AUTH_TOKEN，打开页面并尝试发送。
```

预期：

- 页面显示“无可用模型 / 不可用”
- 不展示假模型
- 后端拒绝执行，而不是降级到本地假模型

学习重点：这是可控系统的底线。没有真实模型时宁可拒绝，也不能生成不可审计的假结果。

## 6. 面试讲法

一句话：

```text
Workbench 的核心不是政策分析本身，而是把业务 Skill 放进统一 Harness 中，让每轮 Agent 运行都有可控上下文和工具边界、可审计运行证据、可复盘 Trace/Artifact，以及可回归自动化验收。
```

更技术化一点：

```text
SkillManifest 定义业务能力的执行契约；ContextManifest 控制模型可见输入；ToolGateway 和 PermissionEngine 统一工具调用边界；AgentRun/AgentStep 记录运行证据；Gate 和 Stop Hook 控制发布；Eval/Acceptance 脚本保证后续修改不会破坏旧能力。
```

## 7. 你学习时的最小闭环

每次只选一个 query，做四件事：

1. 在页面发起请求，确认回答符合业务语义。
2. 展开“运行过程”，看每个 step 的 `loop_phase`、input、output、tool、gate。
3. 用 `run_id` 到 API/SQLite 查完整 trace，确认页面不是假展示。
4. 改一个很小的规则或 Skill 文案，跑对应测试，确认回归能抓住变化。

学完这套，你对项目的掌控感会比“通读 3000 行 agent_runtime.py”强很多。
