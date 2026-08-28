# Harness Engineering 核心问答

整理自 2026-05-29 ~ 2026-05-31 深度讨论，用于面试准备和项目参考。

---

## 一、Hook 机制：如何实现、为什么有效

### Hook 的实现原理

Claude Code 的 Hook 系统是运行时代码的一部分，不在模型的控制范围内。它工作在 Claude Code 进程内部，在"模型决定调用工具"和"模型收到工具结果"两个时间点之间插入执行逻辑。

Claude Code 内部的执行顺序（以 PostToolUse 为例）：

```
步骤 1: Claude 模型输出 tool_use 指令（例如：Edit file X, content Y）
步骤 2: Claude Code 运行时解析 tool_use 指令
步骤 3: Claude Code 运行时执行实际操作（文件被修改）
步骤 4: Claude Code 运行时检查 .claude/settings.json 中 hooks.PostToolUse 的配置
步骤 5: 遍历 hooks 数组，逐一检查 matcher 是否匹配本次工具调用
步骤 6: 对匹配的 hook，执行其 command 字段指定的 shell 命令
步骤 7: 捕获命令的 stdout 和 stderr
步骤 8: 将 stdout 作为 tool_result 的一部分，拼接在工具执行结果后面
步骤 9: 将完整的 tool_result 返回给 Claude 模型
步骤 10: Claude 模型读到 tool_result（包含 hook 输出），决定下一步操作
```

步骤 4-8 完全在模型控制范围之外。模型在步骤 1 发出指令后，不知道中间会发生什么，直到步骤 10 才拿到结果。这就是 Hook 无法被模型绕过的根本原因——模型不知道 Hook 的存在，也无法阻止它执行。

### hooks.json 的结构

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "python3 scripts/harness_linter.py --json 2>/dev/null || true"
          }
        ]
      }
    ],
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "[ -f .understand-anything/config.json ] && echo 'Knowledge graph might be stale.' || true"
          }
        ]
      }
    ]
  }
}
```

关键字段：
- `hooks.PostToolUse`：在每次工具调用完成后触发。这是最常用的 Hook 类型——每次 Agent 写代码、执行命令后都会触发。
- `hooks.SessionStart`：在 Claude Code 会话启动时触发一次。用于检查项目状态（如知识图谱是否过时）。
- `matcher`：一个正则表达式，匹配工具名称。`"Edit|Write"` 表示只在编辑或写入文件时触发。`"Bash"` 表示只在执行 shell 命令时触发。如果不写 matcher，所有工具调用都会触发。
- `command`：要执行的 shell 命令。它的 stdout 会被返回给 Claude 模型。如果命令以 `|| true` 结尾，即便命令"失败"（exit code 非 0），也不会阻断工具调用的正常返回——Hook 只负责"报告"，不负责"阻止"。
- `type`：目前只支持 `"command"` 类型。

### Hook 为什么不被归为"软约束"

软约束的工作方式是：Claude 读到 AGENTS.md 里的规则 → Claude 自己决定是否遵守。这中间有一个"阅读理解 + 自主决策"的环节。Agent 可能读了但没注意、忘了、或者被后续的上下文挤出去了。

Hook 的工作方式是：Claude Code 运行时在工具执行后强制执行 shell 命令，stdout 被强制追加到 tool_result 末尾。这一步没有"Agent 选择读不读"的余地——`tool_result` 是 Claude 获取工具执行反馈的唯一途径，它必须处理 `tool_result` 里的内容。

打个比方：
- 软约束 = 贴在墙上的规章制度。员工（Agent）看到了可以选择遵守，也可以忽略。
- Hook = 每次刷门禁卡时自动响起的语音提示。员工一定会听到，因为语音和门禁反馈是同一个通道。

### stdout 注入的具体机制

当 Hook 的 shell 命令执行完毕后，Claude Code 运行时把 stdout 的内容追加到本次工具调用的返回结果中。模型看到的大致是这样：

```
Tool: Edit
File: src/insight_engine/stages/collect_raw_items.py
Result: File modified successfully. 42 lines changed.

