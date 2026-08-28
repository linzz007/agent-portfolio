# 后台清理 Agent —— 定时任务 Prompt

这个文件是给 AI 编程助手（Claude Code / Codex / Cursor）定期执行的清理任务 Prompt。
建议每周运行一次，或通过 CI 定时触发。

---

## 使用方式

在 Claude Code 中运行：

```
请按照 docs/reference/cleanup-agent-prompt.md 中的检查清单，
对代码库进行卫生清理。对每个发现的问题生成独立的修复 PR。
```

或者设置定时任务：
```bash
# crontab: 每周一早上 9 点触发
# 0 9 * * 1 cd /path/to/project && claude "按 docs/reference/cleanup-agent-prompt.md 执行清理"
```

---

## 检查清单

请执行以下检查，对每个发现的问题生成**独立的 commit**（不要混在一起）：

### 1. 超长文件
找出 `src/` 下超过 300 行的文件。
对每个超大文件：
- 分析是否可以拆分为更小的模块
- 如果可以拆分，执行拆分并提交
- 如果不确定是否安全，跳过并在报告中标注原因

### 2. 缺失测试
找出 `src/` 下没有对应 `test_*.py` 文件的模块。
对每个缺失测试的模块：
- 补充基础的功能测试（至少覆盖主函数的 happy path）
- 提交时注明 "test: add tests for [模块名]"

### 3. TODO/FIXME 扫描
搜索代码库中所有 `TODO` 和 `FIXME` 注释。
对每个：
- 检查 git log 中该注释的引入时间
- 如果超过 30 天未处理，评估是否可以现在修复
- 如果可以修复，生成修复 commit
- 如果暂时无法处理，在注释后面加上日期标记 `TODO(2026-05-29): ...`

### 4. 过时文档
运行 `bash scripts/check-doc-freshness.sh`。
对每个 stale 文档：
- 如果 status=draft 且超过 60 天：标记为 deprecated 或删除
- 如果 status=active 但内容明显过时：更新内容
- 提交时注明 "docs: update/cleanup [文件名]"

### 5. 重复代码
找出高度相似的代码段（>10 行）。
对每处重复：
- 提取为共享工具函数
- 更新所有引用点
- 提交时注明 "refactor: extract shared [函数名]"

---

## 约束

1. **每个修复作为独立 commit**，不要混在一个 commit 里
2. **每次修改后运行以下验证**：
   ```bash
   python3 -m compileall src scripts tests
   python3 scripts/harness_linter.py
   python3 -m pytest
   ```
3. **全部通过才允许提交**
4. **如果不确定某个修改是否安全，跳过**，在最终报告中标注"⚠️ 跳过：[文件名] - [原因]"
5. **Commit 消息格式**：
   - `chore(cleanup): split oversized file [文件名]`
   - `test: add tests for [模块名]`
   - `docs: mark stale design doc [文件名] as deprecated`
   - `refactor: extract shared [函数名]`

## 最终报告

全部完成后，输出一份清理报告：

```
## 代码库清理报告 — YYYY-MM-DD

### 已修复
- [x] 拆分超大文件: [文件名] (350行 → 200行 + 150行)
- [x] 补充测试: [模块名] (新增 3 个测试)
- [x] 清理 TODO: [描述]

### 已跳过
- [ ] [文件名] — 拆分后可能影响性能，需要更深入分析

### 当前状态
- Harness Linter: ✅ 通过 / ❌ 仍有 N 个问题
- 测试: N passed, M failed
- 超大文件: N 个（比上次少 M 个）
```
