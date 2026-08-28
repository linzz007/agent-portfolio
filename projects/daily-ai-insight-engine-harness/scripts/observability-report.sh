#!/usr/bin/env bash
# ============================================================================
# 项目可观测性报告
# ============================================================================
# 生成一份简洁的项目健康报告，Agent 可以在排错前先看这份报告。
# 包含：最近 git 活动、CI 状态概览、文件大小违规、文档状态。
#
# 用法：
#   bash scripts/observability-report.sh           # 终端输出
#   bash scripts/observability-report.sh --json    # JSON 输出（给 Agent 消费）
# ============================================================================

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

JSON_MODE=false
[ "${1:-}" = "--json" ] && JSON_MODE=true

# ------------------------------------------------------------------
# 1. Git 活动摘要
# ------------------------------------------------------------------
LAST_COMMIT=$(git log -1 --format="%h %s (%ar)" 2>/dev/null || echo "无 git 历史")
RECENT_COMMITS=$(git log --since="7 days ago" --format="%h %s (%ar)" 2>/dev/null | wc -l || echo "0")

# ------------------------------------------------------------------
# 2. 文件大小违规
# ------------------------------------------------------------------
MAX_LINES=300
OVERSIZED=""
OVERSIZED_COUNT=0
while IFS= read -r -d '' py_file; do
    LINES=$(wc -l < "$py_file")
    if [ "$LINES" -gt "$MAX_LINES" ]; then
        REL="${py_file#./}"
        OVERSIZED="${OVERSIZED}  ${REL} (${LINES}行)\n"
        OVERSIZED_COUNT=$((OVERSIZED_COUNT + 1))
    fi
done < <(find src -name '*.py' -print0 2>/dev/null)

# ------------------------------------------------------------------
# 3. 测试状态
# ------------------------------------------------------------------
TEST_OUTPUT=$(python3 -m pytest -q 2>&1 || true)
TEST_PASSED=$(echo "$TEST_OUTPUT" | grep -oP '\d+(?= passed)' || echo "0")
TEST_FAILED=$(echo "$TEST_OUTPUT" | grep -oP '\d+(?= failed)' || echo "0")

# ------------------------------------------------------------------
# 4. Harness Linter 状态
# ------------------------------------------------------------------
LINT_OUTPUT=$(python3 scripts/harness_linter.py --json 2>&1 || true)
LINT_PASSED=$(echo "$LINT_OUTPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print('yes' if d['passed'] else 'no')" 2>/dev/null || echo "unknown")
LINT_ISSUES=$(echo "$LINT_OUTPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['total_issues'])" 2>/dev/null || echo "?")

# ------------------------------------------------------------------
# 5. 文档新鲜度
# ------------------------------------------------------------------
DOC_OUTPUT=$(bash scripts/check-doc-freshness.sh --json 2>&1 || true)
DOC_STALE=$(echo "$DOC_OUTPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['stale_count'])" 2>/dev/null || echo "?")

# ------------------------------------------------------------------
# 输出
# ------------------------------------------------------------------

if $JSON_MODE; then
    cat <<EOF
{
  "report_time": "$(date -Iseconds)",
  "git": {
    "last_commit": "$LAST_COMMIT",
    "commits_7d": $RECENT_COMMITS
  },
  "code_health": {
    "oversized_files": $OVERSIZED_COUNT,
    "oversized_list": $(echo -e "$OVERSIZED" | grep -v '^$' | jq -R . | jq -s . 2>/dev/null || echo '[]')
  },
  "tests": {
    "passed": "$TEST_PASSED",
    "failed": "$TEST_FAILED"
  },
  "harness_linter": {
    "passed": "$LINT_PASSED",
    "issues": "$LINT_ISSUES"
  },
  "docs": {
    "stale_count": $DOC_STALE
  }
}
EOF
else
    echo "============================================"
    echo "  Daily AI Insight Engine — 健康报告"
    echo "  $(date)"
    echo "============================================"
    echo ""
    echo "── Git ──"
    echo "  最近提交: $LAST_COMMIT"
    echo "  7 天内提交: $RECENT_COMMITS 次"
    echo ""
    echo "── 代码健康 ──"
    echo "  超大文件 (>${MAX_LINES}行): ${OVERSIZED_COUNT} 个"
    if [ "$OVERSIZED_COUNT" -gt 0 ]; then
        echo -e "$OVERSIZED"
    fi
    echo ""
    echo "── 测试 ──"
    echo "  通过: $TEST_PASSED  |  失败: $TEST_FAILED"
    echo ""
    echo "── Harness Linter ──"
    echo "  状态: $LINT_PASSED  |  问题: $LINT_ISSUES"
    echo ""
    echo "── 文档 ──"
    echo "  过期文档: $DOC_STALE 个"
    echo ""
    echo "============================================"
fi
