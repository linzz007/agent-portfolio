# Harness Engineering 标准参考

> 版本：v1.0 | 最后更新：2026-06-01
> 标注：[官方共识] = Anthropic/OpenAI 明确论述 | [社区实践] = 开源社区广泛采用 | [个人实现] = 本文作者的项目实践

---

## 零、三大域总览

```
Agent = Model + Harness

Harness 拆为三个独立域：

  Coding Agent Harness     Runtime Agent Harness    Learning Loop
  ─────────────────────     ─────────────────────    ─────────────
  管"写代码的 AI"           管"跑任务的 AI"           管"AI 能不能越干越强"
  
  约束对象：                约束对象：                作用对象：
  Claude Code / Codex /    你的 Agent 产品中的       Runtime Agent Harness
  Cursor / Copilot          LLM 推理环节              中的记忆与技能系统
  
  时间维度：                时间维度：                时间维度：
  项目生命周期（周~月）      单次运行（秒~分钟）        跨多次运行（天~月）

  典型产物：                典型产物：                典型产物：
  AGENTS.md / linter /      State Machine / Gate /    Skill 文件 / 记忆索引 /
  Pre-commit / CI            Tool Gateway / Trace      自动清理 PR
```

**三个域互不替代。** 如果你只用 AI 写传统 CRUD 代码，你需要 Coding Agent Harness。如果你的产品本身是靠 AI Agent 运行的，你需要 Runtime Agent Harness。如果你希望 Agent 越用越强，你需要 Learning Loop。三个都用 AI 来写的团队，三个域全要。

---

## 一、CODING AGENT HARNESS

> 约束对象：AI 编程助手（Claude Code / Codex / Cursor / GitHub Copilot）
> 解决的问题：AI 写代码又快又多，怎么保证它不写烂代码、不违反架构规则、不悄悄引入安全漏洞？

---

### C1. 信息层（Information Layer）

#### C1.1 入口上下文文件

**作用：** 每个会话启动时，让 AI 编程助手立刻理解"这是什么项目、规则是什么、该去哪里找详细信息"。

**约束对象：** AI 编程助手的初始上下文内容。

**常见实现：**
- `CLAUDE.md`（Claude Code 入口）
- `AGENTS.md`（多 Agent 通用入口，Codex / Cursor 也读取）
- `.cursorrules`（Cursor 专用规则文件）
- `Copilot Instructions`（GitHub Copilot 专用）

**硬约束/软约束：** 软约束。文件存在 ≠ Agent 一定遵守。Agent 可以忽略、遗忘、或被上下文压缩丢失其中的规则。

**失败点：**
1. 文件太长 → Agent 注意力稀释 → 关键规则被忽略
2. 规则写得太模糊（"保持代码简洁"）→ Agent 不知道具体怎么做
3. 上下文压缩导致规则丢失 → Agent 不再遵守后面的约束

**优先级：** P0。没有入口文件，Agent 对项目一无所知。

**标注：**
- [官方共识] OpenAI 明确提出 AGENTS.md 应控制在约 100 行，作为"目录页"而非"百科全书"
- [官方共识] Anthropic 提出"渐进式披露"（Progressive Disclosure）——主文件只放索引，详细内容按需加载
- [个人实现] AGENTS.md 从 209 行压缩到 74 行，只保留导航表 + 硬性规则 + 验证命令

---

#### C1.2 结构化知识库

**作用：** 把项目知识按稳定程度分层存放，让 Agent 能按需精确获取信息，而非一次性加载全部。

**约束对象：** Agent 获取项目信息的路径和粒度。

**常见实现：**
- `docs/architecture/` — 系统设计文档（最稳定）
- `docs/conventions/` — 编码规范（较稳定）
- `docs/design/` — 功能设计文档（随功能迭代变化）
- `docs/plans/` — 当前迭代计划（频繁变化）
- `docs/reference/` — API 规范、错误码、数据合同（工具性参考）

**硬约束/软约束：** 软约束。文档存在但 Agent 可以选择不读。

**失败点：**
1. 大文档 → Agent 读一次消耗大量上下文
2. 文档过时 → Agent 按错误信息行动
3. Agent 不知道某文档存在 → 漏掉关键约束

**优先级：** P0。没有知识库，Agent 每次都要从头推断项目规则。

**标注：**
- [官方共识] Anthropic 认为上下文优化的本质是"按需给、分层给、在正确的时机给"
- [社区实践] 大量开源项目采用 `docs/` 分层结构
- [个人实现] 5 层文档体系：architecture → conventions → design → plans → reference，每文件标注 `status` 字段（draft/active/stale）

---

#### C1.3 功能追踪文件

**作用：** 用结构化数据（JSON/YAML）替代纯文本描述，让 Agent 和脚本都能解析"项目当前有哪些功能、各自完成到什么程度、下一步该做什么"。

**约束对象：** Agent 对项目进度的认知。

**常见实现：**
- `feature_list.json` — 功能 ID + 验收标准 + 优先级 + 完成状态
- `progress.json` — 跨会话学习进度 + 已完成事项 + 下一步计划
- `roadmap.yml` — 路线图
- GitHub Issues / Project Boards（不是文件，是平台功能）

