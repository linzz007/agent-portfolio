"""Normalize primary RSS/Atom and official policy pages into evidence snapshots."""

from __future__ import annotations

from datetime import date, datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

from policy_impact.harness.artifacts import project_root
from policy_impact.source_connectors.http_client import domain_allowed, fetch_url


ATOM = "{http://www.w3.org/2005/Atom}"


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.heading_parts: list[str] = []
        self._ignored_depth = 0
        self._in_heading = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1
        if tag in {"h1", "h2"}:
            self._in_heading = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1
        if tag in {"h1", "h2"}:
            self._in_heading = False

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        text = " ".join(unescape(data).split())
        if text:
            self.parts.append(text)
            if self._in_heading:
                self.heading_parts.append(text)

    @property
    def text(self) -> str:
        return "\n".join(self.parts)

    @property
    def heading(self) -> str:
        return " ".join(self.heading_parts).strip()


class _LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._active: dict[str, Any] | None = None
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            href = str(dict(attrs).get("href") or "")
            self._active = {"href": href, "parts": []}

    def handle_data(self, data: str) -> None:
        if self._active is not None:
            self._active["parts"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._active is not None:
            title = " ".join("".join(self._active["parts"]).split())
            if self._active["href"] and title:
                self.links.append((self._active["href"], title))
            self._active = None


def source_config_path() -> Path:
    return project_root() / "config" / "sources.json"


def load_source_config() -> dict[str, Any]:
    return json.loads(source_config_path().read_text(encoding="utf-8"))


def collect_news_sources(
    *,
    persist: bool = True,
    force_refresh: bool = False,
    cache_ttl_seconds: int = 300,
) -> dict[str, Any]:
    if not force_refresh:
        cached = _fresh_snapshot("news", cache_ttl_seconds)
        if cached:
            return {
                "items": cached["items"],
                "source_statuses": cached.get("source_statuses") or [],
                "collection_succeeded": True,
                "snapshot_path": cached.get("snapshot_path") or "",
                "collected_at": cached.get("captured_at"),
                "cache_hit": True,
            }

    items: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    sources = [
        source
        for source in load_source_config().get("news_sources", [])
        if source.get("enabled", True)
    ]
    with ThreadPoolExecutor(max_workers=max(1, min(len(sources), 6))) as executor:
        futures = {executor.submit(_collect_news_source, source): source for source in sources}
        results: dict[str, tuple[list[dict[str, Any]], dict[str, Any]]] = {}
        for future in as_completed(futures):
            source = futures[future]
            source_id = str(source.get("source_id") or "")
            results[source_id] = future.result()
    for source in sources:
        selected, status = results[str(source.get("source_id") or "")]
        items.extend(selected)
        statuses.append(status)

    items = _dedupe_items(items)
    snapshot_path = ""
    if persist and items:
        snapshot_path = str(_persist_snapshot("news", items, statuses))
    return {
        "items": items,
        "source_statuses": statuses,
        "collection_succeeded": any(item["status"] == "ok" for item in statuses),
        "snapshot_path": snapshot_path,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "cache_hit": False,
    }


def _collect_news_source(source: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    started_at = datetime.now(timezone.utc)
    try:
        response = fetch_url(
            str(source["url"]),
            allowed_domains=list(source.get("allowed_domains") or []),
        )
        parsed = _parse_feed(response.body, source, response.retrieved_at)
        selected = parsed[: int(source.get("max_items") or 10)]
        return selected, _source_status(
            source,
            "ok",
            len(selected),
            started_at,
            response.sha256,
        )
    except Exception as exc:  # noqa: BLE001
        return [], _source_status(source, "error", 0, started_at, error=repr(exc))


def collect_policy_sources(
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    documents: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    for source in load_source_config().get("policy_sources", []):
        if not source.get("enabled", True):
            continue
        started_at = datetime.now(timezone.utc)
        try:
            source_type = str(source.get("type") or "")
            if source_type == "official_html_list":
                parsed = _collect_official_list(source, date_from=date_from, date_to=date_to)
            elif source_type == "official_pages":
                parsed = _collect_official_pages(source, date_from=date_from, date_to=date_to)
            else:
                raise ValueError(f"unsupported policy source type: {source_type}")
            documents.extend(parsed)
            statuses.append(_source_status(source, "ok", len(parsed), started_at))
        except Exception as exc:  # noqa: BLE001
            statuses.append(_source_status(source, "error", 0, started_at, error=repr(exc)))

    documents = _dedupe_items(documents)
    snapshot_path = ""
    if persist and documents:
        snapshot_path = str(_persist_snapshot("policies", documents, statuses))
        for document in documents:
            document["raw_text_path"] = snapshot_path
    return {
        "documents": documents,
        "source_statuses": statuses,
        "collection_succeeded": any(item["status"] == "ok" for item in statuses),
        "snapshot_path": snapshot_path,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "date_from": date_from,
        "date_to": date_to,
    }


def load_latest_news_snapshot() -> dict[str, Any]:
    return _load_latest_snapshot("news")


def load_latest_policy_snapshot() -> dict[str, Any]:
    return _load_latest_snapshot("policies")


def _parse_feed(body: bytes, source: dict[str, Any], retrieved_at: str) -> list[dict[str, Any]]:
    root = ET.fromstring(body)
    entries = root.findall(f"{ATOM}entry")
    output: list[dict[str, Any]] = []
    if entries:
        for entry in entries:
            title = _xml_text(entry, f"{ATOM}title")
            published = _xml_text(entry, f"{ATOM}published") or _xml_text(entry, f"{ATOM}updated")
            link = ""
            for node in entry.findall(f"{ATOM}link"):
                if node.attrib.get("rel", "alternate") == "alternate" and node.attrib.get("href"):
                    link = str(node.attrib["href"])
                    break
            summary = _xml_text(entry, f"{ATOM}summary") or _xml_text(entry, f"{ATOM}content")
            output.append(_news_item(source, title, published, link, summary, retrieved_at))
        return [item for item in output if item["title"] and item["url"]]

    channel = root.find("channel")
    if channel is None:
        return []
    for entry in channel.findall("item"):
        title = _xml_text(entry, "title")
        published = _xml_text(entry, "pubDate") or _xml_text(entry, "date")
        link = _xml_text(entry, "link")
        summary = _xml_text(entry, "description")
        output.append(_news_item(source, title, published, link, summary, retrieved_at))
    return [item for item in output if item["title"] and item["url"]]


def _news_item(
    source: dict[str, Any],
    title: str,
    published: str,
    link: str,
    summary: str,
    retrieved_at: str,
) -> dict[str, Any]:
    clean_summary = _html_to_text(summary)[:4000]
    clean_title = title.strip()
    if "releases" in str(source.get("source_id") or "") and re.fullmatch(
        r"v?\d[\w.+-]*", clean_title, flags=re.IGNORECASE
    ):
        clean_title = f"{source.get('name')} {clean_title}"
    published_at = _normalize_datetime(published)
    content_hash = _content_hash(clean_title, clean_summary, link)
    return {
        "news_id": f"news_{content_hash[:16]}",
        "title": clean_title,
        "source": str(source.get("name") or source.get("source_id")),
        "source_id": str(source.get("source_id") or ""),
        "source_level": str(source.get("level") or "L5"),
        "source_type": str(source.get("type") or "rss_atom"),
        "published_at": published_at,
        "retrieved_at": retrieved_at,
        "url": link.strip(),
        "summary": clean_summary,
        "topics": list(source.get("topics") or []),
        "data_mode": "live",
        "is_fixture": False,
        "content_hash": content_hash,
    }


def _collect_official_list(
    source: dict[str, Any],
    *,
    date_from: str | None,
    date_to: str | None,
) -> list[dict[str, Any]]:
    list_url = str(source["list_url"])
    response = fetch_url(list_url, allowed_domains=list(source.get("allowed_domains") or []))
    parser = _LinkExtractor()
    parser.feed(_decode_html(response.body, response.content_type))
    pattern = re.compile(str(source.get("article_url_regex") or ".*"))
    keywords = [str(item).lower() for item in source.get("keywords") or []]
    excluded = [str(item).lower() for item in source.get("exclude_title_keywords") or []]
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()
    for href, title in parser.links:
        url = urljoin(list_url, href)
        if url in seen or not domain_allowed(url, list(source.get("allowed_domains") or [])):
            continue
        if not pattern.search(urlparse(url).path):
            continue
        if keywords and not any(keyword in title.lower() for keyword in keywords):
            continue
        if excluded and any(keyword in title.lower() for keyword in excluded):
            continue
        published_at = _date_from_url(url)
        if not _date_in_range(published_at, date_from, date_to):
            continue
        seen.add(url)
        candidates.append({"url": url, "title": title, "published_at": published_at})

    documents: list[dict[str, Any]] = []
    for candidate in candidates[: int(source.get("max_items") or 10)]:
        documents.append(_fetch_policy_page(source, candidate))
    return documents


def _collect_official_pages(
    source: dict[str, Any],
    *,
    date_from: str | None,
    date_to: str | None,
) -> list[dict[str, Any]]:
    documents = []
    for page in source.get("pages") or []:
        published_at = str(page.get("published_at") or "")
        if not _date_in_range(published_at, date_from, date_to):
            continue
        documents.append(_fetch_policy_page(source, page))
    return documents


def _fetch_policy_page(source: dict[str, Any], page: dict[str, Any]) -> dict[str, Any]:
    url = str(page["url"])
    response = fetch_url(url, allowed_domains=list(source.get("allowed_domains") or []))
    extractor = _TextExtractor()
    extractor.feed(_decode_html(response.body, response.content_type))
    title = str(page.get("title") or extractor.heading or url)
    text = extractor.text
    if len(text) < 200:
        raise ValueError(f"official policy page is unexpectedly short: {url}")
    content_hash = _content_hash(title, text, url)
    return {
        "policy_id": f"policy_{content_hash[:16]}",
        "title": title,
        "issuer": str(source.get("issuer") or ""),
        "region_scope": list(source.get("region_scope") or ["全国"]),
        "published_at": str(page.get("published_at") or _date_from_url(url)),
        "retrieved_at": response.retrieved_at,
        "source_url": url,
        "source_id": str(source.get("source_id") or ""),
        "source_level": str(source.get("level") or "L1"),
        "source_type": str(source.get("type") or "official_page"),
        "data_mode": "live",
        "is_fixture": False,
        "policy_type": list(page.get("policy_type") or source.get("keywords") or []),
        "raw_text_path": "",
        "text": text[:120_000],
        "content_hash": content_hash,
        "response_sha256": response.sha256,
    }


def _persist_snapshot(kind: str, items: list[dict[str, Any]], statuses: list[dict[str, Any]]) -> Path:
    payload = {
        "schema_version": "1.0",
        "kind": kind,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source_statuses": statuses,
        "items": items,
    }
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:12]
    root = project_root() / "data" / kind / "snapshots"
    dated = root / date.today().isoformat()
    dated.mkdir(parents=True, exist_ok=True)
    path = dated / f"{kind}-snapshot-{digest}.json"
    path.write_text(encoded, encoding="utf-8")
    root.mkdir(parents=True, exist_ok=True)
    (root / "latest.json").write_text(encoded, encoding="utf-8")
    return path


def _load_latest_snapshot(kind: str) -> dict[str, Any]:
    path = project_root() / "data" / kind / "snapshots" / "latest.json"
    if not path.exists():
        return {"items": [], "source_statuses": [], "snapshot_path": ""}
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = []
    for item in payload.get("items") or []:
        copied = dict(item)
        copied["data_mode"] = "source_snapshot"
        items.append(copied)
    return {
        "items": items,
        "source_statuses": payload.get("source_statuses") or [],
        "captured_at": payload.get("captured_at"),
        "snapshot_path": str(path),
    }


def _fresh_snapshot(kind: str, ttl_seconds: int) -> dict[str, Any] | None:
    snapshot = _load_latest_snapshot(kind)
    captured_at = str(snapshot.get("captured_at") or "")
    if not snapshot.get("items") or not captured_at:
        return None
    try:
        captured = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        if captured.tzinfo is None:
            captured = captured.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    age = (datetime.now(timezone.utc) - captured.astimezone(timezone.utc)).total_seconds()
    return snapshot if 0 <= age <= max(0, ttl_seconds) else None


def _source_status(
    source: dict[str, Any],
    status: str,
    item_count: int,
    started_at: datetime,
    checksum: str = "",
    error: str = "",
) -> dict[str, Any]:
    elapsed_ms = round((datetime.now(timezone.utc) - started_at).total_seconds() * 1000, 2)
    return {
        "source_id": source.get("source_id"),
        "name": source.get("name"),
        "level": source.get("level"),
        "type": source.get("type"),
        "url": source.get("url") or source.get("list_url") or source.get("path") or "",
        "status": status,
        "item_count": item_count,
        "elapsed_ms": elapsed_ms,
        "checksum": checksum,
        "error": error,
    }


def _dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    seen: set[str] = set()
    for item in items:
        key = str(item.get("url") or item.get("source_url") or item.get("content_hash") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def _xml_text(node: ET.Element, path: str) -> str:
    child = node.find(path)
    if child is None:
        return ""
    return "".join(child.itertext()).strip()


def _html_to_text(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value or "")
    return " ".join(parser.parts)


def _decode_html(body: bytes, content_type: str) -> str:
    match = re.search(r"charset=([\w-]+)", content_type, flags=re.IGNORECASE)
    encoding = match.group(1) if match else "utf-8"
    try:
        return body.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        return body.decode("utf-8", errors="replace")


def _normalize_datetime(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        return text


def _date_from_url(url: str) -> str:
    match = re.search(r"/(20\d{2})-(\d{2})/(\d{2})/", url)
    return "-".join(match.groups()) if match else ""


def _date_in_range(value: str, date_from: str | None, date_to: str | None) -> bool:
    if not value:
        return not date_from and not date_to
    day = value[:10]
    if date_from and day < date_from[:10]:
        return False
    if date_to and day > date_to[:10]:
        return False
    return True


def _content_hash(title: str, text: str, url: str) -> str:
    payload = f"{title.strip()}\n{url.strip()}\n{text.strip()}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
