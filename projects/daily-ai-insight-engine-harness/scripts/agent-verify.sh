#!/usr/bin/env bash
# ============================================================================
# Agent 隔离验证脚本
# ============================================================================
# 这个脚本用于在隔离的 git worktree 中验证 Agent 的代码修改。
#
# 工作流程：
#   1. 为当前分支创建隔离的 git worktree
#   2. 在 worktree 中安装依赖、运行所有检查
#   3. 全部通过 → 允许合并回主分支
#   4. 检查失败 → 保留 worktree 供排查，Agent 根据报错自行修复
#
# 用法：
#   bash scripts/agent-verify.sh                    # 验证当前分支
#   bash scripts/agent-verify.sh feature-branch     # 验证指定分支
#   bash scripts/agent-verify.sh --keep             # 验证后保留 worktree（用于排查）
#
# 这是 Harness Engineering Phase 3 的核心组件：
# Agent 在隔离环境中验证自己的修改，不污染主工作区。
# ============================================================================

set -euo pipefail

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KEEP_WORKTREE=false
BRANCH=""

# 解析参数
for arg in "$@"; do
    case "$arg" in
        --keep|-k)
            KEEP_WORKTREE=true
            ;;
        *)
            if [ -z "$BRANCH" ]; then
                BRANCH="$arg"
            fi
            ;;
    esac
done

# 确定要验证的分支
if [ -z "$BRANCH" ]; then
    BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
    if [ -z "$BRANCH" ]; then
        echo -e "${RED}❌ 无法确定当前分支，请指定分支名${NC}"
        exit 1
    fi
fi

echo -e "${CYAN}=====================================================${NC}"
echo -e "${CYAN}  Agent 隔离验证${NC}"
echo -e "${CYAN}=====================================================${NC}"
echo -e "  分支: ${YELLOW}${BRANCH}${NC}"
echo ""

# ------------------------------------------------------------------
# Step 1: 检查是否有未提交的修改
# ------------------------------------------------------------------

echo -e "${CYAN}━━━ Step 1/6: 检查工作区状态 ━━━${NC}"

if ! git diff --quiet 2>/dev/null; then
    echo -e "${YELLOW}⚠️  有未暂存的修改。建议先 commit 或 stash。${NC}"
    echo -e "${YELLOW}   如需继续，这些修改不会被包含在 worktree 验证中。${NC}"
fi

if ! git diff --cached --quiet 2>/dev/null; then
    echo -e "${YELLOW}⚠️  有已暂存但未提交的修改。建议先 commit。${NC}"
fi

echo -e "${GREEN}✅ 工作区状态检查完成${NC}"
echo ""

# ------------------------------------------------------------------
# Step 2: 创建隔离 worktree
# ------------------------------------------------------------------

echo -e "${CYAN}━━━ Step 2/6: 创建隔离 worktree ━━━${NC}"

WORKTREE_DIR="/tmp/agent-verify-$(date +%Y%m%d-%H%M%S)"
WORKTREE_NAME="agent-verify-$(date +%s)"

echo "  创建 worktree: ${WORKTREE_DIR}"

if ! git worktree add "$WORKTREE_DIR" "$BRANCH" 2>&1; then
    echo ""
    echo -e "${RED}❌ 无法创建 git worktree。${NC}"
    echo -e "${GREEN}✅ FIX: 确认分支 '${BRANCH}' 存在，且没有同名 worktree。${NC}"
    echo -e "${GREEN}   查看已有 worktree: git worktree list${NC}"
    echo -e "${CYAN}📖 See: docs/HARNESS_ENGINEERING_GUIDE.md 了解 worktree 隔离验证${NC}"
    exit 1
fi

echo -e "${GREEN}✅ Worktree 创建成功${NC}"
echo ""

# ------------------------------------------------------------------
# Step 3: 编译检查
# ------------------------------------------------------------------

echo -e "${CYAN}━━━ Step 3/6: Python 编译检查 ━━━${NC}"

if python3 -m compileall -q "$WORKTREE_DIR/src" "$WORKTREE_DIR/scripts" "$WORKTREE_DIR/tests" 2>&1; then
    echo -e "${GREEN}✅ 编译检查通过${NC}"
else
    echo ""
    echo -e "${RED}❌ 编译检查失败：有 Python 文件存在语法错误。${NC}"
    echo -e "${GREEN}✅ FIX: 检查上面列出的文件，修复语法问题。${NC}"
    _cleanup_on_failure "$WORKTREE_DIR"
    exit 1
fi
echo ""