**硬约束/软约束：** 软约束。但结构化字段本身可以通过脚本做合同检查（如：每个 feature 必须有 id/name/acceptance/status 四个字段）。

**失败点：**
1. 状态字段和实际代码不一致（标记 done 但代码未实现）
2. Agent 只读了 JSON 但没读对应的设计文档

**优先级：** P1。小项目无需，但功能超过 10 个时必须。

**标注：**
- [官方共识] OpenAI Codex 团队使用结构化 issue 追踪来管理 Agent 的任务粒度
- [个人实现] feature_list.json 10 项功能，每项含 acceptance 数组；progress.json 含 completed + active_harness_concepts + next_steps

---

#### C1.4 跨会话项目记忆

**作用：** 保存"关于这个项目"的长期信息，使得切换新会话后 Agent 不必从零重建对项目的理解。与 Runtime Agent Harness 的记忆层不同——那里管的是"本次运行中跨 Stage 的状态"，这里管的是"跨人类对话会话的项目级知识"。

**约束对象：** Agent 跨会话的知识连续性。

**常见实现：**
- 项目级 `memory/` 目录（Markdown 文件 + YAML frontmatter + `MEMORY.md` 索引）
- Claude Code 服务端自动 Memory（Agent 对话中自己记录）
- 知识图谱文件（如 Understand-Anything 的 `knowledge-graph.json`）

**硬约束/软约束：** 软约束。Agent 可以选择不读。

**失败点：**
1. 记忆文件过时未更新 → Agent 按旧信息行动
2. 索引文件不完整 → Agent 不知道某条记忆存在

**优先级：** P2。小项目不需要，长期维护的多会话项目必须。

**标注：**
- [社区实践] Claude Code 提供 `memory/` 目录机制 + `MEMORY.md` 索引
- [个人实现] memory/ 目录含 user-profile.md + project-context.md + architecture-decisions.md，Git 版本管理

---

### C2. 约束层（Constraint Layer）

#### C2.1 硬约束检查引擎（Linter）

**作用：** 把 AGENTS.md 中的软约束变成可程序化验证的硬约束。每次运行输出统一的三段式格式（❌问题 + ✅FIX + 📖See）。Agent 违规时不是靠"自觉"发现，而是被脚本强制检测。

**约束对象：** Agent 产出的代码的结构、格式、架构合规性。

**常见实现：**
- 项目专属 Python/Node.js 脚本（AST 解析 + 文本扫描 + 文件系统检查）
- ESLint / Pylint 等语言级 linter（管代码风格，不管架构约束）
- Semgrep / CodeQL（AST 级别的安全/架构规则检查）
- import-linter / dependency-cruiser / ArchUnit（依赖图级别的架构约束检查）

**硬约束/软约束：** 硬约束。脚本返回非零 exit code → CI 失败。Agent 无法绕过。

**失败点：**
1. 检查规则不完整 → 存在未被检测的违规类型
2. 检查脚本有 bug → 漏报（比误报危险得多）
3. 检查过度 → Agent 被频繁打断，上下文被错误信息塞满

**优先级：** P0。这是 Coding Agent Harness 的核心。

**标注：**
- [官方共识] Mitchell Hashimoto："每次 Agent 犯错，花时间工程化一个方案让它永远不会再犯同样的错误"——linter 就是这个方案的载体
- [官方共识] OpenAI 提出"每次错误不要只调 prompt，要把修复沉到环境里"——linter 就是环境的一部分
- [个人实现] 15 项检查，分四类：存在性(3)/格式合同(7)/架构安全(2)/CI质量(3)

---

#### C2.2 四道拦截链

**作用：** 在不同时间点、以不同拦截强度检查 Agent 产出的代码。不是一道防线，而是一条纵深防线链——突破一道还有下一道。

**约束对象：** Agent 写出的代码在提交、合并两个关键节点上被强制检查。

**常见实现：**

| 防线 | 触发时机 | 拦截强度 | 能否绕过 |
|------|---------|---------|---------|
| PostToolUse Hook | 每次 Edit/Write 后 | 弱（仅注入信息） | Agent 可以无视 |
| Agent Guardrails | Agent 手动或 Hook 触发 | 中（全量检查） | Agent 可以不跑 |
| Pre-commit Hook | git commit 时 | 强（拒绝提交） | `--no-verify` 可跳过 |
| CI Pipeline | PR/push 时 | 最强（block merge） | 无法绕过 |

**硬约束/软约束：**
- Hook → 软（只能"喊话"，不能"开枪"）。因为它是把检查结果注入 tool_result，Agent 可以选择不修。
- Pre-commit → 硬（能阻止 commit）。但 `git commit --no-verify` 可以跳过。
- CI → 硬（能阻止 merge）。且在远程服务器上执行，本地无法干预。