[Hook Output]
❌ 确定性 stage 中检测到 LLM API 域名：api.openai.com
✅ FIX: 确定性 stage 不允许调用任何 AI 模型 API。移除所有对 api.openai.com 的网络请求代码。
📖 See: docs/architecture/boundaries.md 了解确定性/LLM stage 的边界
```

模型读这个 tool_result 时，无法区分"原始的工具执行结果"和"Hook 注入的警告信息"——它们被拼在了一起。模型必须通读全部内容才能理解工具执行的状态，所以 Hook 输出一定会被处理。

### 你的项目怎么接入 Hook

当前项目没有使用 Hook。它靠的是 CLAUDE.md 里写的"修改完代码后必须运行 harness_linter.py"这条软约束——Agent 读了之后自己决定执行。这个方式的问题在于 Agent 可能忘记、跳过、或被上下文压缩丢失步骤。

要接入 Hook，只需要在 `.claude/settings.json` 中添加 PostToolUse 配置，把 `harness_linter.py` 挂到 `Edit|Write` matcher 上。这样每次 Agent 编辑文件后，linter 自动运行，结果强制返回给 Agent。Agent 不可能不知道违规了。

### Hook 的限制

Hook 不能做的事：
- 不能阻止工具执行。Hook 命令在工具执行完成后才运行。
- 不能修改文件。Hook 只是 shell 命令，如果命令本身包含写文件操作，那是命令自己的行为，不是 Hook 框架提供的"修改"能力。
- 不能调用 Claude API 或操控模型行为。Hook 是纯 shell 层面。
- Hook 的 stdout 如果太长，可能被 Claude Code 截断。因此输出要精简。
- Hook 命令执行有时间开销。如果 linter 跑 3 秒，每次 Edit 后都要等 3 秒。

### Hook 与 Pre-commit / CI 的分工

```
Hook：       每次 Edit 后     → "喊话"（Agent 自己决定修不修）
Pre-commit： git commit 时    → "拦住"（不修就不让提交）
CI：         PR 合并时        → "最后的门"（不修就不让合）
```

三个机制触发的时间点不同，拦截强度也不同。Hook 最频繁、最轻量；Pre-commit 在提交时强制执行；CI 是代码进入主分支的最后一道防线。三道防线各司其职，Agent 在前面被拦住的次数越多，后面两道防线被触发的频率越低。

---

## 二、上下文管理：你能控制的边界

### 上下文窗口里有什么

一次 Claude Code 会话的上下文窗口大致包含：

```
1. System Prompt（Anthropic 写的，约 2-5K tokens）
   - Claude 的身份设定、安全规则、工具定义
   - 用户完全无法修改
   
2. 用户级配置注入
   - CLAUDE.md 文件内容（会话启动时自动加载）
   - .claude/settings.json 中的自定义指令（如果有）
   
3. 对话历史
   - 用户和 Claude 的所有交互记录
   - 包括每次工具调用的输入和输出
   - 随着会话进行不断增长
   
4. 文件内容（按需加载）
   - Agent 通过 Read 工具读取的文件
   - 可能被缓存（Anthropic 的 prompt cache 约 5 分钟 TTL）
   
5. Memory 文件
   - 项目级 memory/ 目录下的 markdown 文件
   - Claude 服务端的自动记忆
   
6. Hook 输出
   - PostToolUse Hook 的 stdout
   - SessionStart Hook 的 stdout
