# CoursePilot Agent Runtime | 基于 RAG + Memory + MCP 的课程学习 Agent Runtime

CoursePilot 面向大学课程学习场景，目标不是做一个单轮问答 Demo，而是解决“学生持续学习时，回答要有教材依据、练习结果要能沉淀为后续复习依据、长轮次上下文不能失控”的问题。

## 待解决问题

- 课程问答如果只依赖模型记忆，容易缺少教材依据，学生也难以判断答案来源。
- 学生会连续追问、做题、提交答案和暴露薄弱点，系统需要维护学习状态与长期画像。
- 历史对话、教材证据、长期记忆同时进入上下文时，token 成本和噪声会快速上升。
- 检索、工具调用、批改反馈和模型回答如果没有统一运行记录，问题很难复盘。

## 关键动作

1. **Agent 编排与 MCP 工具接入**
   通过统一调度和会话状态区分追问、出题、作答和复习链路；以 MCP 接入计算与记忆检索工具，解耦任务决策和工具执行。

2. **RAG 检索与评测**
   构建教材解析、切块、向量建库与多策略检索链路，支持 Dense、BM25、Hybrid RRF + rerank，并基于 `gold_doc_ids` 统计 `hit@k`、`MRR@k` 与检索延迟。

3. **Memory 与学习画像**
   基于 SQLite 维护 `episodes` 情景记忆与 `user_profiles` 用户画像，通过知识掌握度、薄弱点标签和历史练习记录支撑个性化讲解、练习生成和错题强化。

4. **ContextBudgeter 上下文管理**
   按历史对话、教材证据和长期记忆分层组织上下文，采用“近期原文 + 结构化摘要卡”压缩长轮次历史，并结合任务模式与 token 预算动态裁剪。

5. **RunArtifact 与 Trace**
   每轮运行记录检索证据、上下文预算、工具输入输出、异常与耗时，用于定位超预算、检索偏移和工具失败。

## 可验证结果

- 在长轮次学习样例中，平均 prompt tokens 从 `4957.3` 降至 `2524.6`，下降 `49.1%`。
- 主教材证据检索中，`MRR@4` 从 `0.58` 提升至 `0.94`。
- 支持通过运行记录回看检索证据、上下文裁剪、工具调用和最终回答之间的关系。

## 核心模块与代码入口

| 文件 | 作用 |
| --- | --- |
| `core/orchestration/runner.py` | 多角色 Agent 编排主链路。 |
| `core/orchestration/context_budgeter.py` | history / RAG / memory 的上下文预算和裁剪策略。 |
| `core/agents/router.py` | 学习任务意图路由。 |
| `core/agents/tutor.py` | 知识讲解 Agent。 |
| `core/agents/quizmaster.py` | 练习生成 Agent。 |
| `core/agents/grader.py` | 答案批改与反馈 Agent。 |
| `rag/retrieve.py` | Dense / BM25 / Hybrid Retrieval 检索入口。 |
| `memory/manager.py` | 学习记忆写入、读取和画像维护。 |
| `mcp_tools/server_stdio.py` | MCP 工具服务入口。 |
| `core/harness/runtime.py` | 轻量 Harness Runtime。 |
| `core/harness/artifact.py` | RunArtifact 结构化运行记录。 |
| `backend/api.py` | API / 流式响应入口。 |

## 验证方式

```powershell
py -3 -m pip install -r requirements.txt
py -3 -m pytest
py -3 scripts/perf/eval_rag_retrieval.py
```

## 面试讲法

这个项目重点表达“我能把一个 Agent 拆成可运行、可观察、可优化的工程链路”。面试中可以围绕三个问题展开：

- 为什么课程学习 Agent 需要 Memory，而普通 RAG 不够？
- 为什么要做 ContextBudgeter，具体裁剪了哪些内容？
- 检索指标和上下文指标怎么验证，失败 case 怎么定位？

## 脱敏说明

发布版已移除真实 `.env`、本地 memory.db、个人学习计划和面试备考文档，只保留核心代码、公开课程 fixture、测试和可复现实验脚本。
