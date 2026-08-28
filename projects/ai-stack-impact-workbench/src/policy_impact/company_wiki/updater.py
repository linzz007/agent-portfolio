"""Company Wiki update proposal helpers."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import re
from uuid import uuid4

from policy_impact.company_wiki.loader import company_dir


@dataclass
class CorrectionProposal:
    correction_id: str
    company_id: str
    target_file: str
    target_fact_id: str
    old_claim: str
    new_claim: str
    status: str = "pending"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def create_update_proposal(company_id: str, target_file: str, target_fact_id: str, old_claim: str, new_claim: str) -> CorrectionProposal:
    return CorrectionProposal(
        correction_id=f"corr_{uuid4().hex[:8]}",
        company_id=company_id,
        target_file=target_file,
        target_fact_id=target_fact_id,
        old_claim=old_claim,
        new_claim=new_claim,
    )


def apply_update(proposal: CorrectionProposal) -> dict[str, str]:
    path = company_dir(proposal.company_id) / proposal.target_file
    text = path.read_text(encoding="utf-8")
    updated = _replace_fact_value(
        text=text,
        target_fact_id=proposal.target_fact_id,
        old_claim=proposal.old_claim,
        new_claim=proposal.new_claim,
    )
    if updated == text:
        raise ValueError("target FACT block was not changed")
    path.write_text(updated, encoding="utf-8")
    proposal.status = "applied"
    return {"status": "applied", "path": str(path)}


def _replace_fact_value(text: str, target_fact_id: str, old_claim: str, new_claim: str) -> str:
    pattern = re.compile(
        rf"(?P<header>^## FACT:\s*{re.escape(target_fact_id)}\s*\n)(?P<body>.*?)(?=^## FACT:|\Z)",
        re.M | re.S,
    )
    match = pattern.search(text)
    if not match:
        raise ValueError(f"target_fact_id not found: {target_fact_id}")

    body = match.group("body")
    lines = body.splitlines()
    changed = False
    found_value_line = False
    old_claim = old_claim.strip()
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("- value:"):
            continue
        found_value_line = True
        prefix = line[: line.index("- value:")]
        current_value = stripped.split(":", 1)[1].strip()
        if not old_claim:
            replacement_value = new_claim
        elif current_value == old_claim:
            replacement_value = new_claim
        elif old_claim in current_value and len(old_claim) >= 4:
            replacement_value = current_value.replace(old_claim, new_claim)
        elif old_claim in current_value:
            replacement_value = new_claim
        else:
            continue
        lines[idx] = f"{prefix}- value: {replacement_value}"
        changed = True
        break

    if not found_value_line:
        lines.insert(0, f"- value: {new_claim}")
        changed = True

    if not changed:
        raise ValueError("old_claim not found in target FACT value")

    new_body = "\n".join(lines)
    start, end = match.span("body")
    return text[:start] + new_body + text[end:]
