# CoursePilot Agent Runtime

面向大学课程学习场景的 RAG + Memory + MCP 多智能体学习系统，覆盖知识讲解、练习生成、答案批改、薄弱点强化和长轮次学习反馈。

## Highlights

- **Agent Orchestration**：Router、Tutor、QuizMaster、Grader 等角色由 OrchestrationRunner 统一调度。
- **Hybrid RAG**：支持 Dense / BM25 / Hybrid Retrieval，结合课程资料切块、向量索引和 gold_doc_ids 检索评测。
- **Memory System**：使用 SQLite 维护 episodes、user_profiles、weak_points、concept_mastery 等学习画像。
- **MCP Tools**：calculator、memory_search、filewriter、mindmap_generator 等能力通过 MCP 工具层接入。
- **Context Budgeting**：history / RAG / memory 分层上下文裁剪，记录 context budget 和 trace。
- **RunArtifact**：结构化记录 retrieval、tool calls、context budget、output、error 和 metrics。

## Quick Start

```powershell
py -3 -m pip install -r requirements.txt
py -3 -m backend.api
streamlit run frontend/streamlit_app.py
```

## Evaluation

```powershell
pytest
py -3 scripts/perf/eval_rag_retrieval.py
```

## Repository Scope

发布版本已移除真实 `.env`、本地 memory.db、个人学习计划和面试备考文档，只保留核心代码、公开课程 fixture、测试和可复现实验脚本。
