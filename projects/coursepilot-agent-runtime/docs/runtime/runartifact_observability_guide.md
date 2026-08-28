# RunArtifact 可观测性查看指南

本文档对应 `harness.run_artifact.v3`。

## 1. 这次新增了什么

每个新的 RunArtifact 会保留原有机器字段，并新增两个给人看的字段：

| 字段 | 用途 |
|---|---|
| `diagnostics` | 先看这里。它把一次运行中最值得排查的点整理成 `ok / warning / error`。 |
| `timeline` | 再看这里。它按执行顺序展示会话、LLM 调用、RAG 检索、上下文预算、ReAct 阶段、工具调用、最终输出和评估结果。 |

注意：这里不会记录模型的隐藏思维链。它只记录可验证的外部行为，例如输入摘要、检索结果、工具调用、token/耗时、输出摘要和诊断结论。

## 2. 怎么看最新运行

先运行一次前端对话。新的 artifact 会落在：

```powershell
data\runs\YYYY-MM-DD\run_*.json
```

如果希望每次写入 RunArtifact 时自动生成同名 HTML，在 `.env` 里开启：

```powershell
HARNESS_RENDER_HTML=1
```

开启后每次生成 `run_xxx.json` 时，会在同目录生成：

```powershell
data\runs\YYYY-MM-DD\run_xxx.html
```

注意：这个变量是在 Python 进程写 artifact 时读取的。修改 `.env` 后需要重启后端、Streamlit 或测评脚本所在进程；已经运行中的服务不会自动重新读取。

列出最近的运行：

```powershell
py -3.10 scripts\perf\list_run_artifacts.py --limit 5
```

查看最近一条的可读详情：

```powershell
py -3.10 scripts\perf\list_run_artifacts.py --detail
```

查看 LLM 调用的完整输入/输出：

```powershell
py -3.10 scripts\perf\list_run_artifacts.py --detail --full-io
```

按 run_id 过滤：

```powershell
py -3.10 scripts\perf\list_run_artifacts.py --detail --run-id 2917eebb
```

按用户输入过滤：

```powershell
py -3.10 scripts\perf\list_run_artifacts.py --detail --contains "我不太会"
```

批量看工程评估结果：

```powershell
py -3.10 scripts\perf\eval_runs.py data\runs\2026-06-08
```

## 3. 推荐阅读顺序

1. 先看 `Diagnostics`：
   - `run_status`：请求有没有失败。
   - `answer_output`：最终回答是否为空。
   - `retrieval`：是否有 RAG 引用。
   - `context_budget`：上下文有没有超压或硬截断。
   - `tool_call_success`：工具调用是否失败。
   - `tool_governance`：工具调用是否有治理决策记录。
   - `required_calculator_for_grading`：练习/考试批改时是否观察到 calculator 调用。

2. 再看 `Timeline`：
   - `session`：本轮模式、skill、用户输入。
   - `llm_call`：模型、provider、token、耗时、是否 stream / with_tools。
   - `retrieval`：检索模式、返回数量、top chunk。
   - `context_budget`：history / RAG / memory / final token 估算。
   - `react_phase`：ReAct 的 act / synthesize 阶段。
   - `tool_decision` / `tool_call`：工具是否被放行、是否真的调用、是否成功。
   - `assistant_output`：最终输出摘要。
   - `eval`：本地启发式评估结果。

3. 最后再看原始字段：
   - `metrics.trace_events`：完整事件流。
   - `tool_calls`：工具事件原始记录。
   - `retrieval`：所有引用 chunk。
   - `context_budget`：上下文预算完整参数。
   - `output.content`：最终完整回答。

## 4. 业界概念对应关系

| 业界概念 | CoursePilot 当前对应 |
|---|---|
| Trace | 一次请求的完整运行记录，落在一个 RunArtifact 里。 |
| Span / Observation | `timeline` 中的一步，例如 LLM call、retrieval、tool_call。 |
| Attributes | 每一步里的 token、model、latency、status、top_k 等结构化字段。 |
| Tool span | `tool_call` 和 `tool_decision`。 |
| Retriever span | `retrieval`。 |
| Eval / Judge | `eval_result` 和 `diagnostics`。当前是规则型检查，后续可接 LLM-as-Judge。 |
| Replay | 当前还没有完整回放；RunArtifact 已经具备后续 replay 的输入证据基础。 |
| Dataset / Experiment | 后续可把多个 RunArtifact 组成测试集，对比不同 prompt / retriever / model。 |
| Redaction | 后续需要做脱敏，避免把隐私、密钥、完整用户数据直接写入 trace。 |