# ------------------------------------------------------------------
# Step 4: Harness Linter
# ------------------------------------------------------------------

echo -e "${CYAN}━━━ Step 4/6: Harness 静态约束检查 ━━━${NC}"

if python3 "$WORKTREE_DIR/scripts/harness_linter.py" 2>&1; then
    echo -e "${GREEN}✅ Harness 检查通过${NC}"
else
    echo ""
    echo -e "${RED}❌ Harness 静态检查未通过。${NC}"
    echo -e "${GREEN}✅ FIX: 每条报错都包含具体修复步骤（✅FIX）和参考文档（📖See）。${NC}"
    echo -e "${GREEN}   AI 编程助手可以据此自动修复这些问题。${NC}"
    _cleanup_on_failure "$WORKTREE_DIR"
    exit 1
fi
echo ""

# ------------------------------------------------------------------
# Step 5: 单元测试
# ------------------------------------------------------------------

echo -e "${CYAN}━━━ Step 5/6: 单元测试 ━━━${NC}"

if python3 -m pytest "$WORKTREE_DIR/tests" -q 2>&1; then
    echo -e "${GREEN}✅ 所有测试通过${NC}"
else
    echo ""
    echo -e "${RED}❌ 单元测试未通过。${NC}"
    echo -e "${GREEN}✅ FIX: 检查上面的 pytest 输出，定位失败的测试，修复后重新验证。${NC}"
    echo -e "${CYAN}📖 See: docs/conventions/testing.md 了解测试规范${NC}"
    _cleanup_on_failure "$WORKTREE_DIR"
    exit 1
fi
echo ""

# ------------------------------------------------------------------
# Step 6: 文件大小检查
# ------------------------------------------------------------------

echo -e "${CYAN}━━━ Step 6/6: 文件大小检查 ━━━${NC}"

MAX_LINES=300
OVER_SIZE=0

while IFS= read -r -d '' py_file; do
    LINES=$(wc -l < "$py_file")
    if [ "$LINES" -gt "$MAX_LINES" ]; then
        REL="${py_file#$WORKTREE_DIR/}"
        if [ $OVER_SIZE -eq 0 ]; then
            echo ""
        fi
        echo -e "${RED}❌ ${REL} 有 ${LINES} 行（上限 ${MAX_LINES}）${NC}"
        echo -e "${GREEN}✅ FIX: 拆分为更小的模块。${NC}"
        OVER_SIZE=$((OVER_SIZE + 1))
    fi
done < <(find "$WORKTREE_DIR/src" -name '*.py' -print0 2>/dev/null)

if [ $OVER_SIZE -eq 0 ]; then
    echo -e "${GREEN}✅ 所有文件在行数限制内${NC}"
else
    _cleanup_on_failure "$WORKTREE_DIR"
    exit 1
fi

# ------------------------------------------------------------------
# 全部通过
# ------------------------------------------------------------------

echo ""
echo -e "${GREEN}=====================================================${NC}"
echo -e "${GREEN}  ✅ 所有验证通过！${NC}"
echo -e "${GREEN}=====================================================${NC}"
echo ""

if $KEEP_WORKTREE; then
    echo -e "${YELLOW}Worktree 已保留在: ${WORKTREE_DIR}${NC}"
    echo -e "${YELLOW}手动删除: git worktree remove ${WORKTREE_DIR}${NC}"
else
    echo "清理 worktree..."
    cd "$PROJECT_ROOT"
    git worktree remove "$WORKTREE_DIR" 2>&1 || true
    echo -e "${GREEN}✅ Worktree 已清理${NC}"
fi

echo ""
echo -e "${GREEN}可以安全地合并或推送当前分支。${NC}"
exit 0

# ------------------------------------------------------------------
# 失败时的清理逻辑
# ------------------------------------------------------------------

_cleanup_on_failure() {
    local wt_dir="$1"
    echo ""
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    if $KEEP_WORKTREE; then
        echo -e "${YELLOW}Worktree 已保留在: ${wt_dir}${NC}"
        echo -e "${YELLOW}排查完成后手动删除:${NC}"
        echo -e "${YELLOW}  cd $(pwd)${NC}"
        echo -e "${YELLOW}  git worktree remove ${wt_dir}${NC}"
    else
        echo -e "${YELLOW}正在清理 worktree...${NC}"
        cd "$PROJECT_ROOT"
        git worktree remove "$wt_dir" --force 2>/dev/null || true
    fi
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "${RED}⛔ 验证失败。请根据上面的 ✅FIX 和 📖See 提示修复后重试。${NC}"
    echo ""
}
