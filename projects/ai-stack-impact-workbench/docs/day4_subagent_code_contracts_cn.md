# Day 4：Subagent 代码契约与运行验收

> 学习目标：不再只看懂流程图，而是能说清楚一次普通对话里，Subagent 是如何被规划、如何被授权、拿到什么上下文、输出什么 schema、最终怎么进入 Trace 的。

## 1. 今日核心结论

当前实现里，Subagent 不是斜杠命令，也不是 `/report` 固定 workflow 的某个 stage。

它是 `general_chat` 主 AgentLoop 内部的一种受控 action：

```text
用户 query
-> AgentRuntime 选择 general_chat
-> QuerySubagentPlanner 在 observe 阶段生成 recommended_delegation_plan
-> Main AgentLoop 执行 delegate action
-> Runtime 检查 SkillManifest.allowed_subagents
-> SubagentRunner 构造 TaskBrief
-> 子智能体只读取被委派 artifact_ref
-> 子智能体输出严格 schema
-> SubagentResult 回填主循环
-> 主 Agent 综合回答
-> AgentStep 记录 subagent_plan / subagent_context / subagent_delegate / model_call
```

这里有一个工程化取舍：真实模型不稳定时，Runtime 会把显式触发信号转成 plan-guided delegate action sequence，保证“分析+质疑”“证据链+核验”这类用户意图能稳定触发多个子智能体。测试用的 `ScriptedModelAdapter` 不启用这个稳定器，仍按脚本化动作执行，避免测试双桩被二次强制。

## 2. 你今天要重点读的文件

| 目的 | 文件 |
|---|---|
| 主入口和动态委派 | `D:\AAAcode\code-code\agent+\harness\policy_impact\src\policy_impact\harness\agent_runtime.py` |
| 主 AgentLoop 如何执行 delegate action | `D:\AAAcode\code-code\agent+\harness\policy_impact\src\policy_impact\harness\agent_loop.py` |
| 子智能体实际执行器 | `D:\AAAcode\code-code\agent+\harness\policy_impact\src\policy_impact\harness\subagents.py` |
| query 如何变成候选角色 | `D:\AAAcode\code-code\agent+\harness\policy_impact\src\policy_impact\harness\subagent_planner.py` |
| 给主 Agent 看的角色 roster | `D:\AAAcode\code-code\agent+\harness\policy_impact\src\policy_impact\runtime\subagent_manifests.py` |
| Runtime 真正执行的角色契约 | `D:\AAAcode\code-code\agent+\harness\policy_impact\src\policy_impact\runtime\subagent_roles.py` |
| 子智能体输出 schema | `D:\AAAcode\code-code\agent+\harness\policy_impact\src\policy_impact\runtime\subagent_outputs.py` |
| TaskBrief / SubagentResult | `D:\AAAcode\code-code\agent+\harness\policy_impact\src\policy_impact\runtime\task_brief.py` |

## 3. Schema 分层

### 3.1 SubagentManifest：给主 Agent 看的能力说明

文件：`runtime/subagent_manifests.py`

它决定主 Agent 能看到哪些子智能体，以及什么时候应该用。

关键字段：

| 字段 | 含义 |
|---|---|
| `name` | 给主 Agent 看的能力名称，例如 `impact-analyst` |
| `role` | Runtime 执行用角色 id，例如 `analyst` |
| `description` | 这个子智能体负责什么 |
| `use_when` | 什么时候建议使用 |
| `avoid_when` | 什么时候不要用 |
| `tools` | 该角色理论允许工具 |
| `disallowed_tools` | 显式禁止工具 |
| `output_schema_name` | 输出契约名，例如 `claim_set.v1` |
| `max_turns` / `max_model_calls` / `timeout_seconds` | 预算和生命周期上限 |
| `context_policy_id` | 子智能体上下文策略 |
| `memory_scope` | 当前是 `read_only` 或 `none` |
| `can_publish` | 是否允许发布产物，普通聊天角色基本为 `False` |

