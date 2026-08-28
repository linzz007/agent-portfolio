# Agent Workbench 审计记录 2026-07-02

本次审计目标：按 `09_agent_workbench_chat_system_design.md` 和
`10_agent_workbench_implementation_plan.md` 的验收点，检查 `policy_impact`
是否已经从政策 workflow 工作台升级为单用户 Agent Workbench，并用伪造数据实际跑通。

## 结论

当前项目已经具备可演示的 Agent Workbench 形态：

- 页面打开后可以聊天、切换会话、选择模型和 skill。
- 每轮对话会生成 `AgentRun` 和 `AgentStep`，右侧 Trace 可以看到 skill selection、context build、tool/model call、gate、memory、artifact、final answer。
- `policy_weekly_impact`、`recent_news_report`、`research_report`、`general_chat`、`company_wiki_blueprint` 均已作为 skill 暴露。
- 使用伪造公司、伪造新闻、伪造政策、伪造 memory 跑通了 chat、research、news、policy 四条路径。
- 后端门禁通过：compileall、harness_linter、pytest、policy workflow、gold eval、replay、workbench smoke。

## 本次修复

| 问题 | 影响 | 修复 |
| --- | --- | --- |
| 默认模型缺少 `mock-scripted` | 文档要求本地演示/TDD/replay 模型边界可见，页面无法展示完整模型层 | 在 `PolicyMemoryStore.seed_model_configs()` 中增加 `mock-scripted` |
| `search_memory()` 返回的 `metadata` 是 JSON 字符串 | `report_chat`、news/research 等调用方按 dict 读取 `.get()` 时可能报错 | 在 `search_memory()` 返回前解码 `metadata` |
| `research_report` 写了 artifact 但 trace 里没有 memory 写入 | 文档要求 memory read/write 可见，面试展示时证据链不完整 | `research_report` 生成报告后写入 `skill_result` memory，并增加 `memory_write` step |
| 缺少伪造数据端到端 smoke | 只跑单测不能证明页面背后的真实 skill 路径能闭环 | 新增 `scripts/run_workbench_smoke.py`，临时造数据并跑四条路径 |

说明：早期设计文档里出现过 `local-deterministic`，当前实现统一采用
`deterministic-local` 作为本地确定性模型 ID，另外提供 `mock-scripted` 和
`openai-compatible`。

## 验收项对照

| 文档验收项 | 当前状态 | 证据 |
| --- | --- | --- |
| 单用户聊天页面 | 通过 | Streamlit 标题为 `Agent Workbench`，页面包含聊天输入框 |
| 会话列表 | 通过 | 侧栏显示会话 radio，消息发送后会话 message count 更新 |
| 模型选择 | 通过 | 页面下拉显示 `deterministic-local`、`mock-scripted`、`openai-compatible` |
| Skill 选择 | 通过 | 页面下拉显示五个 core skills |
| 普通聊天使用 memory/company facts | 通过 | `general_chat` 路径包含 `memory_read` 和 `tool_call`，输出引用 wiki/memory |
| workflow skill 可从聊天触发 | 通过 | `policy_weekly_impact`、`recent_news_report` 均由 workbench session 触发 |
| research report 生成 artifact | 通过 | `research_report` 生成 markdown report artifact |
| Tool policy / gate 可见 | 通过 | `tool_call` step 带 tool calls 和 allow gate；workflow skill 有 `gate_check` |
| context manifest 可见 | 通过 | 每轮有 `context_build` step，输出 `stage_name=chat_turn` |
| memory read/write 可见 | 通过 | `memory_read` step 可见；`research_report` 现在有 `memory_write` step |
| artifact 可见 | 通过 | assistant message metadata 和 trace 中均记录 artifact path |
| trace persistence | 通过 | SQLite `agent_runs`、`agent_steps` 持久化，每轮可通过 `latest_trace()` 读取 |
| replay | 通过 | `scripts/run_replay.py` 返回 `status=replayed` |
| API/service 层 | 通过 | `ChatWorkbenchService` 提供 sessions、messages、models、skills、trace 方法 |
| 测试覆盖 | 通过 | `90 passed`，覆盖 store、service、skill registry、runtime、workflow、eval、replay |

## 伪造数据试用

新增脚本：

```powershell
py -3 scripts\run_workbench_smoke.py
```

脚本会在临时目录构造：

- `company_smoke` 公司画像
- wiki facts：Agent Harness、ToolGateway、ContextManifest、Gate、Replay、Memory、artifact trace
- news jsonl：agent observability、tool policy
- policy markdown：agent trace/governance policy
- manual memory：用户偏好 trace/gate/memory/artifact evidence

覆盖路径：

- `general_chat`
- `research_report`
- `recent_news_report`
- `policy_weekly_impact`

最后一次运行结果：

```json
{
  "status": "passed",
  "models": ["deterministic-local", "mock-scripted", "openai-compatible"],
  "skills": [
    "general_chat",
    "policy_weekly_impact",
    "recent_news_report",
    "research_report",
    "company_wiki_blueprint"
  ]
}
```

## 最终验证命令

```powershell
py -3 -m compileall -q src scripts tests
py -3 scripts\harness_linter.py
py -3 -m pytest -q
py -3 run_policy_impact.py
py -3 scripts\run_eval.py --suite gold
py -3 scripts\run_replay.py
py -3 scripts\run_workbench_smoke.py
```

结果摘要：

- compileall：通过
- harness_linter：`policy_impact harness lint passed`
- pytest：`90 passed`
- policy workflow：`status=done, policies=2, assessments=3`
- gold eval：`passed=true, score=100`
- replay：`status=replayed`
- workbench smoke：`status=passed`

## 页面烟测

浏览器打开：

```text
http://localhost:8501/
```

已验证：

- 页面标题：`Agent Workbench`
- 侧栏模型下拉包含三档模型
- 侧栏 skill 下拉包含五个 core skills
- 页面能发送 research 消息
- assistant 返回 `Research report generated`
- 右侧 Trace 显示 `research_report`
- 右侧 Trace 显示 `Write research report artifact`
- 右侧 Trace 显示 `Store research report memory`

当前本地 Streamlit 服务已重启并运行在：

```text
http://localhost:8501/
```

## 仍需注意

- 真实模型 `openai-compatible` 目前只是配置边界，默认演示仍走本地确定性逻辑。
- `recent_news_report` 和 `policy_weekly_impact` 的底层 workflow 会写 memory/artifact，但 workbench 顶层 trace 主要展示 workflow 汇总 step；如果后续要展示每个 stage 的细粒度 memory 写入，可以把 workflow stage trace 进一步映射到 `AgentStep`。
- 旧数据里已经有一些标题相同的 `Policy` 会话，功能不受影响，但后续可以加“归档会话”或“按日期折叠”改善页面浏览体验。