**失败点：**
1. Hook 输出太长 → 被 Claude Code 截断 → Agent 看不到完整错误
2. Hook 触发太频繁 → 上下文被大量错误信息污染 → Agent 注意力退化
3. Pre-commit 被 `--no-verify` 跳过 → 不合规代码进入仓库
4. CI 只检查了部分规则 → 漏掉的规则无人检查

**策略：** Hook 默认无声（未违规时零输出），违规时只输出违规项（不输出通过项）。`harness_linter.py --quiet` 模式专为此设计。

**优先级：** P0。没有拦截链，所有约束都靠 Agent 自律。

**标注：**
- [官方共识] PostToolUse Hook 是 Claude Code 的原生机制，Anthropic 官方文档描述
- [社区实践] Pre-commit + CI 是业界通用工程实践，不限于 AI Agent 场景
- [个人实现] Hook = 每次 Edit 后自动跑 `harness_linter.py --quiet`；Pre-commit = 4 门检查；CI = GitHub Actions 3 类检查 + 合同文档同步检查

---

#### C2.3 Git Worktree 隔离验证

**作用：** Agent 在大改动时，先在隔离的 Git 工作树中修改，全部检查通过后才合并回主分支。失败则销毁工作树，主分支不受任何影响。

**约束对象：** Agent 大改动的影响范围（爆炸半径）。

**常见实现：**
- `git worktree add` → 在独立目录中操作 → 验证通过 → 合并 → 删除 worktree
- 验证失败 → 保留 worktree 现场供人工排查 → 或直接销毁

**硬约束/软约束：** 软约束（Agent 可以选择不用 worktree）。但验证步骤（linter + pytest）是硬约束。

**失败点：**
1. Agent 不主动使用 worktree（软约束的通病）
2. Worktree 创建/销毁的时间开销（200-500ms + 磁盘 I/O）

**优先级：** P2。日常小改动不需要，大重构/跨文件改动时建议使用。

**标注：**
- [社区实践] Claude Code Workflow 工具原生支持 worktree 隔离（`isolation: "worktree"` 参数）
- [个人实现] agent-verify.sh：6 步 worktree 验证流程

---

### C3. 反馈闭环（Feedback Loop）

#### C3.1 统一错误格式

**作用：** 所有拦截层（Hook / Guardrails / Pre-commit / CI）输出同一格式的错误信息，让 Agent 不需要重新分析就能直接执行修复。

**格式：**
```
❌ [具体问题 + 位置]
✅ FIX: [可直接执行的修复指令]
📖 See: [相关文档路径，仅需深入理解时阅读]
```

**约束对象：** Agent 收到错误后的修复行为。

**常见实现：**
- 所有检查脚本共用同一个 `_fmt(problem, fix, doc)` 函数生成输出
- CI 的 error annotation 直接引用同样的格式
- Hook stdout 输出同样的格式

**硬约束/软约束：** 信息本身是软约束（Agent 可以选择不修）。但信息的精炼程度决定了 Agent 是否倾向于修——模糊指令容易被忽略，可执行指令容易被执行。

**失败点：**
1. ✅FIX 写得太模糊（"请检查并修复"）→ Agent 还是要自己想 → 消耗 token 且可能修错
2. 📖See 指向的文档不存在 → Agent 浪费 token 读空文件
3. 错误信息太长 → 占用过多上下文预算

**优先级：** P0。没有统一格式，Agent 在每个拦截层都要重新分析"这是什么错误、怎么修"。

**标注：**
- [官方共识] OpenAI："不要把修复留在人脑子里，沉到环境里"——三段式格式就是把修复指令沉到检查脚本里
- [社区实践] LangChain 的 trace 回放机制也是类似思路——用结构化反馈替代自由文本反馈
- [个人实现] `_fmt()` 函数统一定义，所有 15 项检查共享

---

#### C3.2 重试升级

**作用：** 同一个违规重复出现时，错误信息的内容自动升级——从"请修复"到"先读文档"到"强制 Plan Mode"到"人工接管"。

**约束对象：** Agent 修复失败后的下一步行为。

**常见实现：**
- 违规指纹（文件路径 + 规则名 + 违规内容哈希）→ 跟踪重试次数
- 重试状态持久化到文件（如 `.harness_retry_state.json`）
- 根据重试次数输出不同级别的指令

**硬约束/软约束：** 软约束（升级后的指令 Agent 仍可选择无视）。最终硬约束是 CI 的 block merge。

**失败点：**
1. 状态文件未在合适的时机清理 → 误判重试次数
2. Agent 在收到"禁止修复"指令后仍继续尝试 → 浪费 token

**优先级：** P2。小项目不需要。Agent 频繁陷入修复循环时启用。

**标注：**
- [个人实现] 违规指纹 + 4 级升级：标准提示 → 加强警告 → Plan Mode → 人工接管

---

## 二、RUNTIME AGENT HARNESS

> 约束对象：你的 AI Agent 产品中负责执行任务的 LLM Agent
> 解决的问题：Agent 跑长链路任务时，怎么保证每一步的输出合规、下一步的方向正确、全程可追溯？

---

### R1. 上下文管理（Context Management）

#### R1.1 可见性控制

