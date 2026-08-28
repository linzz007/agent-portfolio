from datetime import datetime, timezone

from policy_impact.policy_data import repository
from policy_impact.source_connectors.http_client import FetchResponse, domain_allowed
from policy_impact.source_connectors.ingestion import (
    _collect_official_list,
    _parse_feed,
)


def _response(url: str, body: str, content_type: str) -> FetchResponse:
    encoded = body.encode("utf-8")
    return FetchResponse(
        url=url,
        status=200,
        content_type=content_type,
        body=encoded,
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        sha256="test-sha",
    )


def test_domain_allowlist_rejects_suffix_spoofing():
    assert domain_allowed("https://github.com/openai/codex", ["github.com"])
    assert domain_allowed("https://api.github.com/repos/openai/codex", ["github.com"])
    assert not domain_allowed("https://github.com.attacker.example/release", ["github.com"])
    assert not domain_allowed("file:///tmp/release.xml", ["github.com"])


def test_atom_feed_preserves_primary_source_provenance():
    feed = """<?xml version="1.0" encoding="utf-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>v1.2.3</title>
        <updated>2026-07-15T10:00:00Z</updated>
        <link rel="alternate" href="https://github.com/example/agent/releases/tag/v1.2.3" />
        <content type="html">&lt;p&gt;Adds a tool policy audit event.&lt;/p&gt;</content>
      </entry>
    </feed>"""
    source = {
        "source_id": "example_releases",
        "name": "Example Agent Releases",
        "level": "L2",
        "type": "rss_atom",
        "topics": ["agent"],
    }
    items = _parse_feed(feed.encode("utf-8"), source, "2026-07-16T00:00:00+00:00")
    assert len(items) == 1
    assert items[0]["title"] == "Example Agent Releases v1.2.3"
    assert items[0]["source_level"] == "L2"
    assert items[0]["retrieved_at"] == "2026-07-16T00:00:00+00:00"
    assert items[0]["data_mode"] == "live"


def test_policy_list_excludes_interpretations_and_date_misses(monkeypatch):
    list_url = "https://www.cac.gov.cn/list.htm"
    list_html = """
    <a href="/2026-07/10/c_100.htm">人工智能服务管理办法</a>
    <a href="/2026-07/10/c_101.htm">专家解读｜人工智能服务管理办法</a>
    <a href="/2026-05/01/c_102.htm">旧的数据安全办法</a>
    """
    article_html = "<html><h1>人工智能服务管理办法</h1><p>" + ("正式条款内容。" * 50) + "</p></html>"

    def fake_fetch(url, **kwargs):
        if url == list_url:
            return _response(url, list_html, "text/html; charset=utf-8")
        return _response(url, article_html, "text/html; charset=utf-8")

    monkeypatch.setattr("policy_impact.source_connectors.ingestion.fetch_url", fake_fetch)
    source = {
        "source_id": "cac_test",
        "name": "CAC",
        "level": "L1",
        "type": "official_html_list",
        "list_url": list_url,
        "allowed_domains": ["cac.gov.cn"],
        "article_url_regex": r"/20[0-9]{2}-[0-9]{2}/[0-9]{2}/c_[0-9]+\.htm$",
        "keywords": ["人工智能", "数据安全"],
        "exclude_title_keywords": ["专家解读"],
        "issuer": "国家互联网信息办公室",
        "region_scope": ["全国"],
    }
    docs = _collect_official_list(source, date_from="2026-07-01", date_to="2026-07-16")
    assert len(docs) == 1
    assert docs[0]["title"] == "人工智能服务管理办法"
    assert docs[0]["source_level"] == "L1"
    assert docs[0]["is_fixture"] is False


def test_fixture_repository_honors_date_range():
    docs = repository.load_policy_documents(
        date_from="2026-07-02",
        date_to="2026-07-02",
        refresh_live_sources=False,
        allow_fixture_fallback=True,
    )
    assert docs == []

    docs = repository.load_policy_documents(
        date_from="2026-07-01",
        date_to="2026-07-03",
        refresh_live_sources=False,
        allow_fixture_fallback=True,
    )
    assert len(docs) == 2
    assert all(item["data_mode"] == "fixture" for item in docs)