你要理解：Manifest 是“暴露给主 Agent 的菜单”，但不是最终权限。最终权限还要和 caller skill 的 allowlist 求交集。

### 3.2 SubagentRoleDefinition：Runtime 真正执行的角色契约

文件：`runtime/subagent_roles.py`

它是 Runtime 的硬约束。

关键字段：

| 字段 | 含义 |
|---|---|
| `role` / `version` | 角色和版本 |
| `prompt_template_id` / `prompt_template_version` | prompt 契约 |
| `output_schema_name` / `output_schema` | 输出必须满足的 Pydantic schema |
| `allowed_tools` | 该角色最多能用哪些工具 |
| `max_steps` / `max_model_calls` / `timeout_seconds` | 生命周期上限 |
| `can_publish` | 是否能发布最终产物 |

当前默认角色：

| role | 适合场景 | output_schema |
|---|---|---|
| `collector` | 来源、证据链、数据源盘点 | `evidence_collection.v1` |
| `analyst` | 影响分析、方案拆解、取舍判断 | `claim_set.v1` |
| `skeptic` | 质疑、反例、风险、面试拷打 | `counterexample_set.v1` |
| `verifier` | 核验 claim 是否被证据支撑 | `claim_verdict_set.v1` |
| `editor` | 整理最终发布内容，目前不作为普通聊天角色 | `published_answer.v1` |

### 3.3 DelegateAction：主 AgentLoop 发起委派的动作

主 Agent 每次模型输出只能是一个 action。委派时结构类似：

```json
{
  "type": "delegate",
  "role": "skeptic",
  "task": "读取委派上下文，指出这份简历项目表述中证据不足和容易被面试追问的点。",
  "input_artifact_refs": ["main_chat_context:68d49244eeda"]
}
```

关键点：

- `role` 只能来自 `SkillManifest.allowed_subagents`。
- `task` 必须是给子智能体的具体任务 brief，不应该只是复述用户问题。
- `input_artifact_refs` 只能读 Runtime 显式委派的上下文 artifact。
- 如果模型省略 `input_artifact_refs`，Runtime 会自动绑定默认 artifact。

### 3.4 TaskBrief：Runtime 交给子智能体的任务单

文件：`runtime/task_brief.py`

`TaskBrief` 是父循环到子循环的稳定契约：

```text
task_id
parent_run_id
parent_span_id
role
task
input_artifact_refs
output_schema_name
max_steps
max_model_calls
timeout_seconds
```

你面试时可以这样讲：

> 主 Agent 不能直接把一大段上下文丢给子智能体，而是通过 TaskBrief 把任务、可读 artifact、输出 schema 和预算上限一起传过去，Runtime 负责把这个任务变成隔离的子循环。

### 3.5 SubagentResult：子智能体返回给主循环的公开结果

文件：`runtime/task_brief.py`

关键字段：

```text
task_id
parent_run_id
role
role_version
output
stop_reason
context_manifest_id
parent_span_id
span_id
artifact_refs
verifier_gate_audit_refs
```

注意：

- `output` 必须是 JSON-compatible。
- 禁止出现 `analysis`、`thinking`、`reasoning`、`chain-of-thought` 这类隐藏推理字段。
- 成功结果可以生成 `DelegationEvidence`，用于证明“这个子任务确实执行过并完成”。

## 4. 输出 schema 怎么设置

文件：`runtime/subagent_outputs.py`

当前输出全部是 Pydantic 严格 schema，`extra="forbid"`，多余字段会被拒绝。

### collector

```text
CollectorOutput
└── evidence: tuple[EvidenceItem]
    ├── source_ref
    ├── summary
    └── source_url
```

### analyst

```text
AnalystOutput
└── claims: tuple[ClaimItem]
    ├── claim_ref
    ├── statement
    ├── evidence_refs
    └── confidence: 0.0 - 1.0
```

### skeptic

