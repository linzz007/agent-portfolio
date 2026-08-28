# Agent Workbench 14 天学习与面试表达计划

## 目标

14 天后，你需要能把这个项目讲成：

> 一个面向垂直业务 Agent 的 mini Agent Workbench Runtime，支持普通 Agent Loop、动态 Subagent、固定 Workflow、Context/Memory 治理、Tool Policy、Trace、RunArtifact 和 HTML 报告产物。业务场景是外部新闻/技术变化影响分析，但核心价值是 Agent Harness Infra。

不是要背完整代码，而是要做到三件事：

1. 能独立演示：普通对话、`/subagent`、`/report`。
2. 能画出主链路：User Query -> Runtime -> Skill -> Context -> Tool/Gate -> Trace -> Artifact。
3. 能回答追问：为什么不是普通 Workflow，为什么比纯 Prompt/Skill 更工程化，哪些地方还不是生产级。

## 你的熟练度判断

### 相对熟练，少花时间

- 业务场景：新闻/技术变化对企业 AI 产品的影响分析。
- 基础 LLM 应用概念：RAG、Memory、Context、工具调用。
- 项目启动和页面试用。
- `/report` 这种固定流程的功能理解。

这些每天复盘即可，不要花大量时间重新学。

### 半熟，需要专项练

- Runtime 如何统一接管普通对话、Skill、Workflow。
- SkillManifest 如何定义工具权限、子智能体、执行模式。
- Report Workflow 如何从数据源、事件筛选、影响分析到 HTML 报告。
- 测试和验收怎么证明项目不是只能跑 demo。

### 不熟，重点投入

- Harness 的工程价值：可控、可审计、可复盘、可回归。
- Trace / AgentRun / AgentStep / RunArtifact 的讲法。
- ToolGateway / PermissionEngine / 逻辑沙箱。
- 动态 Subagent：planner、role contract、隔离上下文、输出 schema。
- ContextManifest 和 Memory 的边界治理。

## 每天固定动作

每天开始前 10 分钟：

- 打开页面跑一次普通对话。
- 看最近一次 Trace。
- 口头复述一句：“今天我要把哪个 Harness 能力讲清楚。”

每天结束前 20 分钟：

- 写 3 条面试表达句。
- 记录 1 个还讲不清楚的问题。
- 用自己的话解释今天看的 1 个核心文件。

## Day 1：建立项目全局地图

目标：先知道这个项目到底是什么，不陷入代码细节。

重点文件：

- `D:\AAAcode\code-code\agent+\harness\policy_impact\readme.md`
- `D:\AAAcode\code-code\agent+\harness\policy_impact\src\policy_impact\app\api.py`
- `D:\AAAcode\code-code\agent+\harness\policy_impact\src\policy_impact\app\chat_workbench_service.py`

任务：

1. 启动项目，打开页面。
2. 分别跑：普通聊天、`/subagent`、`/report`。
3. 记录每种入口的输入、输出、产物、Trace。
4. 画出第一版链路图。

当日产出：

- 一段 2 分钟项目介绍。
- 一张粗略流程图。

验收标准：

- 你能说清楚：这个项目不是新闻系统，而是 Agent Workbench。

## Day 2：学 Runtime 主入口

目标：搞懂一句用户输入进入系统后，谁接住它。

重点文件：

- `D:\AAAcode\code-code\agent+\harness\policy_impact\src\policy_impact\harness\agent_runtime.py`

任务：

1. 找到普通聊天、`/subagent`、`/report` 分别进入哪里的代码。
2. 理解 `_parse_slash_command`、skill 选择、run 创建。
3. 记录 Runtime 做了哪些事：选 skill、建 context、执行、记录 trace、返回结果。

当日产出：

- 写出 “User Query -> AgentRuntime -> Skill/Executor” 的链路。

验收标准：

- 面试官问“你的 Runtime 是什么”，你能回答：
  “Runtime 是所有 Agent 执行的统一入口，不让前端直接调用业务逻辑，而是统一处理 skill 路由、上下文、权限、trace 和 artifact。”

## Day 3：学 SkillManifest 和技能系统

目标：理解 Skill 不是一个函数，而是一个执行契约。

重点文件：

- `D:\AAAcode\code-code\agent+\harness\policy_impact\src\policy_impact\skills\manifest.py`
- `D:\AAAcode\code-code\agent+\harness\policy_impact\src\policy_impact\skills\registry.py`

任务：

1. 对比 `general_chat`、`external_impact_report`、`recent_news_report`。
2. 记录每个 skill 的 allowed_tools、allowed_subagents、execution_mode。
3. 总结为什么 SkillManifest 是 Harness 的一部分。

当日产出：

- 一张表：Skill / 执行模式 / 工具权限 / 子智能体权限 / 产物。

验收标准：

- 你能讲清楚：SkillManifest 限制 Agent 能做什么，而不是靠 prompt 祈祷模型听话。

