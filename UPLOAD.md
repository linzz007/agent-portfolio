# Upload

建议在 GitHub 创建一个空仓库：

```text
linzz007/agent-portfolio
```

先设为 Private，确认展示效果后再改 Public。

创建空仓库后执行：

```powershell
git -C "D:\AAAcode\github_portfolio\agent-portfolio" remote add origin "https://github.com/linzz007/agent-portfolio.git"
git -C "D:\AAAcode\github_portfolio\agent-portfolio" push -u origin main
```

如果已经添加过 remote，则执行：

```powershell
git -C "D:\AAAcode\github_portfolio\agent-portfolio" remote set-url origin "https://github.com/linzz007/agent-portfolio.git"
git -C "D:\AAAcode\github_portfolio\agent-portfolio" push -u origin main
```
