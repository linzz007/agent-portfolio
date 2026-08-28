"""Stage 1: 数据获取。

这个阶段是确定性流程，不是 Agent、Tool 或 Skill。
它只做三件事：

1. 读取数据源配置
2. 调用外部 RSS/API 抓取原始数据
3. 按 global / ai 两条数据线写入 State，并保存 raw artifact

放在一个文件里是为了让第一阶段的执行边界清晰：
Graph 只调用 `collect_raw_items(state)`，本文件内的其他函数都是该 stage 的内部实现细节。
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from insight_engine.harness.artifacts import write_json_artifact
from insight_engine.harness.state import InsightEngineState


USER_AGENT = "DailyAIInsightEngine/0.1 (learning harness project)"


@dataclass(frozen=True)
class SourceConfig:
    """单个数据源配置。"""

    id: str
    scope: str
    type: str
    enabled: bool
    access: str
    url: str
    max_items: int
    max_attempts: int | None = None
    min_items: int | None = None
    query: str | None = None
    keywords: list[str] = field(default_factory=list)
    max_age_days: int | None = None
    repos: list[str] = field(default_factory=list)
    disabled_reason: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SourceConfig":
        """从 JSON 字典构造 SourceConfig 实例。"""
        return cls(
            id=str(raw["id"]),
            scope=str(raw.get("scope", "ai")),
            type=str(raw["type"]),
            enabled=bool(raw.get("enabled", True)),
            access=str(raw["access"]),
            url=str(raw["url"]),
            max_items=int(raw.get("max_items", 10)),
            max_attempts=_optional_int(raw.get("max_attempts")),
            min_items=_optional_int(raw.get("min_items")),
            query=raw.get("query"),
            keywords=list(raw.get("keywords", [])),
            max_age_days=_optional_int(raw.get("max_age_days")),
            repos=list(raw.get("repos", [])),
            disabled_reason=raw.get("disabled_reason"),
        )


def collect_raw_items(
    state: InsightEngineState,
    config_path: str | Path = "config/sources.json",
) -> InsightEngineState:
    """执行数据获取阶段。"""
    configs = load_source_configs(config_path)
    state.sources = [config.id for config in configs]

    global_raw_items: list[dict[str, Any]] = []
    ai_raw_items: list[dict[str, Any]] = []
    source_stats: dict[str, dict[str, Any]] = {}
    stage_steps: list[dict[str, Any]] = [
        {
            "step": "load_source_configs",
            "status": "ok",
            "config_path": str(config_path),
            "enabled_source_count": len(configs),
        }
    ]

    for config in configs:
        attempts: list[dict[str, Any]] = []
        try:
            items, attempts = fetch_source_with_attempts(config)
        except SourceFetchAttemptsError as exc:
            attempts = exc.attempts
            state.add_warning(
                stage="collect_raw_items",
                message=f"数据源抓取失败：{config.id}",
                detail=str(exc),
            )
            source_stats[config.id] = {
                "scope": config.scope,
                "source_type": config.type,
                "count": 0,
                "status": "failed",
                "max_attempts": _effective_max_attempts(config),
                "min_items": config.min_items,
                "attempts": attempts,
            }
            stage_steps.append(
                {
                    "step": "fetch_source",
                    "source_id": config.id,
                    "status": "failed",
                    "attempt_count": len(attempts),
                    "error": str(exc),
                }
            )
            continue
        except Exception as exc:  # noqa: BLE001
            state.add_warning(
                stage="collect_raw_items",
                message=f"数据源抓取失败：{config.id}",
                detail=repr(exc),
            )
            source_stats[config.id] = {
                "scope": config.scope,
                "source_type": config.type,
                "count": 0,
                "status": "failed",
                "max_attempts": _effective_max_attempts(config),
                "min_items": config.min_items,
                "attempts": attempts,
            }
            stage_steps.append(
                {
                    "step": "fetch_source",
                    "source_id": config.id,
                    "status": "failed",
                    "attempt_count": len(attempts),
                    "error": repr(exc),
                }
            )
            continue

        if config.scope == "global":
            global_raw_items.extend(items)
        else:
            ai_raw_items.extend(items)

        source_status = "ok"
        if _below_min_items(config, len(items)):
            source_status = "insufficient"
            state.add_warning(
                stage="collect_raw_items",
                message=f"数据源返回数量不足：{config.id}",
                detail={
                    "source_id": config.id,
                    "count": len(items),
                    "min_items": config.min_items,
                    "attempts": attempts,
                },
            )

        source_stats[config.id] = {
            "scope": config.scope,
            "source_type": config.type,
            "count": len(items),
            "status": source_status,
            "max_attempts": _effective_max_attempts(config),
            "min_items": config.min_items,
            "attempts": attempts,
        }
        stage_steps.append(
            {
                "step": "fetch_source",
                "source_id": config.id,
                "status": source_status,
                "count": len(items),
                "min_items": config.min_items,
                "attempt_count": len(attempts),
            }
        )

    state.global_raw_items = global_raw_items
    state.ai_raw_items = ai_raw_items
    all_raw_items = global_raw_items + ai_raw_items

    write_json_artifact(
        state=state,
        artifact_name="raw_items",
        data={
            "run_id": state.run_id,
            "target_date": state.target_date,
            "sources": [_source_to_dict(config) for config in configs],
            "source_stats": source_stats,
            "scope_stats": {
                "global": len(global_raw_items),
                "ai": len(ai_raw_items),
                "total": len(all_raw_items),
            },
            "global_items": global_raw_items,
            "ai_items": ai_raw_items,
            "items": all_raw_items,
            "errors": state.errors,
            "warnings": state.warnings,
            "stage_steps": stage_steps,
        },
        base_dir="data/raw",
        filename="raw_items.json",
    )

    return state


def load_source_configs(path: str | Path = "config/sources.json") -> list[SourceConfig]:
    """读取启用的数据源配置。"""
    config_path = Path(path)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    sources = [SourceConfig.from_dict(item) for item in payload.get("sources", [])]
    return [source for source in sources if source.enabled]


def fetch_source(config: SourceConfig) -> list[dict[str, Any]]:
    """根据数据源配置分派到具体抓取函数。"""
    if config.id == "arxiv":
        return fetch_arxiv(config)
    if config.id in {"hacker_news", "hacker_news_ai"}:
        return fetch_hacker_news(config)
    if config.access == "github_releases":
        return fetch_github_releases(config)
    if config.access == "rss":
        return fetch_rss(config)
    raise ValueError(f"不支持的数据源：{config.id}")


class SourceFetchAttemptsError(RuntimeError):
    """单个数据源多次抓取仍失败。"""

    def __init__(self, source_id: str, error: Exception, attempts: list[dict[str, Any]]) -> None:
        super().__init__(f"{source_id} 抓取失败：{error!r}")
        self.source_id = source_id
        self.attempts = attempts


def fetch_source_with_attempts(config: SourceConfig) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按 config.max_attempts 抓取单个数据源，并用 min_items 判断是否需要重试。"""
    max_attempts = _effective_max_attempts(config)
    retry_sleep_seconds = float(os.getenv("HARNESS_SOURCE_RETRY_SLEEP_SECONDS", "1"))
    attempts: list[dict[str, Any]] = []
    best_items: list[dict[str, Any]] | None = None
    last_error: Exception | None = None

    for attempt_index in range(1, max_attempts + 1):
        try:
            items = fetch_source(config)
            if best_items is None or len(items) > len(best_items):
                best_items = items

            if _below_min_items(config, len(items)):
                attempts.append(
                    {
                        "attempt": attempt_index,
                        "status": "insufficient",
                        "count": len(items),
                        "min_items": config.min_items,
                    }
                )
                if attempt_index < max_attempts:
                    time.sleep(retry_sleep_seconds)
                    continue
                return best_items, attempts

            attempts.append(
                {
                    "attempt": attempt_index,
                    "status": "ok",
                    "count": len(items),
                    "min_items": config.min_items,
                }
            )
            return items, attempts
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            attempts.append(
                {
                    "attempt": attempt_index,
                    "status": "failed",
                    "error": repr(exc),
                }
            )
            if attempt_index < max_attempts:
                time.sleep(retry_sleep_seconds)

    if best_items is not None:
        return best_items, attempts
    assert last_error is not None
    raise SourceFetchAttemptsError(config.id, last_error, attempts)