## Day 4：学 Context 和 Memory

目标：理解模型每轮到底看到了什么、没看到什么。

重点文件：

- `D:\AAAcode\code-code\agent+\harness\policy_impact\src\policy_impact\harness\context_manifest.py`
- `D:\AAAcode\code-code\agent+\harness\policy_impact\src\policy_impact\memory\store.py`
- `D:\AAAcode\code-code\agent+\harness\policy_impact\src\policy_impact\harness\prompt_builder.py`

任务：

1. 跑一轮普通聊天，看 trace 里的 context_summary。
2. 找 visible_keys、hidden_fields、token_budget。
3. 区分短期会话历史、长期 memory、企业 wiki、artifact。

当日产出：

- 写一段 “我的 Context 治理怎么做”。

验收标准：

- 你能回答：为什么不能把所有 state 都塞给模型。

## Day 5：学 ToolGateway、Permission 和逻辑沙箱

目标：理解工具调用怎么被管住。

重点文件：

- `D:\AAAcode\code-code\agent+\harness\policy_impact\src\policy_impact\harness\tool_gateway.py`
- `D:\AAAcode\code-code\agent+\harness\policy_impact\src\policy_impact\harness\permissions.py`
- `D:\AAAcode\code-code\agent+\harness\policy_impact\src\policy_impact\runtime\task_artifacts.py`

任务：

1. 找到 ToolGateway 调用工具前记录了什么。
2. 理解 deny -> ask -> allow。
3. 理解当前沙箱是逻辑沙箱，不是 Docker/OS 沙箱。

当日产出：

- 写出“当前项目有哪些沙箱能力，缺什么生产级沙箱能力”。

验收标准：

- 你能诚实回答：
  “我实现的是工具权限和任务作用域隔离，不是容器级任意代码执行沙箱。”

## Day 6：学 Trace / AgentRun / AgentStep

目标：把“可观测性”讲成最硬的卖点。

重点文件：

- `D:\AAAcode\code-code\agent+\harness\policy_impact\src\policy_impact\memory\store.py`
- `D:\AAAcode\code-code\agent+\harness\policy_impact\src\policy_impact\app\chat_workbench_service.py`

任务：

1. 跑一次 `/report`，看 trace。
2. 找到 agent_runs、agent_steps 的写入逻辑。
3. 解释 step_type、tool_call、gate_result、artifact。

当日产出：

- 一段“坏案例复盘怎么做”的说明。

验收标准：

- 你能讲清楚：如果报告结论错了，如何沿着 run_id 追查原因。

## Day 7：学 RunArtifact 和报告产物

目标：理解产物不是页面展示，而是可复盘证据。

重点文件：

- `D:\AAAcode\code-code\agent+\harness\policy_impact\src\policy_impact\skill_executors\recent_news_report.py`
- `D:\AAAcode\code-code\agent+\harness\policy_impact\data\companies\company_001\runs`
- `D:\AAAcode\code-code\agent+\harness\policy_impact\data\companies\company_001\reports`

任务：

1. 跑 `/report`。
2. 打开 HTML、Markdown、RunArtifact。
3. 对比三者分别给用户、面试官、系统调试看什么。

当日产出：

- 一张表：HTML / Markdown / RunArtifact 的作用。

验收标准：

- 你能说清楚 Artifact 为什么是 Harness 能力。

## Day 8：学动态 Subagent

目标：掌握项目里最像先进 Agent 系统的能力之一。

重点文件：

- `D:\AAAcode\code-code\agent+\harness\policy_impact\src\policy_impact\harness\subagent_planner.py`
- `D:\AAAcode\code-code\agent+\harness\policy_impact\src\policy_impact\harness\subagents.py`
- `D:\AAAcode\code-code\agent+\harness\policy_impact\src\policy_impact\runtime\subagent_roles.py`

任务：

1. 跑 `/subagent 请从 analyst 和 skeptic 两个角度分析这个项目的 Harness 设计`。
2. 看 trace 里的 subagent_plan、subagent_delegate。
3. 理解 role contract、allowed_tools、output_schema。

当日产出：

- 一段“主 Agent 如何动态规划子智能体”的说明。

验收标准：

- 你能区分：固定 workflow stage 里的 subagent 和 query-driven 动态 subagent。

## Day 9：学 Report Workflow

目标：把 `/report` 讲成业务 workflow 嵌入 Agent Loop，而不是孤立脚本。

重点文件：

- `D:\AAAcode\code-code\agent+\harness\policy_impact\src\policy_impact\skill_executors\recent_news_report.py`
- `D:\AAAcode\code-code\agent+\harness\policy_impact\config\sources.json`

任务：

1. 理解数据源：官方源、全网新闻源、L1-L5 可信度。
2. 理解 query_constraints：时间窗口、是否全网、是否官方一手。
3. 理解报告结构：结论、摘要、影响、建议、附录。

