# Coding-Agent Harness 通用操作手册

> 不论拿到什么项目（前端/后端/全栈/CLI 工具/数据处理），
> 按本手册操作，从零到完全由 AI 编程助手自主维护。

---

## 零、核心哲学（先读这个，5 分钟）

### Harness 不是一套工具，是一种工作方式

```
传统开发：你写代码 → 你检查 → 你修复 → 你提交
Harness 开发：你说需求 → Agent 写代码 → Harness 检查 → Agent 自修复 → Agent 提交
```

### 四个核心概念

| 概念 | 是什么 | 举例 |
|------|--------|------|
| **软约束** | 文档里写的规则，Agent 读不读取决于它自己 | "确定性 stage 不调 LLM" 写在 AGENTS.md 里 |
| **硬约束** | CI/Linter 自动检查的规则，Agent 绕不过去 | harness_linter.py 扫描代码里的 LLM API 域名 |
| **Fallback** | 某个环节失败时的降级方案 | LLM 不可用时用规则生成结果 |
| **反馈闭环** | 每个错误都携带修复指令（❌+✅FIX+📖See） | Agent 看到报错就知道怎么修 |

### 螺旋上升原则

**永远不要在写代码之前写完所有文档。永远不要在代码稳定之前加硬约束。**

```
Sprint 0：最简可运行代码
   ↓ 稳定了
软约束（文档）
   ↓ 稳定了
硬约束（Linter）
   ↓ 在新约束的保护下
Sprint 1：下一个功能
   ↓ 发现 Agent 绕过了某条规则
补约束
   ↓
Sprint 2...循环
```

---

## 一、通用项目文件架构

不管什么语言、什么类型的项目，Harness 层的目录结构是一样的：

```text
project-root/
│
├── AGENTS.md                    # 【必选】AI 编程助手的常驻上下文（地图模式）
├── CLAUDE.md                    # 【推荐】Claude Code 专属入口（更精简）
├── README.md                    # 【必选】人类读者的说明文档
│
├── docs/                        # 【必选】结构化知识库
│   ├── architecture/            #    稳定层：系统设计（很少变）
│   │   ├── overview.md          #      架构总览 + 一句话描述
│   │   ├── boundaries.md       #      模块边界 + 依赖规则
│   │   └── data-flow.md        #      数据/请求流转图
│   │
│   ├── conventions/             #    规范层：编码规范（偶尔更新）
│   │   ├── README.md            #      规范总览（索引页）
│   │   ├── naming.md            #      命名规范
│   │   ├── error-handling.md    #      错误处理 + 降级策略
│   │   ├── testing.md           #      测试规范
│   │   └── logging.md           #      日志规范
│   │
│   ├── design/                  #    设计层：功能设计（按功能拆分）
│   │   ├── feature-xxx.md       #      Status: ✅ Implemented
│   │   └── feature-yyy.md       #      Status: 📝 Draft
│   │
│   ├── plans/                   #    计划层：迭代管理（频繁变）
│   │   ├── current-sprint.md    #      当前迭代任务
│   │   └── backlog.md           #      待办列表
│   │
│   └── reference/               #    参考层：自动生成或手动维护
│       ├── api-spec.yaml        #      API 规范
│       └── error-codes.md       #      错误码表
│
├── feature_list.json            # 【必选】功能完成标准（机器可读）
├── progress.json                # 【必选】跨会话学习进度
│
├── scripts/                     # 【必选】Harness 脚本
│   ├── harness_linter.py        #    核心：硬约束检查器（换成你的语言）
│   ├── pre-commit               #    Git pre-commit hook
│   ├── agent-guardrails.sh      #    Agent 一键全检脚本
│   ├── agent-verify.sh          #    Git worktree 隔离验证
│   └── install-hooks.sh         #    一键安装 git hooks
│
├── .github/workflows/           # 【推荐】CI 配置
│   └── harness.yml              #    自动运行 linter + 测试
│
├── .claude/                     # 【可选】Claude Code 专属配置
│   └── settings.json            #    权限 + hooks
│
├── src/                         # 你的业务代码（结构由你决定）
├── tests/                       # 测试代码
└── config/                      # 配置文件
```

### 不同项目类型的业务代码结构建议

**后端 API 项目（Python/Go/Node）：**
```text
src/
├── models/          # 数据模型（不依赖任何模块）
├── repository/      # 数据访问层（只依赖 models/）
├── service/         # 业务逻辑层（依赖 models/ + repository/）
├── api/             # 路由和 handler（依赖 service/）
└── utils/           # 工具函数（不依赖业务模块）
```

