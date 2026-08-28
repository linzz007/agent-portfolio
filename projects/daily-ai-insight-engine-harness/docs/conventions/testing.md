---
last_updated: 2026-05-28
status: active
owner: coding-agent
---

# 测试规范

## 测试框架

- 使用 `pytest` 作为测试框架
- 测试文件放在 `tests/` 目录下
- 文件名格式：`test_{module_name}.py`
- 测试函数名格式：`test_{what}_{condition}`

## 测试覆盖要求

| 模块 | 最低覆盖要求 | 说明 |
|------|-------------|------|
| harness/graph.py | 路由正确性 | 正常流程走到 done，空数据走到 failed |
| harness/tool_gateway.py | 白名单控制 | 未注册的 stage 抛出 PermissionError |
| harness/hooks/ | 解析和校验 | JSON 解析、字段校验 |
| conversation/router.py | 意图分类 | 日报意图 vs 普通聊天 |

## 测试运行

```bash
# 运行全部测试
python -m pytest

# 运行特定测试文件
python -m pytest tests/test_graph.py

# 带覆盖率
python -m pytest --cov=src/insight_engine
```

## 测试编写规范

1. 每个 stage handler 的测试使用最小 mock state
2. Graph 测试使用 `_build_passing_hooks()` 隔离 hook 行为
3. 意图路由测试覆盖 LLM 可用和不可用两种路径
4. 测试数据不依赖外部 API（所有数据源调用需要 mock）
5. 新增 stage 时必须同步新增对应的测试

## CI 中的测试

CI 在 `.github/workflows/harness.yml` 中自动运行：
- `compileall`：语法检查
- `harness_linter.py`：Harness 静态检查
- `pytest`：全部单元测试
