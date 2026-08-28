#!/usr/bin/env bash
# ============================================================================
# 文档新鲜度检查
# ============================================================================
# 扫描 docs/design/ 下的设计文档，检查是否超过指定天数未更新。
# 设计文档有 status 标记（active/draft/deprecated），
# draft 超过 60 天未更新说明可能已废弃，需要人工确认。
#
# 用法：
#   bash scripts/check-doc-freshness.sh           # 默认 60 天
#   bash scripts/check-doc-freshness.sh 30        # 自定义天数
#   bash scripts/check-doc-freshness.sh --json    # JSON 输出
# ============================================================================

set -euo pipefail

DAYS=${1:-60}
JSON_MODE=false

for arg in "$@"; do
    case "$arg" in
        --json|-j) JSON_MODE=true ;;
        [0-9]*)    DAYS="$arg" ;;
    esac
done

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

STALE_FILES=()
WARNINGS=()

# 扫描 docs/ 下所有 .md 文件
while IFS= read -r -d '' doc_file; do
    if ! git log -1 --format="%ct" "$doc_file" &>/dev/null; then
        # 文件未被 git 跟踪，跳过
        continue
    fi

    LAST_MOD=$(git log -1 --format="%ct" "$doc_file" 2>/dev/null || echo "0")
    NOW=$(date +%s)
    DAYS_OLD=$(( (NOW - LAST_MOD) / 86400 ))

    if [ "$DAYS_OLD" -gt "$DAYS" ]; then
        # 检查文件的 status 字段
        STATUS=$(head -20 "$doc_file" | grep "status:" | sed 's/.*status:\s*//' | tr -d ' "')
        REL="${doc_file#./}"

        if [ "$STATUS" = "draft" ]; then
            STALE_FILES+=("$REL")
            WARNINGS+=("$REL: ${DAYS_OLD} 天未更新 (status=draft)")
        elif [ "$STATUS" = "active" ] && [ "$DAYS_OLD" -gt "$((DAYS * 2))" ]; then
            WARNINGS+=("$REL: ${DAYS_OLD} 天未更新 (status=active, 超过 ${DAYS} 天的 2 倍)")
        fi
    fi
done < <(find docs -name '*.md' -print0 2>/dev/null)

if $JSON_MODE; then
    echo "{"
    echo "  \"check_date\": \"$(date -I)\","
    echo "  \"max_age_days\": $DAYS,"
    echo "  \"stale_count\": ${#STALE_FILES[@]},"
    echo "  \"warning_count\": ${#WARNINGS[@]},"
    echo "  \"stale_files\": $(printf '%s\n' "${STALE_FILES[@]}" | jq -R . | jq -s . || echo '[]'),"
    echo "  \"warnings\": $(printf '%s\n' "${WARNINGS[@]}" | jq -R . | jq -s . || echo '[]')"
    echo "}"
else
    echo "📄 文档新鲜度检查（阈值: ${DAYS} 天）"
    echo ""

    if [ ${#WARNINGS[@]} -eq 0 ]; then
        echo "✅ 所有文档在 ${DAYS} 天内更新过"
    else
        echo "⚠️  以下文档可能已过期："
        echo ""
        for warn in "${WARNINGS[@]}"; do
            echo "  ${warn}"
        done
        echo ""
        echo "💡 提示："
        echo "  - draft 超过 ${DAYS} 天未更新 → 考虑标记为 deprecated 或删除"
        echo "  - active 超过 $((DAYS * 2)) 天未更新 → 确认是否仍然准确"
        echo "  - Agent 可以定期扫描并生成清理 PR"
    fi
fi
