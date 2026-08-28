# CoursePilot 中国大厂 Agent 工程化升级路线图

> 版本日期：2026-05-11  
> 适用目标：用 CoursePilot 作为秋招/暑期实习核心项目，冲刺中国大厂 AI 应用工程师、Agent 工程师、大模型应用开发、AI 后端研发相关岗位。  
> 本版原则：不再以 Claude Code、OpenClaw、Codex 等国外架构作为主要论证来源；本版只用中国公开岗位、中文面经、国内企业产品文档和中文社区资料来判断“现在中国大厂到底看重什么”。

## 0. 先说结论

CoursePilot 不应该新开一个“小 Claude Code”项目，也不应该把简历写成“复刻国外顶级 Agent 架构”。最优路线是：继续把当前 CoursePilot 升级成一个面向课程学习场景的轻量 Agent 工程化系统。

推荐定位：

```text
CoursePilot 是一个面向课程学习场景的轻量 Agent Runtime：
以 Runner 编排学习、练习、考试流程；
以 Hybrid RAG 和 Memory 提供知识与个性化上下文；
以 MCP Tool 和 ToolPolicy 管理外部工具调用；
以 RunArtifact、Trace、Eval、Replay 证明系统可观测、可复盘、可优化。
```

证据：牛客 AI Agent 实习岗位明确出现 Harness、SKILL、EVAL、多 Agent、RAG、MLOps、OpenTelemetry、Prometheus 等关键词；阿里云 AgentLoop 官方文档把 Trace、Log、Metric、Conversation、评估、实验、长期记忆定义为 AI Agent 闭环；上海 AI Lab Agent 基础能力实习岗位要求 Function Calling、Agent 决策推理、SFT/Agentic RL 数据构建和评测体系设计。

你当前最应该做的不是“堆名词”，而是把项目从：

```text
RAG + 多 Agent 课程助手
```

升级为：

```text
可观测、可评测、可治理、可回放的课程学习 Agent 工程化系统
```

证据：牛客大模型应用开发面经明确提到“项目效果评估很重要，技术多炫酷最终还是看落地效果”；阿里云 AgentLoop 也强调把运行时数据转成可持续优化的数据飞轮。

## 1. 中文资料来源与可信度

### 1.1 强证据：官方岗位与企业产品文档

