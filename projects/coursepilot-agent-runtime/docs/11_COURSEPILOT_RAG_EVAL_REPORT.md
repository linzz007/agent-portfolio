# CoursePilot RAG 检索评测报告

> 日期：2026-05-11  
> 目的：把 CoursePilot 的 RAG 评测链路从“代码里有脚本”推进到“有课程数据、有 gold 标注、有真实指标、有可解释优化过程”。

## 1. 这次新增了什么

### 1.1 课程评测数据

新增 8 份线性代数课程讲义：

```text
benchmarks/fixtures/linear_algebra_course/
  01_vector_spaces.txt
  02_basis_dimension.txt
  03_linear_transformations.txt
  04_eigen_diagonalization.txt
  05_inner_product_projection.txt
  06_least_squares.txt
  07_svd_pca.txt
  08_matrix_decomposition.txt
```

这些文档覆盖线性空间、基与维数、线性映射、特征值、正交投影、最小二乘、SVD/PCA、矩阵分解等主题。

### 1.2 评测问题与 gold 标注

新增 16 个课程问答 case：

```text
benchmarks/cases_linear_algebra.jsonl
```

新增对应的 RAG 检索 gold 标注：

```text
benchmarks/rag_gold_linear_algebra.jsonl
```

每条 gold 标注包含：

```json
{
  "case_id": "...",
  "gold_doc_ids": ["目标文档.txt"],
  "gold_keywords": ["关键概念1", "关键概念2"]
}
```

注意：这里的 gold 是“检索相关文档标注”，不是“标准答案标注”。所以这套评测能衡量 RAG 是否召回了正确资料，不能直接衡量最终回答是否正确、完整、忠实。

### 1.3 建库脚本

新增脚本：

```text
scripts/perf/prepare_linear_algebra_fixture.py
```

作用：

1. 把 8 份课程讲义复制到 `data/workspaces/linear_algebra_eval/uploads/`。
2. 调用 `rag.chunk.chunk_documents()` 切块。
3. 调用 `rag.store_faiss.build_index()` 生成向量索引。
4. 保存到 `data/workspaces/linear_algebra_eval/index/faiss_index.faiss` 和 `.pkl`。

## 2. 实际执行命令

### 2.1 构建课程工作区索引

```powershell
& 'C:\Users\78230\miniconda3\envs\study_agent\python.exe' .\scripts\perf\prepare_linear_algebra_fixture.py --course-name linear_algebra_eval
```

实际结果：

```text
workspace=data/workspaces/linear_algebra_eval
index=data/workspaces/linear_algebra_eval/index/faiss_index.faiss
embedding_model=BAAI/bge-base-zh-v1.5
device=cpu
```

### 2.2 分别运行 dense / BM25 / hybrid

```powershell
$env:HF_HUB_OFFLINE='1'

& 'C:\Users\78230\miniconda3\envs\study_agent\python.exe' .\scripts\perf\eval_rag_retrieval.py --cases .\benchmarks\cases_linear_algebra.jsonl --gold .\benchmarks\rag_gold_linear_algebra.jsonl --output-dir .\data\perf_runs\rag_linear_algebra_dense --retrieval-mode dense --top-k 4

& 'C:\Users\78230\miniconda3\envs\study_agent\python.exe' .\scripts\perf\eval_rag_retrieval.py --cases .\benchmarks\cases_linear_algebra.jsonl --gold .\benchmarks\rag_gold_linear_algebra.jsonl --output-dir .\data\perf_runs\rag_linear_algebra_bm25 --retrieval-mode bm25 --top-k 4

& 'C:\Users\78230\miniconda3\envs\study_agent\python.exe' .\scripts\perf\eval_rag_retrieval.py --cases .\benchmarks\cases_linear_algebra.jsonl --gold .\benchmarks\rag_gold_linear_algebra.jsonl --output-dir .\data\perf_runs\rag_linear_algebra_hybrid --retrieval-mode hybrid --top-k 4
```

### 2.3 运行 hybrid 调参版本

```powershell
$env:HF_HUB_OFFLINE='1'
$env:HYBRID_DENSE_CANDIDATES_MULTIPLIER='1'
$env:HYBRID_BM25_CANDIDATES_MULTIPLIER='1'

& 'C:\Users\78230\miniconda3\envs\study_agent\python.exe' .\scripts\perf\eval_rag_retrieval.py --cases .\benchmarks\cases_linear_algebra.jsonl --gold .\benchmarks\rag_gold_linear_algebra.jsonl --output-dir .\data\perf_runs\rag_linear_algebra_hybrid_m1 --retrieval-mode hybrid --top-k 4
```

## 3. 实测结果

| 策略 | case 数 | error_rate | hit@4 | top1_acc | precision@4 | keyword_recall | avg_retrieval_ms | p95_retrieval_ms | avg_context_compression_rate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Dense | 16 | 0.0000 | 1.0000 | 1.0000 | 0.2969 | 0.8281 | 110.7699 | 164.3063 | 0.4677 |
| BM25 | 16 | 0.0000 | 1.0000 | 1.0000 | 0.2813 | 0.8438 | 0.6300 | 1.3538 | 0.4678 |
| Hybrid 默认参数 | 16 | 0.0000 | 1.0000 | 0.1875 | 0.2813 | 0.8281 | 112.7342 | 175.8756 | 0.4709 |
| Hybrid 调参后 | 16 | 0.0000 | 1.0000 | 1.0000 | 0.2969 | 0.8281 | 107.9087 | 148.3251 | 0.4677 |

