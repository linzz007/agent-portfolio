"""Stage 2: 数据清洗。

这个阶段是确定性流程，不是 Agent、Tool 或 Skill。
它负责把第一阶段的 raw item 转成后续稳定可消费的 cleaned item。

清洗阶段不静默删除数据：

- global 数据用于观察世界热点，非 AI 内容也保留并参与 global 分析候选。
- ai 数据用于观察 AI 专业热点，只有 AI 相关且质量合格的数据进入 AI 分析候选。
- 重复、过旧、缺字段的数据仍保留，但用标签说明为什么不进入重点分析。


把 global_raw_items 和 ai_raw_items 分别转成 global_cleaned_items / ai_cleaned_items。
清理 HTML、压缩空白、规范 URL、解析发布时间。
判断是否最近 2 天内：is_recent。
判断是否 AI 相关：is_ai_related、ai_match_keywords。
粗分类领域：domain，比如 ai / politics / business / climate / culture / security / other。
生成 topic_tags。
做简单质量评分：quality_score、quality_reasons。
检查是否重复：is_duplicate、duplicate_of。
不删除数据，只打标签：
analysis_exclusion_reasons
drop_reason
should_analyze_global
should_analyze_ai
"""

from __future__ import annotations

import html
import re
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from insight_engine.harness.artifacts import write_json_artifact
from insight_engine.harness.state import InsightEngineState


AI_KEYWORDS = [
    "AI",
    "A.I.",
    "artificial intelligence",
    "generative AI",
    "machine learning",
    "deep learning",
    "LLM",
    "large language model",
    "foundation model",
    "OpenAI",
    "ChatGPT",
    "Claude",
    "Anthropic",
    "Gemini",
    "DeepMind",
    "Llama",
    "DeepSeek",
    "Qwen",
    "agent",
    "AI agent",
    "RAG",
    "multimodal",
    "NVIDIA",
    "GPU",
    "AI regulation",
    "AI safety",
    "AGI",
]

DOMAIN_KEYWORDS = {
    "ai": AI_KEYWORDS,
    "politics": [
        "trump",
        "election",
        "government",
        "senate",
        "congress",
        "supreme court",
        "policy",
        "war",
        "iran",
        "white house",
        "minister",
        "president",
    ],
    "business": [
        "market",
        "stock",
        "funding",
        "raises",
        "valuation",
        "startup",
        "acquisition",
        "invest",
        "economy",
        "prices",
        "revenue",
    ],
    "tech": [
        "software",
        "app",
        "data",
        "cyber",
        "security breach",
        "platform",
        "chip",
        "device",
        "robot",
        "internet",
    ],
    "health": [
        "health",
        "disease",
        "ebola",
        "hospital",
        "medicine",
        "doctor",
        "sleep",
        "lung",
        "diet",
    ],
    "climate": [
        "climate",
        "wildfire",
        "storm",
        "emissions",
        "energy",
        "weather",
        "carbon",
    ],
    "culture": [
        "music",
        "film",
        "show",
        "celebrity",
        "sports",
        "festival",
        "university",
        "commencement",
    ],
    "security": [
        "shooting",
        "attack",
        "police",
        "custody",
        "fraud",
        "lawsuit",
        "trial",
        "crime",
    ],
}

KNOWN_SOURCES = {
    "global_npr_top",
    "google_news_top",
    "ai_techcrunch",
    "ai_the_verge",
    "hacker_news_ai",
    "arxiv",
    "openai_news",
    "github_ai_releases",
}


