"""从 state.py 自动生成 docs/reference/state-contracts.md。

用法：
    python scripts/generate_contract_docs.py          # 生成/更新文档
    python scripts/generate_contract_docs.py --check  # 仅检查文档是否过期（CI 用）

原理：
    读取 state.py 中所有的 FIELD_SPEC 字典，提取每个字段的类型、必填、用途，
    渲染为 markdown 表格。同时在文档头部嵌入内容哈希，供 CI 检测文档是否过期。
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATE_PY = PROJECT_ROOT / "src/insight_engine/harness/state.py"
OUTPUT_DOC = PROJECT_ROOT / "docs/reference/state-contracts.md"

# ============================================================================
# 解析 state.py
# ============================================================================


def extract_dict_source(source: str, var_name: str) -> str | None:
    """从 Python 源码中截取某个顶层 dict 变量的定义区间。"""
    pattern = rf"^{var_name}\s*=\s*\{{"
    match = re.search(pattern, source, re.MULTILINE)
    if not match:
        return None

    start = match.start()
    brace_count = 0
    in_dict = False
    end = start

    for i in range(start, len(source)):
        ch = source[i]
        if ch == "{":
            brace_count += 1
            in_dict = True
        elif ch == "}":
            brace_count -= 1
            if in_dict and brace_count == 0:
                end = i + 1
                break

    return source[start:end]


def parse_field_spec(source: str, var_name: str) -> dict[str, dict[str, str]]:
    """从源码中解析 FIELD_SPEC 变量，提取每个字段的合同信息。

    适用于形如:
        RAW_ITEM_FIELD_SPEC = {
            "source_id": {"type": "str", "required": True, "purpose": "数据源 ID。"},
            ...
        }
    """
    raw = extract_dict_source(source, var_name)
    if not raw:
        return {}

    # 提取赋值表达式右侧的 dict 字面量
    eq_idx = raw.index("=")
    dict_str = raw[eq_idx + 1:].strip()

    spec: dict[str, dict[str, str]] = {}
    entries = re.finditer(
        r'"([^"]+)"\s*:\s*\{([^}]+)\}',
        dict_str,
    )
    for m in entries:
        field_name = m.group(1)
        body = m.group(2)

        type_match = re.search(r'"type"\s*:\s*"([^"]*)"', body)
        required_match = re.search(r'"required"\s*:\s*(True|False)', body)
        purpose_match = re.search(r'"purpose"\s*:\s*"([^"]*)"', body)

        # 也有嵌套的 required_fields 如 ANALYSIS_RESULT_FIELD_SPEC
        required_fields_match = re.search(r'"required_fields"\s*:\s*\[([^\]]*)\]', body)

        entry: dict[str, str] = {
            "type": type_match.group(1) if type_match else "?",
            "required": "是" if (required_match and required_match.group(1) == "True") else "否",
            "purpose": purpose_match.group(1) if purpose_match else "?",
        }
        if required_fields_match:
            entry["required_fields"] = required_fields_match.group(1).strip()

        spec[field_name] = entry

    return spec


def extract_set_source(source: str, var_name: str) -> list[str] | None:
    """从 Python 源码中截取一个 set 变量并解析为字符串列表。"""
    pattern = rf"^{var_name}\s*=\s*\{{"
    match = re.search(pattern, source, re.MULTILINE)
    if not match:
        return None
    block = source[match.start():]
    brace_count = 0
    end = 0
    for i, ch in enumerate(block):
        if ch == "{":
            brace_count += 1
        elif ch == "}":
            brace_count -= 1
            if brace_count == 0:
                end = i + 1
                break
    body = block[:end]
    items = re.findall(r'"([^"]+)"', body)
    return items


def extract_list_source(source: str, var_name: str) -> list[str] | None:
    """从 Python 源码中截取一个 list 变量并解析为字符串列表。"""
    pattern = rf"^{var_name}\s*=\s*\["
    match = re.search(pattern, source, re.MULTILINE)
    if not match:
        return None
    block = source[match.start():]
    bracket_count = 0
    end = 0
    for i, ch in enumerate(block):
        if ch == "[":
            bracket_count += 1
        elif ch == "]":
            bracket_count -= 1
            if bracket_count == 0:
                end = i + 1
                break
    body = block[:end]
    items = re.findall(r'"([^"]+)"', body)
    return items


# ============================================================================
# 内容哈希（检测文档是否过期）
# ============================================================================


def compute_source_hash() -> str:
    """计算 FIELD_SPEC 源码的内容哈希。

    只对 FIELD_SPEC 和关键常量做哈希，不 hash 整个 state.py，
    这样 state.py 的 docstring 修改不会导致合同文档被判为过期。
    """
    source = STATE_PY.read_text(encoding="utf-8")

    var_names = [
        "RAW_ITEM_FIELD_SPEC",
        "CLEANED_ITEM_FIELD_SPEC",
        "STRUCTURED_EVENT_FIELD_SPEC",
        "ANALYSIS_RESULT_FIELD_SPEC",
        "REPORT_PATHS_FIELD_SPEC",
        "STRUCTURED_EVENT_AI_AREAS",
        "STRUCTURED_EVENT_GLOBAL_AREAS",
        "REQUIRED_TREND_KEYS",
        "ANALYSIS_EVENT_DISPLAY_REQUIRED_FIELDS",
        "REPORT_REQUIRED_HEADINGS",
    ]

    parts: list[str] = []
    for name in var_names:
        block = extract_dict_source(source, name)
        if block is None:
            block = extract_set_source(source, name) or []
            block = json.dumps(block, ensure_ascii=False)
        if block is None:
            block = extract_list_source(source, name) or []
            block = json.dumps(block, ensure_ascii=False)
        parts.append(f"{name}={block}")

    combined = "\n".join(parts)
    return hashlib.sha256(combined.encode()).hexdigest()[:16]


# ============================================================================
# 生成 Markdown
# ============================================================================


def build_markdown() -> str:
    source = STATE_PY.read_text(encoding="utf-8")

    contracts: list[tuple[str, str, dict[str, dict[str, str]]]] = [
        ("Stage 1 产出", "RAW_ITEM_FIELD_SPEC", parse_field_spec(source, "RAW_ITEM_FIELD_SPEC")),
        ("Stage 2 产出", "CLEANED_ITEM_FIELD_SPEC", parse_field_spec(source, "CLEANED_ITEM_FIELD_SPEC")),
        ("Stage 3 产出", "STRUCTURED_EVENT_FIELD_SPEC", parse_field_spec(source, "STRUCTURED_EVENT_FIELD_SPEC")),
        ("Stage 4 产出", "ANALYSIS_RESULT_FIELD_SPEC", parse_field_spec(source, "ANALYSIS_RESULT_FIELD_SPEC")),
        ("Stage 5 产出", "REPORT_PATHS_FIELD_SPEC", parse_field_spec(source, "REPORT_PATHS_FIELD_SPEC")),
    ]

    ai_areas = extract_set_source(source, "STRUCTURED_EVENT_AI_AREAS") or []
    global_areas = extract_set_source(source, "STRUCTURED_EVENT_GLOBAL_AREAS") or []
    trend_keys = extract_list_source(source, "REQUIRED_TREND_KEYS") or []
    display_fields = extract_list_source(source, "ANALYSIS_EVENT_DISPLAY_REQUIRED_FIELDS") or []
    required_headings = extract_list_source(source, "REPORT_REQUIRED_HEADINGS") or []

    content_hash = compute_source_hash()

    lines: list[str] = [
        "---",
        "last_updated: auto-generated",
        "status: active",
        "owner: coding-agent",
        "source: src/insight_engine/harness/state.py",
        f"content_hash: {content_hash}",
        "---",
        "",
        "# State 字段合同",
        "",
        "> 此文件由 `scripts/generate_contract_docs.py` 从 `state.py` 自动生成，勿手动编辑。",
        f"> 源码内容哈希：`{content_hash}`",
        "> 如果 CI 报告此文档过期，运行 `python scripts/generate_contract_docs.py` 重新生成。",
        "",
        "## 概述",
        "",
        "`InsightEngineState` 是一次日报生成任务的全局状态对象。它贯穿 5 个 Stage，",
        "每个 Stage 在上面写入产物字段，下游 Stage 按合同消费这些字段。",
        "",
        "5 个 Stage 分别产出 5 份字段合同：",
        "1. RAW_ITEM_FIELD_SPEC — Stage 1 采集的原始数据",
        "2. CLEANED_ITEM_FIELD_SPEC — Stage 2 清洗后的标准化数据",
        "3. STRUCTURED_EVENT_FIELD_SPEC — Stage 3 LLM 抽取的结构化事件",
        "4. ANALYSIS_RESULT_FIELD_SPEC — Stage 4 ReAct 分析结果",
        "5. REPORT_PATHS_FIELD_SPEC — Stage 5 生成的报告和图表路径",
        "",
        "数据流转方向：`raw_items → cleaned_items → structured_events → analysis_result → report_paths`",
        "",
        "## 两条数据线",
        "",
        "系统并行处理两条数据线：",
        "- **global** 线：全球背景事件（政治、经济、气候等），用于报告的「全球热点背景」章节。",
        "- **ai** 线：AI 行业事件（基础模型、AI 应用、政策、投资等），用于报告的主要分析内容。",
        "",
        "每条数据线各自经过 Stage 1-3 的处理，在 Stage 4 合并分析。",
        "State 中对应的字段对：",
        "`global_raw_items` / `ai_raw_items` → `global_cleaned_items` / `ai_cleaned_items` → `global_structured_events` / `ai_structured_events`",
        "",
        "---",
        "",
    ]

    for purpose, name, spec in contracts:
        lines.append(f"## {name}")
        lines.append("")
        lines.append(f"**对应 Stage：** {purpose}")
        lines.append("")
        if not spec:
            lines.append("> 解析失败，请检查 state.py 中的格式。")
            lines.append("")
            continue

        lines.append("| 字段名 | 类型 | 必填 | 用途 |")
        lines.append("|--------|------|------|------|")
        for field_name, info in spec.items():
            lines.append(
                f"| `{field_name}` | `{info['type']}` | {info['required']} | {info['purpose']} |"
            )
        lines.append("")
        lines.append(f"共 {len(spec)} 个字段。")
        lines.append("")

    # 枚举值
    lines.append("---")
    lines.append("")
    lines.append("## 枚举值和约束常量")
    lines.append("")

    lines.append("### STRUCTURED_EVENT_AI_AREAS（AI 事件行业领域）")
    lines.append("")
    for area in sorted(ai_areas):
        lines.append(f"- `{area}`")
    lines.append("")

    lines.append("### STRUCTURED_EVENT_GLOBAL_AREAS（全球事件领域）")
    lines.append("")
    for area in sorted(global_areas):
        lines.append(f"- `{area}`")
    lines.append("")

    lines.append("### REQUIRED_TREND_KEYS（趋势判断维度）")
    lines.append("")
    for key in trend_keys:
        lines.append(f"- `{key}`")
    lines.append("")

    lines.append("### ANALYSIS_EVENT_DISPLAY_REQUIRED_FIELDS（报告展示必选字段）")
    lines.append("")
    for field in display_fields:
        lines.append(f"- `{field}`")
    lines.append("")

    lines.append("### REPORT_REQUIRED_HEADINGS（报告必含章节标题）")
    lines.append("")
    for heading in required_headings:
        lines.append(f"- {heading}")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 合同的使用方式")
    lines.append("")
    lines.append("1. **Stage 负责生成**：每个 Stage handler 负责产出合同的字段值。")
    lines.append("2. **Linter 负责校验**：每个 Stage 的 linter 从 `state.py` import FIELD_SPEC，逐字段检查类型和必填。")
    lines.append("3. **下游按合同消费**：下游 Stage 假定上游产物的字段合同已通过校验，直接取值使用。")
    lines.append("4. **Graph 读 Linter 结果**：Graph 在 `fire_after` 后读取 `evaluate_linter` 返回的 `passed` 字段，决定继续、重试或失败。")
    lines.append("")

    return "\n".join(lines)


# ============================================================================
# 主入口
# ============================================================================


def main() -> int:
    check_mode = "--check" in sys.argv

    new_md = build_markdown()

    if check_mode:
        if not OUTPUT_DOC.exists():
            print("❌ docs/reference/state-contracts.md 不存在。")
            print("✅ FIX: 运行 python scripts/generate_contract_docs.py 生成。")
            return 1

        existing = OUTPUT_DOC.read_text(encoding="utf-8")
        new_hash = compute_source_hash()
        existing_hash_match = re.search(r"content_hash:\s*(\S+)", existing)
        existing_hash = existing_hash_match.group(1) if existing_hash_match else None

        if new_hash != existing_hash:
            print(
                f"❌ docs/reference/state-contracts.md 已过期。"
                f"（state.py 哈希={new_hash}，文档哈希={existing_hash}）"
            )
            print("✅ FIX: 运行 python scripts/generate_contract_docs.py 重新生成。")
            print("📖 See: src/insight_engine/harness/state.py 查看源码变更。")
            return 1

        print("✅ docs/reference/state-contracts.md 与 state.py 一致。")
        return 0

    OUTPUT_DOC.write_text(new_md, encoding="utf-8")
    print(f"✅ 已生成 {OUTPUT_DOC}")
    print(f"   共 5 份 FIELD_SPEC 合同 + 枚举常量")
    return 0


if __name__ == "__main__":
    sys.exit(main())
