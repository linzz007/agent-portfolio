# Trace 设计说明

## 1. 字段与数据对象

### 1.1 MetricsTrace

| 字段 | 含义 | 生成时机 |
|---|---|---|
| trace_id | 单次运行链路 ID | 进入 harness runtime 时创建或复用 |
| created_at_ms | trace 创建时间 | trace_scope 初始化时 |
| meta | 运行元信息 | 由 course_name、mode、skill_id、request_id 等组成 |
| events | 运行事件列表 | 运行过程中由各模块追加 |

### 1.2 Trace Event

| 字段 | 含义 | 示例类型 |
|---|---|---|
| type | 事件类别 | llm_call、retrieval、context_budget、tool_gate_decision、memory_search、react_phase、history_llm_compress |
| ts_ms | 事件时间 | 毫秒时间戳 |
| trace_id | 所属链路 | 与 MetricsTrace 对齐 |
| seq | 事件序号 | 同一 trace 内递增 |
| payload | 事件内容 | 检索耗时、工具决策、预算统计、错误信息等 |

`llm_call` 是当前最重要的 trace event 之一。它除了 token、耗时、模型名、provider、stream、success 等指标外，还会记录 `input_messages` 和 `output_message`，用于排查模型实际看到了什么、输出了什么。这里记录的是外部可验证输入输出，不记录隐藏推理链。

### 1.3 RunArtifact

| 字段 | 含义 |
|---|---|
| schema_version | artifact 结构版本 |
| run_id | 单次运行 ID |
| session | 会话信息：课程、模式、skill、request_id、trace_id、状态、时间 |
| plan | 本次运行的计划信息 |
| user_goal | 从用户输入和计划中提取的目标摘要 |
| retrieval | RAG 检索记录 |
| context_budget | 上下文预算和压缩记录 |
| memory_trace | 记忆读写相关事件 |
| tool_calls | 实际工具调用结果 |
| tool_decisions | 工具准入决策 |
| risk_decisions | 外部访问、写入类工具等风险决策 |
| timeline | 面向人工排查的时间线视图，按顺序展示 session、llm_call、retrieval、tool_call、context_budget、assistant_output、eval 等步骤 |
| diagnostics | 面向人工排查的红黄绿检查结果，例如路由、输出、检索、上下文预算、工具调用、评分输出一致性 |
| output | 最终输出、流式片段、引用等 |
| eval_result | 运行后自动评估结果 |
| metrics | trace 事件统计 |
| error | 异常信息 |
| lifecycle_events | before_plan、after_plan、after_answer、on_error 等生命周期事件 |

## 2. 关键使用位置

| 使用位置 | 写入内容 | 作用 |
|---|---|---|
| Harness runtime 入口 | session、trace_id、request_id | 建立一次运行的外层边界 |
| Lifecycle hooks | before_plan、after_plan、after_answer、on_error | 还原运行阶段 |
| RAG Retriever | retrieval event | 记录检索模式、耗时、候选数、返回数 |
| ContextBudgeter | context_budget event | 记录历史、RAG、memory 的 token 预算和压缩结果 |
| LLM Client | llm_call event | 记录每次模型调用的完整输入消息、输出消息、token、耗时、stream/tool 状态 |
| Tool policy | tool_gate_decision event | 记录工具是否允许、风险等级、审批要求 |
| Memory 流程 | memory_read/search/write 相关事件 | 记录记忆是否参与上下文或写入 |
| ArtifactStore | run artifact JSON | 将一次运行沉淀为可回放、可审计文件 |
| Evaluator | eval_result | 对回答、检索、工具、预算、安全做运行后评分 |
| Conversation Evaluator | conversation_eval_report | 按多轮场景运行 learn/practice/exam，对 skill、输出类型、隐藏 metadata、评分/出题流程做回归检查，可选接入 LLM Judge |
| HTML Viewer | run artifact HTML | 将单个 RunArtifact 渲染为本地静态 HTML，便于查看 diagnostics、timeline、LLM 输入输出、RAG、工具调用和原始 JSON |

## 3. 生命周期