def clean_items(state: InsightEngineState) -> InsightEngineState:
    """执行数据清洗阶段。"""
    target = _parse_target_date(state.target_date)
    global_cleaned_items = clean_raw_items(
        raw_items=state.global_raw_items,
        scope="global",
        target_date=target,
    )
    ai_cleaned_items = clean_raw_items(
        raw_items=state.ai_raw_items,
        scope="ai",
        target_date=target,
    )

    state.global_cleaned_items = global_cleaned_items
    state.ai_cleaned_items = ai_cleaned_items

    all_cleaned_items = global_cleaned_items + ai_cleaned_items
    output_path = write_json_artifact(
        state=state,
        artifact_name="cleaned_items",
        data={
            "run_id": state.run_id,
            "target_date": state.target_date,
            "input_counts": {
                "global_raw_items": len(state.global_raw_items),
                "ai_raw_items": len(state.ai_raw_items),
                "total_raw_items": len(state.global_raw_items) + len(state.ai_raw_items),
            },
            "output_counts": {
                "global_cleaned_items": len(global_cleaned_items),
                "ai_cleaned_items": len(ai_cleaned_items),
                "total_cleaned_items": len(all_cleaned_items),
                "global_analyzable_items": sum(
                    1 for item in global_cleaned_items if item.get("should_analyze_global")
                ),
                "ai_analyzable_items": sum(
                    1 for item in ai_cleaned_items if item.get("should_analyze_ai")
                ),
                "duplicates": sum(1 for item in all_cleaned_items if item.get("is_duplicate")),
            },
            "domain_stats": _count_by_field(all_cleaned_items, "domain"),
            "global_items": global_cleaned_items,
            "ai_items": ai_cleaned_items,
            "items": all_cleaned_items,
        },
        base_dir="data/processed",
        filename="cleaned_items.json",
    )
    state.add_artifact("global_cleaned_items", str(output_path))
    state.add_artifact("ai_cleaned_items", str(output_path))

    return state


def clean_raw_items(
    raw_items: list[dict[str, Any]],
    scope: str,
    target_date: date,
) -> list[dict[str, Any]]:
    """批量清洗 raw items，逐条调用 clean_raw_item 并做去重标记。"""
    cleaned_items: list[dict[str, Any]] = []
    seen_keys: dict[str, str] = {}

    for index, item in enumerate(raw_items):
        cleaned = clean_raw_item(
            item=item,
            index=index,
            scope=scope,
            target_date=target_date,
        )
        dedupe_key = _dedupe_key(cleaned)
        duplicate_of = seen_keys.get(dedupe_key)
        if duplicate_of:
            cleaned["is_duplicate"] = True
            cleaned["duplicate_of"] = duplicate_of
            cleaned["analysis_exclusion_reasons"].append("duplicate")
        else:
            seen_keys[dedupe_key] = str(cleaned["id"])

        _apply_analysis_flags(cleaned)
        cleaned_items.append(cleaned)

    return cleaned_items


