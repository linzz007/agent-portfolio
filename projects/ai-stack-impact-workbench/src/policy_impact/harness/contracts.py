"""Domain contracts for policy impact harness evidence and review surfaces."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ProfileFact:
    key: str
    value: Any
    source: str
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SourceSnapshot:
    source_id: str
    title: str
    url: str
    captured_at: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceSpan:
    source_id: str
    text: str
    start_char: int
    end_char: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ImpactClaim:
    claim_id: str
    company_id: str
    policy_id: str
    summary: str
    impact_area: str
    severity: str
    evidence_spans: list[EvidenceSpan] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ClaimReviewBundle:
    bundle_id: str
    claim: ImpactClaim
    evidence_spans: list[EvidenceSpan] = field(default_factory=list)
    review_notes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GateDecision:
    gate_name: str
    passed: bool
    reasons: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ContextManifestRecord:
    record_id: str
    context_type: str
    source_id: str
    checksum: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ImpactTicket:
    ticket_id: str
    claim_id: str
    company_id: str
    action: str
    priority: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
