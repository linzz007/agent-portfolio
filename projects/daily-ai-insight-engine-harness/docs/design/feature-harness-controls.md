---
last_updated: 2026-05-28
status: "✅ Implemented"
owner: coding-agent
---

# Feature: Harness 控制系统

## 目标
为 Runtime Agent 提供完整的 Harness 控制基础设施：状态管理、流程路由、
Gate 检查、Hook 插槽、Tool Gateway 白名单、Prompt 快照。

## 技术方案

### StageHooks 插槽系统
- before 监听器：prompt 快照、记录开始时间
- after 监听器：linter 检查 → trace 记录 → state 快照
- 注册顺序决定执行顺序（linter 最先，确保 gate 结果可被 trace 读取）

### Tool Gateway 白名单
- `TOOL_REGISTRY`：全局工具注册表
- `STAGE_ALLOWED_TOOLS`：每个 stage 的工具白名单
- 不在白名单的调用 → PermissionError

### Context Router
- 每个 stage 只能看到其需要的 state 字段
- 大型数据列表只传计数和样例，完整数据在 artifact 中

### Prompt Builder
- 拼装 agent prompt + runtime docs + visible state
- 生成 prompt 快照保存到 `data/prompts/{run_id}/`
- 重试时注入上次 linter 失败的具体反馈

### 验收标准
- Hook 系统可注册/触发 before/after 监听器
- Tool Gateway 正确拦截未授权调用
- Context Router 限制 stage 可见数据范围
- Prompt 快照完整可追溯
