# RAG 设计说明

## 1. 字段与数据对象

### 1.1 检索输入

| 字段 | 含义 | 来源 |
|---|---|---|
| query | 当前检索问题 | 用户输入、作答内容或 Agent 子任务 |
| top_k | 返回数量 | 请求参数或 TOP_K_RESULTS 默认值 |
| retrieval_mode | 检索模式 | RETRIEVAL_MODE，支持 dense、bm25、hybrid |
| course_name | 课程范围 | 上层运行上下文 |

### 1.2 RetrievedChunk

| 字段 | 含义 | 用途 |
|---|---|---|
| text | 检索片段正文，已做句子级压缩 | 拼入教材参考上下文 |
| doc_id | 文档 ID | 引用来源 |
| page | 页码 | 引用定位 |
| chunk_id | 分块 ID | 去重和排查 |
| score | 检索分数 | 排序、评估 |

### 1.3 retrieval trace event

| 字段 | 含义 |
|---|---|
| retrieval_ms | 检索耗时 |
| mode | dense、bm25 或 hybrid |
| top_k | 目标返回数量 |
| candidate_count | 候选片段数量 |
| returned_count | 实际返回数量 |
| success | 检索是否成功 |

## 2. 关键使用位置

| 使用位置 | 作用 |
|---|---|
| 学习模式回答前 | 检索教材内容，为 Tutor 提供证据 |
| 练习生成前 | 检索相关知识点，约束题目来源 |
| 练习评分前 | 检索教材依据，辅助判断答案 |
| 考试生成前 | 检索课程知识，生成更贴合教材的题目 |
| 考试评分前 | 检索依据，辅助评分解释 |
| ContextBudgeter | 将 RAG 片段作为“教材参考”段落加入最终上下文 |
| Harness Artifact | 保存 retrieval 结果和检索事件，供运行后审计 |

## 3. 检索模式差异

| 模式 | 数据来源 | 优点 | 风险 |
|---|---|---|---|
| dense | 向量索引 | 适合语义相近但关键词不同的问题 | 可能召回语义相关但不精确的片段 |
| bm25 | 关键词倒排 | 对公式名、术语、原文匹配更直接 | 对改写问题不够鲁棒 |
| hybrid | dense + bm25 + RRF 融合 | 同时利用语义和关键词 | 候选过宽时可能降低 top1 精度 |

Hybrid 并不默认代表“效果一定最好”。项目里保留了评估脚本和参数开关，用 hit@k、top1、precision、keyword_recall、耗时等指标判断当前课程数据上哪种配置更合适。

## 4. 检索流程

| 步骤 | 处理方式 | 输出 |
|---|---|---|
| 读取配置 | 根据 RETRIEVAL_MODE 选择 dense、bm25、hybrid | 检索策略 |
| Dense 检索 | query embedding 后查 FAISS | dense 候选 |
| BM25 检索 | 基于关键词得分查文本索引 | bm25 候选 |
| Hybrid 融合 | 对 dense 和 bm25 候选做 RRF 加权 | 去重后的融合候选 |
| 片段压缩 | 对每个 chunk 做句子级压缩 | 更短的 text |
| 返回结果 | 截取 top_k | RetrievedChunk 列表 |
| 记录 trace | 写 retrieval 事件 | 检索耗时和数量统计 |

## 5. 压缩字段与配置

| 配置 | 含义 | 默认行为 |
|---|---|---|
| CB_RAG_SENT_PER_CHUNK | 每个 chunk 保留句子数 | 默认保留少量最相关句 |
| CB_RAG_SENT_MAX_CHARS | 单句最大字符数 | 超长句截断 |
| HYBRID_RRF_K | RRF 平滑参数 | 控制排名融合敏感度 |
| HYBRID_DENSE_WEIGHT | dense 权重 | 控制语义检索影响 |
| HYBRID_BM25_WEIGHT | bm25 权重 | 控制关键词检索影响 |
| HYBRID_DENSE_CANDIDATES_MULTIPLIER | dense 候选倍数 | 控制候选宽度 |
| HYBRID_BM25_CANDIDATES_MULTIPLIER | bm25 候选倍数 | 控制候选宽度 |

## 6. 与上下文预算的关系

| 阶段 | 责任 |
|---|---|
| Retriever | 默认负责 RAG 片段的句子级压缩 |
| ContextBudgeter | 控制 RAG 总 token 预算，必要时再截断 |
| Prompt 组装 | 将 RAG 片段放入“教材参考”区域 |
| Artifact | 保存 retrieval 和 context_budget，便于判断检索是否挤占上下文 |

## 7. 评估口径

| 指标 | 含义 |
|---|---|
| hit_at_k | top_k 中是否命中目标文档或片段 |
| top1_acc | 第一条是否命中 |
| precision_at_k | 返回片段中有效命中的比例 |
| keyword_recall | 关键词覆盖度 |
| avg_retrieval_ms | 平均检索耗时 |
| p95_retrieval_ms | 高分位耗时 |
| avg_context_compression_rate | RAG 压缩率 |

## 8. 失败与降级

| 异常情况 | 当前处理 | 结果 |
|---|---|---|
| 检索无结果 | 返回空列表 | prompt 中不加入教材参考 |
| 某种模式效果差 | 可切换 dense、bm25、hybrid 或调整权重 | 通过评估脚本验证后选择配置 |
| chunk 句子压缩无匹配 | 退回片段开头句子 | 保证仍有可用文本 |
| RAG 内容过长 | ContextBudgeter 按预算截断 | 避免超过模型上下文 |
| 检索事件异常 | trace 中记录失败 | 主流程可继续，但缺少 RAG 证据 |

## 9. 当前边界

| 能力 | 当前状态 |
|---|---|
| Query 改写 | 当前不是独立模块化能力 |
| Cross-encoder rerank | 当前未接入 |
| 引用可信度校验 | 当前主要依赖检索分数和评估脚本，不做 LLM 二次判定 |
| 知识库更新治理 | 当前文档未覆盖完整 ingestion 生命周期 |
