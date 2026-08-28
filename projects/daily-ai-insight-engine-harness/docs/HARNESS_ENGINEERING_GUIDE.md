# Coding Agent Harness 从零搭建指南

> 本文基于 Daily AI Insight Engine 项目的实际重构经验，告诉你如何将一个普通项目
> 改造为 coding-agent（Claude Code、Codex、Cursor 等）可自主维护的 Harness 工程。

---

## 一、什么是 Coding Agent Harness

Coding Agent Harness 是一套让 AI 编程助手"看得懂、改得对、改不坏"项目的基础设施。
它不是 CI/CD 也不是测试框架，而是这些东西**面向 Agent 的重新表达**：

- **传统工程**：文档给人类看，Linter 告诉人类哪里错了
- **Harness 工程**：文档给 Agent 看，Linter 告诉 Agent 哪里错了**以及怎么修**

核心闭环：**约束（Constrain）→ 告知（Inform）→ 验证（Verify）→ 纠正（Correct）**

---

## 二、落地路线图

落地 Harness Engineering 分三个阶段，不要一步到位：

```
Phase 1: 信息层（1-2天）         Phase 2: 约束层（3-5天）         Phase 3: 自动化层（1-2周）
┌───────────────────┐       ┌───────────────────┐       ┌───────────────────┐
│ AGENTS.md 地图模式  │  →    │ 分层架构 + Linter   │  →    │ Agent 自验证闭环    │
│ docs/ 结构化文档    │       │ CI 约束检查         │       │ 后台清理 Agent      │
│ 编码规范文档化      │       │ 错误信息含修复指令   │       │ Git Worktree 隔离   │
└───────────────────┘       └───────────────────┘       └───────────────────┘
  适合：所有项目               适合：中期项目                适合：长期维护项目
  收益：Agent 输出一致性 ↑     收益：代码质量可控            收益：人工审查量 ↓↓↓
```

---

## 三、Phase 1：信息层——让 Agent "看得懂"你的项目

### 3.1 第一步：创建 AGENTS.md（约 1 小时）

AGENTS.md 是整个 Harness 的入口。它不是 README，而是给 AI 编程助手看的**任务地图**。

**核心原则**：
- 控制在 50-100 行。超过说明你在写百科全书
- "你想做什么 → 去哪里看" 比 "这是什么" 更有效——面向任务而非面向知识
- 硬性规则单独列出，这些是 CI 会强制验证的

**模板**（直接抄）：

```markdown
# AGENTS.md

## 项目简介
[一句话描述你的项目]

## 快速导航
| 你想做什么 | 去哪里看 |
|-----------|---------|
| 了解系统架构 | docs/architecture/overview.md |
| 了解模块边界和依赖规则 | docs/architecture/boundaries.md |
| 了解编码规范 | docs/conventions/README.md |
| 了解当前迭代任务 | docs/plans/current-sprint.md |
| 了解测试规范 | docs/conventions/testing.md |

## 硬性规则（必须遵守，CI 会验证）
1. [你的架构规则，比如依赖方向]
2. [单文件行数限制]
3. [新增代码必须有测试]
4. [特定禁止项]

## 项目文件纲要
[完整的目录结构，让 Agent 知道每个文件在哪]

## 修改约束
1. 修改目录结构时，必须同步更新本文件的文件纲要
2. 新增 X 时，必须同步更新 Y、Z、测试

## 提交规范
- feat: 新功能
- fix: 修复
- refactor: 重构
- docs: 文档
- test: 测试

## 验证命令
[怎么跑编译检查、Lint、测试]
```

### 3.2 第二步：建立 docs/ 结构化知识库（约 2-4 小时）