```text
SkepticOutput
├── counterexamples: tuple[CounterexampleItem]
│   ├── claim_ref
│   ├── issue
│   └── evidence_refs
└── notes: tuple[str]
```

### verifier

```text
VerifierOutput
└── verdicts: tuple[VerdictItem]
    ├── claim_ref
    ├── decision: pass / fail
    ├── evidence_refs
    └── reason
```

## 5. 工具和权限怎么控制

Subagent 工具边界由两层求交决定：

```text
role.allowed_tools ∩ caller_skill.allowed_tools
```

也就是说：

- 角色自己允许用某工具，不代表真的能用。
- caller skill 没开放，子智能体也不能用。
- 普通聊天里的子智能体不能写长期记忆，也不能发布报告。
- 当前主要是逻辑沙箱：artifact 范围、工具范围、输出范围被 Runtime 控制；不是 OS/container 级沙箱。

当前普通聊天的 caller skill 是 `general_chat`，允许子智能体角色：

```text
collector / analyst / skeptic / verifier
```

## 6. Trace 里应该怎么看

一次触发 subagent 的普通对话，至少看这些步骤：

| step_type | 含义 |
|---|---|
| `subagent_plan` | Runtime 在 observe 阶段生成 recommended delegation plan |
| `model_call` | 主 AgentLoop 输出 delegate action，或子智能体生成结构化结果 |
| `subagent_context` | 子智能体 ContextManifest |
| `subagent_delegate` | 子智能体执行完成，包含 role、task_id、output_schema、output |
| `final_answer` | 主 Agent 综合子智能体 observation 后输出最终回答 |

你看 Trace 时重点确认：

- 是否选对 `selected_skill_id`。
- 是否出现 `subagent_plan`。
- `delegate_roles` 是否符合用户意图。
- 每个子智能体是否有自己的 `context_manifest_id`。
- `output_schema` 是否符合角色。
- `tool_calls` 里是否只有允许工具。
- 最终回答是否吸收了子智能体结果，而不是孤立地列执行过程。

## 7. 今日真实 case 验收结果

运行命令：

```powershell
py -3 scripts\run_day4_subagent_cases.py --direct
```

最新结果：

```text
status: passed
report: D:\AAAcode\code-code\agent+\harness\policy_impact\data\acceptance\latest-day4-subagent-runtime-cases.json
```

| Case | 用户问题类型 | 预期 | 实际 | 耗时 |
|---|---|---|---|---|
| D4-C1 | 简单介绍 | 不触发 subagent | 0 次委派 | 2960.4 ms |
| D4-C2 | 分析 + 质疑 | `skeptic` + `analyst` | 2 次委派，通过 | 18107.9 ms |
| D4-C3 | 证据链 + 核验 | `verifier` + `collector` | 2 次委派，通过 | 21834.3 ms |
| D4-C4 | `/report` workflow 对照 | 走 `external_impact_report`，不触发普通聊天 subagent | workflow 通过 | 16751.8 ms |

本轮验证说明：

- 简单问答不会乱启动子智能体。
- 多角度问题能稳定启动多个子智能体。
- 证据链/核验类问题能稳定启动证据收集和验证角色。
- `/report` 仍是固定 workflow，不和普通聊天 subagent 混在一起。

## 8. 今天你要学到什么程度

今天不需要把所有代码背下来。你只要能完成这 4 件事：

1. 说清楚 `SubagentManifest` 和 `SubagentRoleDefinition` 的区别。
2. 说清楚 `DelegateAction -> TaskBrief -> SubagentResult` 三个 schema 分别在什么阶段出现。
3. 打开一轮 Trace，找到 `subagent_plan / subagent_context / subagent_delegate`，并解释每一步为什么存在。
4. 能用一句话讲出这套设计的价值：

```text
Subagent 不是让模型随便创建角色，而是主 AgentLoop 里的受控委派能力；Runtime 用 manifest、task brief、tool gateway、context manifest 和 output schema 把它约束成可审计、可复盘的执行单元。
```