**作用：** 限制每个执行阶段（Stage/Step）的 Agent 能看到哪些数据字段。不是为了防止 Agent 违规（Agent 可以绕过），而是为了控制 LLM 的上下文预算——不让当前阶段不需要的数据占据 prompt 空间。

**约束对象：** 发给 LLM 的 prompt 中包含哪些数据字段。

**常见实现：**
- 白名单：定义每个 Stage 可见的字段集合，只注入这些字段
- 字段压缩：列表类数据只送统计值+前 N 条样例，完整数据放文件引用
- 结构化排布：固定规则 → 动态数据 → 中间结果 → 当前任务，四段分离

**硬约束/软约束：** 硬约束。白名单在代码中定义，Agent 无法在运行时给自己增加可见字段。

**失败点：**
1. 白名单定义错了 → Agent 缺少必要信息 → 产出质量下降
2. 压缩过度 → 样例不足以让 Agent 理解数据全貌

**优先级：** P0。不控制可见性，prompt 会随着数据量增长而膨胀，超出上下文窗口或稀释注意力。

**标注：**
- [官方共识] Anthropic 提出"just-in-time retrieval"——Agent 边干活边按需抓信息
- [社区实践] 多数 AI Agent 框架（LangChain、CrewAI）提供上下文过滤机制
- [个人实现] `STAGE_VISIBLE_FIELDS` 字典 + `compact_for_prompt()` 压缩函数

---

#### R1.2 Agent Prompt 模板

**作用：** 每个 Stage 的 Agent 有独立、精确的角色设定和工作指令。不是所有 Stage 共用一个通用 prompt。

**约束对象：** Agent 在每个 Stage 的角色定位和行为边界。

**常见实现：**
- 每 Stage 一个 prompt 模板文件（Markdown）
- 模板中声明：角色身份 + 当前任务 + 输入数据格式 + 输出格式要求 + 禁止事项
- 运行时由 prompt builder 拼接：Agent 模板 + 上下文数据 + 用户消息

**硬约束/软约束：** 软约束（Agent 可能不遵守 prompt 中的禁止事项）。靠 linter 在输出侧硬约束兜底。

**失败点：**
1. Prompt 写得太长 → 弱化关键指令
2. 输出格式描述模糊 → Agent 产出的格式不稳定

**优先级：** P0。LLM Stage 必须有独立的 Agent Prompt。

**标注：**
- [官方共识] Anthropic 在《Building effective agents》中强调：给 Agent 清晰的角色和明确的工作边界
- [个人实现] 3 个 LLM Stage 各自有 prompt 模板（structuring_agent / analysis_agent / report_agent）

---

### R2. 工具系统（Tool System）

#### R2.1 工具注册与白名单

**作用：** 所有工具集中注册，每个 Stage 只能调用被授权过的工具子集。不在白名单中的工具调用 → 抛异常。

**约束对象：** Agent 在单个 Stage 中可以调用的工具范围。

**常见实现：**
- 全局工具注册表：`TOOL_REGISTRY = {name: function}`
- 每 Stage 白名单：`STAGE_ALLOWED_TOOLS = {stage_name: {tool_a, tool_b}}`
- 调用网关：`ToolGateway.call(stage, tool_name, *args)` → 检查白名单 → 执行或拒绝

**硬约束/软约束：** 硬约束。不在白名单 → 代码抛 `PermissionError`。Agent 无法绕过。

**失败点：**
1. 白名单定义过宽 → Agent 在不合适的时机调用了不该调的工具
2. 白名单定义过窄 → Agent 缺少必要工具 → 任务无法完成

**优先级：** P0。没有白名单，Agent 可以调用任何工具——包括危险的系统操作。

**标注：**
- [官方共识] Anthropic 在 Agent Skills 设计中强调"工具可用性应是声明式约束，不是 prompt 建议"
- [社区实践] MCP（Model Context Protocol）定义工具注册和权限的标准协议
- [个人实现] `TOOL_REGISTRY` + `STAGE_ALLOWED_TOOLS` + `ToolGateway` 三层结构

---

#### R2.2 工具返回精炼

**作用：** 原始工具返回结果在送入 Agent 上下文之前，先提取关键信息、压缩格式、去掉冗余。防止 30 条搜索结果撑爆 prompt。

**约束对象：** 工具返回数据进入 Agent 上下文前的格式和大小。

**常见实现：**
- 字段过滤：只保留 Agent 当前阶段需要的字段
- 列表压缩：`{"count": N, "sample": [前3条], "full_data_path": "..."}`
- 结构化摘要：将非结构化文本转为结构化摘要

**硬约束/软约束：** 硬约束（代码决定的，Agent 无法跳过）。

**失败点：**
1. 压缩丢失关键细节 → Agent 基于不完整信息做决策
2. Agent 不知道有完整数据文件存在 → 不会主动去读取

**优先级：** P1。数据量大时必须。

**标注：**
- [官方共识] Anthropic 在 Context Engineering 指南中强调"不要把完整数据塞进 prompt"
- [个人实现] `compact_for_prompt()` 函数 + `artifact_hint` 字段指向完整数据文件路径

