# Agent Portfolio | 林哲正 Agent 工程作品集

本仓库汇总若干脱敏后的 Agent / LLM 应用工程项目，覆盖业务 Agent 落地、Agent Runtime / Harness、RAG / Memory / Context 工程化、工具治理、运行追踪与评测验证等方向。

项目说明统一按以下结构组织：

```text
待解决问题 -> 关键动作 -> 可验证结果 -> 核心代码入口
```

## 项目列表

| 项目 | 中文名 | 主要场景 | 核心能力 |
| --- | --- | --- | --- |
| [coursepilot-agent-runtime](projects/coursepilot-agent-runtime) | 基于 RAG + Memory + MCP 的课程学习 Agent Runtime | 大学课程学习、教材问答、练习批改、薄弱点复习 | RAG、Memory、ContextBudgeter、MCP、RunArtifact、SSE |
| [ai-stack-impact-workbench](projects/ai-stack-impact-workbench) | Insight Workbench：智能研判 Agent 系统 | 外部资料研判、技术更新分析、政策/事件影响分析 | `/report` workflow、Subagent、Wiki 画像、ToolGateway、Trace、Artifact |
| [daily-ai-insight-engine-harness](projects/daily-ai-insight-engine-harness) | Daily AI Insight Engine 新闻分析 Agent Harness | 新闻日报生成、阶段化报告 workflow | State Contract、Stage Graph、Hook、Gate、Artifact |
| [campus-career-agent-dify-case-study](projects/campus-career-agent-dify-case-study) | 高校就业辅导 Agent Dify 二次开发脱敏案例 | ToB 就业辅导、学生/教师双角色业务 | Dify 工作流适配、角色权限、岗位数据同步、内容安全、操作日志 |
| [myagent-table-reasoning](projects/myagent-table-reasoning) | myAgent 表格推理与低成本评测 | 表格问答、证据构建、低成本推理评测 | 风险路由、证据构建、确定性算子、benchmark 对比 |

## 能力覆盖

| 能力方向 | 对应项目 | 说明 |
| --- | --- | --- |
| Agent Runtime | CoursePilot、Insight Workbench | 将对话、工具、上下文、记忆和产物纳入统一运行链路 |
| Agent Harness | Insight Workbench、Daily AI Insight Engine | 通过 workflow、gate、权限、trace 和 artifact 控制模型执行边界 |
| RAG / Memory / Context | CoursePilot、Insight Workbench | 用检索评测、用户画像、上下文预算支撑长轮次任务 |
| Evaluation / Optimization | CoursePilot、myAgent | 通过检索指标、上下文指标和 benchmark 结果验证优化效果 |
| ToB AI 应用落地 | campus-career-agent-dify-case-study | 结合业务角色、权限入口、外部数据和日志审计完成平台适配 |

## 仓库结构

```text
agent-portfolio/
  README.md
  projects/
    ai-stack-impact-workbench/
    coursepilot-agent-runtime/
    daily-ai-insight-engine-harness/
    campus-career-agent-dify-case-study/
    myagent-table-reasoning/
```

## 脱敏说明

本仓库是公开作品集版本，已移除或替换：

- `.env`、API Key、Token、账号密钥和本地模型凭据；
- 本地数据库、长期记忆库、运行日志、Docker volume、缓存和大体量实验输出；
- 专利交底书、个人备考文档、真实公司/学校接口信息；
- node_modules、虚拟环境、构建产物和不适合公开的历史中间文件。
