# 插件化边界：执行语义与配置解耦

## 1. 这次改造解决什么问题

改造前，项目虽然已经有 Runtime / Skill / Tool / Subagent / Gate 等结构，但很多配置还散落在 Python 代码里：

- Skill 的工具白名单和预算写在 `skills/registry.py`
- Subagent 的角色描述、use_when、工具边界和预算写在 Python 类构造里
- Tool 名称到函数的绑定写在 `mcp_server/registry.py`
- 普通对话和报告类 Prompt 有不少长文本写在 `prompt_builder.py`

这会导致一个问题：你学习 Runtime 时，会被业务知识、Prompt 文案、工具配置、角色说明混在一起干扰。

这次改造的目标是：

```text
Runtime 负责执行语义
Plugin 配置负责业务能力描述
```

也就是使用方式不变，但代码维护边界更清楚。

## 2. 当前插件目录

```text
plugins/
  skills/
    manifest.json
  subagents/
    roles.json
    manifests.json
  tools/
    registry.json
  prompts/
    general_chat_system.md
    conversation_summary_system.md
    research_report_system.md
```

## 3. Runtime 仍然负责什么

Runtime 仍在 `src/policy_impact/` 里，负责不可随意变动的执行语义：

| 模块 | 职责 |
|---|---|
| `harness/agent_runtime.py` | 统一回合入口、skill 路由、上下文构建、执行协调、Trace 写入 |
| `harness/agent_loop.py` | ReAct / action loop，处理 final、tool_call、delegate、request_approval |
| `harness/subagents.py` | 子智能体隔离执行、TaskBrief、SubagentResult、artifact scope |
| `harness/tool_gateway.py` | 工具统一入口和审计 |
| `harness/permissions.py` | deny -> ask -> allow 权限判断 |
| `runtime/gates.py` / `gate_catalog.py` | Gate 执行与硬约束 schema |
| `harness/context_manifest.py` | 上下文可见字段、隐藏字段、token budget、checksum |
| `memory/store.py` | SQLite 持久化、AgentRun、AgentStep、Memory、Wiki 索引 |

你学习 Runtime 时，重点看这些文件里的“怎么执行、怎么拦截、怎么记录”，不要被具体业务词带偏。

## 4. Plugin 配置负责什么

### 4.1 Skill 配置

文件：

```text
plugins/skills/manifest.json
```

这里定义：

- skill id / name / description
- execution_mode
- executor_id
- input_schema / output_schema
- allowed_tools
- allowed_subagents
- context_policy_id
- memory_policy_id
- permission_policy_id
- max_steps / max_model_calls / timeout_seconds

这意味着：新增一个业务能力时，第一眼应该先看这个文件，而不是先去 Runtime 里找 if-else。

### 4.2 Subagent 配置

文件：

```text
plugins/subagents/roles.json
plugins/subagents/manifests.json
```

`roles.json` 是 Runtime 执行契约：

- role
- version
- prompt_template_id
- output_schema_name
- output_schema
- allowed_tools
- max_steps / max_model_calls / timeout_seconds
- can_publish

`manifests.json` 是主 Agent 可见的角色菜单：

- name
- role
- description
- use_when
- avoid_when
- disallowed_tools
- context_policy_id
- memory_scope

区别：

```text
roles.json      = Runtime 真实执行边界
manifests.json  = 主 Agent 看到的 delegation roster
```

### 4.3 Tool 配置

文件：

```text
plugins/tools/registry.json
```

这里定义工具名到 Python 函数路径的绑定。

例如：

```json
{
  "name": "company_wiki_search",
  "callable": "policy_impact.mcp_server.tools_company:company_wiki_search"
}
```

注意：工具函数还是 Python 实现，因为这是能力实现；插件配置只负责“注册和暴露”。

### 4.4 Prompt 配置

文件：

```text
plugins/prompts/general_chat_system.md
plugins/prompts/conversation_summary_system.md
plugins/prompts/research_report_system.md
```

`prompt_builder.py` 现在主要负责拼装上下文和 prompt contract，不再直接塞一大段系统提示词。

## 5. 当前还没有完全插件化的部分

这次是第一阶段，不是完全体。

仍然有一些业务逻辑在 `AgentRuntime` 中：

- `/report` 内部路线：source_inventory / policy / news / research
- 新闻影响报告的部分话术
- 政策 follow-up 和部分示例公司/iFinD 相关防幻觉回答
- 一些 gate reason / answer repair 文案

这些以后可以继续抽到：

```text
plugins/skills/external_impact_report/routes.json
plugins/skills/external_impact_report/report_template.md
plugins/policies/context_policy.json
plugins/policies/tool_policy.json
plugins/gates/publication_gate.json
```

但现在先不动这些，是为了保证使用方式和核心测试稳定。

## 6. 面试讲法

可以这样讲：

> 我把 Agent Runtime 和业务配置做了解耦。Runtime 只负责 loop、context、permission、gate、trace、artifact、subagent delegation 等执行语义；Skill、Subagent、Tool Registry 和 Prompt 通过 plugins 目录下的 manifest 接入。这样新增或调整业务能力时，不需要修改核心 Runtime，只需要调整 manifest 和 prompt，并且所有工具、角色、预算和输出 schema 都能被 Trace 和测试验证。

更短一点：

> 这个项目不是把业务流程写死在代码里，而是把执行内核和插件配置拆开：代码层学 Harness 语义，配置层维护业务能力。