```

你的控制范围覆盖第 2、4、5、6 项。第 1 项完全不可控，第 3 项只能间接影响（通过控制前几项来影响对话走向）。

### 你能控制的每一层

**初始上下文（CLAUDE.md / AGENTS.md）：**
这是你完全控制的第一层。CLAUDE.md 在每次会话启动时自动加载进上下文，AGENTS.md 在 Agent 第一次读取项目时加载。你的控制手段是：精简内容、结构化表达、用导航表替代文件列表。你的 AGENTS.md 从 209 行砍到 74 行的操作，就是把初始上下文预算从约 4K tokens 压缩到约 1.5K tokens，为后续对话留出更多空间。

**运行时注入（Hook stdout）：**
这是你最灵活的控制点。Hook 命令是一个 shell 脚本，你可以用任意复杂的逻辑来决定"什么情况下输出什么内容"。关键原则是"默认无声，命中才响"——Hook 命令应该以 `|| true` 结尾，确保不命中时不输出任何东西。每次多余的输出都在消耗珍贵的上下文预算。

**文档粒度：**
Agent 读取文件时，Read 工具会把整段内容加载进上下文。如果你把全部架构文档写在一个 3000 行的文件里，Agent 读一次就占了大量上下文。如果你拆成 15 个 200 行的小文件，Agent 可以按需精确定位。这就是"粒度控制"——让 Agent 花最少上下文预算获取最相关的信息。

**Memory 文件：**
跨会话的持久化上下文。Memory 文件会在会话启动时通过索引文件加载进上下文。这让你可以把长寿命信息（项目背景、用户偏好、架构决策）从"每次都要解释"变成"自动加载"。

### 你做不到的事

- 修改 System Prompt。Claude 的底层行为约束（安全规则、工具定义）是 Anthropic 控制的。
- 控制对话压缩的时机和算法。当上下文接近窗口上限时，Claude Code 会自动压缩历史对话为摘要。你不能指定压缩哪些段落、保留哪些细节。但你可以通过"结构化表达"提高信息在压缩后的存活率——明确的规则格式（如 `RULE: state.py 修改必须...`）比分散的叙述更容易在摘要中保留。
- 扩大上下文窗口。窗口大小由模型版本决定，不是用户配置项。
- 精确删除某段上下文。你不能说"忘掉刚才那一段对话"。

### 上下文管理的实际策略

1. **结构化优于叙述化**：`⚠️ RULE: state.py 修改 → 必须先读 data-flow.md` 比大段解释更容易在压缩后存活。
2. **条件注入而非全量注入**：Hook 只在命中规则时输出，未命中时零输出。不要每次 Hook 都输出"一切正常"。
3. **粒度控制**：大文档拆小文件。让 Agent 可以读一个 100 行的文件而非 3000 行的文件。
4. **跨会话下沉**：长寿命信息存 Memory 文件。不用每个会话重新解释项目背景。
5. **导航表替代目录树**：AGENTS.md 用"你想做什么 → 去哪看"表格，而非平铺文件列表。

---

## 三、软约束 vs 硬约束

### 软约束为什么不稳定

软约束的执行路径是：

```
CLAUDE.md/AGENTS.md 内容进入上下文
    → Agent 阅读理解规则
    → Agent 在生成代码时回忆起规则
    → Agent 自主遵守规则
```

整个链条上有四个断裂点：
1. Agent 可能在读文件时跳过了规则部分（注意力不在此处）。
2. Agent 可能在生成代码时遗忘了规则（前面的对话被压缩或自然遗忘）。
3. Agent 可能判断规则不适用于当前情况（模型在边缘情况下的判断力不稳定）。
4. 规则本身可能写得太模糊，Agent 不知道具体该怎么做。

这四个断裂点都不是你能直接控制的——它们取决于模型的注意力分布、记忆保持能力、推理质量，以及上下文压缩算法的表现。

### 硬约束为什么稳定

硬约束的执行路径是：

```
Agent 修改代码
    → Hook/CI/Pre-commit 运行检查脚本
    → 脚本输出违规信息
    → 违规信息强制注入上下文
    → Agent 被迫处理
