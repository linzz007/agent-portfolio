# 面试官阅读指南 | Interview Reading Guide

这份指南用于帮助阅读者快速理解本仓库与简历 `9.0` 的对应关系。仓库中的项目不是按“框架名称”展示，而是按真实 Agent 项目里更重要的链路展示：

```text
业务问题 -> 工程动作 -> 可验证结果 -> 复盘与后续优化
```

## 1. 简历主线怎么对应仓库

| 简历经历 | 仓库对应 | 重点能力 |
| --- | --- | --- |
| 报表 Agent 实习 | 公司项目不公开 | 业务测试集、Query 澄清、失败归因、Guardrail、Runtime 适配 |
| CoursePilot 课程学习 Agent | `projects/coursepilot-agent-runtime` | RAG、Memory、ContextBudgeter、MCP、RunArtifact、Trace |
| Insight Workbench 智能研判 Agent | `projects/ai-stack-impact-workbench` | 受控 workflow、Subagent、Wiki 画像、工具权限、报告产物 |
| Daily AI Insight Engine | `projects/daily-ai-insight-engine-harness` | State / Graph / Hook / Gate / Artifact 的小规模机制验证 |
| Dify 校企合作脱敏案例 | `projects/campus-career-agent-dify-case-study` | ToB 业务建模、角色权限、平台适配、操作日志 |

## 2. 最值得追问的项目

### 2.1 实习：报表 Agent 优化

面试中最适合作为主线项目，因为它最接近企业真实落地。

可以追问：

- 如何构造 80+ 条业务用例和澄清专项测试集？
- 为什么关键失败定位在澄清阶段，而不是 SQL 生成阶段？
- 结构化语义约束和执行前 Guardrail 具体检查什么？
- 复杂 Query 通过率从约 60% 到约 90% 的复测方式是什么？
- Claude Code SDK 项目接入 AgentScope 时，工具协议、Skill、Middleware 和产物保存链路如何适配？

### 2.2 CoursePilot：RAG / Memory / Context 工程化

这个项目适合考察基础 Agent 工程能力。

可以追问：

- 为什么课程学习 Agent 需要长期 Memory，而普通 RAG 不够？
- `episodes` 和 `user_profiles` 分别存什么？
- `ContextBudgeter` 如何在 history、教材证据和 memory 之间做预算？
- prompt tokens 从 `4957.3` 降到 `2524.6` 的实验口径是什么？
- Hybrid RRF + rerank 为什么能把 `MRR@4` 从 `0.58` 提升到 `0.94`？
- RunArtifact 如何帮助定位检索偏移、工具失败和上下文超预算？

### 2.3 Insight Workbench：Harness 与受控 Agent 系统

这个项目适合考察对 Agent Harness / Runtime 的理解。

可以追问：

- 为什么 `/report` 采用 workflow，而不是完全自由的 Agent Loop？
- `State -> Graph -> Hook/Gate -> Artifact` 在报告链路中各自解决什么问题？
- Subagent 解决的是准确性、上下文隔离，还是工具权限隔离？
- Wiki 画像如何影响资料筛选、背景匹配和报告追问？
- Trace 里如何关联主 Agent 和子任务？
- 工具调用成功和业务结论正确如何区分？

## 3. 项目之间不是重复关系

| 项目 | 主要表达 |
| --- | --- |
| 实习报表 Agent | 真实业务问题、评测、失败归因、Guardrail、Runtime 适配 |
| CoursePilot | 自己能把 RAG、Memory、Context、MCP 和 Trace 串成完整 Agent Runtime |
| Insight Workbench | 理解 Harness 的控制、隔离、证据、产物和复盘机制 |
| Daily AI Insight Engine | 用小规模 workflow 验证 Harness 基础机制 |
| Dify 案例 | 有 ToB 平台型 Agent 二次开发和交付意识 |

## 4. 后续优化方向

当前最值得继续补强的是实习报表 Agent 的复盘与二次优化：

1. 补全真实失败 case 的分类表：澄清失败、口径歧义、时间范围错误、报表一致性问题等。
2. 把每类失败 case 关联到 trace、输入、澄清内容、执行结果和人工复核结论。
3. 用 SOP 记录每一轮优化：问题与证据、归因与方案、修改与实验、指标变化。
4. 将可复用的方法沉淀到 Agent 优化资料库，形成持续更新的小型方法论闭环。

这条路线与行业分享中强调的企业级 Agent 落地能力一致：不是只做成功 Demo，而是能通过 Trace、Eval、Guardrail 和持续复盘，把 Agent 变成可验证、可优化的业务系统。