def clean_raw_item(
    item: dict[str, Any],
    index: int,
    scope: str,
    target_date: date,
) -> dict[str, Any]:
    """清洗单条 raw item：标准化字段、分类领域、打分、标记可分析性。"""
    source_scope = str(item.get("source_scope") or scope)
    title = normalize_text(str(item.get("title", "")))
    summary = normalize_text(str(item.get("summary", "")))
    raw_content = normalize_text(str(item.get("raw_content", "")))
    url = normalize_url(str(item.get("url", "")))
    published_at_raw = normalize_text(str(item.get("published_at", "")))
    published_dt = parse_datetime(published_at_raw)
    published_at = published_dt.isoformat() if published_dt else published_at_raw
    published_date = published_dt.date().isoformat() if published_dt else None
    recency_days = _recency_days(published_dt, target_date)
    is_recent = recency_days is not None and 0 <= recency_days <= 2

    clean_text = _truncate(summary or raw_content or title, 1800)
    combined_text = f"{title} {summary} {raw_content}"
    ai_match_keywords = match_keywords(combined_text, AI_KEYWORDS)
    is_ai_related = bool(ai_match_keywords)
    domain = classify_domain(combined_text, ai_match_keywords)
    topic_tags = build_topic_tags(combined_text, domain, ai_match_keywords)
    quality_score, quality_reasons = score_quality(
        item=item,
        title=title,
        url=url,
        summary=summary,
        is_recent=is_recent,
        is_ai_related=is_ai_related,
        source_scope=source_scope,
    )

    exclusion_reasons: list[str] = []
    if not title:
        exclusion_reasons.append("missing_title")
    if not url:
        exclusion_reasons.append("missing_url")
    if not is_recent:
        exclusion_reasons.append("not_recent")
    if source_scope == "ai" and not is_ai_related:
        exclusion_reasons.append("not_ai_related")
    if quality_score < 0.45:
        exclusion_reasons.append("low_quality")

    return {
        "id": f"clean_{source_scope}_{index + 1}",
        "source_id": str(item.get("source_id", "unknown")),
        "source_scope": source_scope,
        "source_type": str(item.get("source_type", "unknown")),
        "title": title,
        "url": url,
        "published_at_raw": published_at_raw,
        "published_at": published_at,
        "published_date": published_date,
        "recency_days": recency_days,
        "is_recent": is_recent,
        "author_or_org": normalize_text(str(item.get("author_or_org", ""))),
        "summary": _truncate(summary or raw_content or title, 1200),
        "raw_content": _truncate(raw_content or summary or title, 4000),
        "clean_text": clean_text,
        "retrieved_at": str(item.get("retrieved_at", "")),
        "language_hint": detect_language_hint(combined_text),
        "content_length": len(clean_text),
        "domain": domain,
        "topic_tags": topic_tags,
        "is_ai_related": is_ai_related,
        "ai_match_keywords": ai_match_keywords,
        "is_duplicate": False,
        "duplicate_of": None,
        "quality_score": quality_score,
        "quality_reasons": quality_reasons,
        "should_analyze_global": False,
        "should_analyze_ai": False,
        "should_analyze": False,
        "analysis_exclusion_reasons": exclusion_reasons,
        "drop_reason": None,
        "metadata": item.get("metadata", {}),
        "raw_ref": f"{source_scope}_raw_items[{index}]",
    }


def normalize_text(text: str) -> str:
    """去掉 HTML 标签、反转义 HTML 实体、压缩多余空白。"""
    unescaped = html.unescape(text)
    without_tags = re.sub(r"<[^>]+>", " ", unescaped)
    return " ".join(without_tags.split())


def normalize_url(url: str) -> str:
    """对 URL 做最小规范化，当前仅 strip 首尾空白。"""
    return url.strip()


def parse_datetime(value: str) -> datetime | None:
    """解析 RSS/Atom/API 常见时间格式。"""
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def match_keywords(text: str, keywords: list[str]) -> list[str]:
    """在文本中匹配关键词列表，返回所有命中的关键词，短词做词边界匹配。"""
    lowered = text.lower()
    matched: list[str] = []

    for keyword in keywords:
        normalized = keyword.lower()
        if _should_match_word_boundary(normalized):
            if re.search(rf"\b{re.escape(normalized)}s?\b", lowered):
                matched.append(keyword)
            continue
        if normalized in lowered:
            matched.append(keyword)

    return sorted(set(matched), key=str.lower)


def classify_domain(text: str, ai_match_keywords: list[str]) -> str:
    """粗分类新闻所属领域：命中 AI 关键词时归 ai，否则按 DOMAIN_KEYWORDS 匹配。"""
    if ai_match_keywords:
        return "ai"

    scores = {
        domain: len(match_keywords(text, keywords))
        for domain, keywords in DOMAIN_KEYWORDS.items()
        if domain != "ai"
    }
    best_domain, best_score = max(scores.items(), key=lambda item: item[1])
    if best_score > 0:
        return best_domain
    return "other"


