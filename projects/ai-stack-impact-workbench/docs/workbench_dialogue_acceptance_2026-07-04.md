# Agent Workbench 动态对话验收记录

日期：2026-07-04

本次验收目标不是静态检查页面元素，而是模拟真实用户从简单到复杂地连续对话，并逐轮检查 Agent Workbench 是否符合项目设计：对话入口、模型选择、斜杠技能、Agent loop、context manifest、memory、artifact、trace、前端执行过程展示都要能跑通。

## 本次新增内容

新增脚本：

- `scripts/run_workbench_dialogue_acceptance.py`

最新验收产物：

- JSON 报告：`data/acceptance/latest-workbench-dialogue-acceptance.json`
- 前端截图：`data/acceptance/workbench-dialogue-2026-07-04-1783135530.png`

运行方式：

```powershell
py -3 scripts\run_workbench_dialogue_acceptance.py
```

如果只想验收后端对话和 trace，不检查前端浏览器渲染：

```powershell
py -3 scripts\run_workbench_dialogue_acceptance.py --skip-ui
```

## 验收用例

| 用例 | 难度 | 模拟用户行为 | 主要验收点 |
| --- | --- | --- | --- |
| D01 | 简单 | 单轮普通对话，询问 trace、memory、model selection 证据 | 自动保存 user/assistant；选择 `general_chat`；trace 包含 intent、skill、context、memory、tool、final answer |
| D02 | 简单到中等 | 添加并选择自定义 OpenAI-compatible 模型后对话 | model config 持久化；trace.model_id 保留用户选择 |
| D03 | 中等 | 输入 `/research ...` 生成调研报告 | 前端斜杠命令语义被模拟；切到 `research_report`；生成 report artifact；写入 skill_result memory |
| D04 | 中等 | 直接提政策/合规问题 | auto router 选择 `policy_weekly_impact`；workflow 产生 tool/gate/artifact trace |
| D05 | 中等 | 输入 `/wiki ...` 检查企业知识库缺口 | 切到 `company_wiki_blueprint`；返回结构化 wiki_blueprint artifact |
| D06 | 困难 | 同一会话三轮：普通对话 -> `/research` -> `/auto 最新新闻...` | 多轮消息顺序正确；skill 可切换；latest_trace 指向最后一轮；research 结果进入 memory |
| D07 | 困难 | 尝试设置不存在的 skill | `update_session` 确定性拒绝；不进入 runtime；不写 message 或 trace |
| F01 | 困难 | 通过 HTTP 创建真实 UI 会话并发送消息，再用 headless Chrome 渲染 | 页面有模型选择、模型管理、slash palette、thinking-card、step details；旧 `skill-dock`/`inspector` 不存在 |

## 逐轮 trace 验收标准

每一次 `send_message` 后都检查：

- `result.selected_skill_id` 等于预期 skill。
- `latest_trace.run_id` 等于本轮消息返回的 `run_id`。
- assistant 消息 metadata 里保存同一个 `run_id`。
- `agent_steps.step_index` 从 1 连续递增。
- 所有 step 状态都是 `done`。
- `context_build.output_payload.context_manifest` 存在，并包含可见字段或 token budget 证据。
- `final_answer` 是最后一个 step。
- 如果 skill 应生成产物，则 artifact key 存在，并且磁盘路径真实存在。
- 如果 skill 应写 memory，则后续 memory search 能检索到对应 `skill_id`。

## 本次发现并修复的问题

### 1. Auto router 顺序匹配导致新闻问题误判成政策问题

触发输入：

```text
/auto 最新新闻里和 agent observability、tool policy 相关的内容有什么影响？
```

旧行为：

- 因为 `policy` 关键词先命中，路由到 `policy_weekly_impact`。

修复：

- 将 `SkillRegistry.resolve_skill` 从“按规则顺序命中”改成“关键词计分 + tie breaker”。
- `最新/新闻/latest/news` 等强新闻信号会压过一个偶然出现的 `policy` 词。
- 新增单测覆盖混合意图：
  - `tests/test_skill_registry.py::test_resolve_skill_scores_mixed_intent_news_before_incidental_policy_word`

### 2. 前端 trace 自动滚动位置不符合预期

旧行为：

- 页面加载最新回答后直接滚到底部，导致 trace 卡片只露出后半段。

修复：

- `renderChat()` 中如果最新 assistant 消息带 trace，优先滚动到最新 `.thinking-card` 开头。
- 使用 `getBoundingClientRect()` 计算相对 `chat-stream` 的滚动偏移，避免被 topbar 或布局 offset 影响。

结果：

- 最新截图中能直接看到“执行过程”标题、metrics、前几个 step。
- 回答仍在 trace 下方，符合“思考/执行过程在 LLM 回答上方”的展示逻辑。

## 当前结论

本次动态验收通过。相比之前静态验收，现在已经覆盖了真实用户路径：

- 单轮对话能产生可审计 trace。
- 多轮对话能保持 session 和 latest trace。
- Slash command 能作为 skill 入口。
- Model config 能持久化并进入 trace。
- Memory read/write 能被验证。
- Artifact 能被真实写入并挂到 assistant message。
- 前端能渲染执行过程、step details、模型管理和技能命令。

这个验收脚本以后应该作为 Workbench 的主验收入口之一。只要改动 `AgentRuntime`、`SkillRegistry`、memory、artifact、前端 chat 渲染或模型配置，都应该跑它。