---

### R3. 执行编排（Execution Orchestration）

#### R3.1 阶段状态机

**作用：** 定义任务的所有合法阶段、各阶段之间的合法转换路径、从当前阶段推导下一阶段的确定性规则。Agent 不能自己决定"下一步干什么"——状态机决定。

**约束对象：** Agent 的执行流程方向。

**常见实现：**
- 有限状态机：阶段枚举 + 路由决策函数（`decide_next_stage(state) → next_stage`）
- 路由规则是确定性代码，不依赖 LLM 判断
- 异常路径：产物为空 → failed；产物非空 → 下一阶段

**硬约束/软约束：** 硬约束。路由是代码决定的，Agent 无法更改。

**失败点：**
1. 路由规则遗漏边界情况 → Agent 卡在某个阶段
2. 状态机太僵化 → 无法处理"中间某步需要人工确认"的场景

**优先级：** P0。没有状态机，Agent 自己决定流程走向——这正是 Agent 跑偏的根源。

**标注：**
- [官方共识] Anthropic 在 Harness 设计中明确："Agent 不应该自己决定下一步，应该有一条明确的执行轨道"
- [社区实践] LangGraph（LangChain）、CrewAI Flows 都是状态机实现
- [个人实现] `InsightEngineGraph.decide_next_stage()` → 6 个合法阶段 + 过渡规则

---

#### R3.2 生命周期钩子

**作用：** 在每个阶段的 before/after 节点插入可注册的监听器（Hook）。监听器负责：记录（trace）、快照（prompt/state 保存）、校验（linter）。Graph 不需要知道注册了哪些监听器——它只触发，不关心谁在监听。

**约束对象：** 各阶段的副作用（记录、校验、快照）与业务逻辑的解耦。

**常见实现：**
- 插槽系统：`on_before(fn)` / `on_after(fn)` 注册 → `fire_before()` / `fire_after()` 触发
- 注册顺序决定执行顺序（linter 先注册 → 先执行 → gate 结果先写入 state → trace 能读到）
- Linter 只是 after hook 中的一个普通监听器——它不特殊

**硬约束/软约束：** 硬约束。代码注册的监听器一定会执行，Agent 无法跳过。

**失败点：**
1. 注册顺序错误 → trace 读不到 linter 结果（linter 还没运行）
2. 某个监听器抛异常 → 后续监听器不执行 → 需要 try/except 隔离

**优先级：** P0。没有生命周期钩子，每次加新检查都要改 Graph 代码。

**标注：**
- [社区实践] 观察者模式（Observer Pattern）的经典应用
- [个人实现] `StageHooks` 类 + 5 个默认监听器（2 before + 3 after），注册顺序是刻意设计的

---

#### R3.3 受限推理循环（Constrained ReAct）

**作用：** 当 Stage 需要 LLM 多步推理时（ReAct），每步的 action 被限定在预定义白名单中，步数有上限。Agent 不能自己决定"再多来几步"或"我试试这个新的 action"。

**约束对象：** Agent 在推理阶段的行动范围。

**常见实现：**
- 固定步骤计划：预定义 N 步，每步对应一个 action
- Action 白名单：每步只能从预定义集合中选择 action
- 强制退出条件：到达步数上限 → 强制输出结果 → 不允许继续

**硬约束/软约束：** 硬约束。步数上限 + action 白名单由代码控制。

**失败点：**
1. Action 白名单太窄 → Agent 需要的 action 不在其中 → 任务卡住
2. 步数上限太低 → Agent 推理不充分 → 结果质量差

**优先级：** P1。仅在需要 ReAct 推理的 Stage 中启用。确定性 Stage 不需要。

**标注：**
- [官方共识] Anthropic 和 OpenAI 都观察到：不受限的 ReAct 容易失控（action 漂移、无限循环）
- [社区实践] LangChain 的 `AgentExecutor` 支持 `max_iterations` 参数
- [个人实现] analyze_insights 的 5 步固定 plan → 统计 → Top → 趋势 → finish

---

### R4. 记忆与状态（Memory & State）

#### R4.1 单次运行状态

**作用：** 一次完整任务中的共享状态对象。所有 Stage 在上面读写各自的产物字段。下游 Stage 假定上游产物的字段合同已被 linter 校验通过。

**约束对象：** 各 Stage 之间的数据传递格式和完整性。

**常见实现：**
- 单一 State 对象贯穿所有 Stage（dataclass / TypedDict / Pydantic Model）
- 字段合同定义：每个字段的类型、必填、用途在代码中显式声明
- 产物字段逐 Stage 追加，前向只读（后面 Stage 不能修改前面 Stage 的产出）

**硬约束/软约束：** 硬约束。字段合同由代码定义，linter 在校验时逐字段比对。

**失败点：**
1. 字段合同和实际产出不一致 → linter 漏检 → 下游 Stage 消费到错误格式的数据
2. State 对象太大 → 内存膨胀

**优先级：** P0。没有共享 State，Stage 之间靠约定俗成传数据——迟早不一致。

