---
last_updated: 2026-05-28
status: active
owner: coding-agent
---

# 命名规范

## 文件命名

- Python 模块文件：`snake_case.py`（如 `stage_gates.py`、`llm_client.py`）
- Markdown 文档：`kebab-case.md`（如 `data-flow.md`、`error-handling.md`）
- JSON 配置文件：`snake_case.json`（如 `feature_list.json`）
- SKILL 文件：大写 `SKILL.md`

## Python 代码命名

| 元素 | 规范 | 示例 |
|------|------|------|
| 包/模块 | snake_case | `insight_engine`, `stage_runner` |
| 类 | PascalCase | `InsightEngineState`, `StageHooks` |
| 函数/方法 | snake_case | `collect_raw_items()`, `build_prompt_package()` |
| 变量 | snake_case | `run_id`, `max_retry` |
| 常量 | UPPER_SNAKE_CASE | `REQUIRED_TREND_KEYS`, `AI_KEYWORDS` |
| 私有函数 | _leading_underscore | `_parse_datetime()`, `_fallback_events()` |
| Dataclass | PascalCase | `ContextPackage`, `PromptPackage` |

## 字段命名规范

### State 字段
- 数据容器：`{scope}_{entity}` 模式（如 `global_raw_items`、`ai_structured_events`）
- 布尔标志：`is_` 或 `should_` 前缀（如 `is_ai_related`、`should_analyze_ai`）
- 排除原因：`{entity}_reasons` 或 `{entity}_exclusion_reasons`

### JSON artifact 字段
- 使用 `snake_case`（如 `run_id`、`target_date`、`hotness_score`）
- 列表/数组用复数（如 `events`、`items`、`warnings`）

## 禁止的命名

- ❌ 拼音变量名
- ❌ 无意义的缩写（如 `tmp`、`x`、`foo`）
- ❌ 类型前缀（如 `strName`、`dictData`）——Python 不需要匈牙利命名法
- ❌ 与标准库同名的变量（如 `list`、`dict`、`str`）
