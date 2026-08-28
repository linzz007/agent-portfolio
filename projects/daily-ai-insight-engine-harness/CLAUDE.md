# CLAUDE.md

这个文件是 Claude Code 打开本项目时自动加载的上下文。
它比 AGENTS.md 更精简，聚焦于 Claude Code 的工作流程。

## 项目身份
Daily AI Insight Engine — 多阶段 AI 新闻分析流水线，Python 项目。

## 修改代码的工作流

每次修改代码必须按以下顺序执行：

```
1. 理解任务
   → 从 AGENTS.md 的快速导航表找到相关文档
   → 阅读 docs/architecture/ 了解架构约束

2. 修改代码
   → 遵守 docs/conventions/ 中的编码规范
   → 遵守 docs/architecture/boundaries.md 中的依赖规则
   → 确定性 stage（collect_raw_items、clean_items）绝不允许调 LLM

3. 验证修改
   → python3 -m compileall src scripts tests    # 编译检查
   → python3 scripts/harness_linter.py           # Harness 约束检查
   → python3 -m pytest                            # 单元测试

4. 修复问题
   → 任何失败都会给出 ❌ + ✅FIX + 📖See 的反馈
   → 按 ✅FIX 的指示修改，按 📖See 的路径查阅文档

5. 提交
   → pre-commit hook 会自动运行上面的验证
   → 不通过则不能 commit
```

## 关键禁止项

- 确定性 stage 不能 import OpenAICompatibleChatClient 或调 chat_json()
- 新增 stage 必须同时新增 linter
- 单文件不能超过 300 行
- 不能跳过 tool_gateway 直接注册工具

## 常用命令

```bash
# 一键护栏检查
bash scripts/agent-guardrails.sh

# 隔离验证（给大改动用）
bash scripts/agent-verify.sh

# 查看 harness 检查详情
python3 scripts/harness_linter.py
```