```text
docs/
├── architecture/              # 稳定层（很少变）
│   ├── overview.md            # 系统架构图 + 一段话描述
│   ├── boundaries.md          # 模块边界和依赖规则
│   └── data-flow.md           # 数据流转图
│
├── conventions/               # 规范层（偶尔更新）
│   ├── README.md              # 规范总览（索引）
│   ├── naming.md              # 命名规范
│   ├── error-handling.md      # 错误处理规范
│   ├── testing.md             # 测试规范
│   └── logging.md             # 日志规范
│
├── design/                    # 设计层（按功能组织）
│   ├── feature-xxx.md         # Status: ✅ Implemented
│   ├── feature-yyy.md         # Status: 📋 Approved
│   └── feature-zzz.md         # Status: 📝 Draft
│
├── plans/                     # 计划层（频繁变）
│   ├── current-sprint.md      # 当前迭代
│   └── backlog.md             # 待办
│
└── reference/                 # 参考层（自动生成）
    ├── api-spec.yaml
    └── error-codes.md
```

**每个文档头部必须加元信息**：

```yaml
---
last_updated: 2026-05-28
status: active          # active | deprecated | draft
owner: coding-agent
---
```

这一步的人工工作量最大，但它决定了 Agent 能不能做出正确的架构决策。

### 3.3 第三步：创建跟踪文件（约 30 分钟）

**feature_list.json**：功能完成标准
```json
{
  "project": "项目名",
  "purpose": "一句话目的",
  "features": [
    {
      "id": "F001",
      "name": "功能名",
      "priority": "P0",
      "status": "done",
      "acceptance": ["验收标准1", "验收标准2"]
    }
  ]
}
```

**progress.json**：跨会话学习进度
```json
{
  "project": "项目名",
  "current_learning_goal": "当前学习目标",
  "last_updated": "2026-05-28",
  "completed": ["已完成项"],
  "active_harness_concepts": ["当前使用的 Harness 概念"],
  "next_steps": [{"order": 1, "task": "下一步"}]
}
```

---

## 四、Phase 2：约束层——让 Agent "不得不"写好代码

### 4.1 分层架构和依赖方向文档化（约 1 小时）

在 `docs/architecture/boundaries.md` 中明确写出：

- 每层能依赖什么
- 每层不能依赖什么
- 横切关注点（auth/log/hook）的注入方式
- 具体的违反示例和正确做法

### 4.2 自定义 Linter 规则（约 2-4 小时）

**最核心的原则**：每条 Linter 报错必须包含三要素——

```
❌ [什么错了]
✅ FIX: [怎么改，给出代码片段]
📖 See: [哪个文档有详细说明]
```

Agent 看到这种报错，不需要任何额外提示就能自动修复。

**Python 项目 Linter 实现示例**：

```python
# scripts/harness_linter.py
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

def main() -> int:
    issues = []
    # 检查必需文件是否存在
    issues.extend(check_required_files())
    # 检查 AGENTS.md 是否包含关键短语
    issues.extend(check_agents_contract())
    # 检查确定性模块是否偷偷调了 LLM
    issues.extend(check_deterministic_stages_do_not_call_llm())
    # 检查每个 stage 是否有对应 linter
    issues.extend(check_stage_linter_pairs())

    payload = {"passed": not issues, "issue_count": len(issues), "issues": issues}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not issues else 1

if __name__ == "__main__":
    sys.exit(main())
```

**把主观品味翻译成机械规则**：

| 口头约定 | 机械化规则 | 检查方式 |
|----------|-----------|----------|
| "函数要短" | 单函数 ≤ 50 行 | harness_linter 检查 |
| "文件要短" | 单文件 ≤ 300 行 | harness_linter 检查 |
| "确定性代码不调LLM" | 禁用特定 import token | harness_linter 扫描 |
| "每个 stage 要有 linter" | stage-linter 配对检查 | harness_linter 配对 |
| "文档要新鲜" | 设计文档 60 天内更新过 | CI 文档新鲜度检查 |

**经验法则**：如果一条规则在 Code Review 中被提过 3 次以上，就应该写成 Linter 规则。

### 4.3 CI 管线配置（约 1 小时）

```yaml
# .github/workflows/harness.yml
name: Harness Checks
on: [push, pull_request]

jobs:
  harness:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install test tools
        run: python -m pip install --upgrade pip pytest

      - name: Compile Python files
        run: python -m compileall src scripts tests

      - name: Run harness linter
        run: python scripts/harness_linter.py

      - name: Run tests
        run: python -m pytest
```

