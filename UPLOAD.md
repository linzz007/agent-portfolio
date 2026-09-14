# 作品集投递说明

招聘官网如果要求填写作品集链接，可以直接填写：

```text
https://github.com/linzz007/agent-portfolio
```

## 推荐填写说明

如果系统有“作品集描述”输入框，可以使用：

```text
Agent / LLM 应用工程作品集，包含课程学习 Agent Runtime、智能研判 Agent Workbench、Harness 机制验证和 ToB Agent 脱敏案例。项目按“待解决问题 -> 工程动作 -> 验证结果 -> 复盘优化”组织，重点展示 RAG、Memory、Context 管理、Agent Runtime、Subagent、Workflow、Trace 与 Eval 实践。
```

## 面试官阅读路径

1. 先看仓库首页 `README.md`，了解项目矩阵和简历对应关系。
2. 再看 `docs/interview_reading_guide.md`，了解最值得追问的问题。
3. 如果重点关注基础 Agent 工程能力，看 `projects/coursepilot-agent-runtime`。
4. 如果重点关注 Harness / Runtime / Subagent，看 `projects/ai-stack-impact-workbench`。

## 更新到 GitHub

本地仓库 remote 已配置为：

```text
https://github.com/linzz007/agent-portfolio.git
```

更新内容后执行：

```powershell
git -C "D:\AAAcode\github_portfolio\agent-portfolio" status
git -C "D:\AAAcode\github_portfolio\agent-portfolio" add .
git -C "D:\AAAcode\github_portfolio\agent-portfolio" commit -m "docs: align portfolio with resume 9.0"
git -C "D:\AAAcode\github_portfolio\agent-portfolio" push origin main
```