def _effective_max_attempts(config: SourceConfig) -> int:
    """返回有效的最大抓取尝试次数，未配置时默认 1。"""
    if config.max_attempts is None:
        return 1
    return max(1, config.max_attempts)


def _below_min_items(config: SourceConfig, count: int) -> bool:
    """判断抓取结果数量是否低于配置的最低要求。"""
    return config.min_items is not None and count < config.min_items


def fetch_text(url: str, timeout: int = 10) -> str:
    """读取 URL 文本内容。"""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(content_type, errors="replace")


def fetch_json(url: str, timeout: int = 10) -> Any:
    """读取 URL JSON 内容。"""
    return json.loads(fetch_text(url=url, timeout=timeout))


def fetch_arxiv(config: SourceConfig) -> list[dict[str, Any]]:
    """抓取 arXiv Atom API。"""
    params = {
        "search_query": config.query or "cat:cs.AI",
        "start": 0,
        "max_results": config.max_items,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    url = f"{config.url}?{urllib.parse.urlencode(params)}"
    xml_text = fetch_text(url)
    root = ET.fromstring(xml_text)
    ns = {"atom": "http://www.w3.org/2005/Atom"}

    items: list[dict[str, Any]] = []
    for entry in root.findall("atom:entry", ns):
        title = _xml_text(entry, "atom:title", ns)
        summary = _xml_text(entry, "atom:summary", ns)
        published_at = _xml_text(entry, "atom:published", ns)
        authors = [
            _xml_text(author, "atom:name", ns)
            for author in entry.findall("atom:author", ns)
        ]
        article_url = _xml_text(entry, "atom:id", ns)

        if not _is_recent(published_at, config.max_age_days):
            continue

        items.append(
            {
                "source_id": config.id,
                "source_scope": config.scope,
                "source_type": config.type,
                "title": _normalize_space(title),
                "url": article_url,
                "published_at": published_at,
                "author_or_org": ", ".join(author for author in authors if author),
                "summary": _normalize_space(summary),
                "raw_content": _normalize_space(summary),
                "retrieved_at": retrieved_at_iso(),
                "metadata": {
                    "query": config.query,
                    "api_url": url,
                },
            }
        )

    return items


def fetch_hacker_news(config: SourceConfig) -> list[dict[str, Any]]:
    """抓取 Hacker News 搜索结果。"""
    query = config.query or "AI OR artificial intelligence OR LLM"
    params: dict[str, Any] = {
        "query": query,
        "tags": "story",
        "hitsPerPage": max(config.max_items * 10, 50),
    }
    if config.max_age_days is not None:
        min_created_at = int(
            (datetime.now(timezone.utc) - timedelta(days=config.max_age_days)).timestamp()
        )
        params["numericFilters"] = f"created_at_i>{min_created_at}"

    search_url = f"{config.url}?{urllib.parse.urlencode(params)}"
    payload = fetch_json(search_url, timeout=10)
    items: list[dict[str, Any]] = []

    for story in payload.get("hits", []):
        story_id = story.get("story_id") or story.get("objectID")
        title = str(story.get("title") or story.get("story_title") or "")
        text = str(story.get("story_text") or story.get("comment_text") or "")
        url = str(story.get("url") or f"https://news.ycombinator.com/item?id={story_id}")
        published_at = str(story.get("created_at") or "")

        if not _is_recent(published_at, config.max_age_days):
            continue
        if config.keywords and not _contains_keyword(title, config.keywords):
            continue

        items.append(
            {
                "source_id": config.id,
                "source_scope": config.scope,
                "source_type": config.type,
                "title": title,
                "url": url,
                "published_at": published_at,
                "author_or_org": str(story.get("author", "")),
                "summary": _normalize_space(text or title),
                "raw_content": _normalize_space(text or title),
                "retrieved_at": retrieved_at_iso(),
                "metadata": {
                    "hn_id": story_id,
                    "points": story.get("points"),
                    "num_comments": story.get("num_comments"),
                    "comments_url": f"https://news.ycombinator.com/item?id={story_id}",
                    "api_url": search_url,
                },
            }
        )

        if len(items) >= config.max_items:
            break

        time.sleep(0.05)

    return items


def fetch_rss(config: SourceConfig) -> list[dict[str, Any]]:
    """抓取 RSS/Atom Feed。"""
    xml_text = fetch_text(config.url)
    root = ET.fromstring(xml_text)
    channel = root.find("channel")
    if channel is None:
        return _fetch_atom_items(root=root, config=config)

    items: list[dict[str, Any]] = []
    for item in channel.findall("item"):
        title = _child_text(item, "title")
        link = _child_text(item, "link")
        description = _child_text(item, "description")
        published_at = _child_text(item, "pubDate")
        creator = _dc_creator(item)

        if not _is_recent(published_at, config.max_age_days):
            continue
        if config.keywords and not _contains_keyword(title, config.keywords):
            continue

        items.append(
            {
                "source_id": config.id,
                "source_scope": config.scope,
                "source_type": config.type,
                "title": _normalize_space(title),
                "url": link,
                "published_at": published_at,
                "author_or_org": creator,
                "summary": _normalize_space(description),
                "raw_content": _normalize_space(description),
                "retrieved_at": retrieved_at_iso(),
                "metadata": {
                    "feed_url": config.url,
                },
            }
        )

        if len(items) >= config.max_items:
            break

    return items


def _fetch_atom_items(root: ET.Element, config: SourceConfig) -> list[dict[str, Any]]:
    """解析 Atom Feed。"""
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items: list[dict[str, Any]] = []

    for entry in root.findall("atom:entry", ns):
        title = _xml_text(entry, "atom:title", ns)
        summary = (
            _xml_text(entry, "atom:summary", ns)
            or _xml_text(entry, "atom:content", ns)
            or title
        )
        published_at = (
            _xml_text(entry, "atom:published", ns)
            or _xml_text(entry, "atom:updated", ns)
        )
        author_node = entry.find("atom:author", ns)
        author = _xml_text(author_node, "atom:name", ns) if author_node is not None else ""
        link = _atom_link(entry)

        if not _is_recent(published_at, config.max_age_days):
            continue
        if config.keywords and not _contains_keyword(title, config.keywords):
            continue

        items.append(
            {
                "source_id": config.id,
                "source_scope": config.scope,
                "source_type": config.type,
                "title": _normalize_space(title),
                "url": link,
                "published_at": published_at,
                "author_or_org": author,
                "summary": _normalize_space(summary),
                "raw_content": _normalize_space(summary),
                "retrieved_at": retrieved_at_iso(),
                "metadata": {
                    "feed_url": config.url,
                    "feed_format": "atom",
                },
            }
        )

        if len(items) >= config.max_items:
            break

    return items


def fetch_github_releases(config: SourceConfig) -> list[dict[str, Any]]:
    """抓取 GitHub Releases。"""
    if not config.repos:
        return []

    per_repo_limit = max(1, config.max_items // len(config.repos))
    items: list[dict[str, Any]] = []
    base_url = config.url.rstrip("/")

    for repo in config.repos:
        api_url = f"{base_url}/repos/{repo}/releases?per_page={per_repo_limit}"
        releases = fetch_json(api_url, timeout=10)
        if not isinstance(releases, list):
            continue

        for release in releases:
            release_name = str(release.get("name") or release.get("tag_name") or "")
            tag_name = str(release.get("tag_name") or "")
            title = f"{repo} 发布 {release_name or tag_name}".strip()
            body = _normalize_space(str(release.get("body") or ""))
            author = release.get("author") or {}
            published_at = str(release.get("published_at") or release.get("created_at") or "")

            if not _is_recent(published_at, config.max_age_days):
                continue

            items.append(
                {
                    "source_id": config.id,
                    "source_scope": config.scope,
                    "source_type": config.type,
                    "title": title,
                    "url": str(release.get("html_url") or ""),
                    "published_at": published_at,
                    "author_or_org": str(author.get("login", "")),
                    "summary": _truncate(body, limit=500) or title,
                    "raw_content": body or title,
                    "retrieved_at": retrieved_at_iso(),
                    "metadata": {
                        "repo": repo,
                        "tag_name": tag_name,
                        "draft": release.get("draft"),
                        "prerelease": release.get("prerelease"),
                        "api_url": api_url,
                    },
                }
            )

            if len(items) >= config.max_items:
                return items

    return items


def retrieved_at_iso() -> str:
    """返回抓取时间。"""
    return datetime.now(timezone.utc).isoformat()


def _source_to_dict(config: SourceConfig) -> dict[str, Any]:
    """把 SourceConfig 转为可序列化 dict，用于写入 artifact。"""
    return {
        "id": config.id,
        "scope": config.scope,
        "type": config.type,
        "enabled": config.enabled,
        "access": config.access,
        "url": config.url,
        "max_items": config.max_items,
        "max_attempts": config.max_attempts,
        "min_items": config.min_items,
        "query": config.query,
        "keywords": config.keywords,
        "max_age_days": config.max_age_days,
        "repos": config.repos,
        "disabled_reason": config.disabled_reason,
    }


def _optional_int(value: Any) -> int | None:
    """把可选的 JSON 值转为 int 或 None，用于解析配置中的可选数值字段。"""
    if value is None:
        return None
    return int(value)


def _xml_text(node: ET.Element, path: str, ns: dict[str, str]) -> str:
    """在 XML 节点下按命名空间查找子元素并返回其文本。"""
    found = node.find(path, ns)
    return found.text or "" if found is not None else ""


def _child_text(node: ET.Element, tag: str) -> str:
    """在 XML 节点下查找直接子元素并返回其文本。"""
    found = node.find(tag)
    return found.text or "" if found is not None else ""


def _dc_creator(node: ET.Element) -> str:
    """从 RSS item 的 dc:creator 子元素中提取作者名。"""
    for child in node:
        if child.tag.endswith("creator"):
            return child.text or ""
    return ""


def _atom_link(entry: ET.Element) -> str:
    """从 Atom entry 的 link 子元素中提取 href 属性。"""
    for child in entry:
        if not child.tag.endswith("link"):
            continue
        href = child.attrib.get("href")
        if href:
            return href
    return ""


def _contains_keyword(text: str, keywords: list[str]) -> bool:
    """检查文本是否包含关键词列表中的任意一个，短词做词边界匹配。"""
    lowered = text.lower()
    for keyword in keywords:
        normalized = keyword.lower()
        if _should_match_word_boundary(normalized):
            if re.search(rf"\b{re.escape(normalized)}s?\b", lowered):
                return True
            continue
        if normalized in lowered:
            return True
    return False


def _should_match_word_boundary(keyword: str) -> bool:
    """判断关键词是否太短，需要做词边界匹配以避免误匹配。"""
    compact = keyword.replace(".", "")
    return compact.isalnum() and len(compact) <= 4


def _is_recent(published_at: str, max_age_days: int | None) -> bool:
    """判断发布时间是否在 max_age_days 天内，用于过滤过旧数据。"""
    if max_age_days is None:
        return True

    published_dt = _parse_datetime(published_at)
    if published_dt is None:
        return False

    threshold = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    return published_dt >= threshold


def _parse_datetime(value: str) -> datetime | None:
    """解析多种常见日期时间格式为 UTC datetime，解析失败返回 None。"""
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


def _normalize_space(text: str) -> str:
    """压缩多余空白字符，把连续空白替换为单个空格。"""
    return " ".join(text.split())


def _truncate(text: str, limit: int) -> str:
    """按字符数截断文本，超出限制时末尾加 ..."""
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."