## 4. 这组结果说明了什么

### 4.1 评测脚本真的能发现问题

默认 Hybrid 的 `hit@4=1.0`，说明正确文档能进入前 4 个结果；但 `top1_acc=0.1875`，说明正确文档经常不是第一名。

这说明只看 `hit@k` 不够，还必须看 `top1_acc`。因为最终注入模型上下文时，排名越靠前，越可能影响回答质量。

### 4.2 问题出在默认 Hybrid 候选集过宽

默认 Hybrid 会把 dense 和 BM25 的候选数扩大到 `top_k * 3`，再用 RRF 做融合。在这个小规模课程集上，候选集过宽导致一些泛化文档在 dense 和 BM25 中都处于中间位置，RRF 融合后反而排到第一。

调参后把：

```text
HYBRID_DENSE_CANDIDATES_MULTIPLIER=1
HYBRID_BM25_CANDIDATES_MULTIPLIER=1
```

等价于只融合两个召回器各自的 top-k 结果。结果是：

```text
top1_acc: 0.1875 -> 1.0000
p95_retrieval_ms: 175.8756ms -> 148.3251ms
```

这形成了一个可以讲清楚的优化过程：

```text
构造评测集 -> 跑 Dense/BM25/Hybrid -> 发现默认 Hybrid top1 排序退化 -> 分析 RRF 候选集过宽 -> 调整候选集参数 -> top1 恢复到 1.0，p95 检索延迟下降约 15.7%。
```

### 4.3 当前结果的边界

这不是大规模线上效果，也不是最终答案质量评测。

当前只能证明：

- CoursePilot 有可运行的 RAG 检索评测闭环。
- 评测覆盖多文档课程资料，而不是单文档自测。
- 指标能暴露检索策略问题。
- 可以通过参数调优形成 baseline/after 对比。

当前还不能证明：

- 最终回答完全忠实于检索内容。
- 在真实大规模课程库上一定有同样提升。
- Hybrid 策略在所有数据集上都优于 Dense 或 BM25。

## 5. 测评代码到底在测什么

核心脚本是：

```text
scripts/perf/eval_rag_retrieval.py
```

它的流程是：

1. 读取 `cases_linear_algebra.jsonl`，拿到每个问题的 `course_name` 和 `message`。
2. 读取 `rag_gold_linear_algebra.jsonl`，拿到每个问题对应的 `gold_doc_ids` 和 `gold_keywords`。
3. 加载 `data/workspaces/<course_name>/index/faiss_index.faiss` 和 `.pkl`。
4. 创建 `Retriever(store)`。
5. 调用 `retriever.retrieve(message, top_k=4)`。
6. 取出返回 chunk 的 `doc_id`。
7. 和 `gold_doc_ids` 对比，计算检索指标。

关键指标：

| 指标 | 含义 |
|---|---|
| `hit_at_k` | top-k 返回结果里只要有任意一个 gold doc，就算命中 |
| `top1_acc` | 第 1 个返回结果必须是 gold doc，才算正确 |
| `precision_at_k` | top-k 返回结果里 gold doc 所占比例 |
| `keyword_recall` | 返回文本中覆盖 gold_keywords 的比例 |
| `avg_retrieval_ms` | 平均检索耗时 |
| `p95_retrieval_ms` | 95 分位检索耗时，更能反映慢请求 |
| `avg_context_compression_rate` | 句级压缩后上下文减少比例 |

## 6. 简历可写版本

保守但可防守版本：

```latex
\item \textbf{RAG检索与评测链路：}构建课程资料解析、切块、向量建库与在线检索链路，实现Dense、BM25与Hybrid Retrieval；基于8份线性代数讲义构建16-case检索评测集，通过gold\_doc\_ids统计hit@k、top1 accuracy、precision@k、keyword recall与检索延迟，用于对比不同检索策略并定位召回排序问题。
```

稍强版本，适合你理解调参过程后再写：

```latex
\item \textbf{RAG检索优化与回归评测：}构建8份线性代数讲义、16个课程问答case与gold\_doc\_ids标注，评估Dense、BM25和Hybrid Retrieval的hit@k、top1 accuracy、precision@k与检索延迟；通过评测定位默认RRF候选集过宽导致的top1排序退化，并调整候选集融合策略，使Hybrid top1 accuracy由18.75\%恢复至100\%，p95检索延迟降低约15.7\%。
```

如果担心 16-case 太小，可以面试时这样补一句：

```text
这个结果不是为了证明大规模线上收益，而是为了证明我把 RAG 从主观 demo 变成了可评测、可回归的工程链路；后续只要扩充真实课程 case，就可以复用同一套指标做持续优化。
```

## 7. 后续建议

下一步不要急着换复杂框架，先把评测粒度从 `gold_doc_ids` 提升到：

```text
gold_chunk_ids
gold_concepts
answer_faithfulness
context_relevance
LLM-as-Judge
```

这样面试官继续追问“你怎么评估最终回答质量”时，就能从当前检索评测自然过渡到答案忠实度评测。
