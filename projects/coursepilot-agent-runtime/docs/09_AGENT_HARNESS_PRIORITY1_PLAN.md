# CoursePilot Agent Harness Priority 1 改造规划

## 1. 改造结论

第一优先级不是继续堆业务功能，而是把 CoursePilot 从“课程学习多 Agent 应用”升级成“有显式运行时、治理、轨迹和可复用能力单元的 Agent Harness Runtime”。

改造后的面试表达应从：

> 我做了一个 RAG + Memory + MCP 的课程学习 Agent。

升级为：

> 我基于课程学习场景实现了一套轻量 Agent Harness Runtime。它把一次 Agent 运行抽象为 Session，统一管理 Plan、RAG、Memory、Tool Use、Agent 执行、Trace、Artifact 和 Post Action，并通过 Skill Registry 将不同学习任务声明化，便于评测、回放和生产治理。

### 1.1 版本优化作用与必要性

这一版本优化的核心作用，是把 CoursePilot 从“能回答问题的 RAG + 多 Agent 应用”，升级成“有运行时治理能力的 Agent Harness Runtime”。它重点补上 `RunArtifact`、`SkillRegistry`、`LifecycleHooks` 和 `HarnessRuntime`，让每一次 Agent 执行都可追踪、可落盘、可复盘、可评估，而不是只看最终回答。

优化前，项目虽然已经有 Runner、RAG、Memory、MCP 工具、ContextBudgeter 和 metrics trace，但这些能力分散在业务代码里，整体更像一个课程学习助手。优化后，会显式多出一层 `core/harness/` 运行时抽象：每次请求先创建 `HarnessSession`，再执行 plan、retrieval、memory、tool use、agent answer，最后生成结构化 `RunArtifact`，同时用 `SkillRegistry` 和 hooks 约束不同任务的执行边界。

这样改造是必要的，因为生产级 Agent 的难点不只是“会不会调用大模型”，而是执行过程能不能治理。没有 artifact 和 hooks，出错后很难定位问题来自 RAG、工具、上下文裁剪、路由还是模型输出，也很难做 replay、eval、audit 和后续 sandbox / durable execution。

## 2. 当前项目已有基础

当前代码里已经有 harness 的关键骨架，只是还没有被显式命名和模块化：

| Harness 能力 | 当前代码位置 | 现状判断 |
|---|---|---|
| Runtime 入口 | `core/orchestration/runner.py` | 已有统一 Runner，但职责较重 |
| Agent 编排 | `core/agents/*` | Router/Tutor/QuizMaster/Grader 职责清晰 |
| Tool Loop | `core/llm/openai_compat.py` | 已有 ReAct 工具循环、轮次上限、降级 |
| Tool Governance | `core/orchestration/policies.py` | 已有 capability、preflight、dedup 基础 |
| MCP 工具协议 | `mcp_tools/client.py`, `mcp_tools/server_stdio.py` | 已统一走 MCP stdio |
| Context Governance | `core/orchestration/context_budgeter.py` | 已有 history/RAG/memory 分段预算 |
| Memory | `memory/store.py`, `memory/manager.py` | 已有 episodes/profile/concept_mastery |
| Observability | `core/metrics/collector.py`, `scripts/perf/*` | 已有 trace event 和 benchmark |

当前问题是：这些能力分散在业务代码里，面试官需要听你解释很久才能意识到它不是普通 RAG demo。因此第一阶段目标是“显式化”和“收口”，不是重写主链路。

## 3. P1 目标与非目标

### 目标

1. 新增 `core/harness/` 模块，把 Session、Artifact、Hook、Skill、Runtime 显式建模。
2. 不破坏现有 `/chat` 和 `/chat/stream` 行为，优先采用 wrapper/adapter 接入 Runner。
3. 每次运行产出结构化 `RunArtifact`，包含 plan、检索、上下文预算、工具调用、最终输出、错误和耗时。
4. 引入最小 `SkillRegistry`，让 learn/practice/exam 的执行策略可声明、可查询、可面试讲述。
5. 建立第一版生命周期 hooks，为后续 approval gate、eval、replay、audit 留接口。

### 非目标

1. 不在 P1 里重构所有 Agent prompt。
2. 不在 P1 里接入 LangGraph 或 OpenAI Agents SDK，避免把项目核心变成框架包装。
3. 不在 P1 里做完整分布式任务调度。
4. 不在 P1 里做复杂权限系统，只预留治理点。

## 4. 目标目录结构

新增目录：

```text
core/harness/
  __init__.py
  session.py        # HarnessSession / RunStatus
  artifact.py       # RunArtifact / ArtifactStore
  hooks.py          # HookEvent / LifecycleHooks
  skills.py         # SkillSpec / SkillRegistry
  runtime.py        # HarnessRuntime: 包装 OrchestrationRunner
```

