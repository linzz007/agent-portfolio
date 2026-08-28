# 2026-08-05 `/report` 单入口与 Wiki 数据页改造验收

## 本轮目标

将 Workbench 从“多个显式 skill / 自动关键词路由”调整为更稳定的对话型产品形态：

- 普通输入默认进入 `general_chat`。
- 只有 `/report <要求>` 触发 `external_impact_report`。
- Wiki 不再作为 slash mode，改为独立资料页，支持浏览、编辑、写回 markdown 和同步 SQLite facts 索引。
- 报告流程继续保留 Context、Memory、Tool Policy、Gate、Trace 和 Artifact 证据。

## 设计调整

更新文档：

- `docs/workbench_unified_impact_design_2026-08-05.md`
- `docs/workbench_learning_guide_2026-08-04.md`
- `readme.md`

核心设计：

```text
普通输入 -> general_chat -> Memory/Wiki -> Trace -> Answer
/report -> external_impact_report -> source_inventory/policy/news/research -> Tool/Gate/Artifact -> Trace -> Answer
Wiki 页面 -> workbench/wiki API -> profile/pages/facts/stats -> 前端层级展示和编辑
Wiki 保存 -> workbench/wiki/pages API -> markdown 写回 -> FACT 重新解析 -> SQLite 索引重建
```

## 代码调整

### 路由

- `SkillRegistry.resolve_skill()` 不再根据普通消息关键词自动触发 `external_impact_report`。
- `AgentRuntime._parse_slash_command()` 新增 `/report`。
- 旧 `/impact`、`/policy`、`/news`、`/research`、`/wiki` 保留后端兼容。
- 前端发送消息时不再预先修改 session active skill，而是原文发送，让 Runtime 解析 `/report`。

### Wiki

- 新增 `ChatWorkbenchService.wiki_tree()`。
- 新增 `GET /companies/{company_id}/workbench/wiki`。
- 新增 `POST /companies/{company_id}/workbench/wiki/pages`。
- 前端新增 “对话 / Wiki” 页面切换。
- Wiki 页按 `path parts` 展示页面目录，右侧展示页面正文编辑器、facts、来源、重要性和可信度。
- Wiki 保存时校验路径必须位于 `data/companies/{company_id}/wiki/`，写回 markdown 后调用 `index_company_knowledge()` 同步 `wiki_pages` 和 `company_facts`。

### 前端

- Slash command 面板只展示 `/report`。
- `/` 快捷按钮直接插入 `/report`。
- 默认会话显示为“默认对话”。
- Wiki 页面隐藏 composer，不触发模型。
- 保持 100dvh 工作台布局，页面内部滚动。

## 验收标准

- 无斜杠消息必须选择 `general_chat`。
- `/report` 消息必须选择 `external_impact_report`。
- `/report` 后内部 route 能正确进入 `source_inventory`、`policy`、`news` 或 `research`。
- `/report` 只对当前轮生效，下一轮无斜杠必须回到 `general_chat`。
- Wiki API 必须返回 `workbench.wiki_tree.v1`、pages、facts 和 stats。
- Wiki 保存 API 必须返回 `workbench.wiki_update.v1`，并在临时项目根目录验证 markdown 与 SQLite facts 同步。
- 前端只能展示 1 个公开 slash command。
- Trace 仍必须包含 context、memory、tool、gate、artifact、structured events 等证据摘要。

## 实际验证结果

已运行并通过：

```powershell
node --check src\policy_impact\app\static\workbench\app.js
node --check scripts\run_workbench_ui_cdp_check.mjs
py -3 -m compileall -q src\policy_impact\app\api.py src\policy_impact\app\chat_workbench_service.py src\policy_impact\harness\agent_runtime.py src\policy_impact\skills\registry.py
py -3 -m pytest -q tests/test_skill_registry.py tests/test_chat_workbench_service.py tests/test_agent_runtime.py tests/test_turn_coordinator.py tests/test_full_capability_acceptance_suite.py
py -3 scripts\run_workbench_acceptance.py --skip-ui
py -3 scripts\run_workbench_dialogue_acceptance.py --skip-ui
py -3 -m pytest -q
$env:WORKBENCH_BASE_URL='http://127.0.0.1:8765'; py -3 scripts\run_workbench_full_capability_acceptance.py
```

结果摘要：

- 全量 pytest：535 passed，35 warnings。
- 重点 pytest：93 passed，35 warnings。
- `run_workbench_acceptance.py --skip-ui`：passed。
- `run_workbench_dialogue_acceptance.py --skip-ui`：passed。
- `run_workbench_full_capability_acceptance.py`：21 passed / 0 failed。
- Full-capability 性能：E2E mean 5297.13 ms，E2E max 13913.19 ms，model_call_count 14。

最新报告：

- `data/acceptance/latest-workbench-acceptance.json`
- `data/acceptance/latest-workbench-dialogue-acceptance.json`
- `data/acceptance/latest_workbench_full_capability_acceptance.json`

## 当前结论

本轮改造后，Workbench 的公开产品形态更清晰：

- 平时就是对话助手。
- 报告是显式重型工作流。
- Wiki 是可浏览、可维护的数据面。
- Harness 负责每轮运行的可控、可审计、可复盘和可回归。

面试时应强调 `/report` 是工具权限、上下文、门控和产物写入的显式边界，而不是一个简单 prompt 命令。