```

这个链条只有两个断裂点：检查脚本本身有 bug（漏检），或者 Agent 在收到违规信息后拒绝修复（极其罕见）。模型的主观判断被排除在"检测违规"这一步之外——检测是由确定性脚本完成的。

### 两者的真实互补关系

软约束提升了 Agent 的"下限"——Agent 读了规则后，大部分情况下会遵守，不需要硬约束频繁触发。硬约束提供了"上限保障"——Agent 不遵守时，硬约束一定会拦截。

没有软约束时的情况：Agent 每次都不读规则直接写代码，硬约束每次都触发。Agent 在 10 轮试错后才写对。上下文被 10 轮错误信息塞满，模型注意力退化。

有软约束时的情况：Agent 先读规则，第一次就基本写对。硬约束只在边缘情况触发一两次，Agent 快速修正。上下文保持干净。

### 各自的适用场景

能硬约束的规则（约占 20%）：文件行数、禁止的 import 语句、依赖方向、类型检查、测试覆盖率阈值、commit message 格式。这些规则的特点是"可以用程序在不需要理解语义的情况下判断对错"。

只能软约束的规则（约占 80%）：函数职责是否单一、错误信息是否对用户友好、命名是否清晰、代码是否"简洁"、是否在循环里调数据库、是否有不必要的抽象层次。这些规则需要语义理解，无法用简单脚本判断。

### 规则不需要提前写好

Harness 规则的正确生长方式是"事故驱动"：

```
Day 1：代码 + 5 行 CLAUDE.md（项目身份 + 基本工作流）。零条硬约束，零篇设计文档。
Day 7：Agent 在确定性 stage 里调了 LLM → 加一条域名检测规则 + 一条 SDK 检测规则 + 在 boundaries.md 里写一句话解释为什么。
Day 14：Agent 把 analyze_insights.py 写到 800 行 → 加文件行数上限检查。
Day 30：Agent 改了 state.py 字段名导致下游崩溃 → 加 PostToolUse Hook 检测 state.py 修改 → 自动注入 data-flow.md 提醒。
```

Day 90 时，你的 harness_linter.py 有 14 项检查，AGENTS.md 有 7 条硬规则。回头看，没有一条是你在 Day 1 凭空"设计"出来的。全部是事故的沉淀。目标不是"预见所有问题"，而是"每个问题只发生一次"。

---

## 四、故障处理与反馈闭环

### 三段式错误格式的设计原理

```
❌ 什么问题（What）     → 一眼看懂，不需要思考
✅ FIX: 怎么修（How）   → 可以直接执行，不需要自己想办法
📖 See: 参考什么（Why） → 想深入理解时才需要看
```

这个格式的设计原则是**分层信息获取**：Agent 不需要读完所有内容才能行动。第一行看完就知道发生了什么，第二行看完就知道该改什么。只有想理解"为什么有这个规则"时才需要看第三行的文档。

三段式格式的关键设计考量：
- ❌ 行必须包含具体的文件和位置（如 `collect_raw_items.py:42`），让 Agent 不需要搜索就能定位。
- ✅ FIX 行必须给出可操作的指令（如"移除 X，替换为 Y"），不能是"请检查并修复"这种模糊指令——模糊指令等于没给，Agent 还是要自己想。
- 📖 See 行指向的文档必须存在且确实包含相关解释。如果指向一个不存在的文件或不相关的文档，Agent 会浪费上下文去读一个无用的文件。

### 错误信息的生成流程

不是人工每次写的。是检查脚本在检测到违规时，从预先定义好的模板中生成的：

```python
# harness_linter.py 中的模板定义
def _fmt(problem: str, fix: str, doc: str) -> str:
    return f"❌ {problem}\n✅ FIX: {fix}\n📖 See: {doc}"

# 使用时：
issues.append(_fmt(
    f"`{relative_path}` 包含 LLM API 域名：`{domain}`",        # ← 脚本自动拼接
    f"确定性 stage 不允许调用任何 AI 模型 API。移除所有对 `{domain}` 的网络请求代码。",
    "docs/architecture/boundaries.md 了解确定性/LLM stage 的边界",
))
```

检查脚本负责"检测 + 描述问题"，修复指令是你预先为每类违规定义的模板，参考文档是你预先写好的设计文档。Agent 不需要自己分析问题、自己找修复方案、自己找相关资料——它只需要按指令执行。

### 自修复循环的完整流程

```
第 1 轮：Agent 修改代码
    → Hook 自动运行 linter
    → ❌ 违规注入 tool_result
    → Agent 看到 ✅FIX → 按指令修改代码
    → 再次编辑 → Hook 再次触发 → linter 再次运行
    → ✅ 通过 → 闭环成功

如果第 1 轮未通过：
第 2 轮：Agent 再次尝试修复
    → ❌ 仍然失败
    → Agent 按 📖See 的路径读取设计文档
    → 理解规则背后的原因
    → 更准确地修复

如果第 2 轮仍未通过：
第 3 轮：Agent 进入 Plan Mode
    → 要求 Agent 写修复计划
    → 人类或 Agent 审查计划
    → 按计划执行

如果第 3 轮仍未通过：
第 4 轮：人工介入
    → CI 在 PR 上 comment @ 开发者
    → 开发者直接修复或给 Agent 更详细的指令
