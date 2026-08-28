"""Strict public output contracts for the default delegated roles."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictStr, ValidationInfo, field_validator


Confidence = Annotated[StrictFloat, Field(ge=0.0, le=1.0)]


class _StrictFrozenOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("*", mode="after")
    @classmethod
    def reject_blank_strings(cls, value, info: ValidationInfo):
        if isinstance(value, str) and not value.strip():
            if info.field_name == "source_url":
                return ""
            raise ValueError("string fields must not be blank")
        return value.strip() if isinstance(value, str) else value


class EvidenceItem(_StrictFrozenOutput):
    source_ref: StrictStr
    summary: StrictStr
    source_url: StrictStr = ""


class CollectorOutput(_StrictFrozenOutput):
    evidence: tuple[EvidenceItem, ...]


class ClaimItem(_StrictFrozenOutput):
    claim_ref: StrictStr
    statement: StrictStr
    evidence_refs: tuple[StrictStr, ...]
    confidence: Confidence


class AnalystOutput(_StrictFrozenOutput):
    claims: tuple[ClaimItem, ...]


class CounterexampleItem(_StrictFrozenOutput):
    claim_ref: StrictStr
    issue: StrictStr
    evidence_refs: tuple[StrictStr, ...]


class SkepticOutput(_StrictFrozenOutput):
    counterexamples: tuple[CounterexampleItem, ...]
    notes: tuple[StrictStr, ...]


class VerdictItem(_StrictFrozenOutput):
    claim_ref: StrictStr
    decision: Literal["pass", "fail"]
    evidence_refs: tuple[StrictStr, ...]
    reason: StrictStr


class VerifierOutput(_StrictFrozenOutput):
    verdicts: tuple[VerdictItem, ...]


class EditorOutput(_StrictFrozenOutput):
    answer: StrictStr
    verified_claim_refs: tuple[StrictStr, ...]


__all__ = [
    "AnalystOutput",
    "ClaimItem",
    "CollectorOutput",
    "CounterexampleItem",
    "EditorOutput",
    "EvidenceItem",
    "SkepticOutput",
    "VerdictItem",
    "VerifierOutput",
]
