# Agent Portfolio | 林哲正 Agent 工程作品集

本仓库是简历 `9.0` 对应的公开作品集，整理了若干脱敏后的 Agent / LLM 应用工程项目。仓库不按“用了哪些框架”堆叠内容，而是按真实项目更容易被追问的结构组织：

```text
待解决问题 -> 关键动作 -> 可验证结果 -> 复盘与后续优化
```

整体能力主线是：业务 Agent 落地、Agent Runtime / Harness、RAG / Memory / Context 工程化、工具治理、运行追踪与评测优化。

## 简历主线

简历中的主项目与本仓库对应关系如下：

| 简历侧重点 | GitHub 项目 | 面试官应重点看到的内容 |
| --- | --- | --- |
| 报表 Agent 实习 | 公司项目不公开，仅在简历和面试中说明 | 评测集、失败归因、澄清 Guardrail、Runtime 适配、验证耗时优化 |
| 课程学习 Agent Runtime | [CoursePilot Agent Runtime](projects/coursepilot-agent-runtime) | RAG / Memory / ContextBudgeter / RunArtifact，及可量化检索和上下文优化 |
| 智能研判 Agent 系统 | [Insight Workbench](projects/ai-stack-impact-workbench) | `/report` 受控 workflow、Subagent 委派、Wiki 画像、Trace / Artifact |

## 项目导航

| 项目 | 解决的问题 | 核心动作 / 方法 | 验证点 |
| --- | --- | --- | --- |
| [Insight Workbench：智能研判 Agent 系统](projects/ai-stack-impact-workbench) | 外部资料分散、分析脱离用户背景、历史结论难追问 | 将报告生成拆成受控 workflow；用 Subagent 做分析/质疑/核验；用 Wiki 画像组织背景事实 | `/report` 报告产物、父子 Trace、Wiki 事实索引、运行记录 |
| [CoursePilot：课程学习 Agent Runtime](projects/coursepilot-agent-runtime) | 课程问答缺少教材依据，练习结果难用于后续复习，长轮次上下文容易膨胀 | 多角色编排、Hybrid RAG、学习 Memory、ContextBudgeter、RunArtifact | prompt tokens 4957.3 -> 2524.6；MRR@4 0.58 -> 0.94 |
| [Daily AI Insight Engine Harness](projects/daily-ai-insight-engine-harness) | 新闻日报 workflow 中 LLM 易跳步、漏字段、结果难复盘 | State Contract、Stage Graph、Hook、Gate、Artifact、Coding Harness | 阶段执行记录、质量门、linter、报告产物 |
| [高校就业辅导 Agent Dify 二次开发脱敏案例](projects/campus-career-agent-dify-case-study) | ToB 就业辅导场景中学生/教师角色、知识库和岗位数据需要统一适配 | Dify 工作流适配、角色权限、岗位数据同步、内容安全与操作日志 | 脱敏业务模型、权限入口、日志设计 |
| [myAgent 表格推理与低成本评测](projects/myagent-table-reasoning) | 表格问答中复杂推理路径不稳定、token 成本高、证据不可解释 | 风险路由、证据构建、确定性算子、benchmark 对比 | 多轮 benchmark 报告、holdout 结果 |

## 推荐阅读路径

如果只看 10 分钟，建议按以下顺序：

1. [docs/interview_reading_guide.md](docs/interview_reading_guide.md)：按简历问题、项目亮点和面试追问组织。
2. [CoursePilot README](projects/coursepilot-agent-runtime)：看 RAG、Memory、Context 和量化验证。
3. [Insight Workbench README](projects/ai-stack-impact-workbench)：看 workflow、Subagent、Wiki 画像和 Harness 治理。
4. [Daily AI Insight Engine README](projects/daily-ai-insight-engine-harness)：看更小规模的 State -> Graph -> Gate -> Artifact 机制。

## 能力覆盖

| 能力方向 | 对应项目 | 说明 |
| --- | --- | --- |
| Agent 业务落地 | 实习项目、Dify 案例 | 从业务问题、测试集、失败归因和交付边界出发，不只做 Demo |
| Agent Runtime | CoursePilot、Insight Workbench | 将对话、工具、上下文、记忆和产物纳入统一运行链路 |
| Agent Harness | Insight Workbench、Daily AI Insight Engine | 通过 workflow、gate、权限、trace 和 artifact 控制模型执行边界 |
| RAG / Memory / Context | CoursePilot、Insight Workbench | 用检索评测、用户画像、上下文预算支撑长轮次任务 |
| Evaluation / Optimization | 实习项目、CoursePilot、myAgent | 用用例集、金标、指标和复测证明优化有效 |

## 仓库结构

```text
agent-portfolio/
  README.md
  docs/
    interview_reading_guide.md
    agent-portfolio.pdf
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
