# AI Stack Impact Workbench

面向 AI 工程师与研发/运营团队的外部变化影响分析 Agent Harness。系统把技术发布、政策规则、行业事件和新闻快照统一建模为外部事件，并结合企业/项目画像生成可追问、可审计、可沉淀的结构化分析报告。

## Highlights

- **统一 Agent Runtime**：普通对话、`/report` 分析流程和动态子智能体委派进入同一个运行入口。
- **Skill Manifest**：通过执行模式、工具权限、上下文策略、产物类型约束不同任务的能力边界。
- **Subagent Delegation**：支持 analyst、skeptic、verifier 等角色，使用 role-scoped context 和 tool gateway 做隔离。
- **Harness Governance**：记录 ContextManifest、ToolGateway、PermissionEngine、Gate、AgentRun / AgentStep 和 Artifact。
- **Evidence-first Report**：高风险结论需要绑定证据来源，证据不足时降级为观察项或待确认项。

## Architecture

```text
User Turn
  -> TurnCoordinator
  -> Skill Router / SkillManifest
  -> ContextManifest
  -> ToolGateway / PermissionEngine
  -> Agent Loop / Subagent Runner
  -> Gate / Evidence Check
  -> Artifact + Trace
```

## Quick Start

```powershell
py -3 -m pip install -e .
py -3 run_policy_api.py
```

打开 `http://127.0.0.1:8501/` 使用对话式 Workbench。模型密钥请通过环境变量配置，不要写入仓库。

## Repository Scope

这是脱敏后的作品集版本，已移除真实企业画像、运行日志、长期记忆数据库和本地模型凭据。`data/` 中只保留可公开样例和评测 fixture。