**前端项目（React/Vue）：**
```text
src/
├── types/           # 类型定义（不依赖任何模块）
├── lib/             # 工具函数和 API 客户端（只依赖 types/）
├── hooks/           # 自定义 hooks（依赖 lib/）
├── components/      # UI 组件（只依赖 types/ + lib/）
├── pages/           # 页面（依赖 components/ + hooks/）
└── providers/       # Context/Provider（依赖 lib/）
```

**全栈项目（Next.js/Remix）：**
```text
src/
├── types/           # 共享类型（不依赖任何模块）
├── lib/             # 工具函数（只依赖 types/）
├── server/          # 服务端逻辑（依赖 types/ + lib/）
│   ├── repository/
│   └── service/
├── app/             # 路由页面（依赖所有层）
└── components/      # UI 组件（只依赖 types/ + lib/）
```

---

## 二、从零到 Harness 的完整路线图

### Phase 0：项目评估（30 分钟）

**在写任何代码之前，问自己 3 个问题：**

```
□ 这个项目的核心功能是什么？（一句话能说清楚）
□ 数据的入口和出口在哪？（用户输入 → ? → 最终输出）
□ 最少需要几个步骤？（3-5 个 stage 是黄金数量）
```

**产出物：**
- 在 `docs/plans/current-sprint.md` 里写一句话目标
- 在纸上画一个最简单的数据流草图

---

### Phase 1：Sprint 0 —— 最简可运行骨架（1-3 天）

**目标：让核心流程跑通，不是做出完整产品。**

```
正确做法：
  "先做一个单文件的 Python 脚本，能从 1 个 RSS 源抓新闻、转成 JSON、打印出来"
  → 跑通了 ✅

错误做法：
  "先建完整的项目结构，配好 CI，写好所有文档，再开始写第一行代码"
  → 60% 的时间花在文档上，写代码时发现架构假设是错的 ❌
```

**Sprint 0 的验收标准：**
```
□ 核心流程能跑通（哪怕只有 1 个数据源、1 个处理步骤）
□ 输入 → 处理 → 输出 的链路完整
□ 代码结构是稳定的（你觉得这个结构能撑到 v1.0）
```

---

### Phase 2：软约束层 —— 文档化（1-2 天）

**Sprint 0 代码稳定后，立刻文档化。按这个顺序写：**

#### 第 1 步：AGENTS.md（30 分钟）

**模板（直接填空）：**

```markdown
# AGENTS.md

## 项目简介
[一句话：这是什么项目，解决什么问题]

## 快速导航
| 你想做什么 | 去哪里看 |
|-----------|---------|
| 了解系统架构 | docs/architecture/overview.md |
| 了解模块边界和依赖规则 | docs/architecture/boundaries.md |
| 了解数据流转过程 | docs/architecture/data-flow.md |
| 了解编码规范 | docs/conventions/README.md |
| 了解当前迭代任务 | docs/plans/current-sprint.md |
| 了解测试规范 | docs/conventions/testing.md |

## 硬性规则（CI 会验证）
1. 依赖方向：[写清楚你项目的分层依赖方向]
2. 单文件不超过 [推荐 300] 行
3. [你的项目特有的硬性规则]
4. [你的项目特有的硬性规则]

## 项目文件纲要
[粘贴你的目录结构]

## 修改约束
1. 修改目录结构时，必须同步更新本文件
2. 新增 [你的模块类型] 时，必须同步更新 [依赖的模块]
3. 修改后必须运行验证命令

## 验证命令
[编译检查命令 + Linter 命令 + 测试命令]
```

#### 第 2 步：docs/architecture/overview.md（30 分钟）

```markdown
---
last_updated: YYYY-MM-DD
status: active
---

# 系统架构概览

## 一句话概述
[你的项目一句话描述]

## 分层结构
[画你的代码目录树，标注每层职责]

## 依赖规则
[画依赖方向箭头：A → B → C]

## 核心设计理念
[2-4 个关键设计决策，每个一句话 + 一段解释]
```

#### 第 3 步：docs/architecture/boundaries.md（30 分钟）

```markdown
# 模块边界和依赖规则

## 各层职责边界
[每层：职责 / 允许依赖 / 禁止 / 对外接口]

## 禁止的跨层调用
[❌ 哪些不能做]
[❌ 哪些不能做]

## 横切关注点
[hook/log/auth 等跨层关注点应该怎么注入]
```

