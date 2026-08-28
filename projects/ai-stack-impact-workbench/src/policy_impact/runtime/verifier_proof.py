"""Authoritative persisted review proofs used by publication and editor gates."""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, StrictStr, field_validator

from policy_impact.memory.store import PolicyMemoryStore
from policy_impact.runtime.gate_catalog import ReviewGateProof


class VerifierGateProof(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    audit_ref: StrictStr
    evaluation_id: StrictStr
    run_id: StrictStr
    span_id: StrictStr
    gate_id: Literal["review_verdict"]
    version: Literal["1.0.0"]
    decision: Literal["pass"]
    reviewer_role: Literal["verifier"]
    verified_claim_refs: tuple[StrictStr, ...]
    review_refs: tuple[StrictStr, ...]

    @field_validator(
        "audit_ref", "evaluation_id", "run_id", "span_id", mode="after"
    )
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("proof identifiers must not be blank")
        return stripped

    @field_validator("verified_claim_refs", "review_refs", mode="after")
    @classmethod
    def validate_refs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip() for value in values)
        if not normalized or any(not value for value in normalized):
            raise ValueError("proof refs must contain nonblank refs")
        if len(normalized) != len(set(normalized)):
            raise ValueError("proof refs must not contain duplicates")
        return normalized


class VerifierGateResolver(Protocol):
    def resolve(
        self,
        run_id: str,
        verified_claim_refs: tuple[str, ...],
    ) -> tuple[VerifierGateProof, ...]: ...


class PolicyMemoryReviewGateResolver:
    """Resolve one publication proof from the dedicated Gate audit table."""

    def __init__(self, store: PolicyMemoryStore) -> None:
        self._store = store

    def resolve_review_gate(
        self,
        *,
        audit_ref: str,
        run_id: str,
        claim_id: str,
    ) -> ReviewGateProof | None:
        row = self._store.resolve_gate_evaluation(audit_ref)
        if row is None:
            return None
        metadata = row.get("audit_metadata") or {}
        if (
            row.get("run_id") != run_id
            or row.get("gate_id") != "review_verdict"
            or row.get("gate_version") != "1.0.0"
            or row.get("decision") != "pass"
            or metadata.get("claim_id") != claim_id
        ):
            return None
        try:
            return ReviewGateProof.model_validate(
                {
                    "audit_ref": row.get("audit_ref"),
                    "evaluation_id": row.get("evaluation_id"),
                    "run_id": row.get("run_id"),
                    "span_id": row.get("span_id"),
                    "claim_id": metadata.get("claim_id"),
                    "gate_id": row.get("gate_id"),
                    "gate_version": row.get("gate_version"),
                    "decision": row.get("decision"),
                    "reviewer_role": metadata.get("reviewer_role"),
                    "review_refs": tuple(metadata.get("review_refs") or ()),
                    "claim_snapshot_hash": metadata.get("claim_snapshot_hash"),
                    "evidence_refs": tuple(metadata.get("evidence_refs") or ()),
                }
            )
        except (TypeError, ValueError):
            return None


class PolicyMemoryVerifierGateResolver:
    """Resolve editor proofs only from dedicated verifier Gate evaluations."""

    def __init__(self, store: PolicyMemoryStore) -> None:
        self._store = store

    def resolve(
        self,
        run_id: str,
        verified_claim_refs: tuple[str, ...],
    ) -> tuple[VerifierGateProof, ...]:
        requested = frozenset(ref.strip() for ref in verified_claim_refs)
        if not run_id.strip() or not requested or any(not ref for ref in requested):
            return ()
        run = self._store.get_agent_run(run_id)
        if not run or run.get("status") not in {"running", "done"}:
            return ()

        proofs: list[VerifierGateProof] = []
        covered: set[str] = set()
        for row in self._store.list_gate_evaluations(
            run_id, gate_id="review_verdict"
        ):
            metadata = row.get("audit_metadata") or {}
            if (
                row.get("decision") != "pass"
                or metadata.get("reviewer_role") != "verifier"
            ):
                continue
            refs = tuple(str(ref) for ref in metadata.get("verified_claim_refs") or ())
            if not requested.intersection(refs):
                continue
            try:
                proof = VerifierGateProof.model_validate(
                    {
                        "audit_ref": row.get("audit_ref"),
                        "evaluation_id": row.get("evaluation_id"),
                        "run_id": row.get("run_id"),
                        "span_id": row.get("span_id"),
                        "gate_id": row.get("gate_id"),
                        "version": row.get("gate_version"),
                        "decision": row.get("decision"),
                        "reviewer_role": metadata.get("reviewer_role"),
                        "verified_claim_refs": refs,
                        "review_refs": tuple(metadata.get("review_refs") or ()),
                    }
                )
            except (TypeError, ValueError):
                continue
            proofs.append(proof)
            covered.update(proof.verified_claim_refs)
        if not requested.issubset(covered):
            return ()
        return tuple(proofs)


__all__ = [
    "PolicyMemoryReviewGateResolver",
    "PolicyMemoryVerifierGateResolver",
    "VerifierGateProof",
    "VerifierGateResolver",
]