当日产出：

- 一段 `/report` workflow 讲解。

验收标准：

- 你能回答：为什么 report 是 workflow，但仍然属于 Agent Workbench 的一部分。

## Day 10：学测试与回归

目标：能证明项目不是手动跑通一次。

重点文件：

- `D:\AAAcode\code-code\agent+\harness\policy_impact\tests\test_agent_runtime.py`
- `D:\AAAcode\code-code\agent+\harness\policy_impact\tests\test_subagent_planner.py`
- `D:\AAAcode\code-code\agent+\harness\policy_impact\tests\test_news_and_main_agent.py`
- `D:\AAAcode\code-code\agent+\harness\policy_impact\scripts\harness_linter.py`

任务：

1. 跑核心测试。
2. 看测试分别保护什么能力。
3. 记录 5 个你能在面试里说的测试点。

当日产出：

- 一段“我是怎么做回归保障的”。

验收标准：

- 你能说出至少 3 类测试：路由、subagent、新闻约束、权限、报告生成。

## Day 11：练坏案例复盘

目标：把项目讲得像工程项目，而不是玩具 demo。

任务：

1. 人为构造一个坏问题：要求只用官方源、要求最近 6 个月、要求全网新闻。
2. 看系统如何解析 query_constraints。
3. 看 trace 和 artifact 是否能解释结果。
4. 记录一个 bug 修复故事：比如“最近6个月曾被解析成30天，后来加了日历月解析和测试”。

当日产出：

- 一个 bad case 复盘故事。

验收标准：

- 你能讲清楚：发现问题 -> 定位 -> 修复 -> 加测试。

## Day 12：整理简历表达

目标：把项目压成简历上的 3 条高质量 bullet。

建议主线：

1. Agent Workbench Runtime：统一接管对话、Skill、Workflow。
2. Harness 治理与可观测：Context、ToolGateway、Permission、Trace、Artifact。
3. 动态 Subagent + 多源报告 Workflow：query-driven subagent 和可信来源报告。

任务：

1. 每条 bullet 写“做了什么 + 怎么做 + 结果/价值”。
2. 避免写 KV cache、OS 沙箱这种没实现的东西。
3. 明确写“逻辑沙箱/工具权限”，不要夸成容器沙箱。

当日产出：

- 简历项目经历第一版。

验收标准：

- 你能在 30 秒内讲完项目亮点。

## Day 13：模拟面试追问

目标：准备被拷打。

必练问题：

1. 这和 LangGraph 工作流有什么区别？
2. 这和写一个 Claude/Codex Skill 有什么区别？
3. 你的 subagent 是真的动态的吗？
4. 你的沙箱安全吗？
5. 你的 trace 有什么用？
6. 你怎么证明报告不是幻觉？
7. 哪些地方还不是生产级？

当日产出：

- 每个问题写 3-5 句话回答。

验收标准：

- 不回避缺点，但能把缺点转成后续优化方向。

## Day 14：完整答辩和彩排

目标：形成最终面试表达。

任务：

1. 现场演示 5 分钟：
   - 普通聊天
   - `/subagent`
   - `/report`
   - Trace
   - HTML 报告
2. 项目讲解 5 分钟：
   - 背景
   - 架构
   - Harness 能力
   - Subagent
   - 不足和改进
3. 追问回答 10 分钟。

当日产出：

- 最终项目讲稿。
- 最终简历 bullet。
- 3 个 bad case 故事。

验收标准：

- 你能从业务讲到 Runtime，再讲到 Harness，再讲到可观测和回归。

## 最终你要背下来的 5 句话

1. 这个项目不是单纯新闻分析，而是一个 mini Agent Workbench Runtime。
2. 我把普通对话、动态 Subagent 和固定 Report Workflow 放进同一个 Runtime，而不是让前端直接调用业务逻辑。
3. Harness 的重点是 ContextManifest、ToolGateway、PermissionEngine、AgentRun/AgentStep 和 RunArtifact。
4. Subagent 是 query-driven 的，主 Agent 根据用户问题选择 analyst、skeptic、verifier 等角色，并给它们隔离上下文和工具权限。
5. 当前项目不是生产级 Claude Code，但已经覆盖了 Agent Harness Infra 的核心骨架：可控、可审计、可复盘、可回归。

## 不要这样讲

- 不要说“我实现了业界顶级 Agent 系统”。
- 不要说“我做了完整沙箱”，当前只是逻辑沙箱和工具权限。
- 不要写 KV cache，项目没有实现。
- 不要把 `/report` 讲成主价值，主价值是 Runtime/Harness，report 是验证场景。

## 推荐最终定位

> 我做的是一个面向垂直业务 Agent 的 Workbench Runtime / Harness 样机，用外部技术情报分析作为业务场景，重点验证 Agent 执行过程中的上下文治理、工具权限、动态子智能体委派、运行追踪和报告产物可审计问题。