#### 第 4 步：docs/architecture/data-flow.md（30 分钟）

**画一个完整的 ASCII 流程图**，标注每一步的输入、处理、输出、产物路径、是否调 LLM。

#### 第 5 步：docs/conventions/（1-2 小时）

逐个写：`README.md` → `naming.md` → `error-handling.md` → `testing.md` → `logging.md`

#### 第 6 步：feature_list.json + progress.json（30 分钟）

```json
// feature_list.json
{
  "project": "项目名",
  "purpose": "一句话目的",
  "features": [
    {"id": "F001", "name": "功能名", "priority": "P0", "status": "done",
     "acceptance": ["验收标准1", "验收标准2"]}
  ]
}

// progress.json
{
  "project": "项目名",
  "current_learning_goal": "当前阶段的学习目标",
  "last_updated": "YYYY-MM-DD",
  "completed": ["已完成项1"],
  "next_steps": [{"order": 1, "task": "下一步"}]
}
```

---

### Phase 3：硬约束层 —— Linter + CI（1-3 天）

#### 第 1 步：写 harness_linter

**Python 项目模板：** 直接复用本项目 `scripts/harness_linter.py` 的结构——
`CHECKERS` 列表 + `_fmt()` 格式化函数 + 每个检查一个独立函数。

**JS/TS 项目模板：**

```javascript
// scripts/harness_linter.mjs
const CHECKS = [
  { name: '必需文件检查', fn: checkRequiredFiles },
  { name: '文件大小检查', fn: checkFileSizeLimits },
  // ...更多检查
];

function fmt(problem, fix, doc) {
  return `❌ ${problem}\n✅ FIX: ${fix}\n📖 See: ${doc}`;
}

// 每个检查函数返回 string[]
```

#### 第 2 步：必须覆盖的基础检查项

不管你是什么项目，以下检查必须覆盖：

| 检查项 | 说明 | 通用性 |
|--------|------|--------|
| 必需文件存在 | AGENTS.md、关键源码文件 | 所有项目 |
| AGENTS.md 内容合同 | 包含必要章节和关键词 | 所有项目 |
| 文件大小 | 单文件 ≤ 300 行 | 所有项目 |
| 依赖方向 | 不能反向 import | 所有项目 |
| 特定禁止项 | 你的项目特有的禁止行为 | 项目特定 |
| CI 配置完整性 | CI 文件含编译+Lint+测试 | 所有项目 |

#### 第 3 步：CI 配置

**GitHub Actions 通用模板：**

```yaml
name: Harness Checks
on: [push, pull_request]

jobs:
  harness:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      # --- 语言相关步骤（换成你的） ---
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }

      - name: 编译检查
        run: python -m compileall src scripts tests

      # --- 语言无关步骤 ---
      - name: Harness Linter
        run: python scripts/harness_linter.py

      - name: 单元测试
        run: python -m pytest
```

#### 第 4 步：安装 Git Hooks

```bash
bash scripts/install-hooks.sh
```

---

### Phase 4：自动化层（长期迭代，1-2 周）

在软硬约束稳定运行后，逐步添加：

```
□ Git Worktree 隔离验证（大改动在 worktree 里验证完才合并）
□ 后台清理 Agent（定时扫描 TODO、超长文件、过期文档）
□ 文档新鲜度检查（CI 中检查 design docs 是否超过 60 天未更新）
□ Agent 可观测性（让 Agent 能查日志定位问题）
```

---

## 三、日常操作：怎么和 Agent 协作

### 场景 A：加一个新功能

```
你说的：
  在 [XX 模块] 里加一个 [YY 功能]，输入是 [A]，输出是 [B]。
  如果是确定性逻辑不调 LLM，如果是 LLM 逻辑注明 fallback 策略。
  完成后运行 [验证命令]。

Agent 做的：
  1. 读 AGENTS.md → docs/architecture/boundaries.md
  2. 写代码
  3. 自动跑 linter + 测试
  4. 有报错就按 ✅FIX 修
  5. 全通过后告诉你完成了

如果失败：
  Agent 自己修不了 → 你看 ✅FIX 是不是不够具体 → 改进规则 → 告诉 Agent 再试
```

### 场景 B：Agent 写错了，你改完软约束后

```
你说的：
  我更新了 docs/architecture/boundaries.md 里的约束规则。
  重新读这个文件，按照新规则修改代码。
  改完后运行 [验证命令]。
```

### 场景 C：发现 Harness 漏了，补硬约束

