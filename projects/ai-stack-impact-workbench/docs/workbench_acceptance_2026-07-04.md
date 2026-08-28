# Agent Workbench 验收记录 2026-07-04

## 本次目标

围绕当前项目设计思路，构造从简单到困难、从单轮到多轮的 Agent Workbench 验收用例，并实际运行检查：

- 前端是否符合当前产品形态：聊天为中心、配置进侧栏/弹窗、技能用斜杠命令、Trace 在回答上方。
- 后端是否符合 harness 设计：AgentRun、AgentStep、Skill 路由、Tool Policy、Context、Memory、Artifact 都有可验证证据。
- 运行中发现的 bug 或明显 UX 问题，直接修复并纳入验收门禁。

## 本次新增

- 新增验收脚本：`scripts/run_workbench_acceptance.py`
- 新增 UI 截图证据目录：`data/acceptance/`
- 最新截图证据：`data/acceptance/workbench-2026-07-04-1783098812.png`
- 修复前端侧栏裁剪问题：侧栏改为内部纵向滚动，整页仍保持固定工作台布局。

## 验收用例

| 用例 | 难度 | 场景 | 主要检查 |
| --- | --- | --- | --- |
| F01 | 简单 | 前端工作台壳契约 | 品牌、模型管理、模型选择、slash 命令、无旧 Skill 卡片、无右侧观测栏、固定 100dvh 布局、侧栏可内部滚动 |
| B01 | 简单 | 模型添加和选择 | 添加开放接口兼容模型、API Key 本地 metadata 保存、会话模型选择持久化 |
| B02 | 简单到中等 | 单轮普通对话 | general_chat、memory_read、企业知识库 tool_call、assistant 消息挂 trace |
| B03 | 中等 | 单轮调研报告 | research_report、model_call、artifact_write、memory_write、报告文件存在 |
| B04 | 中等 | 自动政策路由 | auto mode 根据政策/合规关键词路由到 policy_weekly_impact，产生 tool/gate/artifact |
| B05 | 困难 | 多轮技能切换 | 同一会话连续普通对话 -> research -> news，消息顺序正确，latest_trace 指向最后一轮，中间 research 结果进入 memory |
| F02 | 中等 | 前端真实渲染 | HTTP 页面可访问，headless Chrome 生成 1186x742 截图，页面不含旧右侧观测栏和旧技能卡片 |

## 运行结果

命令：

```powershell
py -3 scripts\run_workbench_acceptance.py
```

结果：

- 总状态：passed
- 用例数：7
- 截图大小：122882 bytes
- 前端 URL：`http://127.0.0.1:8501/`
- 结构化结果：`data/acceptance/latest-workbench-acceptance.json`

说明：PowerShell 终端可能把脚本输出中的中文显示成乱码，这是 Windows 控制台编码问题；脚本实际运行和 Markdown 文件均为 UTF-8。

## 本次发现并修复

| 问题 | 影响 | 修复 |
| --- | --- | --- |
| 侧栏在 1186x742 高度下底部内容被裁剪 | 当前工具边界/后续会话区可能只露出一部分，视觉像未完成 demo | `.sidebar` 改为 `overflow-y: auto`，保留整页 `100dvh` 固定布局 |
| 验收脚本调用 Chrome 时 stderr 可能触发 GBK 解码错误 | Windows 下 headless UI 验收不稳定 | Chrome 子进程改为二进制捕获，错误输出用 UTF-8 replace 解码 |
| Chrome 截图路径和落盘检查不够稳 | 偶发误判截图不存在或太小 | 截图路径改为绝对路径，并加入等待循环 |

## 当前前端结论

当前前端已经从 Streamlit/demo 风格转为更接近产品工作台：

- 主区域聚焦对话，不再把 Skill 卡片和运行面板堆在页面中间。
- 左侧承载会话、模型、技能命令、工具边界，符合 agent workbench 的控制台形态。
- 模型管理是独立弹窗，不再只是一个下拉旁边的配置按钮。
- Skill 入口改为 slash command，更接近 Claude/Codex 类交互。
- Trace 保留在 assistant 回答上方，既不占右侧空间，也能展示 harness 证据链。

谨慎结论：当前达到“面试演示可接受的产品级工作台”标准；如果要接近真正大厂设计系统，还需要继续做组件规范、动效状态、空状态、错误态、可访问性和移动端细节。

## 后续验收标准

每次改 Workbench，至少跑：

```powershell
py -3 scripts\run_workbench_acceptance.py
py -3 -m pytest -q
py -3 scripts\harness_linter.py
node --check src\policy_impact\app\static\workbench\app.js
```

必须通过以下标准：

- 前端不能重新出现旧 `skill-dock` 中心技能卡片。
- 前端不能重新出现右侧 `inspector` 运行观测栏。
- 全局页面不能上下滑出空白；滚动应限定在聊天区或侧栏内部。
- 模型添加后必须能被会话选择并持久化。
- slash 命令必须包含 `/auto`、`/chat`、`/policy`、`/news`、`/research`、`/wiki`。
- 单轮普通对话必须有 `memory_read` 和 `tool_call`。
- research 必须有 `model_call`、`artifact_write`、`memory_write`。
- 自动政策路由必须产生 `tool_call`、`gate_check`、`artifact_write`。
- 多轮对话必须保持 user/assistant 顺序，latest trace 必须指向最后一轮。
- UI 截图必须能由 headless Chrome 生成，文件大小超过 40KB。
