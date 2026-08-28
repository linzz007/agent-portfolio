#!/usr/bin/env bash
# ============================================================================
# Agent 护栏 —— Coding-Agent 工作流入口
# ============================================================================
# 这是 AI 编程助手修改代码后应该运行的一键检查脚本。
# 它按顺序运行所有质量门禁，任何一步失败都会阻止后续步骤。
#
# 用法：
#   bash scripts/agent-guardrails.sh           # 运行全部检查
#   bash scripts/agent-guardrails.sh --quick   # 快速模式（跳过测试）
#   bash scripts/agent-guardrails.sh --json    # JSON 格式输出（给 Agent 消费）
#
# 在 Agent 工作流中的位置：
#   Agent 读取任务 → 分析代码 → 修改代码 → 【运行本脚本】→ 修复问题 → Commit
# ============================================================================

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

QUICK_MODE=false
JSON_MODE=false

for arg in "$@"; do
    case "$arg" in
        --quick|-q) QUICK_MODE=true ;;
        --json|-j)  JSON_MODE=true ;;
    esac
done

# 汇总结构
SUMMARY='{"gates": []}'

# ------------------------------------------------------------------
# Gate 1: Python 编译检查
# ------------------------------------------------------------------

if $JSON_MODE; then
    if python3 -m compileall -q src scripts tests 2>&1; then
        :
    else
        echo '{"gate":"compileall","status":"fail","fix":"检查 Python 语法错误，修复上面列出的文件","doc":"src/ 目录下的 Python 文件"}'
        exit 1
    fi
else
    echo "🔍 Gate 1/4: Python 编译检查..."
    python3 -m compileall -q src scripts tests 2>&1 && echo "   ✅ 通过" || {
        echo "   ❌ 编译失败"
        echo "   ✅ FIX: 修复上面列出的 Python 语法错误"
        exit 1
    }
fi

# ------------------------------------------------------------------
# Gate 2: Harness Linter
# ------------------------------------------------------------------

if $JSON_MODE; then
    python3 scripts/harness_linter.py --json 2>&1 || exit 1
else
    echo "🔍 Gate 2/4: Harness 静态约束检查..."
    python3 scripts/harness_linter.py 2>&1 || exit 1
fi

# ------------------------------------------------------------------
# Gate 3: 单元测试（快速模式跳过）
# ------------------------------------------------------------------

if $QUICK_MODE; then
    if ! $JSON_MODE; then
        echo "⏭  Gate 3/4: 单元测试（--quick 模式跳过）"
    fi
else
    if $JSON_MODE; then
        if ! python3 -m pytest -q 2>&1; then
            echo '{"gate":"pytest","status":"fail","fix":"检查上面的 pytest 输出，修复失败的测试","doc":"docs/conventions/testing.md"}'
            exit 1
        fi
    else
        echo "🔍 Gate 3/4: 单元测试..."
        python3 -m pytest -q 2>&1 && echo "   ✅ 通过" || {
            echo "   ❌ 测试失败"
            echo "   ✅ FIX: 根据 pytest 输出修复失败测试"
            echo "   📖 See: docs/conventions/testing.md"
            exit 1
        }
    fi
fi

# ------------------------------------------------------------------
# Gate 4: 文件大小检查
# ------------------------------------------------------------------

MAX_LINES=300
OVER_SIZE=0
OVERSIZED_FILES=""

while IFS= read -r -d '' py_file; do
    LINES=$(wc -l < "$py_file")
    if [ "$LINES" -gt "$MAX_LINES" ]; then
        REL="${py_file#$PROJECT_ROOT/}"
        OVER_SIZE=$((OVER_SIZE + 1))
        OVERSIZED_FILES="${OVERSIZED_FILES}${REL} (${LINES}行)\n"
    fi
done < <(find src -name '*.py' -print0 2>/dev/null)

if [ $OVER_SIZE -gt 0 ]; then
    if $JSON_MODE; then
        echo "{\"gate\":\"file_size\",\"status\":\"fail\",\"fix\":\"将超大文件拆分为更小的模块（≤${MAX_LINES}行）\",\"oversized\":$(echo -e "$OVERSIZED_FILES" | head -5)}"
    else
        echo "🔍 Gate 4/4: 文件大小检查..."
        echo "   ❌ ${OVER_SIZE} 个文件超过 ${MAX_LINES} 行："
        echo -e "$OVERSIZED_FILES"
        echo "   ✅ FIX: 拆分为更小的模块，辅助函数移到 utils/"
        echo "   📖 See: docs/conventions/naming.md"
    fi
    exit 1
fi

if ! $JSON_MODE; then
    echo "🔍 Gate 4/4: 文件大小检查..."
    echo "   ✅ 通过"
fi

# ------------------------------------------------------------------
# 全部通过
# ------------------------------------------------------------------

if $JSON_MODE; then
    echo '{"status":"all_passed","message":"所有护栏检查通过，可以安全提交"}'
else
    echo ""
    echo "✅ 所有护栏检查通过！代码可以安全提交。"
fi
