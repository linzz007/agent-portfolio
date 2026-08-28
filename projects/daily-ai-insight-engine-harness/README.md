# Daily AI Insight Engine Harness

面向 AI 新闻与舆情日报生成场景的 Agent Harness 项目。系统从 RSS/API 等公开来源抓取新闻，经过数据清洗、事件结构化、洞察分析和报告生成，输出 Markdown / HTML 报告与运行留痕。

## Highlights

- **Coding Agent Harness**：通过 `AGENTS.md`、`CLAUDE.md`、`feature_list.json`、`progress.json` 和 harness linter 约束 AI 编程助手的修改边界。
- **Runtime Agent Harness**：用 State Contract、Stage Graph、Hook、Stage Gate 和 Artifact 约束运行时 Agent 的阶段输入输出。
- **Stage Pipeline**：将 collect、clean、structure、analyze、report 拆成可测试阶段，确定性步骤和 LLM 步骤分离。
- **Tool Gateway**：对外部工具调用做白名单控制，避免 LLM 直接越权访问未声明工具。
- **Context Router**：按 stage 构造可见上下文，减少把完整状态无差别塞给模型导致的噪声。
- **Fallback & Validation**：对 LLM JSON 输出、报告格式和阶段产物做校验，失败时记录错误并进入可解释兜底。

## Architecture

```text
User Request
  -> Conversation Router
  -> Skill Executor
  -> Stage Graph
  -> Stage Runner
  -> Hook / Linter / Gate
  -> Report Artifact
```

## Quick Start

```powershell
py -3 -m pytest
py -3 scripts/harness_linter.py
py -3 run_chat.py "帮我生成今日 AI 新闻分析报告"
```

模型密钥请通过本地 `.env` 或环境变量配置，不要提交到仓库。

## Repository Scope

这是脱敏后的作品集版本，已移除真实 `.env`、历史 `data/` 运行状态、`outputs/` 报告产物和外部参考原文，只保留核心代码、测试、配置模板和 Harness 设计文档。
