# Agent Workbench 全功能验收记录

生成时间：2026-08-03

## 本次目标

搭建一套覆盖 Agent Workbench 设计能力的自动化测试集，并使用真实 `deepseek-v4-flash` 跑完整验收。验收重点不是“模型能不能回答”，而是每轮运行是否符合项目设计里的四个核心目标：

- 可控：模型、Skill、工具、上下文、门控边界都可约束。
- 可审计：每次运行都有 AgentRun、AgentStep、tool calls、gate result、context summary。
- 可复盘：可以通过 run_id 查询完整 trace，定位路由、上下文、工具、门控、产物。
- 可回归：修改代码后可以重复跑测试集，发现行为漂移。

## 新增测试集

脚本：

`scripts/run_workbench_full_capability_acceptance.py`

最新结果：

`data/acceptance/latest_workbench_full_capability_acceptance.json`

本次完整报告：

`data/acceptance/workbench_full_capability_acceptance_20260803_162506.json`

覆盖能力轴：

- `model_runtime`
- `session_contract`
- `skill_registry`
- `slash_commands`
- `general_chat`
- `context_manifest`
- `memory_read`
- `memory_write`
- `tool_gateway`
- `permission_boundary`
- `gate`
- `stop_hook`
- `research_artifact`
- `wiki_blueprint`
- `policy_workflow`
- `news_workflow`
- `subject_scope_gate`
- `multi_turn`
- `frontend_trace`

## 测试用例分层

L1 基础契约：

- FC01：模型运行时只暴露真实 `deepseek-v4-flash`，且鉴权可用。
- FC02：SkillRegistry 和 `/auto`、`/chat`、`/policy`、`/news`、`/research`、`/wiki` 斜杠命令完整。
- FC03：会话创建、消息持久化、latest trace、run trace 查询可用。
- FC04：不存在模型不能执行。
- FC05：不存在 skill 不能绑定。

L2 Harness 主链路：

- FC06：普通对话有 AgentRun、AgentStep、ContextManifest、MemoryRead、ToolGateway、Gate、StopHook。
- FC07：`/chat` 强制普通对话，不误路由成政策报告。
- FC08：明确“请记住”时写入长期 Memory。
- FC09：普通表达不会隐式写 Memory。
- FC16：指定 `run_id` 可以查询完整 trace。

L3 业务 Skill：

- FC10：`/research` 生成调研报告 Artifact。
- FC11：`/wiki` 生成知识库规划 Artifact。
- FC12：`/policy` 运行政策影响 Subagent Workflow。
- FC13：`/news` 运行新闻影响 Workflow。
- FC14：新闻影响分析缺少对象画像时触发 subject scope gate。
- FC15：Auto Router 能识别政策合规问题并路由到 policy workflow。
- FC18：实际工具调用必须是 SkillManifest 允许工具的子集。
- FC19：ContextManifest 的 visible fields、hidden fields、token budget 可审计。

L4 复杂交互：

- FC17：多轮上下文和跨会话 Memory 读写闭环。
- FC20：前端展示 trace、slash commands、模型选择、布局稳定性。

## 本次真实运行结果

运行服务：

`http://127.0.0.1:8502`

完整验收：

- 总 case：20
- 通过：20
- 失败：0
- 平均端到端耗时：5537.40 ms
- 最大端到端耗时：15781.15 ms
- 真实模型调用次数：15
- 模型总延迟均值：4222.51 ms
- 模型总延迟最大值：7651.65 ms

旧对话矩阵回归：

- 总 case：12
- 通过：12
- 失败：0
- 报告：`data/acceptance/workbench_api_dialogue_matrix_20260803_162910.json`

前端 CDP 验收：

- 通过：true
- 截图：`data/acceptance/screenshots/workbench-ui-check-20260803082812.png`
- 检查点：聊天记录、Slash 命令、Trace 卡片、Evidence Grid、模型选择器、中文文本、无页面级横向溢出、侧边栏不重叠、composer 内模型选择均通过。

项目级回归：

- `py -3 -m pytest -q`：530 passed
- `py -3 scripts/harness_linter.py`：HK001-HK009 passed

## 本次发现并修复的问题

1. 未知 skill 创建会话返回 500

原问题：

创建 session 时如果传入不存在的 `active_skill_id`，底层抛出 `KeyError`，API 未捕获，最终变成 500。

修复：

`src/policy_impact/app/api.py`

在 `create_workbench_session` 中捕获 `KeyError`，映射为 400 Bad Request。

对应测试：

`tests/test_chat_workbench_service.py::test_workbench_api_create_session_maps_unknown_skill_to_bad_request`

2. policy workflow 实际调用 `memory_search`，但 SkillManifest 未声明

原问题：

政策分析 workflow 会读取长期偏好 Memory，trace 里会出现 `memory_search`，但 `policy_weekly_impact.allowed_tools` 没有声明 `memory_search`，导致工具边界验收失败。

修复：

`src/policy_impact/skills/registry.py`

将 `memory_search` 加入 `policy_weekly_impact.allowed_tools`。

对应测试：

`tests/test_skill_registry.py::test_default_manifest_tools_and_subagents_preserve_current_contracts`

## 当前判断

当前项目已经具备一套比较完整的验收闭环：不是只靠页面点一点，而是能用真实模型、真实 API、真实前端检查去验证 Harness 的核心设计。接下来学习项目时，建议按 FC01 到 FC20 的顺序看，每个 case 对应一个能力点，看完 case 再去 trace 里找证据。