**标注：**
- [社区实践] 多数 Agent 框架都有 State 概念（LangGraph State、CrewAI Task Output）
- [个人实现] `InsightEngineState` dataclass + 5 份 `FIELD_SPEC` 字典 + `to_dict()` 序列化

---

#### R4.2 产物持久化

**作用：** 每个 Stage 的产出落盘为独立文件。所有产物路径注册在 State 的 artifacts 字典中。任何时候可以从磁盘恢复完整运行状态。

**约束对象：** 任务中断后的恢复能力和全链路审计能力。

**常见实现：**
- 每 Stage 产出一个或多个 JSON/Markdown 文件
- 统一产物目录：`data/{stage}/{run_id}/` 或 `outputs/{type}/{run_id}/`
- artifacts 字典：`{"raw_items": "data/raw/abc123/raw_items.json", ...}`
- run_artifact：单文件自包含的完整运行审计产物

**硬约束/软约束：** 硬约束。代码控制写入，Agent 无法跳过。

**失败点：**
1. 磁盘空间不足 → 写入失败
2. 产物格式与 FIELD_SPEC 不一致 → 下游读取失败

**优先级：** P0。没有持久化，出了 bug 无法追溯"哪个 Stage 的哪一步产出有问题"。

**标注：**
- [官方共识] Anthropic："Agent 的状态不应该只留在上下文窗口里，应该外化到文件系统"
- [个人实现] 每 Stage 产物落盘 + artifacts 路径注册表 + 自包含 run_artifact JSON

---

### R5. 评估与观测（Evaluation & Observability）

#### R5.1 产物合同校验（Linter）

**作用：** 每个 Stage 执行后，确定性代码检查产物是否满足 FIELD_SPEC 合同。不靠 Agent 自评——Agent 永远觉得自己做得不错。

**约束对象：** 每个 Stage 产出的数据质量。

**常见实现：**
- 每 Stage 一个 linter 函数：`lint(state) → {passed, issues, retryable}`
- Linter 读取 FIELD_SPEC 合同，逐字段检查：存在性、类型、非空、格式
- Linter 作为 after-stage hook 注册，由 Graph 读取其返回结果决定去向

**硬约束/软约束：** 硬约束。Linter 是确定性代码，Agent 的输出必须通过检查。

**失败点：**
1. FIELD_SPEC 定义不完整 → linter 检查不到真正的质量问题
2. Linter 太严格 → Agent 反复被拒 → 耗光重试次数

**优先级：** P0。没有 linter，Agent 输出质量的唯一保障是 prompt 中的"请确保格式正确"。

**标注：**
- [官方共识] OpenAI："不要让 Agent 给自己打分，让独立系统来评估"——linter 就是这个独立系统
- [官方共识] Mitchell Hashimoto："把校验写成代码，不要写成 prompt"
- [个人实现] 5 个 Stage 各一个 linter + 共用 `lint_result()` 格式函数

---

#### R5.2 全链路追踪

**作用：** 记录每次运行的每一步真实足迹——哪个 Stage 在什么时间开始/结束、耗时多少、gate 通过与否、产生了哪些产物。

**约束对象：** 事后排查问题时的信息可追溯性。

**常见实现：**
- `stage_trace` 数组：每 Stage 一条记录（stage / started_at / finished_at / duration_ms / status / gate_result / artifact_keys）
- 全链路 run_artifact：单文件（JSON + Markdown）按时间线串起所有关键事件
- 业界工具：LangSmith、Langfuse、Weights & Biases

**硬约束/软约束：** 硬约束。trace 记录由生命周期钩子自动执行，Agent 无法跳过。

**失败点：**
1. trace 数据太多 → 单文件过大
2. 缺少可视化 → 人工读 trace 效率低

**优先级：** P1。生产环境必须。开发调试阶段强建议。

**标注：**
- [社区实践] LangSmith / Langfuse 是业界标配的 Agent trace 工具
- [个人实现] `stage_trace` + `stage_gate_results` + `run_artifact.json`（自包含，所有 artifact 内容嵌入）

---

#### R5.3 Eval 基准

**作用：** 手写一批典型任务，每个标注正确答案。每次修改 Agent 的 prompt 或工具后，跑一遍 Eval 集，对比成功率和输出质量的变化。没有 Eval 集，你对 Agent 的判断永远是"我感觉这次变好了"。

**约束对象：** Agent 改动的效果评估。

**常见实现：**
- 手写 `tests/` 中的 Eval case（给定输入 → 期望输出）
- CI 中每次跑 Eval 集
- 对比改前改后的成功率、输出字段完整率、幻觉率

**硬约束/软约束：** 硬约束（CI 中跑测试）。但 Eval 集的覆盖面是有限制的——你只能测你想到的 case。

**失败点：**
1. Eval 集覆盖面不够 → 改动的负面影响未被发现
2. Eval 集过时 → 测试的是旧行为，不是新需求

**优先级：** P1。Agent 上了生产或频繁改 prompt 时必须。