可选新增测试：

```text
tests/test_harness_session.py
tests/test_harness_artifact.py
tests/test_harness_hooks.py
tests/test_harness_skills.py
```

## 5. 核心对象设计

### 5.1 HarnessSession

作用：给每次运行一个稳定身份，记录这次运行的上下文和生命周期状态。

建议字段：

```python
@dataclass
class HarnessSession:
    run_id: str
    course_name: str
    mode: str
    skill_id: str
    user_message: str
    started_at: str
    ended_at: str | None = None
    status: str = "running"
    trace_id: str | None = None
    request_id: str | None = None
```

面试价值：说明你不是“调一次接口拿答案”，而是把 Agent 执行变成可追踪的运行实例。

### 5.2 RunArtifact

作用：把一次运行产生的关键证据全部落盘，支撑 debug、eval 和 replay。

建议字段：

```python
@dataclass
class RunArtifact:
    run_id: str
    session: dict
    plan: dict | None
    retrieval: list[dict]
    context_budget: dict | None
    tool_calls: list[dict]
    output: dict | None
    metrics: dict
    error: dict | None
```

建议落盘路径：

```text
data/runs/YYYY-MM-DD/<run_id>.json
```

P1 只要求 JSON 落盘；后续 P2 再做索引、查询页面和 replay UI。

### 5.3 LifecycleHooks

作用：在固定执行阶段插入治理逻辑，避免 Runner 继续膨胀。

第一版 hook 点：

| Hook | 触发时机 | P1 用途 | 后续用途 |
|---|---|---|---|
| `before_plan` | Router 前 | 记录输入 | 输入安全检查 |
| `after_plan` | Router 后 | 记录 plan | plan grader |
| `before_retrieve` | RAG 前 | 记录 query | query rewrite |
| `after_retrieve` | RAG 后 | 记录 citations | recall eval |
| `before_agent` | Agent 执行前 | 记录 agent/mode | approval gate |
| `after_agent` | Agent 输出后 | 记录输出摘要 | output guardrail |
| `before_tool` | 工具调用前 | 记录参数 | tool permission |
| `after_tool` | 工具调用后 | 记录结果 | tool eval |
| `on_error` | 异常时 | 记录错误 | fallback/retry |
| `after_answer` | 最终输出后 | 写 artifact | post action |

P1 不要求所有 hook 都深度接入；至少要让 Runtime 能按顺序触发并记录事件。

### 5.4 SkillSpec 与 SkillRegistry

作用：把不同模式下的能力从 if/else 和 prompt 中抽出来，形成声明式策略。

第一版 SkillSpec 字段：

```python
@dataclass
class SkillSpec:
    skill_id: str
    mode: str
    agent: str
    description: str
    allowed_tools: list[str]
    context_policy: dict
    post_actions: list[str]
```

默认注册：

| skill_id | mode | agent | 说明 |
|---|---|---|---|
| `learn.answer.v1` | learn | Tutor | 教材讲解 |
| `learn.mindmap.v1` | learn | Tutor | 讲解 + 思维导图 |
| `practice.quiz.v1` | practice | QuizMaster | 单题练习 |
| `practice.paper.v1` | practice | QuizMaster | 多题练习 |
| `practice.grade.v1` | practice | Grader | 练习评分 |
| `exam.paper.v1` | exam | QuizMaster | 模拟考试 |
| `exam.grade.v1` | exam | Grader | 考试批改 |

面试价值：说明你理解大厂 Agent 岗位里的 Skills 不是单句 prompt，而是“工具、上下文、后处理、执行约束”的能力单元。

### 5.5 HarnessRuntime

作用：作为 Runner 外层包装，不直接替代现有 Runner。

第一版职责：

1. 创建 `HarnessSession`。
2. 根据 mode/user_message 解析 `skill_id`。
3. 进入 `trace_scope`。
4. 调用现有 `OrchestrationRunner.run()` 或 `run_stream()`。
5. 从 trace events、response、citations、tool_calls 中组装 `RunArtifact`。
6. 触发 hooks 并落盘。

建议先做非流式链路，再扩展流式链路。流式链路需要注意 generator 生命周期，不要因为 artifact 写入导致 SSE 卡死。

## 6. 接入策略

### 6.1 保持现有 API 不破坏

现有后端接口先不变：

```text
/chat
/chat/stream
```

P1 可以新增可选环境变量：

```text
ENABLE_HARNESS_RUNTIME=1
HARNESS_ARTIFACT_DIR=./data/runs
```

后端接入方式：

