# 上下文压缩设计说明

## 1. 字段与输入对象

| 输入段 | 来源 | 内容 | 后续去向 |
|---|---|---|---|
| query | 当前用户输入 | 本轮问题或作答内容 | 用于压缩相关性判断 |
| history | 对话历史 | 近期原文 + 较早历史 | 生成 history_context |
| rag_text | RAG 检索结果 | 教材参考片段 | 生成 rag_context |
| memory_text | Memory 检索结果 | 历史学习记录 | 生成 memory_context |

## 2. 预算字段

| 配置 | 含义 |
|---|---|
| CTX_TOTAL_TOKENS | 模型上下文总预算 |
| CTX_SAFETY_MARGIN | 预留安全边界 |
| CB_RECENT_RAW_TURNS | 保留最近原文轮数 |
| CB_HISTORY_SUMMARY_MAX_TOKENS | 历史摘要最大预算 |
| CB_RAG_MAX_TOKENS | RAG 段最大预算 |
| CB_MEMORY_MAX_TOKENS | Memory 段最大预算 |
| RAG_COMPRESS_OWNER | RAG 压缩归属，默认由 retriever 负责 |
| CB_ENABLE_LLM_HISTORY_COMPRESS | 是否启用 LLM 历史压缩 |
| CB_LLM_HISTORY_COMPRESS_TRIGGER_TOKENS | 触发 LLM 历史压缩的长度阈值 |
| CB_LLM_HISTORY_COMPRESS_TARGET_TOKENS | LLM 摘要目标长度 |
| CB_LLM_HISTORY_COMPRESS_TIMEOUT_MS | LLM 压缩超时时间 |

## 3. 输出字段

| 字段 | 含义 |
|---|---|
| history_text | 压缩后的历史上下文 |
| rag_text | 预算内的教材参考 |
| memory_text | 预算内的历史记忆 |
| final_text | 最终拼入 prompt 的完整上下文 |
| history_tokens_est | 历史 token 估算 |
| history_recent_tokens_est | 近期原文 token 估算 |
| history_summary_tokens_est | 历史摘要 token 估算 |
| history_summary_source | 摘要来源：llm、heuristic、none、llm_failed |
| history_llm_compress_applied | 是否实际使用 LLM 压缩 |
| history_llm_compress_ms | LLM 压缩耗时 |
| rag_tokens_est | RAG token 估算 |
| memory_tokens_est | Memory token 估算 |
| final_tokens_est | 最终上下文 token 估算 |
| budget_tokens_est | 可用预算 |
| hard_truncated | 是否触发硬截断 |

## 4. 关键使用位置

| 使用位置 | 作用 |
|---|---|
| 学习模式回答前 | 压缩历史、教材参考和记忆，交给 Tutor |
| 练习生成前 | 控制历史、RAG、Memory 对出题上下文的占用 |
| 练习评分前 | 保留作答上下文，同时加入必要历史和教材依据 |
| 考试生成前 | 限制长历史对考试生成的影响 |
| 考试评分前 | 控制评分上下文，避免超过模型限制 |
| 流式响应 | 输出 context_budget 状态事件 |
| Harness Artifact | 保存上下文预算结果，供 trace 和评估使用 |

## 5. 压缩流程

| 步骤 | 处理方式 | 结果 |
|---|---|---|
| 拆分历史 | 最近若干轮保留原文，更早历史进入摘要流程 | recent + older |
| 判断 LLM 压缩 | older 超过阈值且开关开启时调用 LLM | LLM summary card |
| LLM 失败降级 | 超时、异常、格式不合规时放弃 LLM 摘要 | 回退 heuristic 摘要 |
| 历史截断 | 历史摘要和近期原文共同受预算限制 | history_text |
| RAG 处理 | 默认认为 retriever 已做句子压缩 | budgeter 只做总量截断 |
| Memory 处理 | 按 memory token 预算截断 | memory_text |
| 最终拼接 | 按 history、教材参考、memory 顺序拼接 | final_text |
| 硬预算检查 | final_text 超过总预算减安全边界时硬截断 | hard_truncated=true |

## 6. 不同上下文段的差异

| 上下文段 | 压缩方式 | 保留优先级 | 失败后行为 |
|---|---|---|---|
| history | 最近原文 + 旧历史摘要 | 近期对话优先 | LLM 压缩失败后用 heuristic |
| RAG | 句子级压缩 + token 截断 | 与 query 相关片段优先 | 超预算截断，检索失败则为空 |
| Memory | 检索排序 + token 截断 | 高重要度、近期记忆优先 | 检索失败则为空 |
| final_text | 总预算硬截断 | 前序拼接内容优先 | 标记 hard_truncated |

## 7. 上下文预算事件

| 字段 | 用途 |
|---|---|
| history_tokens_est | 判断历史是否挤占上下文 |
| rag_tokens_est | 判断教材参考是否过长 |
| memory_tokens_est | 判断记忆是否过长 |
| final_tokens_est | 判断最终 prompt 压力 |
| context_pressure_ratio | final_tokens / budget，流式状态中输出 |
| history_summary_source | 判断使用了 LLM 摘要还是启发式摘要 |
| hard_truncated | 判断是否发生不可避免截断 |

## 8. 失败与降级

| 异常情况 | 当前处理 | 对主流程影响 |
|---|---|---|
| LLM 历史压缩关闭 | 不调用 LLM | 使用启发式摘要或只保留近期历史 |
| LLM 历史压缩超时 | 记录失败事件 | 回退启发式摘要 |
| LLM 返回格式异常 | 视为压缩失败 | 回退启发式摘要 |
| RAG 为空 | 不拼教材参考 | 回答缺少教材证据 |
| Memory 为空 | 不拼历史记忆 | 回答缺少个性化历史 |
| final_text 过长 | 执行硬截断 | 保证不会超过预算 |

## 9. 当前边界

| 能力 | 当前状态 |
|---|---|
| 压缩质量评估 | 已记录预算指标，但未对摘要真实性做专门评分 |
| 上下文重排 | 当前按 history、RAG、memory 固定顺序拼接 |
| 语义级去重 | 当前主要依赖截断和上游检索，不做复杂语义去重 |
| 重要信息保护 | 超预算时会硬截断，尚未实现“关键事实不可丢失”约束 |