```
你说的：
  在 scripts/harness_linter.py 里加一条检查规则：[描述规则]。
  报错格式遵循现有的 ❌+✅FIX+📖See。
  加完后运行 linter 验证。
```

### 场景 D：Agent 绕过了一条规则

```
你说的：
  Agent 用 [XX 手法] 绕过了 [YY 检查]。更新 linter 的检测逻辑，
  确保 [XX 手法] 以后能被拦截。然后运行 linter 验证。
```

---

## 四、Fallback（降级）策略设计

### 为什么 Fallback 是 Harness 的核心

Agent 不是 100% 可靠的。LLM 会超时、JSON 会解析失败、API 会限流。
**如果系统在 LLM 不可用时直接崩溃，那 Harness 没有真正起作用。**

### 所有项目的通用降级层级

```
第 1 层：正常路径
  LLM/Agent 正常完成
      ↓ 失败
第 2 层：自动修复
  把错误信息反馈给 LLM，让它再试一次（repair）
      ↓ 仍失败
第 3 层：程序化 Fallback
  用确定性的规则/模板生成结果，标注 "⚠️ 由 fallback 生成"
      ↓ 系统始终可运行
```

### 不同项目类型的 Fallback 示例

**LLM 调用（后端）：**
```
正常：LLM 生成分析结果
repair：把 linter 错误反馈给 LLM 要求修复
fallback：用规则统计生成基础分析，标记 source="rule_fallback"
```

**API 调用（后端）：**
```
正常：调用外部 API 获取数据
repair：重试 3 次，指数退避
fallback：返回缓存数据或默认值，记录 warning
```

**UI 渲染（前端）：**
```
正常：加载远程数据渲染组件
repair：静默重试 1 次
fallback：展示骨架屏/占位内容 + "加载失败，点击重试" 按钮
```

**数据抓取：**
```
正常：从数据源抓取
repair：切换备用数据源
fallback：使用上次成功抓取的缓存数据，记录 warning
```

---

## 五、软约束文档编写清单

### 每个文档的元信息

```yaml
---
last_updated: 2026-05-29
status: active          # active | deprecated | draft
owner: coding-agent     # 谁负责维护这个文档
---
```

### 文档层级和更新频率

| 层级 | 目录 | 更新频率 | 什么时候改 |
|------|------|----------|-----------|
| 稳定层 | docs/architecture/ | 每月 1 次或更少 | 架构变更时 |
| 规范层 | docs/conventions/ | 每 2-4 周 | 编码规范变化时 |
| 设计层 | docs/design/ | 每个功能实现时 | 新功能设计/完成时 |
| 计划层 | docs/plans/ | 每周 | Sprint 变更时 |
| 参考层 | docs/reference/ | 自动或按需 | API/错误码变更时 |

---

## 六、硬约束 Linter 编写清单

### 每条规则的必备三要素

```
❌ [可机器解析的：什么文件、什么位置、什么错误]
✅ FIX: [人/Agent 可执行的：具体怎么改、改什么]
📖 See: [指向项目内文档的路径：去哪看详细说明]
```

### 检查函数的通用模式

```python
def check_xxx() -> list[str]:
    """检查 [什么规则]。"""
    issues = []
    # 1. 遍历要检查的目标
    # 2. 判断是否违规
    # 3. 如果违规，issues.append(_fmt(问题, 修复, 文档))
    return issues
```

### 从"Agent 出错"到"补规则"的标准流程

```
1. 观察 Agent 的违规行为
2. 问自己："这个违规有没有共性？以后还会不会出现？"
3. 如果有共性 → 写一条 Linter 规则
4. 确保 ✅FIX 指向 Agent 能执行的具体操作
5. 运行一遍确认规则生效
6. 告诉 Agent 按新规则修复代码
```

---

## 七、多语言支持

### Python 项目

| 组件 | 工具 |
|------|------|
| Linter | 自定义 harness_linter.py |
| 分层检查 | import-linter |
| 类型检查 | mypy |
| 测试 | pytest |
| 覆盖率 | pytest-cov |

### JavaScript/TypeScript 项目

| 组件 | 工具 |
|------|------|
| Linter | ESLint 自定义规则 |
| 分层检查 | ESLint no-restricted-imports |
| 类型检查 | tsc --noEmit |
| 测试 | vitest / jest |
| 覆盖率 | c8 / istanbul |