```python
if ENABLE_HARNESS_RUNTIME:
    runtime = HarnessRuntime(runner)
    response = runtime.run(...)
else:
    response = runner.run(...)
```

### 6.2 Artifact 数据来源

优先从已有信息收集，不重复调用模型：

| 信息 | 来源 |
|---|---|
| session | HarnessRuntime 创建 |
| plan | Runner/Router 返回值，必要时在 Runner meta 中暴露 |
| retrieval | response.citations + trace retrieval event |
| context_budget | `__context_budget__` 事件或 trace event |
| tool_calls | response.tool_calls + trace tool events |
| metrics | `core.metrics` active trace |
| output | ChatMessage |
| error | exception 捕获 |

## 7. 分阶段实现

### Day 1: 建 Harness 数据结构

产出：

1. `core/harness/session.py`
2. `core/harness/artifact.py`
3. 对应单测

验收：

1. 能创建 session。
2. 能写入一份最小 artifact JSON。
3. JSON 可被重新读取。

### Day 2: 建 SkillRegistry

产出：

1. `core/harness/skills.py`
2. 默认 skills 注册表
3. 根据 mode 和用户动作解析 skill_id

验收：

1. `learn` 默认解析到 `learn.answer.v1`。
2. `practice` 出题解析到 `practice.quiz.v1` 或 `practice.paper.v1`。
3. 提交答案解析到 `practice.grade.v1` 或 `exam.grade.v1`。

### Day 3: 建 LifecycleHooks

产出：

1. `core/harness/hooks.py`
2. 内置 `ArtifactHook`
3. 内置 `MetricsHook`

验收：

1. hook 顺序可测试。
2. hook 异常不拖垮主流程。
3. on_error 能记录错误。

### Day 4: 接入 HarnessRuntime 非流式链路

产出：

1. `core/harness/runtime.py`
2. 后端 `/chat` 可通过环境变量切换 harness runtime

验收：

1. 原有非流式行为不变。
2. 每次 `/chat` 生成 artifact。
3. 失败请求也生成 artifact，并记录 error。

### Day 5: 接入流式链路与文档

产出：

1. `/chat/stream` 支持 artifact 收集。
2. 更新 `docs/ARCHITECTURE.md` 或 README 的 harness 说明。
3. 新增最小 replay CLI 设计草案。

验收：

1. 流式输出不卡死。
2. Artifact 最终包含完整输出。
3. 原有 tests 通过。

## 8. 验收标准

P1 完成后，至少满足：

1. `pytest` 或现有 unittest 测试通过。
2. 一次 learn 请求会生成一份 artifact。
3. artifact 中能看到 run_id、mode、skill_id、retrieval、output、metrics。
4. 工具调用失败时，artifact 中能看到 tool error 或 on_error 记录。
5. 不重复执行有副作用工具，例如 filewriter、memory 写入。
6. README 或 docs 能清楚说明 CoursePilot Harness Runtime 架构。

## 9. 面试讲法

可以这样讲：

> 最开始项目只是一个课程学习 Agent，后来我发现单纯做 RAG 和多 Agent 不够，线上真正难的是运行过程不可控、不可复盘、不可评测。所以我把主链路抽象成轻量 Agent Harness：每次请求都会创建 HarnessSession，经过 Router plan、RAG、Memory、Tool Use、Agent 执行和 Post Action，最后生成 RunArtifact。这样我可以基于 artifact 做 debug、benchmark、replay 和后续 guardrail。这个改造的重点不是换框架，而是把模型外的执行治理层显式化。

如果面试官追问“为什么不直接用 LangGraph/OpenAI Agents SDK”，回答：

> 我没有直接替换框架，是因为当前项目已有稳定 Runner、MCP、Memory、ContextBudgeter 和 SSE 链路。第一阶段更合理的是做轻量 runtime 抽象，把现有能力收口，同时保留业务稳定性。等 hooks、artifact、skill registry 稳定后，再评估是否迁移到 LangGraph checkpoint 或接入标准 Agents SDK。

## 10. 后续 P2/P3 方向

P1 完成后，后续优先级：

1. P2: Replay + Eval，把 artifact 转成可重复评测数据。
2. P2: Tool approval gate，对 filewriter、websearch、外部 API 做风险分级。
3. P2: RAG faithfulness eval，引入 LLM-as-judge 和引用一致性检查。
4. P3: Sandbox，把高风险工具执行隔离到受控工作区。
5. P3: Durable execution，引入 checkpoint/resume，支持长任务恢复。

## 11. 网站查询依据

查询日期：2026-05-06。

