"""Company Wiki loader."""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from policy_impact.harness.artifacts import project_root


def classify_source_kind(source: str) -> str:
    normalized = str(source or "").strip().lower()
    if normalized.startswith(("http://", "https://")):
        return "public_disclosure"
    if normalized == "analysis_inference_from_disclosed_business":
        return "analysis_inference"
    if normalized == "user_confirmed":
        return "user_confirmed"
    if normalized == "project_design":
        return "project_design"
    return "other"


def company_dir(company_id: str) -> Path:
    return project_root() / "data" / "companies" / company_id


def resolve_wiki_page_path(company_id: str, page_path: str) -> Path:
    """Resolve a user-provided Wiki page path inside the company wiki directory."""

    normalized = str(page_path or "").replace("\\", "/").strip()
    posix_path = PurePosixPath(normalized)
    if not normalized or posix_path.is_absolute() or ".." in posix_path.parts:
        raise ValueError(f"invalid wiki page path: {page_path}")
    if not posix_path.parts or posix_path.parts[0] != "wiki":
        raise ValueError("wiki page path must start with wiki/")
    if posix_path.suffix.lower() != ".md":
        raise ValueError("wiki page path must be a markdown file")
    base_dir = company_dir(company_id).resolve()
    wiki_root = (base_dir / "wiki").resolve()
    resolved = (base_dir / Path(*posix_path.parts)).resolve()
    if not resolved.is_relative_to(wiki_root):
        raise ValueError(f"wiki page path escapes company wiki: {page_path}")
    return resolved


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        return [item.strip().strip("\"'") for item in value[1:-1].split(",") if item.strip()]
    if value in {"true", "false"}:
        return value == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value.strip("\"'")


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    raw_meta, body = parts[1], parts[2]
    meta: dict[str, Any] = {}
    for line in raw_meta.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = _parse_scalar(value)
    return meta, body.strip()


def _format_frontmatter_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(json.dumps(str(item), ensure_ascii=False) for item in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


def dump_frontmatter(meta: dict[str, Any]) -> str:
    if not meta:
        return ""
    lines = ["---"]
    for key, value in meta.items():
        lines.append(f"{key}: {_format_frontmatter_value(value)}")
    lines.append("---")
    return "\n".join(lines)


def load_company_profile(company_id: str) -> dict[str, Any]:
    path = company_dir(company_id) / "company.yaml"
    if not path.exists():
        raise FileNotFoundError(f"company.yaml not found: {path}")
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        profile: dict[str, Any] = {}
        for line in text.splitlines():
            if ":" not in line or line.startswith(" "):
                continue
            key, value = line.split(":", 1)
            profile[key.strip()] = _parse_scalar(value)
        return profile


def _extract_fact_blocks(body: str, page_meta: dict[str, Any], source_path: str) -> list[dict[str, Any]]:
    facts = []
    pattern = re.compile(r"^## FACT:\s*(?P<fact_id>[^\n]+)\n(?P<body>.*?)(?=^## |\Z)", re.M | re.S)
    for match in pattern.finditer(body):
        fact_id = match.group("fact_id").strip()
        block = match.group("body").strip()
        fact_meta: dict[str, Any] = {}
        description_lines = []
        for line in block.splitlines():
            striped = line.strip()
            if striped.startswith("- ") and ":" in striped:
                key, value = striped[2:].split(":", 1)
                fact_meta[key.strip()] = _parse_scalar(value)
            elif striped:
                description_lines.append(striped)
        facts.append(
            {
                "fact_id": fact_id,
                "source_path": source_path,
                "domain": page_meta.get("domain", ""),
                "owner": page_meta.get("owner", ""),
                "freshness": page_meta.get("freshness", ""),
                "last_updated": page_meta.get("last_updated", ""),
                "page_source": page_meta.get("source", ""),
                "importance": int(fact_meta.get("importance", page_meta.get("importance", 3)) or 3),
                "confidence": float(fact_meta.get("confidence", page_meta.get("confidence", 0.5)) or 0.5),
                "source": fact_meta.get("source", page_meta.get("source", "")),
                "source_kind": classify_source_kind(
                    str(fact_meta.get("source", page_meta.get("source", "")))
                ),
                "value": fact_meta.get("value", ""),
                "policy_relevance": fact_meta.get("policy_relevance", []),
                "text": "\n".join(description_lines) or block,
            }
        )
    return facts


def load_wiki_pages(company_id: str) -> list[dict[str, Any]]:
    wiki_dir = company_dir(company_id) / "wiki"
    pages = []
    for path in sorted(wiki_dir.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)
        rel_path = path.relative_to(company_dir(company_id)).as_posix()
        facts = _extract_fact_blocks(body, meta, rel_path)
        pages.append({"path": rel_path, "meta": meta, "body": body, "facts": facts})
    return pages


def load_company_knowledge(company_id: str) -> dict[str, Any]:
    profile = load_company_profile(company_id)
    pages = load_wiki_pages(company_id)
    facts = [fact for page in pages for fact in page["facts"]]
    return {"profile": profile, "pages": pages, "facts": facts}


def update_wiki_page_body(company_id: str, page_path: str, body: str) -> dict[str, Any]:
    path = resolve_wiki_page_path(company_id, page_path)
    if not path.exists():
        raise FileNotFoundError(f"wiki page not found: {page_path}")
    text = path.read_text(encoding="utf-8")
    meta, _old_body = parse_frontmatter(text)
    normalized_body = str(body or "").strip()
    frontmatter = dump_frontmatter(meta)
    if frontmatter:
        next_text = f"{frontmatter}\n{normalized_body}\n"
    else:
        next_text = f"{normalized_body}\n"
    path.write_text(next_text, encoding="utf-8")
    refreshed = load_company_knowledge(company_id)
    for page in refreshed["pages"]:
        if page["path"] == page_path:
            return page
    raise FileNotFoundError(f"wiki page disappeared after update: {page_path}")
