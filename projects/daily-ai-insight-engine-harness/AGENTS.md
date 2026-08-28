# AGENTS.md

这个文件是给 Claude Code / Codex / Cursor 等 AI 编程助手看的项目级常驻上下文。
它约束"怎么改这个仓库"，不是日报运行时给新闻分析模型看的业务 prompt。

## 项目简介

Daily AI Insight Engine：从每日新闻信息中提取结构化洞察，生成可读的 Markdown 分析报告和 HTML 可视化结果。

## 两层 Harness 边界

1. **Coding Agent Harness**：约束 AI 编程助手如何修改仓库。对应：`AGENTS.md`、`feature_list.json`、`progress.json`、`scripts/harness_linter.py`、`.github/workflows/harness.yml`。
2. **Runtime Agent Harness**：约束运行时 Agent 如何分阶段处理数据。对应：`state.py`、`graph.py`、`stage_gates.py`、`hooks/`、`context_router.py`、`tool_gateway.py`。

## 快速导航

| 你想做什么 | 去哪里看 |
|-----------|---------|
| 了解系统架构和分层设计 | docs/architecture/overview.md |
| 了解模块边界和依赖规则 | docs/architecture/boundaries.md |
| 了解数据流转过程 | docs/architecture/data-flow.md |
| 了解编码规范 | docs/conventions/README.md |
| 了解当前迭代任务 | docs/plans/current-sprint.md |
| 了解 API 规范和错误码 | docs/reference/api-spec.yaml、docs/reference/error-codes.md |
| 了解测试规范 | docs/conventions/testing.md |
| 了解 Agent 工作流和 Hook 系统 | CLAUDE.md |

## 项目文件纲要

关键路径速查：`src/insight_engine/{conversation,harness,stages,linters,skill_executors}/`、`tests/`、`scripts/`、`docs/{architecture,conventions,design,plans,reference}/`、`config/`、`prompts/`、`skills/`。完整目录见 `CLAUDE.md`。

## 硬性规则（CI 会验证）

1. 依赖方向：conversation/ → skill_executors/ → harness/ → stages/ → linters/
2. 横切关注点（hook/linter/tool_gateway）只能通过 harness/ 的注册机制注入
3. 单文件不超过 300 行
4. 新增 stage 必须有对应的 linter
5. 确定性 stage（collect_raw_items、clean_items）不允许调用 LLM
6. 修改目录结构时必须同步更新本文件

## Stage 规则

1. `collect_raw_items` — 确定性数据抓取，不允许 LLM
2. `clean_items` — 确定性清洗，不允许 LLM
3. `structure_events` — LLM 单次 Chat + repair + fallback
4. `analyze_insights` — 受限 ReAct（5 步固定计划，action 白名单化）
5. `generate_report` — 确定性模板渲染（仅标题翻译调 LLM）

产物链路：`raw_items → cleaned_items → structured_events → analysis_result → report/chart → run_artifact`

## 修改约束

1. 修改目录结构时，必须同步更新本文件
2. 新增 stage 时，必须同步更新 `state.py`、`graph.py`、`stage_gates.py`、测试
3. 新增 LLM stage 时，必须说明 prompt、skill、校验方式、fallback 策略
4. 新增工具必须经过 `tool_gateway.py`
5. 修改后必须运行：`python3 scripts/harness_linter.py` → `python3 -m pytest`
6. 每次运行必须生成 run_artifact 作为审计产物

## 提交规范

- feat: 新功能 / fix: 修复 / refactor: 重构 / docs: 文档 / test: 测试 / chore: 工程

## 验证命令

```bash
bash scripts/agent-guardrails.sh    # 一键全部检查（编译 + lint + 测试 + 文件大小）
bash scripts/agent-verify.sh        # git worktree 隔离验证
bash scripts/pre-commit             # 手动运行 pre-commit 检查
bash scripts/install-hooks.sh       # 安装 git hooks
python3 -m compileall src scripts tests  # 仅编译检查
python3 scripts/harness_linter.py   # 仅 Harness 静态检查
python3 -m pytest                   # 仅单元测试
```