```

### 为什么不让 Agent "自己想"

让 Agent 自己分析错误 → 自己找修复方案 → 自己执行的路径有两个风险：第一，Agent 可能误判错误的原因，修错方向；第二，分析和查找过程消耗大量上下文，一次复杂错误的修复可能烧掉 30K tokens 的对话历史。预先写好修复指令和参考文档路径，相当于把"排查 + 学习"的上下文成本压缩到一个文件路径的引用——Agent 只需要读相关文档，不用自己找。

---

## 五、AI Agent 的六种通用坏习惯

以下六类行为模式是跨项目、跨语言、跨 Agent 模型通用的。它们在 Claude Code、Codex、Cursor、GitHub Copilot 中都有观察到。

### 类型 1：偷懒型（LLM Overuse）

表现：Agent 能用 LLM 解决的地方就调 LLM，不会写确定性逻辑。哪怕需求用纯计算就能完成，也会 import openai 然后调 API。

原因：LLM 的训练数据中，"调用 API 解决问题"是高频模式。加上 Agent 的工作流中，调用 LLM 对它来说是一条"轻松路径"——不需要思考算法，发一个 prompt 就行。

检测方式：在确定性代码区域检查 LLM API 域名（`api.openai.com`、`api.anthropic.com` 等 16+ 个已知域名）+ AI SDK import 模式（`import openai`、`import anthropic`、`ChatOpenAI` 等 14 个模式）。两层叠加，Agent 换了 SDK 换不了域名，换了域名换不了 SDK——除非它手写 HTTP 请求 + 用 IP 地址，但这种绕过的复杂度本身就已经超过了"老老实实写确定性代码"的成本。

### 类型 2：膨胀型（File Bloat）

表现：Agent 持续往一个文件里追加代码，不会主动拆分。一个 stage 文件从 100 行涨到 300 行、500 行、800 行。

原因：Agent 在单次任务中通常只关注"如何完成这个功能"，拆分的决策需要"预判将来会变复杂"或"感知当前文件已经过长"——这两种能力在 Agent 的默认行为中很弱。

检测方式：遍历所有 `.py` 文件，检查行数是否超过阈值（300 行）。在 `✅ FIX` 中明确要求"把辅助函数移到 utils/，把子逻辑拆成独立模块"。

### 类型 3：遗忘型（Missing Counterpart）

表现：Agent 新增了一个 stage 但不创建对应的 linter，新增了一个功能但不写测试。

原因：Agent 遵照你的指令"创建 X"时，只做被要求的那一件事。创建 linter 或测试是一个隐式的配套任务，需要 Agent 自己推断出来。

检测方式：维护 stage-linter 的配对映射表，检查每个 stage 是否有对应的 linter 文件。测试覆盖率同理——CI 中跑 pytest 并检查覆盖率是否下降。

### 类型 4：扩散型（Dependency Sprawl）

表现：Agent 跨层调用依赖——底层的 stages 直接 import 上层的 conversation，或 linter 直接依赖外部的工具库。

原因：Agent 在写 import 时只考虑"我需要用这个功能"，不考虑"这个 import 是否符合架构的分层方向"。

检测方式：工具如 import-linter（Python）、dependency-cruiser（JavaScript）、ArchUnit（Java）。它们允许你声明允许的依赖方向（如 conversation → skill_executors → harness → stages → linters），然后自动检查所有实际 import 是否违反声明。

### 类型 5：过时型（Doc Drift）

表现：Agent 修改了代码逻辑但不更新对应的设计文档。文档和代码逐渐脱节，后来的 Agent 读到过时文档产生错误理解。

原因：Agent 的默认行为是"修改代码 → 完成任务"。更新文档是一个额外步骤，除非明确要求，不会自动做。

检测方式：文档新鲜度脚本扫描 docs/ 目录下所有文件的最后修改时间，超过阈值（如 60 天）且状态为 "active" 的标记为 "stale"。这不能判断内容是否正确（那是语义问题），但可以提醒"这个文件很久没动过了，代码可能已经变了"。

### 类型 6：格式型（Format Violation）

表现：commit message 写成"fix bug"而不是"fix: 修复 collect_raw_items 中的数据丢失问题"；新写的函数没有 docstring。

原因：Agent 在完成任务时关注"功能是否正确"，格式规范在训练数据中的优先级较低。

检测方式：commit message 正则匹配（如 `^(feat|fix|refactor|docs|test|chore):`），函数 docstring 的 AST 检查（解析 Python 文件，检查入口函数是否有 docstring 且包含中文）。

---

## 六、Memory：你如何规定、Claude 如何判断、存在哪里

### 两种 Memory 的完整对比

类型 A（项目级 Memory）是你主动管理的。位置在你的项目目录下的 `memory/` 文件夹中，每个 `.md` 文件是一条记忆，带 YAML frontmatter 描述元数据。你写什么、用什么格式、怎么组织结构——完全由你决定。文件可以用 Git 版本管理，团队成员共享。Claude 在每个新会话启动时，会自动加载 `memory/MEMORY.md` 索引文件（里面列出了所有 memory 文件的路径和简介），然后按需读取具体的 memory 文件。

类型 B（Claude 自动 Memory）是 Claude 在对话中自己记录的。存储在 Claude 的服务端（Anthropic 的服务器上），你看不到完整的存储形式。你可以通过对话中的指令来控制它：说"记住：xxx"让 Claude 记录，说"忘掉 yyy"让 Claude 删除。你可以在 CLAUDE.md 中告诉它记忆策略（比如"每次踩坑后把新规则记住"）。但你不能直接编辑服务端存储，不能查看完整存在什么，不能规定 Claude 用什么格式存储。

### 项目级 Memory 的具体机制

文件格式示例：

```markdown
---
name: architecture-decision-llm-detection
description: 为什么 LLM 检测使用域名匹配而非 token 黑名单
metadata:
  type: project
