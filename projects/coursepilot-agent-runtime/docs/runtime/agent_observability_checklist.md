# Agent 可观测性最小清单

本文用于判断 CoursePilot 的 RunArtifact 是否接近业界 Agent 可观测性做法，以及后续还需要补哪些能力。参考口径主要来自 OpenTelemetry GenAI agent spans、LangSmith、Langfuse、Arize Phoenix 这类 Agent/LLM observability 方案。

## 1. 业界怎么做

成熟 Agent 可观测性一般不是只保存最终回答，而是把一次用户请求拆成一个 `trace`，再把内部步骤拆成多个 `span` / `observation`。常见步骤包括：

- `session / trace`：一次用户请求或一段多轮会话的入口，包含用户输入、模式、版本、状态、耗时、标签。
- `llm_call`：每次模型调用的完整输入、完整输出、模型名、provider、token、耗时、是否 stream、是否要求工具调用。
- `retrieval`：RAG 查询、top_k、命中的 chunk、分数、文档来源、是否为空。
- `memory / context`：历史压缩、memory 命中、上下文预算、是否截断。
- `tool_call`：工具选择、参数、返回值、成功失败、错误、权限或风险决策。
- `output / eval`：最终回答、评分、规则诊断、人工反馈或 LLM-as-Judge 结果。

所以业界核心不是“参数越多越好”，而是让人能沿着一条时间线回答三个问题：

1. 输入是不是对的？
2. Agent 中间有没有走错路？
3. 最终输出为什么变成这样？

## 2. CoursePilot 当前水平

以 `data\runs\2026-06-08\run_20260608152440_28292e60.json` 为例，当前 RunArtifact 已经接近一个本地轻量版的 Agent trace artifact：

- 有 `run_id`、`trace_id`、`request_id`，能定位一次请求。
- 有 `session`，能看到 `mode=practice`、`skill_id=practice.grade.v1`、用户输入是“我不太会”。
- 有 `timeline`，能按顺序看到 session、LLM 调用、RAG、memory_search、context_budget、ReAct 阶段、最终输出和 eval。
- 有完整 LLM 输入输出，原始位置在 `metrics.trace_events[*]` 中 `type=llm_call` 的 `input_messages` 和 `output_message`。
- 有 `diagnostics`，能直接提示本次运行是否有异常。本例中评分链路已走对，但出现 `required_calculator_for_grading` warning，说明评分时没有观察到 calculator 工具调用。

它现在已经不是普通日志，而是具备“排查 Agent 行为”的结构化运行证据。  
但它还不是成熟生产级平台，主要差在：

- 没有可视化 trace UI / span tree。
- 没有按 OpenTelemetry 标准字段导出到外部系统。
- 没有批量实验、数据集评测、prompt/model 版本对比面板。
- 没有 full input/output 的脱敏、采样、保留周期策略。
- 没有成本、延迟、成功率的聚合 dashboard。
- 没有一键 replay 或从某个步骤恢复执行。

## 3. 最小必看字段

| 观测层 | 人应该看什么 | CoursePilot 当前位置 |
|---|---|---|
| 请求入口 | run_id、trace_id、mode、skill、用户输入、状态 | `session` |
| 时间线 | 每一步做了什么、顺序是否正确、哪里 warning/error | `timeline` |
| LLM 调用 | 完整 prompt/messages、完整输出、token、耗时、工具请求 | `metrics.trace_events[*].type == "llm_call"` |
| RAG | 检索是否发生、返回多少 chunk、来源是否相关 | `retrieval` / `timeline.retrieval` |
| 上下文 | history/RAG/memory token、是否压缩、是否截断 | `context_budget` |
| 工具 | 调了什么工具、参数是什么、成功失败、是否被治理策略拦截 | `tool_calls` / `tool_decisions` / `timeline.tool_call` |
| 最终输出 | 用户看到的答案是否符合当前 mode | `output.content` / `timeline.assistant_output` |
| 诊断评测 | 路由、输出类型、工具使用、评分一致性是否正常 | `diagnostics` / `eval_result` |

## 4. 推荐查看顺序

1. 先看 `diagnostics`：快速判断本次运行有没有红灯或黄灯。
2. 再看 `timeline`：确认 Agent 是不是按正确顺序走完了。
3. 如果怀疑模型判断错误，看 `llm_call.input_messages` 和 `llm_call.output_message`。
4. 如果怀疑 RAG 或上下文污染，看 `retrieval` 和 `context_budget`。
5. 如果怀疑工具没调用或调用错，看 `tool_calls`、`tool_decisions` 和 ReAct 阶段。

查看当前运行的命令：

```powershell
py -3.10 scripts\perf\list_run_artifacts.py --detail --run-id 28292e60
```

查看完整 LLM 输入输出：

```powershell
py -3.10 scripts\perf\list_run_artifacts.py --detail --full-io --run-id 28292e60
```

## 5. 下一步优先级

如果继续往业界成熟形态靠，优先级建议是：

1. 先做脱敏、采样、保留周期。因为 full input/output 很有用，但也最容易泄露隐私或密钥。
2. 再做标准 span 命名和 OpenTelemetry 导出。这样 RunArtifact 可以接入 Langfuse、Phoenix、Grafana、Datadog 一类外部系统。
3. 再做批量 eval 和 dashboard。单条 trace 用来 debug，多条 trace 才能判断 prompt、retriever、model 是否真的变好。
4. 最后做 replay。能从历史 RunArtifact 复现当时输入、RAG、工具和输出，才算进入更强的工程闭环。