**标注：**
- [官方共识] Anthropic："没有 Eval 集，Agent 迭代就是蒙眼走路"
- [社区实践] LangChain 的 LangSmith 提供 Eval 管理功能
- [个人实现] `tests/test_graph.py` + `test_harness_controls.py` + `test_conversation_router.py`

---

### R6. 约束与恢复（Constraint & Recovery）

#### R6.1 硬约束规则

**作用：** 架构安全规则写成代码而非 prompt。典型例子：确定性阶段不能调用 LLM、Stage 必须有对应 linter、依赖方向不能反向。

**约束对象：** Agent 的行为边界（能做什么、不能做什么）。

**常见实现：**
- 域名检测（检查代码中是否包含 LLM API 域名——换 SDK 改不了域名）
- SDK import 检测（辅助层，检查 AI SDK 导入）
- 工具白名单（不在白名单的工具无法调用）
- 字段可见性控制（不在白名单的字段无法注入 prompt）

**硬约束/软约束：** 硬约束。所有检查都是代码执行，Agent 无法干预。

**失败点：**
1. 检测手段是文本级而非 AST 级 → Agent 可通过字符串拼接绕过（`"api." + "openai.com"`）
2. 规则不完整 → 存在未被定义的违规路径

**优先级：** P0。安全相关的硬约束必须存在。

**标注：**
- [官方共识] OpenAI："安全约束不能写在 prompt 里，要写在代码里"——这是 Coding Agent Harness 和 Runtime Agent Harness 的共享哲学
- [个人实现] 域名检测（16+ 域名）+ SDK 检测（14 种模式）两层交叉覆盖

---

#### R6.2 失败分级与恢复

**作用：** 不是所有失败一视同仁。区分可重试（API 超时、LLM 输出格式错误）和不可重试（字段合同不满足、依赖缺失），每种失败有独立的恢复路径。

**约束对象：** Agent 执行失败后的系统行为。

**常见实现：**
- 错误分级：`error`（致命，不可恢复） / `warning`（非致命，记录但不中断） / `retryable`（可重试）
- 重试策略：指数退避（API 429）、固定间隔（LLM 输出格式错误）、不重试（合同失败）
- Fallback 降级：LLM 不可用 → 关键词规则；LLM 输出 repair 失败 → fallback 模板
- Context Reset：上下文腐化时丢弃旧窗口，从文件系统恢复状态

**硬约束/软约束：** 硬约束。策略由代码控制。

**失败点：**
1. 重试次数太多 → 耗光 token 预算和时间
2. Fallback 规则太弱 → 降级后结果不可用
3. Context Reset 时状态文件不完整 → 新窗口不知道从哪继续

**优先级：** P0。生产环境 Agent 必须有失败恢复机制。

**标注：**
- [官方共识] Anthropic："失败不是例外，是常态。每种典型失败都应该有明确的恢复路径"
- [官方共识] Anthropic Context Reset："重启胜过修补，状态沉到文件里"
- [个人实现] graph 层重试控制 + LLMClient 层重试 + fallback 规则 + artifacts 持久化

---

## 三、LEARNING LOOP

> 作用对象：Runtime Agent Harness 中的记忆与技能系统
> 解决的问题：Agent 每次都是"新手"，不记得上周处理过类似任务。怎么让它从经验中学习、越用越强？

---

### L1. 经验提取

**作用：** Agent 完成一次复杂任务后，自动从执行轨迹中提取可复用的模式——成功路径、失败原因、关键决策点。

**约束对象：** Agent 对自身经验的提炼能力。

**常见实现：**
- LLM 事后总结：任务完成后 → LLM 读取 trace → 生成"这次学到了什么"
- 结构化输出：提取为 `{trigger: "...", steps: [...], success_criteria: [...]}`

**硬约束/软约束：** 软约束（提取质量取决于 LLM）。提取结果仍需人工或自动化验证。

**优先级：** P3。

**标注：**
- [社区实践] Hermes Agent 的 Learning Loop 核心就是经验提取 → 技能生成 → 复用

---

### L2. 技能生成与复用

**作用：** 从经验中提取的模式序列化为 skill 文件（Markdown + YAML frontmatter），存于技能库中。下次遇到相似任务时，搜索已有 skill 直接复用——不重新规划、不重复犯错。

**约束对象：** Agent 面对重复任务时的效率。

**常见实现：**
- Skill 文件格式：`SKILL.md`（描述触发条件 + 执行步骤 + 成功标准 + 已知陷阱）
- 技能库：`~/.hermes/skills/` 或项目级 `skills/` 目录
- 技能匹配：用户任务 → 语义搜索技能库 → 匹配 skill → 优先复用

**硬约束/软约束：** 软约束。技能复用是 Agent 的决策，不是强制执行。

**优先级：** P3。

**标注：**
- [社区实践] Hermes Agent 的 skill 生成 + agentskills.io 标准化
- [社区实践] Claude Code 的 Skill 系统（SKILL.md + 语义匹配）提供了基础设施，但技能仍需人工编写

---

### L3. 跨会话记忆搜索

**作用：** Agent 能搜索历史会话，找到"上次这类问题是怎么处理的"——把跨会话记忆变成可检索的资产而非废弃的日志。