| 阶段 | Trace 行为 | Artifact 行为 |
|---|---|---|
| 请求进入 | 创建或复用 active trace | 初始化 session |
| 计划前 | 记录 before_plan | lifecycle_events 增加节点 |
| 计划后 | 记录 after_plan | plan 和 user_goal 可被写入 |
| 检索阶段 | 追加 retrieval 事件 | 最终汇总到 retrieval 字段 |
| 上下文组装 | 追加 context_budget 事件 | 最终汇总到 context_budget 字段 |
| 工具调用前 | 追加 tool_gate_decision | 最终汇总到 tool_decisions、risk_decisions |
| 答案生成后 | 记录 after_answer | output 写入最终结果 |
| 异常时 | 记录 on_error | error 字段保存异常摘要 |
| 运行结束 | 计算 metrics 和 eval_result | 持久化为 JSON 文件 |

## 4. 非流式与流式差异

| 项目 | 非流式 | 流式 |
|---|---|---|
| 输出采集 | 一次性拿到 response | 持续收集 chunks、status、citations、context_budget 等事件 |
| artifact 写入 | answer 返回后写入 | 流结束后统一写入 |
| 错误处理 | 异常进入 error 字段 | 流中异常同样进入 error，并保留已收集片段 |
| 用户可见过程 | 通常只看到最终结果 | 可看到检索、生成、评分等过程状态 |

## 5. 工具与风险决策

| 字段 | 含义 |
|---|---|
| tool_name | 工具名称 |
| allowed | 是否允许调用 |
| reason | 放行或拒绝原因 |
| phase | 当前阶段 |
| mode | 当前模式 |
| signature | 工具调用签名 |
| risk_level | 风险等级 |
| approval_mode | 审批模式 |
| required_approval | 是否需要人工审批 |

当前风险分类中，calculator 属于安全工具；memory_search 属于读工具；websearch 属于外部访问；filewriter、mindmap_generator 属于写入类工具。Trace 会记录决策，但不等价于自动安全审查模型。

## 6. 评估字段

| 评估项 | 判断依据 |
|---|---|
| answer_presence_score | 是否有可用输出 |
| retrieval_coverage_score | 是否有检索证据 |
| tool_success_score | 工具是否失败或被阻断 |
| context_budget_score | 上下文压力是否过高 |
| safety_score | 是否触发外部访问、写入风险或错误 |
| verdict | passed、warning、failed |

除了单个 RunArtifact 的启发式 `eval_result`，当前还新增了多轮 conversation eval。它读取 `benchmarks/eval_scenarios_v1.jsonl`，按场景保留 history 连续运行多轮对话，并在每一轮检查：

- 实际 `skill_id` 是否符合预期。
- 输出类型是否是 answer、question、exam 或 grading。
- 出题/出卷 metadata 是否存在且不展示给用户。
- “我不会”是否进入评分且低分。
- “再出一个新的题目 / 再出一套新的”是否进入重新出题/出卷，而不是误判成答案。
- RunArtifact diagnostics 是否出现 error。

`--llm-judge` 是附加语义复核，不覆盖规则结论。当前 Judge prompt 只判断输出是否符合 expected，还不是完整的“优秀/合格/不合格”质量评分 rubric。

## 7. 失败与降级

| 异常情况 | 当前处理 | 结果 |
|---|---|---|
| 业务流程异常 | 捕获后写入 error | artifact 仍尽量保存 |
| 工具被拒绝 | 写入 tool_decisions 和 risk_decisions | 主流程按策略继续或返回受限结果 |
| RAG 或 Memory 失败 | 对应事件记录失败原因 | 下游上下文缺少该部分 |
| 上下文超预算 | context_budget 记录 hard_truncated | 输入被硬截断后继续 |
| 评估未通过 | eval_result verdict 为 warning 或 failed | 不阻断输出，只用于审计 |

## 8. 当前边界

| 能力 | 当前状态 |
|---|---|
| Trace 当前形态 | 已从普通日志升级为 RunArtifact + timeline + diagnostics，并记录完整 LLM 输入输出，但仍是本地 artifact 形态 |
| LLM 风险判断 | 当前主要是规则和启发式评估；`--llm-judge` 只做可选语义复核，未接入专门安全裁判或质量 rubric |
| 可视化 | 已提供本地静态 HTML viewer，能查看单个 artifact；尚未提供完整前端 trace UI / span tree / dashboard |
| 多轮测评 | 已有 `eval_scenarios_v1.jsonl` 和 `eval_conversations.py`，可回归 learn/practice/exam 核心流程；质量评分仍需更细 rubric |
| 回放能力 | artifact 已保存关键数据，多轮 eval 会真实重放场景输入；从任意历史 artifact 自动恢复执行仍需要配套工具 |