---

## 五、Phase 3：自动化层（长期项目推荐）

### 5.1 Git Worktree 隔离验证脚本

让 Agent 在隔离环境中修改和验证代码，不污染主分支：

```bash
#!/bin/bash
# scripts/agent-verify.sh
BRANCH=$1
WORKTREE_DIR="/tmp/agent-verify-$(date +%s)"

git worktree add "$WORKTREE_DIR" "$BRANCH"
cd "$WORKTREE_DIR"

python -m compileall src scripts tests || exit 1
python scripts/harness_linter.py || exit 1
python -m pytest || exit 1

cd -
git worktree remove "$WORKTREE_DIR"
echo "✅ 所有验证通过"
```

### 5.2 后台清理 Agent Prompt 模板

```
# 任务：代码库卫生清理

请执行以下检查，对每个发现的问题生成独立的修复 PR：

## 检查清单
1. 超长文件：找出超过 300 行的文件，拆分为更小模块
2. 缺失测试：找出没有对应测试文件的模块，补充基础测试
3. TODO/FIXME：列出所有 TODO/FIXME，超过 30 天未处理的生成清理 PR
4. 过时文档：检查 docs/design/ 中 Status 为 Draft 但已超过 30 天的文档

## 约束
- 每个修复作为独立 commit
- 确保所有测试通过
- 如果不确定某个修改是否安全，跳过并标注原因
```

### 5.3 文档新鲜度检查（CI 中）

```yaml
- name: Doc Freshness
  run: |
    find docs/design/ -name '*.md' | while read f; do
      last_mod=$(git log -1 --format=%ct "$f")
      now=$(date +%s)
      days_old=$(( (now - last_mod) / 86400 ))
      if [ "$days_old" -gt 60 ]; then
        echo "⚠️ $f 已 ${days_old} 天未更新，可能已过期"
      fi
    done
```

---

## 六、从零到一的完整操作清单

假设你有一个现有的项目，按以下顺序操作：

### 第 1 天（信息层）

```
□ 创建 AGENTS.md
  □ 写一句话项目简介
  □ 创建快速导航表（5-8 行）
  □ 列出 5-7 条硬性规则
  □ 画出项目文件纲要
  □ 写修改约束和验证命令
  □ 确认在 50-100 行以内

□ 创建 docs/ 目录结构
  □ mkdir -p docs/{architecture,conventions,design,plans,reference}
  □ 写 docs/architecture/overview.md（系统架构图 + 一句话）
  □ 写 docs/architecture/boundaries.md（依赖方向和禁止项）
  □ 写 docs/architecture/data-flow.md（数据流转图）
  □ 写 docs/conventions/README.md（规范总览）
  □ 写 docs/conventions/naming.md
  □ 写 docs/conventions/error-handling.md
  □ 写 docs/conventions/testing.md
  □ 写 docs/conventions/logging.md

□ 创建跟踪文件
  □ 写 feature_list.json（列出所有功能 + 验收标准）
  □ 写 progress.json（当前学习目标 + 进度 + 下一步）
```

### 第 2-3 天（约束层）

```
□ 编写自定义 Linter
  □ 创建 scripts/harness_linter.py
  □ 加必需文件检查
  □ 加 AGENTS.md 内容合同检查
  □ 加确定性 stage 不调 LLM 检查
  □ 加 stage-linter 配对检查
  □ 每一个报错都包含 ❌ + ✅FIX + 📖See

□ 配置 CI
  □ 创建 .github/workflows/harness.yml
  □ 加 compileall 步骤
  □ 加 harness_linter 步骤
  □ 加 pytest 步骤

□ 跑通闭环
  □ 运行 python scripts/harness_linter.py → 确认全部 pass
  □ 运行 python -m pytest → 确认全部 pass
  □ 故意违反一条规则 → 确认 CI 报红
  □ 按 CI 报错信息修复 → 确认 CI 变绿
```

