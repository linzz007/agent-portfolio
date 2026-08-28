#!/usr/bin/env bash
# ============================================================================
# 安装 Git Hooks
# ============================================================================
# 将 scripts/pre-commit 安装到 .git/hooks/ 目录。
# 安装后每次 git commit 前都会自动触发 Coding Agent Harness 检查。
#
# 用法：
#   bash scripts/install-hooks.sh           # 安装所有 hooks
#   bash scripts/install-hooks.sh --force   # 强制覆盖已有 hooks
# ============================================================================

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

FORCE=false
[ "${1:-}" = "--force" ] && FORCE=true

GIT_DIR=$(git rev-parse --git-dir 2>/dev/null || echo "")
if [ -z "$GIT_DIR" ]; then
    echo "❌ 当前目录不是 git 仓库。请先运行 git init。"
    exit 1
fi

HOOKS_DIR="$GIT_DIR/hooks"

# ------------------------------------------------------------------
# pre-commit
# ------------------------------------------------------------------

TARGET="$HOOKS_DIR/pre-commit"
SOURCE="scripts/pre-commit"

if [ -f "$TARGET" ] && ! $FORCE; then
    echo "⏭  pre-commit hook 已存在，跳过（用 --force 强制覆盖）"
else
    cp "$SOURCE" "$TARGET"
    chmod +x "$TARGET"
    echo "✅ pre-commit hook 已安装到 .git/hooks/pre-commit"
    echo "   每次 git commit 前会自动运行："
    echo "     - Python 编译检查"
    echo "     - Harness 静态约束检查"
    echo "     - 单元测试"
    echo "     - 文件大小检查"
fi

# 验证安装
echo ""
echo "🔍 验证 hook 安装..."

if [ -x "$TARGET" ]; then
    echo "✅ pre-commit hook 可执行"
else
    echo "❌ pre-commit hook 不可执行，请检查权限"
fi

echo ""
echo "安装完成。当前已安装的 hooks："
ls -la "$HOOKS_DIR/" | grep -v '\.sample' | grep -v '^total' | grep -v '^d' || echo "  (无自定义 hooks)"

echo ""
echo "提示：可以手动运行一次验证："
echo "  bash scripts/pre-commit"