---

## 背景

最初版本的 harness_linter.py 使用 4 个 token（openai、anthropic 等）做黑名单检测。
Agent 轻易绕过——使用 langchain 封装调用。

## 决策

改为两层检测：Layer 1 域名匹配（无法绕过——调 API 必须用域名），Layer 2 SDK import 检测（辅助）。

## 影响

所有确定性 stage 检查 CI 都会验证。如果有人质疑"为什么检查域名"，这个文档就是解释。
```

`MEMORY.md` 索引文件的内容：

```markdown
- [LLM 检测方案决策](architecture-decision-llm-detection.md) — 为什么用域名而非 token 黑名单
- [用户角色和偏好](user-profile.md) — 架构师角色，偏好直接结论
- [项目当前阶段](project-phase.md) — Harness Engineering 搭建阶段
```

索引文件的每行是一个链接 + 一句话说明（约 150 字符以内）。Claude 会话启动时自动加载 `MEMORY.md`，看到索引概览，然后根据当前任务按需读取具体的 memory 文件。这个机制保证大量记忆不会一次性塞满上下文。

### Claude 怎么判断什么值得记录（类型 B）

这是 Claude 模型内部的判断逻辑，由 Anthropic 训练。大致规则是：
- 用户在对话中明确说"记住 X"时，Claude 会记录。
- Claude 推断这是"关于用户的事实"（如偏好、背景、知识水平）而非"关于当前任务的临时信息"时，可能会自动记录。
- 信息在对话中被反复提及或用户给出了明确的正向/负向反馈时，可能会记录。

你无法直接控制这个判断逻辑。但你可以在 CLAUDE.md 中写一条指令（如"每次发现新规则后主动建议我记录下来"），这样 Claude 会在合适的时机提醒你，你再决定是否记录。

### 两条原则

1. 让 Claude 自动记的：关于"你这个人"——偏好、沟通风格、知识背景。这些信息 Claude 在对话中自己观察和推断。
2. 你主动写的：关于"这个项目"——硬性约束、架构决策、踩坑历史。这些是项目的基础设施，需要版本管理、需要团队共享、需要确保正确性。

---

## 七、硬约束的进化等级

### Level 1：字符串黑名单

最简单的形式：一个 grep 命令或 Python 脚本，在代码中扫描特定的字符串。你的 harness_linter.py 的第一版就是这样——在文本中搜索 `import openai`。

优点：实现只需要 5 分钟。缺点：Agent 可以用同义词绕过——`from openai import ChatCompletion`、`import openai as ai`、或者用 `eval('import openai')`。黑名单穷举成本随绕过方式线性增长。

### Level 2：多模式检测（你现在的水平）

不再依赖单一字符串，而是多模式组合。你的 LLM 检测从单纯的 4 个 token 升级到了两层：域名检测（16+ 个已知 LLM API 域名）+ SDK import 检测（14 个模式）。两层叠加，Agent 绕过一层的成本很高，同时绕过两层的成本更高。

加上文件行数检查、stage-linter 配对检查、必需文件检查、JSON 合同验证——你的项目在 Level 2 有 14 个检查项，覆盖了结构、格式、依赖、完整性四个方面。

优点：覆盖面广，扩展简单（加一行域名或 import 模式即可）。缺点：仍然是文本级别匹配。Agent 可以通过字符串拼接绕过域名检测——`"api." + "openai.com"` 这类 URL 构建在源代码中被拆开，文本匹配找不到完整域名。

### Level 3：AST 语义级检测

用 tree-sitter 或 Python 的 `ast` 模块将代码解析为抽象语法树（AST），在 AST 节点级别检查。

tree-sitter 是一个增量解析库，能理解代码的语法结构。用它解析 Python 代码后，你不是在检查文本，而是在检查"这个文件中所有的 Import 节点"——不管 Agent 怎么写字符串拼接、变量展开、动态构建，最终的 Import 节点逃不掉。

Understand-Anything 的 fingerprint.ts 就使用了 tree-sitter。它的用途不是检查违规，而是提取函数签名、类签名、import 列表来构建代码的结构指纹。同样的技术用于约束检查时，可以做到：解析所有 `ast.Import` 和 `ast.ImportFrom` 节点，提取 `module` 属性，检查是否为 LLM 相关的包名。

优点：Agent 无法通过字符串技巧绕过。缺点：需要集成 tree-sitter 或编写 AST 遍历代码，实现成本比 Level 2 高。

### Level 4：图/关系级检测

不检查"点"（单个 import 是否违规），而检查"图"（整个代码的 import 关系是否违反架构声明）。

实现方式是：先用工具解析所有文件的 import，生成依赖图；然后读取架构声明文件（如 yaml 或 py 声明），生成允许/禁止的依赖关系；最后对比两张图，找出违反声明的边。

业界现成工具：import-linter（Python 生态，可以声明 contracts——如 `forbidden_modules`、`layers` 约束）；dependency-cruiser（JavaScript/TypeScript 生态，生成依赖图和违规报告）；ArchUnit（Java 生态）。

优点：不需要手动逐条写"A 不能 import B"的检查代码——你在架构声明中定义一次规则（如"conversation 层不能 import stages 层"），工具自动找到所有违规实例。缺点：架构声明本身需要维护。但声明的维护成本远低于逐条写检查脚本的成本。

### Level 5：架构适应度函数

来自《Building Evolutionary Architectures》的概念。不再是二元检查（"有循环依赖吗？有就拦截"），而是趋势监控（"循环依赖涉及的文件数量在增加吗？增加了多少？"）。

关键指标：
- 不稳定性 I = Ce / (Ce + Ca)，Ce 是出向耦合数（你依赖了多少别人），Ca 是入向耦合数（多少人依赖你）。I 越接近 1 越不稳定，越接近 0 越僵化。
- 抽象度 A = 抽象类数 / 总类数。
- I + A 应该约等于 1（主序列线）。偏离主序列的区域 = "痛苦区"（太僵化，谁都想依赖它但改不动）或"无用区"（太抽象但没人真正调用）。

这些指标在每个 PR 中计算一次，形成时间序列。如果某个包的 I 值连续 3 个 PR 在上升，说明它在变脆弱——即便每个 PR 都不违反架构声明。

优点：能发现"慢慢恶化"的问题，而不是只拦截"一次性越界"。缺点：需要统计基础设施和可视化，适合 10+ 人的团队。

### Level 6：定理级约束

用形式化验证（formal verification）证明代码修改不会破坏关键不变量。目前只在安全攸关系统（操作系统内核、智能合约、航空软件）中使用。AI Agent 约束领域的实践几乎为零，这还是一个研究课题。

### 你当前的定位和下一步

当前：Level 2（多模式文本检测）。14 项检查，覆盖结构、格式、依赖、完整性。
推荐下一步：
- Level 3：把域名检测升级为 AST 遍历——用 Python 的 `ast` 模块解析文件，遍历所有 `Import` 和 `ImportFrom` 节点，检查导入的模块名是否在已知 LLM 包名列表中。
- Level 4：集成 import-linter，声明分层依赖规则，让工具自动检查所有 import 的合规性。

---

## 八、Harness Engineering 完整架构

### 六层模型

Layer 1：信息层（Information）。让 Agent 知道"这是什么项目、该怎么做"。组件：CLAUDE.md（会话入口）、AGENTS.md（全规则 + 导航表）、docs/architecture/（系统设计）、docs/conventions/（编码规范）、docs/design/（功能设计文档）、feature_list.json（功能状态）、progress.json（学习进度）、Memory 文件（跨会话记忆）、知识图谱（可选，Understand-Anything 风格的结构指纹 + 关系网络）。

Layer 2：约束层（Constraint）。让 Agent 想违规前被拦住。四道拦截链：
- PostToolUse Hook：每次工具调用后自动运行检查脚本，stdout 注入上下文。最频繁的拦截层，覆盖所有写操作。
- Agent Guardrails：Agent 手动运行或不定期触发的综合检查（你项目的 agent-guardrails.sh——compileall + linter + pytest + 文件大小，一键执行）。
- Pre-commit Hook：git commit 时自动运行。不通过则 commit 被拒绝。这是第一道"执法权"所在的防线——Agent 不能提交不合规的代码。
- CI Pipeline：GitHub Actions / Jenkins。PR 合并前运行所有检查。失败则 block merge。这是最后一道防线，也是团队共享的防线。

Layer 3：反馈闭环（Feedback Loop）。被拦住的 Agent 知道怎么修。核心组件：三段式错误格式（❌+✅FIX+📖See），统一在所有拦截层输出。自动修复循环（拦截 → 读 ✅FIX 修改 → 再次检查 → 通过/升级）。失败升级机制（第 1 轮自修复 → 第 2 轮读文档深入学习 → 第 3 轮 Plan Mode → 第 4 轮人工介入）。

Layer 4：自动化层（Automation）。系统自己维护自己。组件：文档新鲜度检查（check-doc-freshness.sh，定期扫描超期文档）、可观测性报告（observability-report.sh，综合健康报告）、清理 Agent 调度（结构化 prompt 模板指导 Agent 周期性清理超大文件、缺失测试、TODO/FIXME、过时文档、重复代码）、知识图谱自动更新（Git commit 触发 → tree-sitter 结构指纹提取 → 对比 → 仅 STRUCTURAL 变更触发增量更新）。

Layer 5：上下文管理层（Context Management）。让每 1K tokens 花在最有价值的信息上。策略：四区上下文模型（常驻区不可驱逐、缓存区命中加载、永久存储按需读取、闲置区可被压缩）、条件注入（Hook 默认无声，仅在命中规则时输出）、结构化文档（明确标记 > 叙述解释）、粒度控制（大文档拆小、按需加载）、跨会话下沉（长寿命信息存 Memory 和 docs/）。

Layer 6：运行时 Harness（Runtime，仅 AI Agent 项目需要）。约束的不是"AI 写代码"，而是"运行时 AI Agent 处理任务的流程"。组件：state.py（全局状态机 + 字段合同）、graph.py（阶段编排 + 合法转换路径）、stage_gates.py（每阶段入口/出口守卫）、context_router.py（限制 Agent 的上下文可见范围）、tool_gateway.py（工具白名单，Agent 只能用注册过的工具）、hooks/（运行时钩子，如 after_llm_call）、linters/（每阶段输出校验，不合格打回）。如果你的项目不是 AI Agent 流水线而是普通 CRUD 应用，这层不需要。

### 各层之间的关系

```
Layer 1（信息层）"告诉它"
    ↓ Agent 知道了规则
