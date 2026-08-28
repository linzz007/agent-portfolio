# AI Stack Impact Workbench | 外部变化影响分析 Agent Harness

这是一个面向 AI 工程师与研发/运营团队的 **外部变化影响分析系统**。项目把技术发布、政策规则、行业事件和新闻快照统一建模为 External Event，再结合企业/项目画像生成可追问、可审计、可沉淀的结构化影响报告。

## 面试官先看

- **业务问题**：外部信息很多，但企业/项目真正关心的是“这件事和我有什么关系、风险多大、证据是什么、后续怎么跟踪”。
- **技术重点**：不是简单新闻总结，而是用 AgentRuntime + SkillManifest + Subagent + Tool Policy + Gate 把长链路 Agent 做成可控系统。
- **可追问点**：为什么需要 Harness、如何限制工具权限、如何构造上下文、如何让 skeptic/verifier 子智能体只看到自己该看的信息、如何记录每轮运行证据。

## 核心设计

1. **统一运行入口**：普通对话、`/report` 外部变化分析和动态子智能体委派都进入同一个 TurnCoordinator，避免每个功能各写一套流程。
2. **Skill 编排**：通过 SkillManifest 定义 execution_mode、allowed_tools、allowed_subagents、context_policy 和 artifact 类型，让不同任务有明确能力边界。
3. **动态 Subagent**：主 AgentLoop 根据问题复杂度委派 analyst、skeptic、verifier 等角色；子智能体使用独立 Role Manifest、TaskBrief、工具权限和输出 Schema。
4. **工具治理**：ToolGateway 与 PermissionEngine 负责工具白名单、deny/ask/allow 决策和工具审计，避免模型绕过业务规则直接调用工具。
5. **上下文治理**：ContextManifest 按任务阶段构造上下文包，显式记录哪些 memory、evidence、history 被放入模型上下文。
6. **证据优先报告**：高风险结论必须绑定 fact_id / source_ref；证据不足时降级为观察项或待确认项。

## 面试官可看的代码入口

| 文件 | 看点 |
| --- | --- |
| `run_policy_api.py` | 后端启动入口和对话式 Workbench 服务入口。 |
| `src/policy_impact/app/chat_workbench_service.py` | 对话、报告、trace、artifact 的服务层组织方式。 |
| `src/policy_impact/harness/agent_loop.py` | 主 AgentLoop、动作执行和工具调用闭环。 |
| `src/policy_impact/harness/context_manifest.py` | 上下文包构造与可见上下文记录。 |
| `src/policy_impact/harness/permissions.py` | 工具权限、deny/ask/allow 和策略判断。 |
| `src/policy_impact/harness/subagent_planner.py` | 子智能体委派判断与任务拆分。 |
| `src/policy_impact/harness/artifacts.py` | RunArtifact / 报告产物沉淀。 |
| `plugins/skills/manifest.json` | SkillManifest 配置化能力边界。 |
| `plugins/tools/registry.json` | 工具注册与工具边界。 |
| `plugins/subagents/roles.json` | 子智能体角色定义。 |

## 验证方式

```powershell
py -3 -m pip install -e .
py -3 -m pytest
py -3 scripts/run_workbench_smoke.py
py -3 scripts/run_workbench_dialogue_acceptance.py
```

## 项目定位

这个项目适合在面试中表达 **Agent Harness / Agent Infra** 能力：我关注的不是“让模型多说几句话”，而是让 Agent 在有状态、有工具、有证据、有权限边界的业务链路里稳定运行，并且能定位问题、复盘运行、沉淀产物。

## 脱敏说明

发布版已移除真实企业画像、长期记忆数据库、运行日志、本地模型凭据和历史运行数据，只保留可公开样例、测试 fixture、核心代码和设计文档。