def build_topic_tags(text: str, domain: str, ai_match_keywords: list[str]) -> list[str]:
    """基于领域、AI 关键词和跨领域关键词生成 topic tags。"""
    tags = [domain]
    tags.extend(_normalize_tag(keyword) for keyword in ai_match_keywords[:8])

    for current_domain, keywords in DOMAIN_KEYWORDS.items():
        if current_domain == "ai":
            continue
        for keyword in match_keywords(text, keywords)[:4]:
            tags.append(_normalize_tag(keyword))

    return sorted(set(tag for tag in tags if tag))[:12]


def score_quality(
    item: dict[str, Any],
    title: str,
    url: str,
    summary: str,
    is_recent: bool,
    is_ai_related: bool,
    source_scope: str,
) -> tuple[float, list[str]]:
    """基于标题/URL/摘要/时效/AI相关性等维度计算质量评分和原因列表。"""
    score = 0.0
    reasons: list[str] = []
    source_id = str(item.get("source_id", ""))

    if title:
        score += 0.2
        reasons.append("has_title")
    if url:
        score += 0.2
        reasons.append("has_url")
    if summary:
        score += 0.15
        reasons.append("has_summary")
    if is_recent:
        score += 0.2
        reasons.append("recent")
    if source_id in KNOWN_SOURCES:
        score += 0.1
        reasons.append("known_source")
    if source_scope == "ai" and is_ai_related:
        score += 0.15
        reasons.append("ai_related")

    return round(min(score, 1.0), 2), reasons


def detect_language_hint(text: str) -> str:
    """根据中文字符与 ASCII 字母比例粗略判断文本语言为 zh/en/unknown。"""
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    ascii_letters = len(re.findall(r"[A-Za-z]", text))

    if chinese_chars and chinese_chars >= ascii_letters * 0.2:
        return "zh"
    if ascii_letters:
        return "en"
    return "unknown"


def _apply_analysis_flags(item: dict[str, Any]) -> None:
    """根据排除原因和 scope 决定 should_analyze_global / should_analyze_ai 标志。"""
    source_scope = str(item.get("source_scope", ""))
    exclusion_reasons = list(item.get("analysis_exclusion_reasons") or [])
    is_valid = not exclusion_reasons

    item["should_analyze_global"] = source_scope == "global" and is_valid
    item["should_analyze_ai"] = source_scope == "ai" and is_valid
    item["should_analyze"] = item["should_analyze_ai"] if source_scope == "ai" else item["should_analyze_global"]
    item["drop_reason"] = ", ".join(exclusion_reasons) if exclusion_reasons else None


def _dedupe_key(item: dict[str, Any]) -> str:
    """生成去重键：优先用 URL，无 URL 时用标题。"""
    url = str(item.get("url", "")).lower()
    if url:
        return f"url:{url}"
    title = str(item.get("title", "")).lower()
    return f"title:{title}"


def _recency_days(published_dt: datetime | None, target_date: date) -> int | None:
    """计算发布时间与目标日期的相差天数，解析失败返回 None。"""
    if published_dt is None:
        return None
    return (target_date - published_dt.date()).days


def _parse_target_date(value: str) -> date:
    """把 State 中的 target_date 字符串解析为 date，失败时返回今天。"""
    try:
        return date.fromisoformat(value)
    except ValueError:
        return date.today()


def _should_match_word_boundary(keyword: str) -> bool:
    """判断关键词是否太短（≤4个纯字母数字），需要做词边界匹配以防误命中。"""
    compact = keyword.replace(".", "")
    return compact.isalnum() and len(compact) <= 4


def _normalize_tag(value: str) -> str:
    """把标签文本转为小写并替换非字母数字字符为下划线。"""
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _truncate(text: str, limit: int) -> str:
    """按字符数截断文本，超出限制时末尾加 ..."""
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _count_by_field(items: list[dict[str, Any]], field: str) -> dict[str, int]:
    """按指定字段统计值的分布，用于生成 artifact 中的 domain_stats。"""
    counts: dict[str, int] = {}
    for item in items:
        key = str(item.get(field) or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))
