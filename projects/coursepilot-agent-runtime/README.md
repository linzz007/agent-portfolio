# CoursePilot Agent Runtime | 课程学习 Agent Runtime

CoursePilot 是一个面向大学课程学习场景的 RAG + Memory + MCP 多角色 Agent 系统。系统覆盖知识讲解、练习生成、答案批改、薄弱点复习和长轮次学习反馈，重点解决学习 Agent 在多轮状态、课程资料检索、个性化记忆和工具调用中的工程化问题。

## 核心问题

- 课程学习不是单轮问答，学生会连续追问、做题、提交答案并暴露薄弱点。
- 学习 Agent 需要长期维护用户画像，同时根据当前任务动态组合历史对话、检索片段和记忆内容。
- RAG、Memory、工具调用和模型输出需要进入统一运行链路，才能定位长轮次学习中的不稳定问题。

## 核心设计

1. **多角色编排**：Router 判断任务类型，Tutor 负责讲解，QuizMaster 负责出题，Grader 负责批改和反馈，OrchestrationRunner 统一编排。
2. **Hybrid RAG**：课程资料经过解析、切块、向量建库和在线检索，支持 Dense / BM25 / Hybrid Retrieval，并提供 gold_doc_ids 检索评测。
3. **学习 Memory**：用 SQLite 维护 episodes、user_profiles、weak_points、concept_mastery，支撑个性化讲解和薄弱点复习。
4. **上下文预算**：将 history / RAG / memory 分层管理，按任务模式、token 预算和上下文压力动态裁剪，避免长轮次学习时上下文失控。
5. **MCP 工具接入**：calculator、memory_search、filewriter、mindmap_generator 等能力通过 MCP 工具层接入，让 Agent 决策层和工具层解耦。
6. **RunArtifact 留痕**：每轮运行记录 retrieval、tool calls、context budget、output、error 和 metrics，方便复盘和问题定位。

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

## 工程价值

项目将学习流程拆成可维护角色，把检索、记忆、工具调用和上下文预算纳入统一运行链路。RunArtifact 和 trace 记录用于沉淀每轮运行证据，支撑长轮次学习场景下的问题定位和效果分析。

## 脱敏说明

发布版已移除真实 `.env`、本地 memory.db、个人学习计划和面试备考文档，只保留核心代码、公开课程 fixture、测试和可复现实验脚本。