Layer 2（约束层）"拦住它" ← 如果 Agent 不遵守
    ↓ 违规信息
Layer 3（反馈闭环）"教会它" ← 拦住不够，得告诉它怎么修
    ↓ 修复后通过
Layer 4（自动化层）"打理它" ← 不依赖 Agent 手动维护
    ↓ 保证资源高效
Layer 5（上下文管理）"省内存" ← 贯穿所有层的基础设施
    ↓ 对 AI Agent 项目才有
Layer 6（运行时层）"约束运行" ← 和上层正交，管的是业务 Agent
```

### 面试表述

"Harness Engineering 是一个六层防御纵深体系，核心思想是不信任 Agent 会遵守规则，而是设计系统让它不可能不遵守。"

"第一层信息层用 CLAUDE.md、AGENTS.md、架构文档和 Memory 文件让 Agent 知道上下文。第二层约束层用四道拦截链——PostToolUse Hook 实时告警、Guardrails 一键检查、Pre-commit 拦截提交、CI 最后兜底——确保 Agent 绕过一层还有下一层在等它。软约束靠 Prompt 指导行为，硬约束靠运行时强制检查，两者互补。"

"第三层反馈闭环是 Harness 的'自我修复'能力。所有拦截层输出统一的三段式格式——❌ 什么问题、✅ 怎么修、📖 去哪学。Agent 看到错误不靠自己分析，而是直接按修复指令执行。自修复三轮仍失败则升级为人工介入。这个闭环让 Agent 从'犯错者'变成了'自修者'。"

"第四层自动化层让系统自己维护自己——文档新鲜度扫描、可观测性报告、清理 Agent 周期性调度。第五层上下文管理层是贯穿所有层的基础设施——通过条件注入、结构化文档、粒度控制把每 1K tokens 花在最有价值的信息上。"

"Harness 规则不是来自我的想象力，而是来自 Agent 的事故记录。一条规则背后就是一个真实的事故——我们不做预测，我们保证同一个坑永远不踩第二次。"
