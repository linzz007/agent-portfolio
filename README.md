# Agent Portfolio | Agent 工程作品集

这是一个面向 **Agent / LLM 应用工程 / Agent Runtime / Agent Harness** 岗位的作品集仓库。它不是简单代码备份，而是把几个脱敏项目按“面试官想快速判断的能力”重新组织：业务场景是否真实、Agent 链路是否可控、RAG/Memory/Context 是否讲得清楚、评测和可观测是否有工程落点。

## 30 秒导航

| 面试官想判断什么 | 推荐先看 | 你能看到的信号 |
| --- | --- | --- |
| 是否懂 Agent Harness / Runtime | [AI Stack Impact Workbench](projects/ai-stack-impact-workbench) | Skill 编排、Subagent 委派、工具门控、上下文治理、运行审计 |
| 是否做过 RAG + Memory + 多角色 Agent | [CoursePilot Agent Runtime](projects/coursepilot-agent-runtime) | RAG 检索、学习画像、上下文预算、MCP 工具、RunArtifact |
| 是否接触过 ToB Agent 落地 | [Campus Career Agent Dify Case Study](projects/campus-career-agent-dify-case-study) | Dify 二次开发、业务模型、角色权限、知识库问答、交付适配 |
| 是否理解 Harness 的基础演进 | [Daily AI Insight Engine Harness](projects/daily-ai-insight-engine-harness) | State Contract、Stage Graph、Hook、Gate、Coding Agent Harness |
| 是否做过推理任务评测与成本优化 | [myAgent Table Reasoning](projects/myagent-table-reasoning) | 风险路由、证据构建、确定性算子、表格问答评测、token 成本对比 |

## 项目索引

| 项目 | 业务问题 | 重点技术 |
| --- | --- | --- |
| [AI Stack Impact Workbench](projects/ai-stack-impact-workbench) | 外部新闻、政策、技术发布与企业/项目画像脱节，人工筛选成本高，影响结论缺少证据链。 | AgentRuntime、SkillManifest、Subagent、ContextManifest、ToolGateway、PermissionEngine、Gate、Artifact |
| [CoursePilot Agent Runtime](projects/coursepilot-agent-runtime) | 课程学习场景中，学生需要连续问答、练习生成、批改反馈和薄弱点复习。 | OrchestrationRunner、Hybrid RAG、Memory、MCP Tools、Context Budget、Trace、SSE |
| [Campus Career Agent Dify Case Study](projects/campus-career-agent-dify-case-study) | 高校就业辅导 ToB 场景需要面向学生和教师的简历、岗位、政策问答和班级分析能力。 | Dify Workflow、业务模型、权限角色、操作日志、内容安全、外部岗位数据同步 |
| [Daily AI Insight Engine Harness](projects/daily-ai-insight-engine-harness) | AI 新闻日报生成流程中，LLM 容易跳步、漏字段、输出不可复盘。 | Coding Agent Harness、Runtime Harness、Stage Gate、Hook、Tool Gateway、Context Router |
| [myAgent Table Reasoning](projects/myagent-table-reasoning) | 表格问答中复杂问题消耗 token 多、推理路径不稳定、证据不可解释。 | Task Contract、Risk-adaptive Routing、Evidence Builder、Deterministic Operators、Evaluation |

## 推荐浏览顺序

1. **先看 Workbench**：这是最贴近 Agent Harness / Infra 岗位的项目，适合追问“为什么通用 Agent 不够、如何做能力边界和审计”。
2. **再看 CoursePilot**：这是最容易展开技术细节的项目，适合追问 RAG、Memory、上下文预算、MCP 工具和 trace。
3. **然后看 Dify Case**：这是 ToB 落地案例，适合判断是否理解真实业务交付中的角色、数据和平台适配。
4. **补看 Daily Harness**：这是 Harness 思路的早期项目，适合解释从固定流程到受控运行时的演进。
5. **最后看 myAgent**：这是偏推理和评测的项目，适合讨论 accuracy、token、risk routing 和 benchmark。

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