### 第 4-5 天（验证与迭代）

```
□ 用 Agent 验证
  □ 用 Claude Code 打开项目
  □ 给它一个修改任务："在 stage X 中加一个新功能"
  □ 观察 Agent 是否：
    □ 能通过 AGENTS.md 找到正确文件
    □ 遵守了 docs/conventions/ 中的规范
    □ 修改后跑了 harness_linter 和测试
    □ 在 CI 报错时能根据 ❌✅📖 信息自行修复

□ 迭代完善
  □ Agent 在哪里走错路 → 加强那个方向的文档
  □ 哪种错误反复出现 → 加一条 Linter 规则
  □ 哪个文档 Agent 从来不看 → 精简或删除
```

---

## 七、踩坑指南

### 坑 1：AGENTS.md 写太长

**症状**：Agent 输出质量下降，经常忽略部分规则。

**原因**：上下文窗口被指令文件占满，留给"正事"的空间不够。

**解法**：砍到 50-100 行。超过就移到 docs/，在 AGENTS.md 中只留链接。

### 坑 2：Linter 规则太多，Agent 陷入死循环

**症状**：Agent 修了一个错误，引入了另一个，反复循环。

**解法**：
- 逐条加规则，每次加完都让 Agent 试跑一遍
- 每条规则的 FIX 信息要给出具体代码片段
- 互相冲突的规则只保留一条

### 坑 3：架构约束太严，阻碍合理调用

**症状**：合理的代码模式被 CI 拦截，团队开始绕过规则。

**解法**：
- 设置"豁免白名单"机制
- 定期回顾约束规则，根据实际需要调整

### 坑 4：文档没人维护，比不写还误导

**症状**：Agent 参考了过时文档，基于错误假设写代码。

**解法**：
- CI 中加文档新鲜度检查
- 每两周跑一次 doc-gardening Agent
- 设计文档加 status 字段，过期的标 deprecated

### 坑 5：过度依赖 Agent，忘了审查

**解法**：每周花 30 分钟做"环境审查"：
- 最近一周 CI 失败率是否上升？
- Linter 规则是否覆盖了新出现的 bad pattern？
- AGENTS.md 和 docs/ 是否跟代码库一致？

---

## 八、关键工具参考

### Agent 工具选择

| 工具 | 最适合场景 |
|------|-----------|
| Claude Code | 深度自主任务，强大的 CLI agent |
| Aider | 个人开发者，终端结对编程 |
| Cline | VS Code 内 Plan/Act 模式 |
| Cursor | IDE 内 Agent 辅助编程 |

### Python 项目工具推荐

| 用途 | 工具 |
|------|------|
| 分层架构检查 | import-linter |
| 自定义规则检查 | 自写 harness_linter.py |
| 类型检查 | mypy |
| 代码覆盖率 | pytest-cov |

---

## 九、总结

Harness Engineering 的核心不是搭建复杂的基础设施，而是一个简单的闭环：

```
约束（Constrain）→ 告知（Inform）→ 验证（Verify）→ 纠正（Correct）
```

从 AGENTS.md 和一条 Linter 规则开始，比什么都不做强一百倍。

**最终检查清单**：

```
□ Phase 1: 信息层
  □ AGENTS.md（50-100 行，地图模式）
  □ docs/ 结构化目录（architecture/conventions/design/plans/reference）
  □ feature_list.json + progress.json

□ Phase 2: 约束层
  □ 分层架构文档 + Linter 规则
  □ 每条 Linter 错误包含 ❌+✅FIX+📖See
  □ CI：compileall + harness_linter + pytest

□ 持续维护
  □ 每周 30 分钟"环境审查"
  □ 每月回顾并更新 Linter 规则
  □ 每两周运行 doc-gardening

□ 终极验证
  □ 让 Agent 修改一处代码 → Agent 自己跑通验证 → CI 通过
```

当你看到 Agent 收到 CI 报错后，自己读了文档、找到了修复方法、提交了通过所有检查的代码时，
你就知道 Harness Engineering 真正开始运转了。