| 来源 | 观察到的信息 | 对 CoursePilot 的判断 |
|---|---|---|
| [牛客 AI Agent 应用开发工程师实习岗位](https://www.nowcoder.com/jobs/detail/438215?urlSource=sitemap) | 岗位写到 Harness 架构设计与部署、Prompt Engineering、SKILL 工程、EVAL 评估体系、多 Agent、RAG、LoRA、vLLM、TensorRT-LLM、MLOps、OpenTelemetry、Prometheus。 | “Harness / Skill / Eval”可以写，但必须落地成可运行、可观测、可评测的能力，不能只写概念。 |
| [上海 AI Lab 大模型算法实习生（Agent 基础能力方向）](https://www.shlab.org.cn/joinus/detail/75943100<redacted-phone>?jobFunction=&jobType=&keyword=&location=&mode=campus&subject=) | 岗位要求多轮交互、Function Calling、Agent 框架决策推理、SFT/Agentic RL 数据构建、工具调用、评测任务与指标。 | 中国头部 AI 机构看重工具调用稳定性、训练数据意识、评测体系和工程实现。 |
| [阿里云 AgentLoop](https://www.alibabacloud.com/help/zh/cms/cloudmonitor-2-0/what-is-agentloop) | 官方文档把 Trace、Log、Metric、Conversation、评估、实验、Prompt/版本管理、长期记忆描述为 AI Agent 闭环。 | CoursePilot 应补 RunArtifact、Eval、Replay、Trace、Memory Governance，而不是只展示页面。 |
| [阿里云 AgentLoop 对话质量评估模板](https://www.alibabacloud.com/help/zh/cms/cloudmonitor-2-0/dialogue-quality-assessment) | 评估覆盖需求理解、回答质量、逻辑连贯、格式规范、安全合规，并要求结构化评分和理由。 | CoursePilot 的 Eval 需要覆盖回答质量、忠实度、格式、安全、工具调用，而不只是延迟和 token。 |

### 1.2 中强证据：牛客面经和岗位讨论

| 来源 | 高频问题 | 对 CoursePilot 的判断 |
|---|---|---|
| [牛客大模型应用开发面经](https://www.nowcoder.com/feed/main/detail/129eaa1c20444651ac3b932e200d3da4) | RAG 难点、文档切割、多路召回、向量库、幻觉、效果量化、MCP 与 Function Call、ReAct、长短期记忆、延迟优化、高可用。 | 你的项目必须能讲清 RAG、工具调用、记忆、评测、延迟和高可用，不能只讲“用了大模型”。 |
| [牛客 Agent 开发面经总结](https://www.nowcoder.com/discuss/877<redacted-phone>7968) | 阿里/蚂蚁/字节相关面经围绕 Agent 架构、多 Agent、RAG、混合检索、MCP、Function Calling、API、数据库。 | CoursePilot 的 Router/Tutor/Quiz/Grader 适合讲“中心编排式多 Agent”，但需要补工具治理和质量保障。 |
| [阿里淘天 AI Agent 应用开发二面](https://www.nowcoder.com/feed/main/detail/fac28b92963047aca5d6dafae55d1a3d) | Function Call 完整流程、Workflow 与自主 Agent 如何选择、长短期记忆、上下文压缩、RAG 如何避免无关知识干扰、Rerank、多 Agent 路由。 | CoursePilot 应主打“Workflow 约束下的可控 Agent”，而不是完全自主的 Agent。 |
| [阿里云 AI 应用研发一面](https://www.nowcoder.com/feed/main/detail/c0e228255bc84c9ab3cbb72b59caad59?sourceSSR=%E5%85%B6%E4%BB%96) | Agent 循环、状态和上下文、死循环处理、工具注册发现、RAG 切分/向量/距离度量、SSE 稳定性、Redis/Token 会话。 | AI 应用岗仍然会问后端、缓存、会话、稳定性；CoursePilot 要补工程化表达。 |
| [腾讯 RAG/Agent 面经整理](https://www.nowcoder.com/discuss/878945851924627456) | Text2SQL、Embedding、RAG、Rerank、Query Rewrite、Agent、MCP、向量检索。 | RAG 深度仍然是中国大厂最硬的项目主线。 |
| [美团 Agent 方向面经整理](https://www.nowcoder.com/discuss/881209<redacted-phone>0) | 项目主线通常按 Agent、LangGraph、上下文、工具、评测追问，RAG 和训练会顺着简历挖到底。 | 你的准备方式应按模块拆，不要只背一段项目介绍。 |
| [百度 Agent 面经](https://www.nowcoder.com/discuss/88084<redacted-phone>88?sourceSSR=post) | 追问项目讲法、LangGraph、记忆、Agent 形态、工具协议、RAG、后端基础。 | 简历写 Agent 会被从架构问到后端基础，不能只准备 AI 名词。 |

### 1.3 辅助证据：中文社区与学习资料

| 来源 | 可参考内容 | 使用方式 |
|---|---|---|
| [CSDN 100 道大模型应用开发面试题](https://blog.csdn.net/teddyandwolf/article/details/148159001) | 覆盖 Agent、Memory、Tool Use、Multi-Agent、Evaluation、RAG、部署、微调、Transformer。 | 作为八股和知识面清单，不作为岗位趋势的唯一证据。 |
| [知乎：如何系统性学习 RAG、Agent、MCP](https://www.zhihu.com/question/<redacted-phone>94623025/answer/20<redacted-phone>363292) | 反映中文社区把 RAG、Agent、MCP 作为一条学习路线。 | 用来辅助学习路线排序，不作为企业招聘强证据。 |
| [牛客：Agent 岗位爆发，后端开发要不要转](https://www.nowcoder.com/discuss/1629849) | 讨论后端能力如何迁移到 Agent：API、鉴权、异步、重试、降级、限流、MySQL/Redis、MCP、AgentDevOps。 | 说明后端基础不是过时能力，而是 Agent 工程落地的底盘。 |

证据分级结论：官方岗位和企业产品文档最可信；牛客面经适合判断高频问题；CSDN/知乎适合补知识清单，但不能单独支撑简历卖点。

## 2. 中国大厂当前真正需要什么

### 2.0 前沿架构和中国大厂需求不冲突

观点：Claude Code、OpenClaw、Codex 这类前沿 Agent 系统当然有参考价值，中国大厂也会喜欢候选人知道这些方向；真正的风险不是“提到国外架构”，而是“只提名字但没有落地机制”。

证据：牛客 AI Agent 实习岗位已经出现 Harness、SKILL、EVAL、MLOps、OpenTelemetry、Prometheus 等与前沿 Agent 工程高度一致的关键词；阿里云 AgentLoop 也把 Trace、Metric、Conversation、Eval、长期记忆、数据飞轮作为企业 Agent 产品能力，这说明国内企业确实在吸收前沿 Agent 工程思想。

正确写法：

```text
参考 Claude Code / OpenClaw 等前沿 Agent 工程思想，
在 CoursePilot 中落地运行证据、工具治理、上下文治理、记忆治理和评测闭环。
```

不推荐写法：

```text
复刻 Claude Code / OpenClaw
实现同款 Agent Harness
实现完整通用 Agent 平台
```

证据：中文面经会继续追问 RAG 指标、工具注册、SSE 稳定性、Agent 死循环、记忆污染、项目效果量化；如果只写“用了 Claude Code 架构”，但讲不清这些细节，反而会暴露项目深度不足。

落到 CoursePilot 的平衡策略：

```text
国外前沿架构负责提供“设计灵感”：
  - Session / Artifact
  - Tool Governance
  - Skill Registry
  - Context Governance
  - Memory Governance
  - Eval / Replay

中国大厂资料负责决定“优先级”：
  - RAG 指标先做
  - 工具调用治理先做
  - 评测闭环先做
  - 后端稳定性先做
  - 模型基础和工程八股补齐
```

证据：国内岗位和面经已经覆盖 Harness、SKILL、EVAL、RAG、MCP、SSE、Redis、Function Calling、记忆和评测；因此前沿架构不是被排除，而是要翻译成中国面试官能追问、能验证、能落地的项目能力。

### 2.1 需要能落地的 RAG，不是只会调向量库

观点：RAG 仍然是 AI 应用工程师最核心的面试主线，尤其是文档解析、chunk、embedding、混合检索、rerank、query rewrite、召回率、幻觉控制和评测。

证据：牛客大模型应用开发面经列出文档切割、多路召回、向量数据库、幻觉、检索效果量化；腾讯面经整理强调 Embedding、RAG、Rerank、Query Rewrite、向量检索。

CoursePilot 对应改造：

```text
rag/retrieve.py
  -> 增加 query rewrite 可选链路
  -> 增加 rerank 或 mock rerank 接口
  -> 增加 hit@k / empty_rate / faithfulness 评测
  -> 将检索结果、文档名、页码、score 写入 RunArtifact
```

### 2.2 需要可控 Agent，不是完全自主 Agent

观点：中国大厂面试更喜欢你能讲清什么时候用 Workflow，什么时候用 Agent，什么时候必须加人工审批，而不是宣称“全自动智能体”。

证据：阿里淘天面经直接问固定 Workflow 与自主 Agent 如何选择；阿里云面经问 Agent 死循环如何处理、工具如何注册发现、新增工具需要改什么。

CoursePilot 对应改造：

```text
OrchestrationRunner
  -> 保持 learn/practice/exam 的 Workflow 约束
  -> 给每个模式定义最大 replan 次数
  -> 记录 replan_reason
  -> 不把系统讲成不可控的 autonomous swarm
```

### 2.3 需要工具调用治理，不是只会 Function Calling

观点：Function Calling 只是入口，真正的大厂工程问题是工具注册、参数校验、失败重试、权限、审计、降级和安全。

证据：上海 AI Lab 岗位要求 Function Calling 与 Agent 框架决策推理；牛客面经追问工具注册发现、新增工具改动、工具调用与质量保障。

CoursePilot 对应改造：

```text
core/orchestration/policies.py
  -> ToolRiskLevel: safe/read/external/write
  -> ToolDecision: allow/deny/ask/defer
  -> ApprovalMode: off/log/strict
  -> tool_call 写入 artifact
```

### 2.4 需要评测闭环，不是主观 demo

观点：未来中国大厂会越来越看重“你怎么证明 Agent 变好了”，这比“页面看起来能用”更重要。

证据：阿里云 AgentLoop 明确把评估体系描述为把模糊语义感受转成统计指标，并支持回归测试、A/B 对比和 LLM-as-Judge；上海 AI Lab 岗位要求设计 Agent 能力相关评测任务和指标。

CoursePilot 对应改造：

```text
scripts/eval_runs.py
  -> retrieval_empty_rate
  -> tool_failure_rate
  -> context_truncated_rate
  -> replan_rate
  -> answer_faithfulness_score
  -> p50/p95 latency
```

### 2.5 需要记忆治理，不是简单保存聊天记录

观点：记忆系统要能说明“写什么、什么时候写、怎么检索、怎么遗忘、怎么避免错误记忆污染”。

证据：阿里淘天面经追问长短期记忆、上下文压缩、选择性遗忘；阿里云 AgentLoop 把 Facts、Episodic、Summary、自定义策略列为记忆策略。

CoursePilot 对应改造：

```text
memory/store.py
  -> qa/practice/mistake/exam episode 保留
  -> 增加 memory_write_policy
  -> 失败 run 不写长期 memory
  -> 低置信内容不更新 weak_points
  -> 保存 memory_hit 到 artifact
```

### 2.6 需要后端工程能力，不是只会 AI 框架

观点：AI 应用岗位仍然会问 Redis、会话、SSE、分布式锁、缓存、接口稳定性、服务降级和高并发。

证据：阿里云 AI 应用研发面经问 SSE 稳定性、Redis + Token 分布式会话、缓存穿透/击穿/雪崩、Redis 分布式锁；牛客后端转 Agent 讨论强调 API、鉴权、异步、重试、降级、限流、MySQL/Redis 是后端转 Agent 的优势。

CoursePilot 对应改造：

```text
backend/api.py
  -> 流式输出错误事件规范化
  -> run_id 贯穿请求
  -> 记录 SSE start/end/error
  -> 准备 Redis session/cache 作为后续扩展答案
```

### 2.7 需要模型基础，但不要求你伪装成训练岗

观点：AI 应用工程师不一定要从零训练大模型，但必须懂 Transformer、Embedding、SFT、RLHF/DPO/LoRA、推理成本、模型选择和微调/RAG取舍。

证据：牛客大模型应用开发面经包含 Transformer、Function Call 如何训练、微调方案、Embedding、模型部署；上海 AI Lab 岗位要求 SFT、RLHF、Agentic RL、Function Calling 训练数据。

CoursePilot 对应策略：

```text
简历主线写应用工程；
面试准备补模型基础；
不要写自己做了 SFT/DPO/RL，除非真的有实验。
```

## 3. CoursePilot 当前能力映射

| 大厂关注点 | CoursePilot 当前基础 | 可写程度 | 下一步 |
|---|---|---|---|
| RAG | `rag/retrieve.py` 有 Dense/BM25/Hybrid/RRF 基础 | 可以写 | 补 hit@k、empty_rate、rerank、query rewrite |
| Agent 编排 | `core/orchestration/runner.py` 编排 Router/Tutor/Quiz/Grader | 可以写 | 补 plan/replan 状态机和 run artifact |
| 工具调用 | MCP 工具层和 `ToolPolicy` 已有基础 | 可以写 | 补风险分级、审批、审计和失败降级 |
| 记忆系统 | `memory/store.py` 有 episode/profile | 可以写 | 补写入策略、污染防护、遗忘策略 |
| 上下文治理 | `ContextBudgeter` 有 history/RAG/memory 预算 | 可以写 | 补 context_truncated_rate 和裁剪原因 |
| 可观测 | `core/metrics/collector.py` 有 trace/perf 基础 | 可以写 | 补 RunArtifact、Replay、Eval |
| SSE/后端 | `backend/api.py` 有接口和流式输出 | 可以写基础能力 | 补断连、错误事件、run_id、后端稳定性答案 |
| Skill | 目前更多是规划，不是完整实现 | 暂不写“已实现” | 做 `SkillSpec` 后再写 |
| Sandbox | 目前不是完整 OS sandbox | 不建议写已实现 | 先做工具级 sandbox 和 approval |
| Agentic RL | 当前没有训练链路 | 不写已实现 | 只说 RunArtifact 可沉淀后续训练数据 |

证据：牛客和阿里云面经对项目深挖通常按“项目是什么、RAG 怎么做、工具怎么调、状态怎么管、效果怎么量化、线上怎么稳”展开；上表正好对应这些追问。

## 4. 是否新开项目：不建议

### 4.1 不建议新开“小 Claude Code”

观点：秋招前新开一个小 Claude Code 风险高，容易变成浅层 demo；继续优化 CoursePilot 更容易形成完整闭环。

证据：中文岗位和面经高频关注 RAG、Function Calling、MCP、评测、后端稳定性、项目落地，而不是要求候选人复刻某个国外 coding agent。

更合理的表达：

```text
不是复刻通用 Coding Agent，
而是在课程学习场景下落地 Agent 工程化能力。
```

### 4.2 可以做一个极小 side project，但不能抢主线

观点：如果你特别想展示 AI Coding/Agent Harness 方向，可以做一个 2-3 天的小实验，但它只能作为补充，不应该替代 CoursePilot。

证据：牛客面经里项目深挖会围绕你简历最主要项目展开；如果主项目不扎实，多一个浅 demo 只会增加被追问的风险。

建议的小实验范围：

```text
mini-agent-devops-demo
  -> 输入一个任务
  -> 生成 plan
  -> 调用 1-2 个本地工具
  -> 记录 trace
  -> 生成 eval report
```

不要写：

```text
复刻 Claude Code
实现通用 Agent 平台
实现完整 sandbox
```

证据：这些说法会引出权限模型、文件系统隔离、session resume、工具审批、并发执行、评测体系等深追问，当前短期很难全部补齐。

## 5. 从 0 开始的改造优先级

> 状态口径：  
> 代码层面基于当前工作区代码核对，包括已修改/未跟踪但实际存在的 `core/orchestration/runner.py`、`rag/retrieve.py`、`memory/store.py`、`core/orchestration/policies.py`、`backend/api.py`、`core/harness/*`、`scripts/perf/*` 等文件。  
> 简历层面基于 `D:/AAAcode/研究生简历/lzz简历4.0.pdf` 中 CoursePilot 项目描述核对。  
> `部分完成` 表示已有核心基础，但还没有达到本节建议的完整工程化目标。

### P0：统一项目故事

> 代码层面：基本完成（Runner、RAG、Memory、MCP、ContextBudgeter、trace/perf、HarnessRuntime 基础都已存在）；简历层面：已提到（简历已写“学习-练习-考试闭环”“多智能体学习系统”，但还可以升级成“课程学习 Agent Runtime”表述）。

目标：把项目一句话讲清楚。

推荐说法：

```text
CoursePilot 是面向课程学习场景的轻量 Agent Runtime，
通过 Runner 编排 RAG、Memory、MCP Tool 和教学角色，
支持学习问答、练习生成、批改反馈和个性化复习。
```

证据：牛客面经强调项目深挖和落地效果；如果开场讲不清，后面的 RAG/MCP/Memory 都会显得像堆技术。

### P1：RunArtifact 运行证据链

> 代码层面：已完成基础版（`core/harness/artifact.py`、`session.py`、`runtime.py` 已实现，`backend/api.py` 已通过 `ENABLE_HARNESS_RUNTIME` 接入，harness 相关 unittest 已通过）；简历层面：部分提到（简历写了 benchmark、状态事件、可观测性，但没有明确写 `RunArtifact / Replay / Eval`）。

目标：每次请求生成一份结构化运行记录。

最小字段：

```text
run_id
mode
user_query
plan
retrieval
context_budget
tool_calls
memory_hits
output
error
metrics
```

代码落点：

```text
core/harness/artifact.py
core/harness/runtime.py
backend/api.py
core/metrics/collector.py
```

证据：阿里云 AgentLoop 将 Trace、Log、Metric、Conversation 作为生产 Agent 闭环；牛客面经也会问怎么排查失败和怎么量化效果。

### P2：RAG 评测闭环

> 代码层面：已完成基础评测闭环（Dense/BM25/Hybrid/RRF、引用、句级压缩已实现；`benchmarks/cases_v1.jsonl`、`benchmarks/rag_gold_v1.jsonl` 和 `scripts/perf/bench_runner.py` 已支持 `hit_at_k`、`top1_acc`、`precision_at_k`、retrieval latency 等 RAG 指标）；简历层面：部分提到（简历已写课程资料解析、切块、向量建库、Hybrid Retrieval、引用证据，但没写 RAG benchmark 和指标）。仍缺：answer faithfulness、context relevance、LLM-as-Judge 等生成质量评测。

目标：不要只说“用了混合检索”，要能拿出指标。

最小指标：

```text
hit@1 / hit@3 / hit@5
retrieval_empty_rate
answer_faithfulness
context_relevance
context_duplicate_rate
```

代码落点：

```text
rag/retrieve.py
scripts/eval_rag.py
data/eval/coursepilot_rag_cases.jsonl
```

证据：腾讯面经标题就是“做了三个月 RAG，召回率多少”；阿里云 AgentLoop 的 RAG 评估器包含 context relevance、answer relevance、diversity、duplicate。

### P3：Tool Governance

> 代码层面：部分完成（`ToolPolicy` 已有 allowed tools、required args、phase gate、dedup、preflight；但还没有 `ToolRiskLevel / ToolDecision / ApprovalMode` 完整分级审批）；简历层面：部分提到（简历已写 MCP、tool schema、调用协议、参数校验、错误处理，但没写工具风险分级和审批）。

目标：让工具调用可控、可查、可降级。

最小实现：

```text
ToolRiskLevel = safe | read | external | write
ToolDecision = allow | deny | ask | defer
ApprovalMode = off | log | strict
```

代码落点：

```text
core/orchestration/policies.py
core/llm/openai_compat.py
core/harness/hooks.py
```

证据：上海 AI Lab 岗位强调 Function Calling 和 Agent 决策推理；牛客面经追问 Function Call 完整流程、工具注册和新增工具改动。

### P4：Memory Governance

> 代码层面：部分完成（`memory/store.py` 已有 episodes、user_profiles、weak_points、concept_mastery、FTS5/LIKE 检索，Runner 会写入 QA/练习/考试记忆；但还没有 `memory_write_policy`、失败 run 不入库、decay、脱敏等治理策略）；简历层面：部分提到（简历已写 SQLite 长期记忆、qa/practice/mistake/exam、weak_points、concept_mastery，但没写污染防护和写入策略）。

目标：把记忆从“存历史”升级为“有策略的学习画像”。

最小实现：

```text
memory_write_policy:
  - never
  - on_success
  - on_grade_only
  - require_approval

memory_types:
  - qa_episode
  - practice_episode
  - mistake_episode
  - exam_episode
  - weak_point_profile
```

代码落点：

```text
memory/store.py
memory/manager.py
core/orchestration/runner.py
```

证据：阿里淘天面经问长短期记忆、上下文压缩和选择性遗忘；阿里云 AgentLoop 把 Facts、Episodic、Summary 作为记忆策略。

### P5：SSE 和后端稳定性

> 代码层面：部分完成（`backend/api.py` 已有 `/chat/stream`、JSON SSE chunk、heartbeat、request_id、错误事件和 harness stream artifact；但还没有完整取消、断线恢复、run control、Redis 会话等生产级方案）；简历层面：未提到（CoursePilot 简历项目没有明确写 SSE/后端稳定性）。

目标：面试时能讲清流式输出的边界和稳定性。

最小实现：

```text
sse_event:
  - run_started
  - token
  - tool_started
  - tool_finished
  - error
  - run_finished
```

代码落点：

```text
backend/api.py
frontend/streamlit_app.py
```

证据：阿里云 AI 应用研发面经直接问 SSE 流式输出如何实现、是否有连接稳定性保障；后端题还会继续问 Redis 会话和缓存问题。

### P6：Eval / Replay

> 代码层面：部分完成且偏性能/RAG 回归评测（已有 `core/metrics`、trace、`scripts/perf/bench_runner.py`、`delta_report.py`、benchmark cases/gold 和 RunArtifact 基础，可统计 RAG hit、top1、precision、latency、token、tool success、error/replan 等指标；但还没有正式 `replay_run.py`、artifact-driven `eval_runs.py`、answer faithfulness 和 LLM-as-Judge 质量评测）；简历层面：部分提到（简历写了 benchmark、状态事件、调试效率、链路可观测性，但没有写具体评测指标体系）。

目标：每次改 prompt、RAG、工具策略后都能对比效果。

最小实现：

```text
scripts/replay_run.py
scripts/eval_runs.py
reports/eval_summary.md
```

指标：

```text
retrieval_empty_rate
tool_failure_rate
context_truncated_rate
replan_rate
error_rate
p50_latency
p95_latency
estimated_token_cost
```

证据：阿里云 AgentLoop 强调回归测试、实验记录、A/B 对比、Token 成本和首字延迟；这正是项目从 demo 到工程系统的分水岭。

### P7：Skill Registry

> 代码层面：已完成基础版（`core/harness/skills.py` 已实现 `SkillSpec / SkillRegistry / default_skill_registry`，覆盖 `learn.answer`、`learn.mindmap`、`practice.quiz`、`practice.paper`、`practice.grade`、`exam.paper`、`exam.grade`；但还没有 YAML 配置化和 risk_level 字段）；简历层面：未提到（简历没有写 Skill/Skill Registry）。

目标：把高频学习流程变成可版本化、可评测、可治理的 Skill。

建议 Skill：

```text
learn.answer.v1
learn.mindmap.v1
practice.quiz.v1
practice.grade.v1
exam.paper.v1
exam.grade.v1
```

每个 Skill 声明：

```yaml
skill_id: practice.grade.v1
mode: practice
agent: Grader
allowed_tools:
  - calculator
  - memory_search
context_policy:
  rag: true
  memory: true
post_actions:
  - save_practice_record
  - update_weak_points
risk_level: write_memory
```

证据：牛客 AI Agent 岗位 JD 直接写 SKILL 工程搭建；美团/百度 Agent 面经也会追问 MCP、Skill、工具协议和项目能否 skill 化。

### P8：工具级安全与审批

> 代码层面：部分完成（`filewriter` 已用 `basename` 降低路径穿越风险，`ToolPolicy` 有基础 preflight；但还没有后缀/大小限制、untrusted_external 标记、memory 写入审批、strict approval gate）；简历层面：未提到（简历没有写工具级安全、审批、sandbox）。

目标：先做工具级 sandbox，不要宣称完整 OS sandbox。

最小实现：

```text
filewriter:
  - 禁止绝对路径
  - 禁止 ..
  - 限制写入目录
  - 限制后缀
  - 限制文件大小

websearch:
  - 标记 untrusted_external
  - 进入 prompt 前加外部内容不可信提示

memory_write:
  - 失败 run 不写入
  - 敏感信息脱敏
```

证据：牛客面经会问金融安全、失败重试、工具调用安全；阿里云 AgentLoop 也强调企业级安全合规、多租户隔离和完整审计日志。

### P9：离线 Judge / Reviewer

> 代码层面：未完成（当前没有 `retrieval_judge`、`answer_faithfulness_judge`、`tool_use_judge` 或 `EvalResult` 模块）；简历层面：未提到（简历没有写 LLM-as-Judge 或离线 Reviewer）。

目标：先做离线评测 worker，不要先做复杂在线多 Agent。

建议：

```text
retrieval_judge
answer_faithfulness_judge
tool_use_judge
format_judge
safety_judge
```

输入：

```text
RunArtifact
```

输出：

```text
EvalResult
```

证据：阿里云 AgentLoop 支持自定义评估 Prompt，让 LLM 作为裁判进行量化打分；中文面经也会问 Agent 系统有哪些量化评估方式。

## 6. 必做项和加分项

### 6.1 秋招前必做

必做项：

```text
P0 项目故事
P1 RunArtifact
P2 RAG 评测
P3 Tool Governance
P4 Memory Governance
P5 SSE/后端稳定性
P6 Eval/Replay
```

证据：牛客、阿里、腾讯、美团、百度相关面经的高频问题集中在 RAG、工具调用、记忆、上下文、评测、后端稳定性和项目深挖。

### 6.2 简历加分项

加分项：

```text
P7 Skill Registry
P8 工具级安全与审批
P9 离线 Judge / Reviewer
```

证据：牛客 AI Agent 岗位 JD 明确出现 SKILL 工程、EVAL、多 Agent、MLOps；但如果没有实现细节，这些词也最容易被面试官追问。

### 6.3 暂时不建议投入

不建议短期投入：

```text
完整复刻 Claude Code
完整 OpenClaw 网关
完整 OS sandbox
Agentic RL 训练
复杂在线 multi-agent swarm
```

证据：上海 AI Lab 的确要求 SFT/Agentic RL，但那是更偏模型算法和训练数据岗位；你的项目优势是 AI 应用工程闭环，短期强行做训练会牺牲主线可信度。

## 7. 简历推荐写法

### 7.1 现在可以写

```text
设计并实现课程学习 Agent 系统 CoursePilot，基于 OrchestrationRunner 编排 Tutor/QuizMaster/Grader 等角色，串联 Hybrid RAG、SQLite 长期记忆、MCP 工具层与 SSE 流式输出，支持学习问答、练习生成、答案批改和个性化复习。
```

证据：这段能被当前代码支撑，且覆盖牛客面经中常问的项目架构、RAG、记忆、工具调用和流式输出。

```text
实现 Hybrid RAG 检索链路，支持 Dense + BM25 召回与 RRF 融合，并返回文档名、页码和相关度等引用信息，用于降低课程问答中的幻觉风险。
```

证据：RAG 切分、混合检索、召回、幻觉控制是牛客/腾讯/阿里 AI 应用面经的高频追问。

```text
实现 ToolPolicy 工具调用治理基础能力，对 MCP 工具进行参数校验、阶段约束和重复调用去重，降低模型错误调用工具和重复执行的风险。
```

证据：Function Calling、工具注册、工具参数、工具失败和新增工具改动是上海 AI Lab 岗位和牛客面经共同覆盖的问题。

```text
基于 SQLite 构建学习记忆系统，记录 QA、练习、错题和考试 episode，并维护 weak_points 与 concept_mastery，用于个性化讲解和薄弱点强化。
```

证据：阿里淘天面经追问长短期记忆、上下文压缩、选择性遗忘；AgentLoop 官方文档将长期记忆作为 Agent 闭环能力。

### 7.2 完成 P1-P6 后再写

```text
补齐 Agent 运行证据链，设计 RunArtifact 结构化记录 plan、retrieval、context budget、tool calls、memory hits、output、error 与 metrics，支持 replay、eval 和问题定位。
```

证据：阿里云 AgentLoop 强调 Trace、Log、Metric、Conversation 与评估实验；这比泛泛说“可观测”更可落地。

```text
构建 CoursePilot 评测闭环，基于课程问答样本统计 retrieval_empty_rate、hit@k、tool_failure_rate、context_truncated_rate、answer_faithfulness 和 p95 latency，用于对比 RAG、prompt 与工具策略改动。
```

证据：阿里云 AgentLoop 评估体系强调统计指标、回归测试和实验对比；腾讯面经会直接追问 RAG 召回率。

```text
将 learn/practice/exam 高频流程抽象为 SkillSpec，声明 allowed_tools、context_policy、post_actions 和 risk_level，实现能力版本化、工具权限收敛和回归评测。
```

证据：牛客岗位 JD 出现 SKILL 工程；美团/百度 Agent 面经追问 MCP、Skill、工具协议和可测流程。

### 7.3 不建议写

不要写：

```text
复刻 Claude Code / OpenClaw 架构
实现完整 Agent Harness
实现完整 sandbox-aware orchestration
实现 Agentic RL
实现完全自主多 Agent 协作平台
```

证据：这些表述会引出权限模型、sandbox、session resume、subagent 隔离、训练数据、RL 实验、并发控制等深追问；如果没有真实实现，面试风险大。

## 8. 面试回答模板

### 8.1 面试官问：你这个项目和普通 RAG 问答有什么区别？

推荐回答：

```text
普通 RAG 问答主要解决“检索资料并回答”的问题。CoursePilot 更偏学习场景的 Agent 工程化系统：它不是一次性问答，而是围绕学习、练习、批改、复习形成流程。Runner 会根据模式调度 Tutor、QuizMaster、Grader，RAG 提供课程证据，Memory 提供个性化画像，MCP 工具处理计算、检索、文件和思维导图，后续我会用 RunArtifact 把每次运行的检索、工具、上下文和指标沉淀下来，做回放和评测。
```

证据：牛客面经高频追问项目架构、RAG、工具调用、记忆和效果量化；这个回答把这些点串成一条业务闭环。

### 8.2 面试官问：为什么不直接用 LangChain/LangGraph？

推荐回答：

```text
我不是排斥框架，而是这个项目里核心目标是掌握 Agent 工程化链路。CoursePilot 的流程比较固定，learn/practice/exam 都有清晰边界，所以我用 OrchestrationRunner 做中心编排，方便控制工具权限、上下文预算、记忆写入和评测埋点。如果后续流程复杂到需要图状态机，我会优先把当前 Runner 的 plan/replan、RunArtifact 和 ToolPolicy 抽象稳定，再考虑接入 LangGraph 这类框架。
```

证据：牛客面经会问“为什么手搓 agent，而不是用框架”；阿里淘天面经问固定 Workflow 和自主 Agent 如何选择。

### 8.3 面试官问：RAG 召回率多少？

推荐回答：

```text
当前我会把这个问题拆成检索和生成两层。检索层看 hit@k、empty_rate、context_relevance；生成层看 answer_faithfulness、引用覆盖和幻觉 badcase。下一步我会用课程样本构建 jsonl 评测集，每条样本标注标准文档页或标准知识点，然后对 Dense、BM25、Hybrid、Rerank 做对比。这样就不是主观说效果好，而是能给出版本前后的指标变化。
```

证据：腾讯面经明确追问 RAG 召回率；阿里云 AgentLoop 的 RAG 评估器覆盖上下文相关性、答案相关性、多样性和重复性。

### 8.4 面试官问：Agent 死循环怎么处理？

推荐回答：

```text
我会从三层处理。第一层是流程约束，learn/practice/exam 不是无限自主循环，而是由 Runner 控制最大 replan 次数。第二层是工具治理，ToolPolicy 会对工具做参数校验、阶段约束和重复调用去重。第三层是运行证据，RunArtifact 记录每次 replan_reason、tool_call 和 error，用于排查为什么进入循环。这样比单纯让模型自己反思更可靠。
```

证据：阿里云 AI 应用研发面经直接问如何处理 Agent 死循环、如何管理状态和上下文。

### 8.5 面试官问：记忆系统怎么避免污染？

推荐回答：

```text
我的记忆不是所有内容都写入长期库。短期上下文只在当前 run 中使用；长期记忆只保存学习相关 episode 和聚合画像，比如错题、薄弱点、掌握度。失败 run、低置信回答、用户临时表达不会直接更新 weak_points。后续会加 memory_write_policy，比如 on_success、on_grade_only、require_approval，并记录每次 memory write 的来源和理由。
```

证据：阿里淘天面经追问长短期记忆、上下文压缩和选择性遗忘；阿里云 AgentLoop 把 Facts、Episodic、Summary 作为不同记忆策略。

### 8.6 面试官问：怎么证明你的系统稳定？

推荐回答：

```text
我会从工程指标和效果指标两类证明。工程指标包括 p50/p95 latency、error_rate、tool_failure_rate、SSE 中断率、token 成本；效果指标包括 retrieval hit@k、answer_faithfulness、context_relevance、replan_rate。每次运行都沉淀 RunArtifact，后续通过 eval_runs.py 做回归评测和版本对比。
```

证据：阿里云 AgentLoop 强调 Trace、Metric、评估、实验和 Token 成本；牛客面经也问端到端延迟、高可用和效果量化。

## 9. 学习路线

### 第 1 周：项目故事 + RAG 指标

任务：

```text
1. 重写 README 项目介绍
2. 准备 20-50 条课程 RAG 评测样本
3. 实现 hit@k / empty_rate 统计
4. 准备 RAG 面试回答
```

证据：RAG 是腾讯/阿里/牛客面经最密集的追问点，也是 CoursePilot 当前最容易变强的部分。

### 第 2 周：RunArtifact + Eval

任务：

```text
1. 新增 RunArtifact
2. 每次请求记录 retrieval/tool/context/output/error
3. 新增 eval_runs.py
4. 输出 reports/eval_summary.md
```

证据：阿里云 AgentLoop 将运行时数据、评估、实验和长期记忆串成闭环；这能把项目从 demo 拉到工程化系统。

### 第 3 周：Tool Governance + Memory Governance

任务：

```text
1. ToolRiskLevel / ToolDecision
2. ApprovalMode=off/log/strict
3. memory_write_policy
4. 失败 run 不写长期 memory
```

证据：上海 AI Lab 和牛客面经都高频覆盖 Function Calling、工具调用、状态管理和记忆治理。

### 第 4 周：后端稳定性 + 八股补齐

任务：

```text
1. 梳理 SSE 事件和断连策略
2. 准备 Redis 会话、缓存、分布式锁答案
3. 复习 FastAPI/HTTP/并发/限流/降级
4. 每天刷算法题
```

证据：阿里云 AI 应用研发面经在 Agent/RAG 后继续问 Redis、SSE、Java/Spring、会话和缓存；AI 应用岗不是只问 AI。

### 第 5 周：模型基础 + 简历打磨

任务：

```text
1. Transformer / Attention / Embedding
2. SFT / RLHF / DPO / LoRA
3. RAG vs 微调
4. 模型部署与成本优化
5. 简历每个词都准备追问答案
```

证据：牛客大模型应用开发面经包含 Transformer、Function Call 训练、微调、Embedding、部署；上海 AI Lab 岗位要求 SFT、RLHF、Agentic RL。

## 10. 未来核心竞争力

### 10.1 定义问题和评测

观点：未来最不容易被 Agent 替代的是“定义什么叫好”的能力，包括业务目标、评测集、指标、badcase 分类和上线阈值。

证据：阿里云 AgentLoop 强调把模糊语义感受转成统计指标；上海 AI Lab 岗位要求设计 Agent 能力相关评测任务和指标。

### 10.2 设计可控运行时

观点：企业不会只要一个会写 prompt 的人，而是需要能设计状态、工具、权限、回放、监控和降级的人。

证据：牛客 AI Agent 岗位 JD 写到 Harness、SKILL、EVAL、MLOps、OpenTelemetry、Prometheus；这些都属于运行时工程能力。

### 10.3 工具和权限治理

观点：Agent 越强，越需要工程师设计边界，否则会出现错误调用、越权调用、数据泄露和业务风险。

证据：牛客面经问金融场景失败/重试如何保证安全；阿里云 AgentLoop 强调多租户隔离、完整审计日志和安全合规。

### 10.4 业务流程抽象

观点：把真实业务拆成可执行、可评测、可复用的 workflow/skill，是 AI 应用工程师的关键能力。

证据：阿里淘天面经问固定 Workflow 和自主 Agent 的选择；牛客岗位 JD 直接写 SKILL 工程。

### 10.5 后端工程与系统集成

观点：Agent 生成代码会越来越强，但真实企业系统仍然需要接口设计、鉴权、并发、缓存、观测、成本和稳定性取舍。

证据：阿里云 AI 应用研发面经在 Agent 问题之后继续问 Redis、会话、缓存、SSE 和 Spring；牛客后端转 Agent 讨论也强调后端基础是迁移优势。

### 10.6 数据和记忆治理

观点：企业 Agent 的长期价值来自高质量数据飞轮、长期记忆和可控数据沉淀，而不是一次性回答。

证据：阿里云 AgentLoop 将运行时数据沉淀为评估集、后训练数据集和长期记忆；阿里淘天面经追问长期画像与当前会话冲突如何处理。

## 11. 最终执行建议

第一优先级：

```text
RunArtifact + Eval + RAG 指标
```

证据：这三件事最能把 CoursePilot 从普通项目提升为“工程化 Agent 项目”，也最能回应牛客/阿里/腾讯面经里的效果量化追问。

第二优先级：

```text
Tool Governance + Memory Governance
```

证据：Function Calling、工具注册、工具失败、长短期记忆、上下文压缩是中文面经高频问题。

第三优先级：

```text
Skill Registry + Approval Gate
```

证据：牛客岗位 JD 出现 SKILL 工程和 Harness，阿里云 AgentLoop 强调安全合规和审计；这是加分项，不是第一天必须完成的底座。

最终简历主线：

```text
CoursePilot：面向课程学习场景的轻量 Agent Runtime。
核心不是“我用了某个国外架构”，而是“我把 RAG、Memory、MCP Tool、Eval、Trace、Replay 做成了一个可运行、可复盘、可优化的学习 Agent 系统”。
```

证据：这个表述覆盖中国大厂当前最常见的面试追问：RAG 怎么做、工具怎么调、状态怎么管、记忆怎么写、效果怎么量化、后端怎么稳、项目是否真实落地。

## 12. P0-P6 学习与简历同步表

> 目标：只聚焦 P0-P6，把“代码里已经做了但你还不会讲”的能力整理成学习顺序和简历表达。  
> 简历原则：能被代码支撑的写进简历；只有规划但未落地的放进面试扩展，不写成已完成；没有实验报告的数据不写具体百分比。

### 12.0 2026-05-11 当前学习状态

这一版的学习状态要按你现在的真实掌握程度重新排序：

| 模块 | 当前掌握状态 | 简历口径 | 下一步 |
|---|---|---|---|
| P0 项目故事 | 主要是简历表达更新，不是代码大改 | 可以立刻更新，把“RAG + 多 Agent”升级成“课程学习 Agent Runtime” | 用 P1/P2/P4/P5 的真实代码能力支撑简历表述 |
| P1 RunArtifact / Trace / HarnessRuntime | 已基本理解：知道 HarnessRuntime 是包装原 Runner 的运行层，RunArtifact 是落盘证据，Trace 是运行时观测，SSE 是流式输出协议 | 可以写“运行证据链与可观测性”，但不要写完整 Eval/Replay 平台 | 整理成 1 分钟面试回答即可 |
| P2 RAG 评测闭环 | 今天主线。代码有评测基础，但真实课程数据不足，暂时不适合夸大成“明显提升” | 当前简历只能写“具备 benchmark / gold_doc_ids / hit@k 等评测基础”，不要写具体提升百分比 | 先跑通小规模课程文本评测，再扩充真实课程 case |
| P3 Tool Governance | 代码有 ToolPolicy/preflight/去重/参数校验，但你还没系统复盘 | 可以写 MCP 工具封装和基础治理 | 后续再学工具权限、失败降级和调用审计 |
| P4 Memory Governance | 设计已经理解：episodes + profile + weak_points/concept_mastery | 可以写长期记忆与学习画像 | 暂时不写完整记忆治理、记忆衰减、污染防护 |
| P5 SSE 与后端稳定性 | SSE 主流程已经理解：请求、队列、后台线程、heartbeat、StreamingResponse | 可以写流式响应与 trace 观测 | 后续补取消、断连恢复、长任务控制 |
| P6 Eval / Replay | 只理解了 trace/benchmark 的一部分，还没有完整学习 | 当前不要写完整 Agent Eval 平台 | 等 P2 跑通后再学 delta_report、Replay、LLM Judge |

当前最优学习路径：

```text
今天：P2 RAG 评测闭环
然后：把 P0 简历段落更新成真实、可追问、可防守的版本
随后：把 P1 RunArtifact / Trace / HarnessRuntime 整理成面试回答
再后：P6 Eval，再到 P3/P4/P5 的深入治理能力
```

为什么今天先学 P2：

- RAG 是中国大厂 Agent/AI 应用岗最容易追问的主线。
- 你现在已经能讲 RunArtifact、Trace、Memory、SSE 的大概流程，短板反而变成“怎么证明 RAG 变好”。
- 当前项目数据少，不代表不能学评测；正确做法是先用小规模可控课程文本跑通 `gold_doc_ids -> hit@k/top1/precision -> baseline/after` 的闭环，再逐步扩充数据。

### 12.1 总体判断

CoursePilot 的 P0-P6 不是从零开始。当前代码已经具备：

```text
P0 项目主线：Runner + RAG + Memory + MCP + ContextBudgeter + trace/perf
P1 运行证据：core/harness 下已有 Session / RunArtifact / Runtime / Hooks
P2 RAG 评测：benchmarks + bench_runner 已能统计 hit_at_k / top1 / precision_at_k
P3 工具治理：ToolPolicy 已有参数校验、phase gate、去重和 preflight
P4 记忆系统：SQLite episodes/profile + weak_points/concept_mastery
P5 SSE：/chat/stream 已有 request_id、heartbeat、错误事件、流式输出
P6 Eval：已有 perf benchmark、delta_report、trace 指标，但还缺 answer faithfulness / LLM Judge
```

所以接下来不是“先实现”，而是两条线同步推进：

```text
学习线：把 P0-P6 每个模块的原理、代码链路、面试回答讲熟。
简历线：把已实现能力写得更工程化，但不夸大成完整生产级平台。
```

### 12.2 P0：项目故事

#### 代码里已经有什么

- `core/orchestration/runner.py` 统一编排 learn/practice/exam。
- Router/Tutor/QuizMaster/Grader 是中心编排式多 Agent，不是互相聊天的 swarm。
- RAG、Memory、MCP Tools、ContextBudgeter、trace/perf 都已经接入主链路。

#### 你要学会讲什么

一句话：

```text
CoursePilot 是面向课程学习场景的轻量 Agent Runtime，用 Runner 编排学习、练习、考试流程，把 RAG、Memory、MCP Tool、上下文预算和运行指标串成一个可复盘的学习闭环。
```

面试时要强调：

- 业务闭环是“学习-练习-批改-复习”，不是普通问答。
- 多 Agent 是中心编排式职责拆分，不要说成完全自主多 Agent。
- 项目价值是让课程学习流程可控、可追踪、可优化。

#### 简历推荐写法

```latex
\item \textbf{Agent Runtime架构设计：}面向大学课程学习场景，设计覆盖“知识讲解—练习生成—答案批改—薄弱点强化”的轻量Agent Runtime，基于OrchestrationRunner统一编排Router/Tutor/QuizMaster/Grader等角色，串联RAG、Memory、MCP工具与上下文治理模块，实现学习流程的可控调度与个性化反馈。
```

### 12.3 P1：RunArtifact 运行证据链

#### 代码里已经有什么

- `core/harness/session.py`：生成 `run_id`、状态、开始/结束时间。
- `core/harness/artifact.py`：定义 `RunArtifact` 和 `ArtifactStore`。
- `core/harness/runtime.py`：包裹原 Runner，保留原响应结构，同时记录 artifact。
- `backend/api.py`：通过 `ENABLE_HARNESS_RUNTIME` 接入普通 `/chat` 和 `/chat/stream`。
- harness 相关 unittest 已通过。

#### 当前学习状态

你现在已经基本理解 P1 的核心目的：

```text
HarnessRuntime 不是替代原 Runner，而是在外面套一层运行管理；
RunArtifact 不是 Agent 本体，而是一轮对话结束后的结构化证据文件；
Trace 不是只记录工具调用，而是记录 LLM、RAG、Tool、ContextBudget、SSE、错误等后台事件；
SSE 负责把流式 chunk 发给前端，Harness 负责在旁路收集这些 chunk 和 trace，最后落盘成 RunArtifact。
```

面试上 P1 已经不再是今天最高优先级。后续只需要把这段话练熟，并能解释：

- 为什么要有 HarnessRuntime：为了不侵入原业务链路，又能统一生成运行证据。
- 为什么要有 RunArtifact：为了按 `run_id` 保存可回放、可评测、可排查的结构化记录。
- 为什么 Trace 和 Stream 要一起收集：Trace 是后台内部观测，Stream 是用户实际收到的事件，两者视角不同。

#### 你要学会讲什么

核心逻辑：

```text
原来系统只返回答案；现在每次运行还会沉淀一份 RunArtifact，记录 plan、retrieval、context_budget、tool_calls、output、metrics、error 和 lifecycle_events。
```

面试追问：

- 为什么需要 artifact？为了失败排查、评测回归、运行审计。
- 和普通日志区别？artifact 是结构化证据，按 run_id 组织，可以被 eval/replay 脚本消费。
- 是否改变原主流程？不改变，HarnessRuntime 是薄包装层。

#### 简历推荐写法

```latex
\item \textbf{运行证据链与可观测性：}设计轻量HarnessRuntime，在不改变原有问答响应结构的前提下，为每次Agent运行生成RunArtifact，结构化记录plan、retrieval、context budget、tool calls、output、error与metrics，为后续问题定位、回放评测和版本对比提供数据基础。
```

### 12.4 P2：RAG 评测闭环

#### 代码里已经有什么

- `rag/retrieve.py` 支持 dense / BM25 / hybrid 三种模式。
- Hybrid 使用 RRF 融合 dense 和 BM25。
- 检索结果包含 `doc_id`、`page`、`chunk_id`、`score`。
- `benchmarks/cases_v1.jsonl` 存放评测 case。
- `benchmarks/rag_gold_v1.jsonl` 存放 gold doc ids。
- `scripts/perf/bench_runner.py` 统计：
  - `hit_at_k`
  - `top1_acc`
  - `precision_at_k`
  - `avg_retrieval_ms`
  - `p95_retrieval_ms`

#### 当前学习状态

你现在对 P2 的判断应该改成：

```text
项目不是没有 RAG 评测，而是“有评测框架，但真实课程数据和 gold 标注还不够多”。
所以现在不能把 P2 写成已经有充分业务数据证明的优化成果；
更合理的目标是先跑通小规模闭环，再逐步扩充真实课程数据。
```

今天学习 P2 时，先不要追求“很厉害的量化结果”，而是要把优化过程讲清楚：

```text
1. 准备一份课程文本，切成 chunk 并建索引。
2. 准备若干问题，每个问题标注 gold_doc_ids 或 gold_chunk_ids。
3. 分别跑 dense、BM25、hybrid 三种检索策略。
4. 对比 hit@k、top1_acc、precision@k、empty_rate、retrieval latency。
5. 观察 hybrid 是否比单一路径更稳。
6. 把结果作为后续简历和面试的“优化过程证据”，而不是直接夸大成生产级评测平台。
```

当前数据不足时，简历不能写：

```text
在大规模课程数据上显著提升检索准确率。
```

可以写：

```text
构建了基于 gold_doc_ids 的 RAG 检索评测基础，可统计 hit@k、top1 accuracy、precision@k 与检索延迟，用于对比 Dense、BM25 与 Hybrid Retrieval 策略。
```

2026-05-11 已补充一轮小规模实测，详见 `docs/COURSEPILOT_RAG_EVAL_REPORT.md`：

```text
数据：8 份线性代数讲义 + 16 个课程问答 case + gold_doc_ids/gold_keywords 标注。
结果：Dense 与 BM25 在该小评测集上 top1_acc 均为 1.0；默认 Hybrid hit@4 为 1.0 但 top1_acc 仅 0.1875，暴露 RRF 候选集过宽导致的排序退化。
优化：将 Hybrid 的 dense/BM25 候选扩展倍数从 3 调整为 1 后，top1_acc 恢复到 1.0，p95 检索延迟从 175.8756ms 降至 148.3251ms。
边界：这是小规模自建检索评测，不是大规模线上实验，也不是最终答案质量评测。
```

#### 你要学会讲什么

先纠正一个点：你的项目确实有 RAG 评测基础，不是没有。

更准确的说法：

```text
当前实现了基于 gold_doc_ids 的 RAG benchmark，可以统计 hit@k、top1 accuracy、precision@k 和检索延迟；下一步还要补 answer faithfulness、context relevance 和 LLM-as-Judge，评估生成答案是否忠实于检索内容。
```

面试追问：

- hit@k 是什么？只要 top-k 里命中任一 gold doc 就算命中。
- top1_acc 是什么？第一条引用命中 gold doc 才算对。
- precision@k 是什么？返回的引用里 gold doc 占比。
- 当前不足？gold 粒度是 doc id，后续应细到 page/chunk/concept。

#### 简历推荐写法

当前保守版，适合现在就放进简历：

```latex
\item \textbf{RAG检索与评测链路：}构建课程资料解析、切块、向量建库与在线检索链路，实现Dense+BM25 Hybrid Retrieval与RRF融合；设计基于gold\_doc\_ids的检索评测基础，可统计hit@k、top1 accuracy、precision@k与检索延迟，用于对比Dense、BM25与Hybrid Retrieval策略。
```

等你补充更多真实课程 case、跑出稳定 baseline/after 后，再升级成：

```latex
\item \textbf{RAG检索链路优化：}构建课程资料解析、切块、向量建库与在线检索链路，实现Dense+BM25 Hybrid Retrieval与RRF融合；基于课程问答benchmark对比Dense、BM25与Hybrid策略的hit@k、top1 accuracy、precision@k及检索延迟，形成可回归的检索优化闭环。
```

### 12.5 P3：Tool Governance

#### 代码里已经有什么

- `core/orchestration/policies.py` 定义工具能力。
- 已有：
  - allowed tools
  - required args
  - phase gate
  - retry policy
  - dedup scope
  - normalized signature
  - `tool_preflight`
- `core/llm/openai_compat.py` 会使用 preflight 控制工具调用。
- `mcp_tools/client.py` 封装 calculator、websearch、filewriter、memory_search、mindmap_generator、get_datetime。

#### 你要学会讲什么

核心逻辑：

```text
我不是只把工具暴露给模型，而是在模型调用前做 preflight：检查工具是否允许、是否处于正确阶段、必填参数是否完整，并用 normalized signature 做重复调用去重。
```

当前不足：

```text
还没有 ToolRiskLevel、ApprovalMode、strict approval gate，所以不能说完整工具权限系统。
```

#### 简历推荐写法

```latex
\item \textbf{MCP工具调用治理：}将calculator、websearch、filewriter、memory\_search、mindmap等能力统一封装为MCP工具，并通过ToolPolicy实现工具白名单、必填参数校验、阶段约束、调用去重与错误兜底，降低LLM错误调用和重复执行风险，实现Agent与工具层解耦。
```

### 12.6 P4：Memory Governance

#### 代码里已经有什么

- `memory/store.py` 有两类核心表：
  - `episodes`
  - `user_profiles`
- episodes 支持 `qa/mistake/practice/exam`。
- profile 支持 `weak_points`、`concept_mastery`、`avg_score`。
- 支持 FTS5，失败时回退 LIKE。
- Runner 会在学习问答、练习批改、考试批改后写入记忆。

#### 你要学会讲什么

核心逻辑：

```text
记忆系统分为情景记忆和用户画像。episodes 记录具体学习事件，profile 聚合薄弱点和掌握度；生成时先检索相关历史，再注入到上下文中，让系统能做个性化讲解和错题强化。
```

当前不足：

```text
还没有严格的 memory_write_policy、失败 run 不入库、记忆衰减和敏感信息脱敏，所以简历可以写“长期记忆系统”，暂时不要写“完整记忆治理体系”。
```

#### 简历推荐写法

```latex
\item \textbf{长期记忆与学习画像：}基于SQLite构建episodes与user\_profiles两层记忆结构，按QA/练习/错题/考试事件沉淀学习轨迹，并维护weak\_points与concept\_mastery画像；在生成阶段检索相关历史记忆并动态注入上下文，实现个性化讲解、错题强化与薄弱点复习。
```

### 12.7 P5：SSE 与后端稳定性

#### 代码里已经有什么

- `backend/api.py` 有 `/chat/stream`。
- 流式输出使用 `StreamingResponse`。
- chunk 使用 JSON 序列化，避免换行破坏 SSE 协议。
- 有 `request_id`、heartbeat、错误事件、`[DONE]`。
- harness 开启后，stream 也会生成 artifact。

#### 你要学会讲什么

核心逻辑：

```text
SSE 适合模型单向流式输出，CoursePilot 用队列和后台线程承接 Runner 的流式生成，再用 JSON SSE chunk 返回前端，同时记录 first token latency、e2e latency、emitted chunks 和错误事件。
```

当前不足：

```text
SSE 不适合复杂双向控制；取消、审批、断点恢复、长任务控制后续更适合补 control plane 或 WebSocket。
```

#### 简历推荐写法

```latex
\item \textbf{流式响应与链路稳定性：}实现FastAPI SSE流式输出，将长回答按JSON chunk返回前端，并通过request\_id、heartbeat、错误事件与[DONE]结束标记维护请求状态；结合trace记录首token延迟、端到端耗时和异常信息，提升长文本生成场景下的可观测性。
```

### 12.8 P6：Eval / Replay

#### 代码里已经有什么

- `core/metrics/collector.py` 有 request-scoped `MetricsTrace`。
- `trace_scope` + `ContextVar` 记录每次请求事件。
- `scripts/perf/bench_runner.py` 能跑 benchmark 并输出 summary。
- `scripts/perf/delta_report.py` 能比较 baseline 和 after。
- 指标覆盖：
  - prompt tokens
  - first token latency
  - e2e latency
  - retrieval latency
  - RAG hit/top1/precision
  - tool call/success
  - error rate
  - replan rate
  - duplicate tool call rate

#### 你要学会讲什么

核心逻辑：

```text
当前 Eval 更偏工程指标和 RAG 回归评测：通过 benchmark case 跑端到端链路，再从 trace 里聚合延迟、token、RAG命中、工具调用和错误率。后续要把 RunArtifact 接入 replay/eval，并补 answer faithfulness、LLM Judge 和安全合规评测。
```

面试表达边界：

- 可以说“已有 benchmark 与性能/RAG 指标评测”。
- 不要说“完整 Agent Eval 平台”。
- 不要写没跑出来的具体提升百分比。

#### 简历推荐写法

```latex
\item \textbf{Benchmark与效果回归：}构建端到端benchmark评测脚本，基于trace事件聚合RAG hit@k/top1/precision、retrieval latency、首token延迟、端到端耗时、tool success rate、error rate与replan rate等指标，并支持baseline/after差异报告，用于量化检索、上下文和工具策略改动效果。
```

### 12.9 推荐替换后的 CoursePilot 简历段落

下面这版只写 P0-P6，且都能被当前代码或已有 benchmark 基础支撑。建议替换你原 LaTeX 中第一个项目的 `itemize`。

```latex
\begin{itemize}
  \item \textbf{Agent Runtime架构设计：}面向大学课程学习场景，设计覆盖“知识讲解—练习生成—答案批改—薄弱点强化”的轻量Agent Runtime，基于OrchestrationRunner统一编排Router/Tutor/QuizMaster/Grader等角色，串联RAG、Memory、MCP工具与上下文治理模块，实现学习流程的可控调度与个性化反馈。

  \item \textbf{运行证据链与可观测性：}设计轻量HarnessRuntime，在不改变原有问答响应结构的前提下，为每次Agent运行生成RunArtifact，结构化记录plan、retrieval、context budget、tool calls、output、error与metrics，为后续问题定位、回放评测和版本对比提供数据基础。

  \item \textbf{RAG检索与评测链路：}构建课程资料解析、切块、向量建库与在线检索链路，实现Dense+BM25 Hybrid Retrieval与RRF融合；设计基于gold\_doc\_ids的检索评测基础，可统计hit@k、top1 accuracy、precision@k与检索延迟，用于对比Dense、BM25与Hybrid Retrieval策略。

  \item \textbf{长期记忆与学习画像：}基于SQLite构建episodes与user\_profiles两层记忆结构，按QA/练习/错题/考试事件沉淀学习轨迹，并维护weak\_points与concept\_mastery画像；在生成阶段检索相关历史记忆并动态注入上下文，实现个性化讲解、错题强化与薄弱点复习。

  \item \textbf{MCP工具调用治理：}将calculator、websearch、filewriter、memory\_search、mindmap等能力统一封装为MCP工具，并通过ToolPolicy实现工具白名单、必填参数校验、阶段约束、调用去重与错误兜底，降低LLM错误调用和重复执行风险，实现Agent与工具层解耦。

  \item \textbf{Benchmark与效果回归：}构建端到端benchmark评测脚本，基于trace事件聚合RAG hit@k/top1/precision、retrieval latency、首token延迟、端到端耗时、tool success rate、error rate与replan rate等指标，并支持baseline/after差异报告，用于量化检索、上下文和工具策略改动效果。
\end{itemize}
```

### 12.10 下一步学习顺序

1. 今天先学 P2：RAG benchmark。  
   原因：你已经理解 RunArtifact、Trace、Memory、SSE 的大流程，当前最需要补的是“RAG 怎么评测、怎么证明优化有效”。今天的目标不是跑出很夸张的数据，而是跑通 `课程文本 -> gold 标注 -> 检索策略对比 -> 指标解释` 这条闭环。

2. 同步做 P0：更新简历表达。  
   原因：P0 本质是包装项目主线。等 P2 的评测边界搞清楚后，简历就能写得更稳，不会出现“代码有但自己讲不清”或“数据不足却写过头”的问题。

3. 复盘 P1：RunArtifact / Trace / HarnessRuntime。  
   原因：你已经大概理解了流程，下一步只需要整理成面试 1 分钟回答和追问答案。

4. 再学 P6：trace + benchmark + delta report。  
   原因：P6 是 P2 的延伸。先会 RAG 指标，再学如何用 trace 和 delta report 做版本对比，会更顺。

5. 最后补 P3/P4/P5 的深入版。  
   原因：P4 Memory 和 P5 SSE 你已经理解基础流程，P3 ToolPolicy 还需要系统复盘；这些适合放到 P2/P6 之后继续加深。
