# Subagent Harness 集中设计文档

这份文档只讲当前项目里的 Subagent 设计，不展开 `/report` workflow、普通 Skill、Memory 全局治理等内容。你学会这一份，就能把 Day 3-Day 4 的 Subagent 部分讲清楚。

## 1. 一句话定位

Subagent 不是按钮，也不是固定 workflow stage，而是 **Main AgentLoop 中的受控 delegation 能力**。

用户正常提问后，主 Agent 可以选择：

1. 直接 `final` 回答。
2. 返回 `delegate` action，让 Runtime 启动一个受控子智能体。
3. 收到子智能体 observation 后，再生成最终回答。

最终回答权始终在主 Agent，子智能体只负责完成一个隔离的小任务。

## 2. 两层触发：主动要求 + 自主判断

这里容易混。我们不是实现了两套 subagent，而是同一个 `spawn_subagent` 能力有两种触发信号。

### 2.1 用户主动要求触发

用户明确提出：

- “请用分析和质疑两个角度看”
- “帮我找反例”
- “用 verifier 核验证据”
- “让一个子智能体单独收集来源”

这时主 Agent 应该识别到用户明确需要多角色视角，然后返回 `delegate` action。

示例：

```json
{
  "type": "delegate",
  "role": "skeptic",
  "task": "读取委派上下文，指出当前结论中的证据缺口和可能被面试官追问的点。"
}
```

### 2.2 主 Agent 自主判断触发

用户没有直接说“调用子智能体”，但问题本身具备高风险或复杂度：

- 需要证据收集，主 Agent 当前上下文不足。
- 要给高影响建议，最好先让 skeptic 挑错。
- 结论需要绑定 evidence_ref，最好让 verifier 核验。
- 问题包含多目标、多约束、多角色视角。

这时主 Agent 可以自主返回 `delegate` action。

示例：

```text
用户：最近外部 Agent Harness 变化对我们 AI 产品有什么影响，哪些要立刻跟进？
```

主 Agent 可以判断：这是高影响建议，先委派 `collector` 收集证据，或委派 `skeptic` 做风险审查。

### 2.3 两层触发的共同点

无论是用户主动要求，还是主 Agent 自主判断，后面都走同一条 Runtime 链路：

```text
delegate action
-> Runtime 校验 role / scope / budget / permissions
-> 构建子智能体上下文包
-> SubagentRunner 执行
-> observation 回到 Main Agent
-> Main Agent 生成最终回答
```

所以面试时不要说“我有主动 subagent 和被动 subagent 两套系统”。更准确的说法是：

> 我的 Subagent 是 Main AgentLoop 里的同一套 delegate 能力，触发信号分为用户显式要求和主 Agent 基于任务复杂度/风险的自主判断。

## 3. 为什么不能任意角色任意思考

“让模型想创建什么角色就创建什么角色”看起来很灵活，但不适合 Harness。

问题有三个：

1. 角色漂移：今天叫专家，明天叫高级专家，Trace 和评测无法稳定。
2. 权限漂移：动态角色很难确定能用哪些工具、不能用哪些工具。
3. 输出漂移：每个角色输出结构不同，主 Agent 难以稳定消费。

所以本项目使用 `SubagentManifest`：

```text
Main Agent 只能从 agent_roster 中选择角色。
Runtime 根据 manifest 校验角色、工具、上下文和输出 schema。
```

当前内置角色：

| Role | 作用 | 适合触发场景 |
| --- | --- | --- |
| `collector` | 收集证据和来源 | 用户问来源、证据链、数据源 |
| `analyst` | 做结构化影响分析 | 用户问影响、方案、取舍 |
| `skeptic` | 找反例、风险、证据缺口 | 用户要求质疑，或高影响建议前 |
| `verifier` | 核验 claim 和 evidence 是否匹配 | 用户要求验证，或结论必须可证明 |

核心文件：

- `src/policy_impact/runtime/subagent_manifests.py`
- `src/policy_impact/runtime/subagent_roles.py`
- `src/policy_impact/skills/registry.py`

## 4. 运行前：主 Agent 看见什么

普通聊天进入 `general_chat` 后，Runtime 会构建主 Agent 的上下文。

主 Agent 能看到：

1. 当前用户问题。
2. 当前可见 Wiki / Memory / 历史摘要。
3. `agent_roster`：可委派的子智能体清单。
4. `delegation_policy`：什么时候该委派，什么时候不该委派。
5. `default_delegated_artifact_ref`：默认给子智能体读取的上下文包引用。

运行前的关键点：

```text
主 Agent 有决策权，但没有越权权。
它只能选择 Runtime 暴露的 role，不能临时编造新角色。
```

核心文件：

- `agent_runtime.py::_run_general_chat_agent_loop`
- `agent_runtime.py::_general_chat_loop_task_prompt`

## 5. 运行中：delegate 如何被执行

主 Agent 如果决定委派，会输出结构化 action：

```json
{
  "type": "delegate",
  "role": "skeptic",
  "task": "读取委派上下文，指出证据缺口。",
  "input_artifact_refs": ["main_chat_context:xxxx"]
}
```

然后 Runtime 做确定性校验：

1. role 是否存在。
2. role 是否在当前 SkillManifest.allowed_subagents 中。
3. artifact_ref 是否在当前任务作用域内。
4. max_steps、max_model_calls、timeout 是否有预算。
5. 工具权限是否满足 `SubagentManifest.tools ∩ SkillManifest.allowed_tools`。

通过后才启动 `SubagentRunner`。

运行中的关键点：

```text
模型负责提出 delegate，Runtime 负责决定能不能真的执行。
```

核心文件：

