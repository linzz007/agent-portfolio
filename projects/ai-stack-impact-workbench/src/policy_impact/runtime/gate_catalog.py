from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from policy_impact.runtime.gates import (
    GateDecision,
    GateDefinition,
    GateRegistry,
)


ImpactLevel = Literal["P0", "P1", "P2", "P3", "P4"]
ReviewerRole = Literal["verifier", "deterministic_evidence_gate"]
ReviewVerdict = Literal["pass", "fail", "uncertain"]
RepairRequest = Literal["downgrade", "retry_verification"]


def _normalize_nonblank_refs(
    values: tuple[str, ...],
    *,
    required_prefix: str | None = None,
) -> tuple[str, ...]:
    normalized = tuple(value.strip() for value in values)
    if any(not value for value in normalized):
        raise ValueError("references must not be blank")
    if len(normalized) != len(set(normalized)):
        raise ValueError("references must be unique")
    if required_prefix and any(not value.startswith(required_prefix) for value in normalized):
        raise ValueError(f"references must use {required_prefix} category")
    return tuple(sorted(normalized))


class ClaimGateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: StrictStr = Field(min_length=1)
    claim_text: StrictStr = Field(min_length=1)
    impact_level: ImpactLevel
    policy_evidence_refs: tuple[StrictStr, ...] = ()
    company_evidence_refs: tuple[StrictStr, ...] = ()

    @field_validator("claim_id", "claim_text", mode="after")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("claim fields must not be blank")
        return stripped

    @field_validator("policy_evidence_refs", mode="after")
    @classmethod
    def validate_policy_refs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _normalize_nonblank_refs(values, required_prefix="policy:")

    @field_validator("company_evidence_refs", mode="after")
    @classmethod
    def validate_company_refs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _normalize_nonblank_refs(values, required_prefix="company:")


class ReviewGateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: StrictStr = Field(min_length=1)
    claim_id: StrictStr = Field(min_length=1)
    reviewer_role: ReviewerRole
    verdict: ReviewVerdict
    unresolved_skeptic_count: Annotated[StrictInt, Field(ge=0)]
    review_refs: tuple[StrictStr, ...]
    claim_snapshot_hash: StrictStr = Field(min_length=1)
    evidence_refs: tuple[StrictStr, ...]
    verifier_execution_audit_ref: StrictStr | None = None
    requested_repair: Literal["retry_verification"] | None = None

    @field_validator("run_id", "claim_id", "claim_snapshot_hash", mode="after")
    @classmethod
    def reject_blank_claim_id(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("claim_id must not be blank")
        return stripped

    @field_validator("review_refs", mode="after")
    @classmethod
    def validate_review_refs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = _normalize_nonblank_refs(values)
        if not normalized:
            raise ValueError("review_refs must not be empty")
        return normalized

    @field_validator("evidence_refs", mode="after")
    @classmethod
    def validate_evidence_refs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = _normalize_nonblank_refs(values)
        if not normalized:
            raise ValueError("evidence_refs must not be empty")
        return normalized

    @field_validator("verifier_execution_audit_ref", mode="after")
    @classmethod
    def normalize_verifier_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("verifier_execution_audit_ref must not be blank")
        return stripped

    @model_validator(mode="after")
    def deterministic_review_cannot_claim_verifier_execution(self) -> "ReviewGateInput":
        if (
            self.reviewer_role == "deterministic_evidence_gate"
            and self.verifier_execution_audit_ref is not None
        ):
            raise ValueError("deterministic review cannot carry verifier execution proof")
        return self


class PublicationGateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    publication_kind: Literal["claim", "no_updates"] = "claim"
    run_id: StrictStr = Field(min_length=1)
    claim_id: StrictStr = ""
    review_audit_ref: StrictStr = ""
    claim_snapshot_hash: StrictStr = ""
    evidence_refs: tuple[StrictStr, ...]
    no_updates: bool = False
    collection_succeeded: bool = False
    requested_repair: RepairRequest | None = None

    @field_validator("run_id", mode="after")
    @classmethod
    def reject_blank_identity(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("publication identity fields must not be blank")
        return stripped

    @field_validator("evidence_refs", mode="after")
    @classmethod
    def validate_evidence_refs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = _normalize_nonblank_refs(values)
        if not normalized:
            raise ValueError("publication evidence_refs must not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_publication_kind(self) -> "PublicationGateInput":
        claim_id = self.claim_id.strip()
        review_ref = self.review_audit_ref.strip()
        snapshot_hash = self.claim_snapshot_hash.strip()
        if self.publication_kind == "claim":
            if not claim_id or not review_ref or not snapshot_hash:
                raise ValueError("claim publication requires claim and review identity")
            if self.no_updates or self.collection_succeeded:
                raise ValueError("claim publication cannot use no-updates flags")
        else:
            if claim_id or review_ref or snapshot_hash:
                raise ValueError("no-updates publication must not fabricate a claim")
            if not self.no_updates or not self.collection_succeeded:
                raise ValueError("no-updates publication requires successful collection")
            if any(not ref.startswith("source:") for ref in self.evidence_refs):
                raise ValueError("no-updates publication requires source evidence refs")
        return self


class ReviewGateProof(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    audit_ref: StrictStr
    evaluation_id: StrictStr
    run_id: StrictStr
    span_id: StrictStr
    claim_id: StrictStr
    gate_id: Literal["review_verdict"]
    gate_version: Literal["1.0.0"]
    decision: Literal["pass"]
    reviewer_role: ReviewerRole
    review_refs: tuple[StrictStr, ...]
    claim_snapshot_hash: StrictStr
    evidence_refs: tuple[StrictStr, ...]


class ReviewGateProofResolver(Protocol):
    def resolve_review_gate(
        self,
        *,
        audit_ref: str,
        run_id: str,
        claim_id: str,
    ) -> ReviewGateProof | None: ...


class VerifierExecutionProofResolver(Protocol):
    def resolve_verifier_execution(
        self,
        *,
        audit_ref: str,
        run_id: str,
        claim_id: str,
    ) -> bool: ...


class DenyVerifierExecutionProofResolver:
    def resolve_verifier_execution(
        self,
        *,
        audit_ref: str,
        run_id: str,
        claim_id: str,
    ) -> bool:
        return False


class InMemoryReviewGateProofResolver:
    def __init__(self) -> None:
        self._proofs: dict[str, ReviewGateProof] = {}

    def register(self, proof: ReviewGateProof) -> None:
        existing = self._proofs.get(proof.audit_ref)
        if existing is not None and existing != proof:
            raise ValueError("conflicting review gate proof")
        self._proofs[proof.audit_ref] = proof

    def resolve_review_gate(
        self,
        *,
        audit_ref: str,
        run_id: str,
        claim_id: str,
    ) -> ReviewGateProof | None:
        proof = self._proofs.get(audit_ref)
        if proof is None or proof.run_id != run_id or proof.claim_id != claim_id:
            return None
        return proof


class AgentStepGateDecisionSink:
    """Persist one authoritative Gate evaluation and its trace step atomically."""

    def __init__(self, store: Any) -> None:
        self._store = store

    def record(self, run_id: str, span_id: str, decision: GateDecision) -> str:
        return self._store.record_gate_evaluation(
            run_id=run_id,
            span_id=span_id,
            decision=decision.model_dump(mode="json"),
        )


class StateGateDecisionSink:
    """Explicit direct-pipeline audit sink stored inside the immutable run artifact."""

    def __init__(self, state: Any) -> None:
        self._state = state
        self._authoritative_records: dict[str, dict[str, Any]] = {}

    def record(self, run_id: str, span_id: str, decision: GateDecision) -> str:
        if run_id != self._state.run_id:
            raise ValueError("state gate run identity mismatch")
        audit_ref = f"state-gate-evaluation:{decision.evaluation_id}"
        record = {
            "record_type": "business_gate_evaluation",
            "stage": str(span_id).split(":", 1)[0],
            "run_id": run_id,
            "span_id": span_id,
            **decision.model_dump(mode="json"),
            "audit_ref": audit_ref,
        }
        existing = self._authoritative_records.get(decision.evaluation_id)
        if existing is not None:
            if existing != record:
                raise ValueError(
                    f"conflicting state gate evaluation: {decision.evaluation_id}"
                )
            return audit_ref
        authoritative_record = json.loads(
            json.dumps(record, ensure_ascii=False, sort_keys=True)
        )
        self._authoritative_records[decision.evaluation_id] = authoritative_record
        self._state.add_stage_gate_result(
            json.loads(json.dumps(authoritative_record, ensure_ascii=False))
        )
        return audit_ref

    def resolve_review_gate(
        self,
        *,
        audit_ref: str,
        run_id: str,
        claim_id: str,
    ) -> ReviewGateProof | None:
        for record in self._authoritative_records.values():
            metadata = record.get("audit_metadata") or {}
            if (
                record.get("record_type") != "business_gate_evaluation"
                or record.get("audit_ref") != audit_ref
                or record.get("run_id") != run_id
                or record.get("gate_id") != "review_verdict"
                or record.get("gate_version") != "1.0.0"
                or record.get("decision") != "pass"
                or metadata.get("claim_id") != claim_id
            ):
                continue
            try:
                return ReviewGateProof.model_validate(
                    {
                        "audit_ref": audit_ref,
                        "evaluation_id": record.get("evaluation_id"),
                        "run_id": run_id,
                        "span_id": record.get("span_id"),
                        "claim_id": claim_id,
                        "gate_id": record.get("gate_id"),
                        "gate_version": record.get("gate_version"),
                        "decision": record.get("decision"),
                        "reviewer_role": metadata.get("reviewer_role"),
                        "review_refs": tuple(metadata.get("review_refs") or ()),
                        "claim_snapshot_hash": metadata.get("claim_snapshot_hash"),
                        "evidence_refs": tuple(metadata.get("evidence_refs") or ()),
                    }
                )
            except (TypeError, ValueError):
                return None
        return None


def _claim_input_refs(payload: BaseModel) -> tuple[str, ...]:
    claim = ClaimGateInput.model_validate(payload)
    return (
        claim.claim_id,
        *claim.policy_evidence_refs,
        *claim.company_evidence_refs,
    )


def canonical_claim_snapshot_hash(payload: dict[str, Any] | BaseModel) -> str:
    claim = ClaimGateInput.model_validate(payload)
    canonical = json.dumps(
        claim.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _review_input_refs(payload: BaseModel) -> tuple[str, ...]:
    review = ReviewGateInput.model_validate(payload)
    return (
        review.claim_id,
        review.claim_snapshot_hash,
        *review.evidence_refs,
        *review.review_refs,
        *(
            (review.verifier_execution_audit_ref,)
            if review.verifier_execution_audit_ref
            else ()
        ),
    )


def _publication_input_refs(payload: BaseModel) -> tuple[str, ...]:
    publication = PublicationGateInput.model_validate(payload)
    if publication.publication_kind == "no_updates":
        return ("publication:no_updates", *publication.evidence_refs)
    return (
        publication.claim_id,
        publication.claim_snapshot_hash,
        publication.review_audit_ref,
        *publication.evidence_refs,
    )


def evaluate_claim_schema(payload: BaseModel) -> GateDecision:
    claim = ClaimGateInput.model_validate(payload)
    return GateDecision(
        gate_id="claim_schema",
        gate_version="1.0.0",
        decision="pass",
        reason="claim contract is valid",
        input_refs=_claim_input_refs(claim),
        output_ref=claim.claim_id,
    )


def evaluate_evidence_binding(payload: BaseModel) -> GateDecision:
    claim = ClaimGateInput.model_validate(payload)
    input_refs = _claim_input_refs(claim)
    policy_count = len(claim.policy_evidence_refs)
    company_count = len(claim.company_evidence_refs)
    total_count = len(set((*claim.policy_evidence_refs, *claim.company_evidence_refs)))
    if claim.impact_level in {"P0", "P1"}:
        passed = policy_count >= 1 and company_count >= 1 and total_count >= 2
    else:
        passed = policy_count >= 1
    return GateDecision(
        gate_id="evidence_binding",
        gate_version="1.0.0",
        decision="pass" if passed else "block",
        reason=(
            "claim evidence binding is sufficient"
            if passed
            else "claim evidence binding is insufficient"
        ),
        input_refs=input_refs,
        output_ref=claim.claim_id if passed else None,
    )


def _review_evaluator(
    verifier_execution_resolver: VerifierExecutionProofResolver,
):
    def evaluate(payload: BaseModel) -> GateDecision:
        review = ReviewGateInput.model_validate(payload)
        input_refs = _review_input_refs(review)
        trusted_reviewer = review.reviewer_role == "deterministic_evidence_gate"
        if review.reviewer_role == "verifier":
            trusted_reviewer = bool(
                review.verifier_execution_audit_ref
                and verifier_execution_resolver.resolve_verifier_execution(
                    audit_ref=review.verifier_execution_audit_ref,
                    run_id=review.run_id,
                    claim_id=review.claim_id,
                )
            )
        passed = (
            trusted_reviewer
            and review.verdict == "pass"
            and review.unresolved_skeptic_count == 0
        )
        metadata = {
            "claim_id": review.claim_id,
            "reviewer_role": review.reviewer_role,
            "verified_claim_refs": [review.claim_id] if passed else [],
            "review_refs": list(review.review_refs),
            "claim_snapshot_hash": review.claim_snapshot_hash,
            "evidence_refs": list(review.evidence_refs),
            "verifier_execution_audit_ref": review.verifier_execution_audit_ref,
            "unresolved_skeptic_count": review.unresolved_skeptic_count,
        }
        if passed:
            return GateDecision(
                gate_id="review_verdict",
                gate_version="1.0.0",
                decision="pass",
                reason="review verdict permits publication",
                input_refs=input_refs,
                output_ref=review.claim_id,
                audit_metadata=metadata,
            )
        if review.requested_repair == "retry_verification":
            return GateDecision(
                gate_id="review_verdict",
                gate_version="1.0.0",
                decision="repair",
                reason="review requires another verification attempt",
                input_refs=input_refs,
                repair_action={"action": "retry_verification"},
                audit_metadata=metadata,
            )
        return GateDecision(
            gate_id="review_verdict",
            gate_version="1.0.0",
            decision="block",
            reason=(
                "verifier execution proof is missing or invalid"
                if review.reviewer_role == "verifier" and not trusted_reviewer
                else "review verdict does not permit publication"
            ),
            input_refs=input_refs,
            audit_metadata=metadata,
        )

    return evaluate


def _publication_evaluator(
    proof_resolver: ReviewGateProofResolver,
):
    def evaluate(payload: BaseModel) -> GateDecision:
        publication = PublicationGateInput.model_validate(payload)
        input_refs = _publication_input_refs(publication)
        if publication.publication_kind == "no_updates":
            return GateDecision(
                gate_id="publication",
                gate_version="1.0.0",
                decision="pass",
                reason="successful source collection confirms no updates",
                input_refs=input_refs,
                output_ref="publication:no_updates",
                audit_metadata={"publication_kind": "no_updates"},
            )
        proof = proof_resolver.resolve_review_gate(
            audit_ref=publication.review_audit_ref,
            run_id=publication.run_id,
            claim_id=publication.claim_id,
        )
        if (
            proof is not None
            and proof.claim_snapshot_hash == publication.claim_snapshot_hash
            and proof.evidence_refs == publication.evidence_refs
        ):
            return GateDecision(
                gate_id="publication",
                gate_version="1.0.0",
                decision="pass",
                reason="persisted review proof permits publication",
                input_refs=input_refs,
                output_ref=publication.claim_id,
                audit_metadata={
                    "claim_id": publication.claim_id,
                    "review_audit_ref": publication.review_audit_ref,
                    "reviewer_role": proof.reviewer_role,
                    "review_evaluation_id": proof.evaluation_id,
                },
            )
        if publication.requested_repair is not None:
            return GateDecision(
                gate_id="publication",
                gate_version="1.0.0",
                decision="repair",
                reason="publication proof is missing or invalid",
                input_refs=input_refs,
                repair_action={"action": publication.requested_repair},
            )
        return GateDecision(
            gate_id="publication",
            gate_version="1.0.0",
            decision="block",
            reason="publication proof is missing or invalid",
            input_refs=input_refs,
        )

    return evaluate


def build_default_gate_registry(
    *,
    proof_resolver: ReviewGateProofResolver,
    verifier_execution_resolver: VerifierExecutionProofResolver | None = None,
) -> GateRegistry:
    verifier_execution_resolver = (
        verifier_execution_resolver or DenyVerifierExecutionProofResolver()
    )
    registry = GateRegistry()
    definitions = (
        (
            GateDefinition(
                gate_id="claim_schema",
                version="1.0.0",
                description="Validate the canonical policy impact Claim contract.",
                input_schema=ClaimGateInput,
                hard_constraint=True,
                repairable=False,
                input_ref_extractor=_claim_input_refs,
            ),
            evaluate_claim_schema,
        ),
        (
            GateDefinition(
                gate_id="evidence_binding",
                version="1.0.0",
                description="Bind P0-P4 Claims to typed policy and company evidence.",
                input_schema=ClaimGateInput,
                hard_constraint=True,
                repairable=False,
                input_ref_extractor=_claim_input_refs,
            ),
            evaluate_evidence_binding,
        ),
        (
            GateDefinition(
                gate_id="review_verdict",
                version="1.0.0",
                description="Require a resolved verifier or deterministic evidence review.",
                input_schema=ReviewGateInput,
                hard_constraint=True,
                repairable=True,
                allowed_repair_actions=("retry_verification",),
                input_ref_extractor=_review_input_refs,
            ),
            _review_evaluator(verifier_execution_resolver),
        ),
        (
            GateDefinition(
                gate_id="publication",
                version="1.0.0",
                description="Publish only Claims backed by persisted review audit proof.",
                input_schema=PublicationGateInput,
                hard_constraint=True,
                repairable=True,
                allowed_repair_actions=("downgrade", "retry_verification"),
                input_ref_extractor=_publication_input_refs,
            ),
            _publication_evaluator(proof_resolver),
        ),
    )
    for definition, evaluator in definitions:
        registry.register(definition, evaluator)
    return registry


__all__ = [
    "AgentStepGateDecisionSink",
    "DenyVerifierExecutionProofResolver",
    "ClaimGateInput",
    "InMemoryReviewGateProofResolver",
    "PublicationGateInput",
    "ReviewGateInput",
    "ReviewGateProof",
    "ReviewGateProofResolver",
    "StateGateDecisionSink",
    "VerifierExecutionProofResolver",
    "build_default_gate_registry",
    "canonical_claim_snapshot_hash",
]
