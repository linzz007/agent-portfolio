# Runtime Kernel Task 8 验收记录

## 验收元数据

- 执行日期：2026-07-18（Asia/Shanghai）
- 工作树：`D:/AAAcode/code-code/agent+/harness/.worktrees/policy-impact-runtime-kernel`
- 分支：`codex/runtime-kernel`
- Task 8 验收基线：`1e458a14fabe1fa46cff4992d133834bd37f0442`
- Runtime Plan 起点：`9c082b50b5839a4186825337d8ff4f98ceb917ac`
- Python：`Python 3.10.9`
- Node.js：`v24.15.0`
- Task 8 自引用提交：不记录；本验收文件与 linter 变更将在验收完成后提交。

## Tasks 1-7 提交溯源

| Task | 提交 |
|---|---|
| Task 1 Runtime Contracts | `d368ae2b7a03067f823dbd582ba16e378fd1948b`、`58b280eaeaac2f716a6d967f281e9dac7cd5106b`、`ac14fe00c499e0a5d9d18de0c65b30914864a451`、`c53a488d715daeab9de2350dfcf470257780eb3c` |
| Task 2 Executable Skill Manifests | `f6eca630bcea328cba8d1eb7b05487ced7f27bbb`、`3fc5d5246fb42e6b74bfca65d34c3a25fc44f5e9`、`5e3ed7f79284502232be69ef5da8461c4fdee08f` |
| Task 3 Executor Registry | `f1e34082e6ee12c7623046b8e5b4f093866b2dbd`、`f9514bc7e25e6edc8fae7fad524aee7fc1e9b477` |
| Task 4 Turn Coordinator | `f24809a43232177b02d79a581114791a607f47a8`、`0f1183384fd6f6979e0ea94268891f135d7dee46`、`20698e84c0558c6210fd2d2ffbb380000514d888` |
| Task 5 Agent Loop | `e56ba02850f9081828cc9a635aa7e3c8248396f1`、`51639055c923d38f8eacf430a81fb7984eb4d448` |
| Task 6 Subagent Delegation | `68454015440748b945dc3806c827cb2a0ff2688f`、`82fe05e9aea6002ae4af1821ccb2c4babd824500` |
| Task 7 Business Gates | `1e458a14fabe1fa46cff4992d133834bd37f0442` |

## Task 8 静态验收门

- `HK001`：`TurnCoordinator` 不按默认 Skill ID 或其静态数据流别名建立业务控制分支。
- `HK002`：`AgentRuntime` 不得固定写入伪 `subagent_driven`；`TurnCoordinator` 的实际模式必须直接来自可信 executor 返回的 `SkillResult.actual_execution_mode`，且拒绝不可信 reaching definition。
- `HK003`：默认 manifest 的 `executor_id` 必须能在组合根的直接、可达注册集合中静态证明。
- `HK004`：模型 Action 必须经过 `TypeAdapter(AgentAction).validate_python`；校验必须支配 Action 分支，且 `ValidationError` 路径受控终止本次迭代或函数。
- `HK005`：只有 `runtime/gates.py` 可以访问私有 `_evaluate`。
- `HK006`：兼容 Gate 模块不能建立第二套 Registry 或 Decision 权威类型。
- `HK007`：生产 `GateRunner` 构造必须显式传入非空 sink，展开参数不视为可证明。
- `HK008`：`GateEngine` 必须显式注入非空 runner，展开参数不视为可证明。
- `HK000/HK009`：继续执行 required path、空文件、UTF-8/语法 fail-closed，以及旧模型 Name、字符串与可静态折叠字符串表达式检查。

两轮独立对抗复核后，契约测试新增 18 个绕过/误报夹具，覆盖 Skill 路由别名、ExecutionMode 来源与顺序、不可达注册、Action 校验支配、原始 payload 辅助函数、Gate 私有入口/构造器别名、空依赖别名和旧模型字符串拼接。

## 验收命令与结果

以下结果均在上述工作树与基线提交上执行，Task 8 未提交改动包含 Linter、契约测试、README 和本验收文件。

| 验收项 | 命令 | 结果 |
|---|---|---|
| Harness 静态契约夹具 | `py -3 -m pytest tests/test_harness_contracts.py -q` | 通过，`57 passed in 11.45s` |
| Runtime Kernel 核心回归 | `py -3 -m pytest tests/test_runtime_contracts.py tests/test_skill_registry.py tests/test_executor_registry.py tests/test_turn_coordinator.py tests/test_agent_loop.py tests/test_subagents.py tests/test_agent_runtime.py tests/test_gate_catalog.py tests/test_gate_audit_store.py tests/test_gate_engine.py tests/test_stage_gates.py tests/test_full_harness_pipeline.py -q` | 通过，`340 passed in 20.16s` |
| Python 静态编译 | `py -3 -m compileall -q src scripts tests` | 通过，退出码 `0` |
| Harness Linter | `py -3 scripts/harness_linter.py` | 通过，`policy_impact harness lint passed (HK001-HK009)` |
| 仓库全量测试 | `py -3 -m pytest -q` | 通过，`526 passed in 36.71s` |
| Workbench JavaScript 语法 | `node --check src/policy_impact/app/static/workbench/app.js` | 通过，退出码 `0` |
| Git 空白错误检查 | `git diff --check` | 通过，退出码 `0`；仅出现 Windows `core.autocrlf` 的 LF/CRLF 提示，无空白错误 |

## 结论

- Runtime Kernel 当前计划范围内的执行契约、Skill/Executor、TurnCoordinator、Agent Loop、Subagent delegation 与政策业务 Gate 已通过静态和运行时回归。
- HK001-HK009 是针对已知架构漂移的工程守卫，不是形式化证明或业务质量分数；真实模型效果仍需独立 E2E/Eval 数据证明。
- 该结论只覆盖本文件列出的提交、命令和范围，不外推到“全部 Harness 能力完成”。

## 已知限制与未验证声明

- Business Gate Catalog 与 Publication Gate 当前只覆盖政策分析链路，不代表所有 Skills 已统一接入。
- Context Hard Gate 尚未覆盖所有模型调用；当前 ContextManifest 与预算治理不能表述为全链路硬门控。
- Permission `ask` 仍按阻断处理，没有完整审批暂停/恢复。
- Memory 尚不是完整的四层版本化 Memory；Proposal、版本、Namespace 查询和 Profile Snapshot 未完成。
- Replay 仅具备部分 Trace/Checkpoint 查看，不具备完整 Recorded/Fork/Fresh Replay。
- `AgentRuntime` 仍承担组合根和较多业务实现职责。
- AST Linter 检查目标静态结构不变量，不替代完整控制流证明、真实模型 E2E、运行时安全测试或业务效果评估。
- 本次验收不声明 42 Case、真实模型 E2E、B0/B1/T、性能提升、完整审批/Replay、全链路 Context Gate，或显著优于 Codex/Claude。