- `agent_loop.py::_run_delegate_action`
- `agent_runtime.py::_run_general_chat_delegate`
- `harness/subagents.py::SubagentRunner`

## 6. 子智能体上下文：为什么要隔离

子智能体不会继承完整父对话，而是读取一个 Runtime 构造的任务上下文包。

这个包叫：

```text
main_chat_context:<run_id>
```

子智能体第一步会被强制执行：

```json
{
  "type": "tool_call",
  "tool_name": "artifact_read",
  "arguments": {
    "artifact_ref": "main_chat_context:xxxx"
  }
}
```

这样做的原因：

1. 降低上下文污染。
2. 控制 token 成本。
3. 让子智能体“看到了什么”可以被 Trace 复盘。
4. 避免模型自己猜要读哪些资料。

运行中的关键点：

```text
子智能体不是拿完整父上下文，而是拿一个受控 ContextPack。
```

核心文件：

- `agent_runtime.py::_build_dynamic_subagent_context_artifact`
- `agent_runtime.py::_ForcedFirstActionModelAdapter`

## 7. 工具权限：逻辑沙箱，不是代码沙箱

当前项目没有任意代码执行需求，所以不做 OS/container 级沙箱。

当前实现的是逻辑沙箱：

1. 子智能体只能用 role 允许的工具。
2. 子智能体工具还必须在 caller skill 的 allowed_tools 中。
3. 子智能体不能写长期 Memory。
4. 子智能体不能直接发布报告。
5. 子智能体不能修改 Wiki。
6. 子智能体不能执行任意代码。

面试中要诚实讲：

> 当前是工具权限和任务作用域隔离，不是容器级任意代码执行沙箱。因为本项目当前业务不需要执行代码；未来如果加入代码执行，只能放进临时目录或容器。

核心文件：

- `harness/tool_gateway.py`
- `harness/permissions.py`
- `agent_runtime.py::_subagent_tool_gateway`

## 8. 运行后：结果如何回到主 Agent

子智能体不会直接回答用户。

它返回结构化结果，比如：

- `collector` 返回 evidence。
- `analyst` 返回 claims。
- `skeptic` 返回 counterexamples。
- `verifier` 返回 verdicts。

Runtime 把这些结果包装成 observation，回传给 Main AgentLoop。

然后主 Agent 基于：

1. 原始用户问题。
2. 可见上下文。
3. 子智能体 observation。

生成最终回答。

运行后的关键点：

```text
子智能体提供局部结论，主 Agent 负责整合和最终表达。
```

## 9. Trace：如何证明这不是空设计

一次动态 subagent 的 Trace 大致应该看到：

```text
model_call(delegate)     # 主 Agent 决定委派
subagent_plan            # Runtime 记录委派计划和 AgentManifest
subagent_context         # Runtime 记录子智能体上下文
model_call(tool_call)    # 子智能体强制 artifact_read
model_call(final)        # 子智能体生成结构化输出
subagent_delegate        # Runtime 记录子智能体结果
model_call(final)        # 主 Agent 整合最终回答
final_answer
run_stopped
```

你学习时最重要的是能回答：

1. 哪一步说明主 Agent 决定委派？
2. 哪一步说明 Runtime 校验了角色和权限？
3. 哪一步说明子智能体上下文被隔离？
4. 哪一步说明子智能体结果被回传？
5. 如果最终答案错了，应该从哪一步开始查？

核心文件：

- `agent_runtime.py::_record_general_chat_agent_loop`
- `agent_runtime.py::_record_general_chat_subagent_plan`
- `agent_runtime.py::_record_dynamic_subagent_child`
- `memory/store.py`

## 10. Day 3-Day 4 学习安排

### Day 3：学设计

目标：只理解体系，不深挖所有代码。

任务：

1. 读本文档第 1-9 节。
2. 看 `subagent_day3_design.svg`。
3. 对比四个角色 manifest。
4. 讲清楚主动触发和自主判断触发。

当日产出：

- 一张表：Role / use_when / tools / output_schema / 禁止事项。
- 一段 2 分钟表达。

验收标准：

> 你能讲清楚 Subagent 是 Main AgentLoop 中的受控委派能力。

### Day 4：跑 case + 看 Trace

目标：用实际运行结果把设计串起来。

任务：

1. 跑一个普通简单问题，确认不触发 subagent。
2. 跑一个“请用分析和质疑两个角度...”的问题，确认触发 `analyst` 和 `skeptic`。
3. 在前端 Trace 中找到 `model_call(delegate)`、`subagent_plan`、`subagent_context`、`subagent_delegate`。
4. 写一个坏案例复盘：如果答案错了，如何沿 run_id 查原因。

验收标准：

> 你能拿一条真实 Trace 讲清楚运行前、运行中、运行后的完整生命周期。

## 11. 面试表达

推荐表达：

> 我实现的 Subagent 不是固定 workflow stage，也不是用户点一个按钮启动，而是 Main AgentLoop 中的受控 delegation 能力。主 Agent 可以根据用户显式要求或任务复杂度自主返回 delegate action；Runtime 再根据 SubagentManifest、SkillManifest、ContextPack、ToolGateway、PermissionEngine 和预算限制进行确定性校验。子智能体只拿隔离上下文包，执行局部任务并返回 observation，最终回答仍由主 Agent 整合。所有过程都会落到 AgentStep，能复盘一次委派为什么发生、看了什么、用了什么工具、输出了什么。

不要这样讲：

> 我做了几个固定 prompt 的子智能体。

不要这样讲：

> 我的子智能体可以任意创建角色、自由思考。

不要这样讲：

> 我实现了生产级代码沙箱。

