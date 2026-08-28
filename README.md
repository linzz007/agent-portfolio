# Agent Portfolio

这是一个面向 Agent / LLM 应用工程 / Agent Runtime / Agent Harness 岗位的作品集仓库，汇总 4 个脱敏后的核心项目。每个子项目都保留独立 README、代码、测试与可公开样例数据，方便面试官按主题快速浏览。

## Project Index

| Project | Focus |
| --- | --- |
| [AI Stack Impact Workbench](projects/ai-stack-impact-workbench) | 外部变化影响分析 Agent Harness，重点展示 Skill 编排、Subagent 委派、工具门控、上下文治理和运行观测。 |
| [CoursePilot Agent Runtime](projects/coursepilot-agent-runtime) | 课程学习 Agent Runtime，重点展示 RAG、Memory、MCP 工具接入、上下文预算和 RunArtifact 留痕。 |
| [myAgent Table Reasoning](projects/myagent-table-reasoning) | 复杂表格问答推理系统，重点展示风险自适应路由、证据构建、确定性算子和 token 成本评估。 |
| [Campus Career Agent Dify Case Study](projects/campus-career-agent-dify-case-study) | 高校就业辅导 Agent 脱敏案例，重点展示 Dify 二次开发、业务模型、角色权限、知识库问答和 ToB 交付适配。 |

## Positioning

这组项目覆盖三个互补方向：

- **业务 Agent 落地**：从真实场景出发，把用户问题拆成可执行链路，并沉淀数据、产物和评测。
- **Agent Runtime**：关注多角色编排、上下文预算、Memory、MCP 工具接入和流式响应。
- **Agent Harness**：关注能力边界、工具策略、子智能体隔离、运行审计和问题复盘。

## Repository Layout

```text
agent-portfolio/
  README.md
  projects/
    ai-stack-impact-workbench/
    coursepilot-agent-runtime/
    myagent-table-reasoning/
    campus-career-agent-dify-case-study/
```

## Sanitization

本仓库由本地项目的脱敏版本整理而来，已移除：

- `.env`、API Key、Token、账号密钥和本地模型凭据；
- 本地数据库、长期记忆库、运行日志、Docker volume 和缓存；
- 专利交底书、个人备考文档、真实公司/学校接口信息；
- 大体量实验输出、node_modules、虚拟环境和构建产物。

## How to Review

建议按以下顺序浏览：

1. 看 `ai-stack-impact-workbench`：外部变化影响分析 Agent Harness，重点展示 Skill 编排、Subagent 委派、工具门控、上下文治理和运行观测。
2. 看 `coursepilot-agent-runtime`：课程学习 Agent Runtime，重点展示 RAG、Memory、MCP 工具接入、上下文预算和 RunArtifact 留痕。
3. 看 `myagent-table-reasoning`：复杂表格问答推理系统，重点展示风险自适应路由、证据构建、确定性算子和 token 成本评估。
4. 看 `campus-career-agent-dify-case-study`：高校就业辅导 Agent 脱敏案例，重点展示 Dify 二次开发、业务模型、角色权限、知识库问答和 ToB 交付适配。