**ESLint 规则模板：**
```javascript
// eslint.config.js
export default [{
  rules: {
    'no-restricted-imports': ['error', {
      patterns: [{
        group: ['../../services/*'],
        message: [
          '❌ UI 层不能直接引用 Service 层。',
          '✅ FIX: 通过 app/ 路由调用 service，通过 props 传递数据。',
          '📖 See: docs/architecture/boundaries.md'
        ].join('\n')
      }]
    }],
    'max-lines': ['error', { max: 300, skipBlankLines: true, skipComments: true }]
  }
}];
```

### Go 项目

| 组件 | 工具 |
|------|------|
| Linter | 自定义 shell 脚本 |
| 分层检查 | go-architect 或 grep 脚本 |
| 测试 | go test |
| CI | GitHub Actions |

### 通用（所有语言适用）

| 组件 | 工具 |
|------|------|
| Git Hooks | scripts/pre-commit（bash 脚本） |
| CI | GitHub Actions |
| 文件大小 | find + wc -l |
| 文档新鲜度 | git log + find |

---

## 八、快速启动：30 分钟搭出最小 Harness

拿到一个新项目，30 分钟内建立最小可行的 Harness：

```bash
# 1. 创建目录（2 分钟）
mkdir -p docs/{architecture,conventions,design,plans,reference}
mkdir -p scripts .github/workflows

# 2. 写 AGENTS.md（10 分钟）
# 用本文 Phase 2 的模板填空

# 3. 写架构概览（8 分钟）
# 用本文 Phase 2 的 docs/architecture/overview.md 模板

# 4. 创建最小 harness_linter（8 分钟）
# 只包含 3 个检查：必需文件存在、AGENTS.md 完整、编译通过

# 5. 写 CI 配置（2 分钟）
# 包含编译检查 + harness_linter + 测试
```

**最小 harness_linter 模板（30 行，适合任何项目）：**

```python
"""最小 Harness Linter。"""
import sys, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED = ["AGENTS.md", "README.md"]

def fmt(p, f, d):
    return f"❌ {p}\n✅ FIX: {f}\n📖 See: {d}"

def main():
    issues = []
    for f in REQUIRED:
        if not (ROOT / f).exists():
            issues.append(fmt(f"缺少 {f}", f"创建 {f}", "AGENTS.md"))
    print(json.dumps({"passed": not issues, "issues": issues}, ensure_ascii=False, indent=2))
    return 0 if not issues else 1

if __name__ == "__main__":
    sys.exit(main())
```

---

## 九、日常维护清单

### 每次 Agent 完成工作后
```
□ 跑一遍 agent-guardrails.sh → 确认全部通过
□ 看一下 diff → 如果没有问题就接受
□ 如果 Agent 在某些问题上反复循环 → 去改进 ✅FIX 描述
```

### 每周 30 分钟环境审查
```
□ 最近一周 CI 失败率是否上升？
□ Linter 规则是否覆盖了新出现的 bad pattern？
□ AGENTS.md 和 docs/ 是否跟代码库一致？
□ 有没有 3 次以上重复出现的人工纠正？→ 写成 Linter 规则
```

### 每月回顾
```
□ 哪些规则从来没触发过？→ 考虑删除或调整优先级
□ 哪些设计文档需要更新 status？
□ 有没有新的架构模式需要文档化？
□ feature_list.json 和 progress.json 是否反映真实状态？
```

---

## 十、FAQ

**Q: 我有多个 Agent 同时工作，Harness 能管住吗？**
A: 能。因为 Harness 检查的是代码状态（文件存在、行数、依赖方向），不关心谁写的。多个 Agent 同时改代码，CI 和 git hooks 会统一拦截。

**Q: Agent 改的代码质量差但不违反规则怎么办？**
A: 这是规则不够细的信号。把它翻译成一条机械规则加进 linter。比如"代码质量差"→"单函数 ≤ 50 行"或"函数复杂度 ≤ 10"。

**Q: 我的项目已经很老了，怎么加 Harness？**
A: 不需要一次加满。从 AGENTS.md 开始（1 小时），然后是 1 条 linter 规则（1 小时），然后是 CI（30 分钟）。逐步生长。

**Q: 前端项目和后端项目，Harness 有区别吗？**
A: 文档结构完全一样。Linter 工具不同（后端用 import-linter，前端用 ESLint），但检查逻辑相同。

**Q: 文档太多了 Agent 会不会不看？**
A: 这就是为什么 AGENTS.md 要控制在 50-100 行且使用"地图模式"——Agent 只在需要时按导航表跳转到具体文档，不会一次性全部加载。