这一版 P1 规划不是只按项目内部想象推出来的，而是对齐了 2026 年 Agent 实习岗位和官方 Agent 工程趋势。结论是：大厂和头部机构现在看重的不是“会不会调用大模型 API”，而是能否把 Agent 做成可治理、可评测、可回放、可上线的系统。

### 11.1 岗位侧信号

| 来源 | 网站信息 | 对 CoursePilot 的启发 |
|---|---|---|
| [上海人工智能实验室：大模型算法实习生（Agent基础能力方向）](https://www.shlab.org.cn/joinus/detail/75943100<redacted-phone>?jobFunction=&jobType=&keyword=&location=&mode=campus&subject=) | 岗位职责覆盖多轮交互、Function Calling、Agent 框架下的决策推理、SFT/Agentic RL 数据构建、工具调用、Agent 评测体系。 | 说明面试会从应用追到模型训练、工具调用稳定性和评测闭环；CoursePilot 需要把 `benchmark/trace/artifact` 做成一等能力。 |
| [美团：Agent开发实习生（AI 产品方向）](https://bebee.com/cn/jobs/agentai--techmap_cn_81303173) | 岗位直接写到 Prompt、Chain、Harness、Skills、任务自动化、上下文管理、高并发、SQL。 | 说明 `Harness + Skills + Context` 已经是岗位关键词；P1 要显式新增 `SkillRegistry` 和 `HarnessRuntime`，避免只讲“多 Agent”。 |
| [牛客：AI Agent 应用开发工程师（实习）](https://www.nowcoder.com/jobs/detail/438217?urlSource=sitemap) | 岗位强调 Harness 架构设计与部署、SKILL 工程、EVAL 评估、多 Agent、RAG、LoRA/DPO、vLLM/TensorRT-LLM、MLOps 和 OpenTelemetry/Prometheus。 | 说明项目简历要从功能描述升级到工程闭环：运行轨迹、评测、可观测、推理性能和部署治理都要有路线。 |

### 11.2 官方技术趋势

| 来源 | 网站信息 | 对 P1 设计的影响 |
|---|---|---|
| [OpenAI：The next evolution of the Agents SDK](https://openai.com/index/the-next-evolution-of-the-agents-sdk/) | OpenAI 在 2026-04-15 发布的 Agents SDK 更新中强调 model-native harness、controlled workspace、sandbox execution、configurable memory、sandbox-aware orchestration，以及把 harness 和 compute 分离以获得安全、耐久和扩展性。 | P1 不应只做 Router 重构，而要围绕 Session、Artifact、Hooks、Runtime 建一层模型外运行时；P2/P3 再做 sandbox 和 durable execution。 |
| [Model Context Protocol：Tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools) | MCP Tools 规范说明工具可暴露数据库/API/计算等外部系统，工具由模型发现和调用；同时建议对工具暴露、调用提示和高风险操作保留 human-in-the-loop，并支持 structured output schema。 | CoursePilot 已有 MCP stdio，下一步应该补 tool approval gate、output schema 校验、tool artifact 记录，而不是把工具继续散落在 Agent 内部。 |
| [LangGraph：Persistence](https://docs.langchain.com/oss/python/langgraph/persistence) | LangGraph persistence 通过 checkpoint 保存图状态，支持 human-in-the-loop、conversation memory、time travel debugging、fault-tolerant execution 和 replay。 | 证明 `RunArtifact + Replay` 是 Agent 工程必需能力；P1 先落 artifact，P2 再做 replay/checkpoint。 |
| [OpenAI：Evaluate agent workflows](https://developers.openai.com/api/docs/guides/agent-evals) | OpenAI Agent Evals 建议从 traces 开始调试 agent 行为，再用 datasets/eval runs 做可重复评测；trace 应覆盖模型调用、工具调用、guardrails、handoffs。 | CoursePilot 的 `core.metrics` 和 `scripts/perf` 应升级为正式 Eval 体系，指标要覆盖工具选择、路由、约束遵守、RAG 和最终输出。 |

### 11.3 对 P1 优先级的修正

结合网站查询后，P1 的优先级顺序应更明确：

1. 先做 `RunArtifact`，因为岗位和官方文档都把 eval/trace/replay 作为生产 Agent 的核心能力。
2. 再做 `SkillRegistry`，因为岗位已经明确出现 Skills/Skill 工程，且这能直接改善简历表达。
3. 再做 `LifecycleHooks`，因为 hooks 是后续 approval gate、guardrail、eval、post action 的插入点。
4. `Sandbox` 不放在 P1 正式实现，但必须在文档和代码接口里预留，因为 OpenAI Agents SDK 已经把 sandbox-aware orchestration 作为新趋势。
5. `Durable execution` 不在 P1 完成，但 artifact 的字段设计必须能支撑后续 replay/checkpoint。
