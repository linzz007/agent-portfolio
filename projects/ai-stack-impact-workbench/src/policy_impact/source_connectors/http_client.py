"""Small, dependency-free HTTP client with source-boundary checks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import time
from urllib.parse import urlparse
from urllib.request import Request, urlopen


USER_AGENT = "PolicyImpactWorkbench/0.1 (+local primary-source monitor)"


@dataclass(frozen=True)
class FetchResponse:
    url: str
    status: int
    content_type: str
    body: bytes
    retrieved_at: str
    sha256: str


def domain_allowed(url: str, allowed_domains: list[str]) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.lower().rstrip(".")
    return any(
        host == domain.lower().rstrip(".")
        or host.endswith("." + domain.lower().rstrip("."))
        for domain in allowed_domains
    )


def fetch_url(
    url: str,
    *,
    allowed_domains: list[str],
    timeout_seconds: float = 18.0,
    retries: int = 1,
    max_bytes: int = 3_000_000,
) -> FetchResponse:
    if not domain_allowed(url, allowed_domains):
        raise ValueError(f"source URL is outside the allowlist: {url}")

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            request = Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/atom+xml, application/rss+xml, application/json, text/html;q=0.9, */*;q=0.5",
                },
            )
            with urlopen(request, timeout=timeout_seconds) as response:
                body = response.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise ValueError(f"source response exceeds {max_bytes} bytes")
                return FetchResponse(
                    url=response.geturl(),
                    status=int(getattr(response, "status", 200)),
                    content_type=str(response.headers.get("content-type") or ""),
                    body=body,
                    retrieved_at=datetime.now(timezone.utc).isoformat(),
                    sha256=hashlib.sha256(body).hexdigest(),
                )
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < retries:
                time.sleep(0.25 * (attempt + 1))
    assert last_error is not None
    raise last_error
