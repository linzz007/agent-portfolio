# Agent Portfolio | Agent 工程作品集

本仓库汇总 5 个脱敏后的 Agent / LLM 应用工程项目，覆盖业务 Agent 落地、Agent Runtime、Agent Harness、RAG / Memory / Context 工程化、工具治理、运行观测与评测验证等方向。

仓库内容按“业务问题 -> 技术方案 -> 核心模块 -> 验证方式”组织，重点呈现项目背景、工程边界、实现入口和可复现程度。

## 作品集导航

| 方向 | 项目 | 核心能力 |
| --- | --- | --- |
| Agent Harness / Runtime | [AI Stack Impact Workbench](projects/ai-stack-impact-workbench) | Skill 编排、Subagent 委派、工具门控、上下文治理、运行审计 |
| RAG + Memory + 多角色 Agent | [CoursePilot Agent Runtime](projects/coursepilot-agent-runtime) | RAG 检索、学习画像、上下文预算、MCP 工具、RunArtifact |
| ToB Agent 落地 | [Campus Career Agent Dify Case Study](projects/campus-career-agent-dify-case-study) | Dify 二次开发、业务建模、角色权限、知识库问答、交付适配 |
| Harness 机制验证 | [Daily AI Insight Engine Harness](projects/daily-ai-insight-engine-harness) | State Contract、Stage Graph、Hook、Gate、Coding Agent Harness |
| 推理任务评测与成本优化 | [myAgent Table Reasoning](projects/myagent-table-reasoning) | 风险路由、证据构建、确定性算子、表格问答评测、token 成本对比 |

## 项目索引

| 项目 | 业务问题 | 重点技术 |
| --- | --- | --- |
| [AI Stack Impact Workbench](projects/ai-stack-impact-workbench) | 外部新闻、政策、技术发布与企业/项目画像脱节，人工筛选成本高，影响结论缺少证据链。 | AgentRuntime、SkillManifest、Subagent、ContextManifest、ToolGateway、PermissionEngine、Gate、Artifact |
| [CoursePilot Agent Runtime](projects/coursepilot-agent-runtime) | 课程学习场景中，学生需要连续问答、练习生成、批改反馈和薄弱点复习。 | OrchestrationRunner、Hybrid RAG、Memory、MCP Tools、Context Budget、Trace、SSE |
| [Campus Career Agent Dify Case Study](projects/campus-career-agent-dify-case-study) | 高校就业辅导 ToB 场景需要面向学生和教师的简历、岗位、政策问答和班级分析能力。 | Dify Workflow、业务模型、权限角色、操作日志、内容安全、外部岗位数据同步 |
| [Daily AI Insight Engine Harness](projects/daily-ai-insight-engine-harness) | AI 新闻日报生成流程中，LLM 容易跳步、漏字段、输出不可复盘。 | Coding Agent Harness、Runtime Harness、Stage Gate、Hook、Tool Gateway、Context Router |
| [myAgent Table Reasoning](projects/myagent-table-reasoning) | 表格问答中复杂问题消耗 token 多、推理路径不稳定、证据不可解释。 | Task Contract、Risk-adaptive Routing、Evidence Builder、Deterministic Operators、Evaluation |

## 阅读路径

- **Agent Harness / Infra**：从 `AI Stack Impact Workbench` 入手，重点查看运行时入口、Skill 边界、Subagent 委派、工具权限和运行审计。
- **RAG / Memory / Context**：查看 `CoursePilot Agent Runtime`，重点关注学习画像、检索评测、上下文预算和运行留痕。
- **ToB AI 应用落地**：查看 `Campus Career Agent Dify Case Study`，重点关注业务建模、角色权限、知识库问答和平台适配。
- **Harness 基础机制**：查看 `Daily AI Insight Engine Harness`，重点关注 State、Graph、Hook、Gate、Artifact 的完整闭环。
- **推理与评测**：查看 `myAgent Table Reasoning`，重点关注风险路由、证据构建、确定性算子和 benchmark 报告。

## 仓库结构

```text
agent-portfolio/
  README.md
  projects/
    ai-stack-impact-workbench/
    coursepilot-agent-runtime/
    campus-career-agent-dify-case-study/
    daily-ai-insight-engine-harness/
    myagent-table-reasoning/
```

## 脱敏说明

本仓库是公开作品集版本，已移除或替换：

- `.env`、API Key、Token、账号密钥和本地模型凭据；
- 本地数据库、长期记忆库、运行日志、Docker volume、缓存和大体量实验输出；
- 专利交底书、个人备考文档、真实公司/学校接口信息；
- node_modules、虚拟环境、构建产物和不适合公开的历史中间文件。
