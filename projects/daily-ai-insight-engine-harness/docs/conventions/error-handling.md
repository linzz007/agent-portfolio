---
last_updated: 2026-05-28
status: active
owner: coding-agent
---

# 错误处理规范

## 错误分级

| 级别 | 含义 | 处理方式 | 记录位置 |
|------|------|----------|----------|
| Error | 致命错误，流程无法继续 | `state.add_error()` → graph 终止 | state.errors + pipeline_summary |
| Warning | 非致命问题，流程继续 | `state.add_warning()` → 流程继续 | state.warnings + pipeline_summary |
| Exception | Python 异常 | graph 捕获 → 记录 error → 终止 | state.errors |

## 渐进降级策略

```text
优先级 1：LLM 正常输出
    ↓ 失败
优先级 2：LLM repair（把 linter 错误反馈给 LLM，要求修复）
    ↓ 仍失败
优先级 3：Rule-based fallback（确定性规则生成结果，标注 fallback 来源）
    ↓ 确保系统始终可运行
```

## 各 Stage 的错误处理模式

### 确定性 Stage（collect_raw_items、clean_items）
- 数据源抓取失败：记录 warning，继续处理其他源
- 全部数据源失败：记录 error，graph 走到 failed
- **不动用 LLM 兜底**（这是原则性问题）

### LLM Stage（structure_events、analyze_insights、generate_report）
- LLM 超时/不可用：自动降级到规则 fallback，记录 warning
- JSON 解析失败：`after_llm_call.parse_json_output` 尝试从文本中提取 JSON
- Schema linter 不通过：触发一次 repair，仍不通过则 fallback
- ReAct 动作违规：记录 observation，强制 LLM 按计划执行
- ReAct 超步数：超过 `ANALYZE_REACT_MAX_STEPS` 后自动 fallback

## 错误消息规范

错误和 warning 使用一致的格式：

```python
state.add_error(
    stage="stage_name",
    message="简洁描述什么问题",
    detail={"key": "value", ...}  # 可选的详细上下文
)
```

## 禁止的错误处理方式

- ❌ 裸 `try/except: pass` ——吞掉异常不留痕
- ❌ `print()` 代替 warning/error ——脱离审计链路
- ❌ 在确定性 stage 中调用 LLM 做兜底
- ❌ 在 linter 中修改 state 数据