**约束对象：** Agent 跨会话的知识连续性。

**常见实现：**
- FTS5 全文搜索（Hermes Agent 使用 SQLite FTS5 索引历史会话）
- 向量搜索（embedding + 向量数据库检索语义相似的过往经验）
- 关键词/标签索引

**硬约束/软约束：** 软约束。搜索结果的质量决定 Agent 是否采用。

**优先级：** P3。

**标注：**
- [社区实践] Hermes Agent 内置 FTS5 会话搜索
- [社区实践] Claude Code 的 Memory 系统提供跨会话持久化，但不提供语义搜索

---

### L4. 自动化清理与维护

**作用：** 后台 Agent 定期扫描仓库——检查超大文件、缺失测试、TODO/FIXME 遗留、过时文档、代码重复——自动开修复 PR，人类快速审核合并。技术债从"攒着集中还"变成"每天自动还一点"。

**约束对象：** 项目技术债的积累速度。

**常见实现：**
- 定时调度（cron / GitHub Actions scheduled workflow）
- Cleaning Agent Prompt 模板（结构化检查项 + 修复策略）
- 自动开 PR + auto-merge（CI 全部通过 → 人类仅需 approve）

**硬约束/软约束：** 软约束（Agent 开的 PR 仍需人类审核）。但 CI 验证是硬约束——PR 不过 CI 不能合并。

**优先级：** P2。小项目人工维护即可。Agent 产出大量代码的长期项目必须。

**标注：**
- [官方共识] OpenAI Codex 团队："技术债像高利息贷款，几乎永远应该每天小额还一点"
- [社区实践] Dependabot、Renovate 是依赖更新的自动化清理
- [个人实现] cleanup-agent-prompt.md（5 类检查）+ check-doc-freshness.sh + observability-report.sh

---

## 四、最小可用 Harness vs 完整 Hermes-like Harness

| 能力模块 | 最小可用（1 人项目，2 周搭建） | 完整 Hermes-like（10+ 人团队，持续迭代） |
|---|---|---|
| **Coding: 入口文件** | 一个 CLAUDE.md（50 行以内） | CLAUDE.md + AGENTS.md + .cursorrules + 多 Agent 适配 |
| **Coding: 知识库** | 1-2 个架构文档 | 5 层文档体系 + status 标记 + CI 新鲜度检查 |
| **Coding: Linter** | 3-5 项核心检查（行数/禁止 import/文件存在） | 15+ 项检查 + AST 级检测 + import 图分析 + 合同文档同步 |
| **Coding: 拦截链** | Pre-commit + CI (GitHub Actions 免费额度) | PostToolUse Hook + Guardrails + Pre-commit + CI 四道全开 |
| **Coding: 反馈闭环** | 三段式错误格式（❌✅📖） | + 违规指纹 + 重试计数 + 4 级升级 + CI 最终拦截 |
| **Runtime: 上下文管理** | 固定 System Prompt | 可见性白名单 + 字段压缩 + prompt 模板 + 缓存友好设计 |
| **Runtime: 工具系统** | 无（Agent 不调工具） | 工具注册表 + 阶段白名单 + 工具返回精炼 + MCP 标准化 |
| **Runtime: 执行编排** | 线性脚本（无状态机） | 状态机 + 生命周期钩子 + 受限 ReAct + 重试控制 |
| **Runtime: 记忆状态** | 无（每次独立运行） | 共享 State + 字段合同 + 产物持久化 + run_artifact 审计 |
| **Runtime: 评估观测** | 人工抽查输出 | 每 Stage linter + 全链路 trace + Eval 基准 + LangSmith/Langfuse |
| **Runtime: 约束恢复** | 无（失败就报错退出） | 失败分级 + 重试策略 + Fallback 降级 + Context Reset |
| **Learning: 技能** | 无 | 经验提取 → skill 生成 → 技能库 → 语义匹配复用 |
| **Learning: 记忆搜索** | 无 | FTS5/向量搜索历史会话 → 找到类似经验 → 注入上下文 |
| **Learning: 自动清理** | 无 | 定时后台 Agent 扫描 → 自动开 PR → 人类审核合并 |

### 搭建顺序建议

```
Week 1-2（最小可用）:
  CLAUDE.md + harness_linter（3-5项）+ Pre-commit + CI + ❌✅📖 格式
  → 此时 Agent 已经有基本的约束，不会写出明显违规的代码

Week 3-4:
  docs/ 知识库 + 文件行数检查 + Stage-linter 配对检查
  → Agent 知道去哪找信息，产出结构合规

Month 2:
  Runtime: state + graph + stage_gates + linters + trace + run_artifact
  → Agent 跑任务时有完整的状态机和审计链

Month 3:
  PostToolUse Hook + 重试升级 + context_router + tool_gateway
  → 实时拦截 + 自动修复 + 上下文控制 + 工具管控

Month 4+:
  Eval 基准 + Learning Loop + 自动清理 + 适应度函数
  → Agent 越用越强，系统自己维护自己
```
