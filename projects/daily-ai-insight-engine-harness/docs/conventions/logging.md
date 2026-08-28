---
last_updated: 2026-05-28
status: active
owner: coding-agent
---

# 日志规范

## 日志体系

本项目不使用 `console.log` 或 `print()`。所有运行时信息通过 State 的 `errors` 和 `warnings` 机制记录：

```python
# 记录致命错误
state.add_error(stage="stage_name", message="错误描述", detail={...})

# 记录非致命警告
state.add_warning(stage="stage_name", message="警告描述", detail={...})
```

## 日志出现在哪里

| 位置 | 说明 |
|------|------|
| `state.errors` | 运行时致命错误列表 |
| `state.warnings` | 运行时非致命警告列表 |
| `state.stage_trace` | 每个 stage 的执行轨迹（时间、状态、产物） |
| `state.stage_gate_results` | 每个 stage gate linter 的检查结果 |
| `pipeline_summary.md` | 流程摘要（含 counts、trace、gate、warnings、errors） |
| `run_artifact.json` | 完整自包含审计产物 |

## 结构化日志格式

Error 和 Warning 条目使用统一格式：

```python
{
    "stage": "collect_raw_items",      # 哪个 stage
    "message": "数据源抓取失败：xxx",    # 可读描述
    "detail": {...},                    # 可选的结构化上下文
    "created_at": "2026-05-28T12:00:00+00:00"  # UTC 时间戳
}
```

## 各 Stage 的日志关注点

| Stage | 正常日志 | 异常日志 |
|-------|----------|----------|
| collect_raw_items | 源统计、抓取数量 | 单个源失败 warning，全部失败 error |
| clean_items | 清洗计数，去重数 | 缺少 title/URL warning |
| structure_events | LLM 状态，事件数量 | LLM 失败 → fallback warning |
| analyze_insights | ReAct trace，分析结果 | ReAct 失败 → fallback warning |
| generate_report | 报告产物路径 | 翻译失败 → 保留英文原题 warning |

## 禁止的做法

- ❌ `print()` ——脱离审计链路
- ❌ `logging.info()` ——不适合 CLI 工具（可以考虑，但需确保结构化）
- ❌ 在 linter 中写日志 ——linter 只返回检查结果，不写日志
- ❌ 直接抛出未捕获异常 ——应通过 `state.add_error()` 或 graph 的 try/except 处理
