"""Runtime schemas for policy impact artifacts and gates."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator


ImpactLevel = Literal["P0", "P1", "P2", "P3", "P4"]
ImpactType = Literal[
    "opportunity",
    "qualification",
    "compliance",
    "operational_requirement",
    "market_signal",
]
ApplicabilityType = Literal[
    "direct",
    "conditional",
    "not_applicable",
    "insufficient_evidence",
]
BindingEffect = Literal["mandatory", "encouraged", "guidance", "unknown"]
EffectiveStatus = Literal["effective", "not_yet_effective", "unknown"]


class CompanyFact(BaseModel):
    fact_id: str
    source_path: str
    domain: str = ""
    importance: int = Field(default=3, ge=1, le=5)
    confidence: float = Field(default=0.5, ge=0, le=1)
    source: str = ""
    value: str
    policy_relevance: list[str] = Field(default_factory=list)
    text: str = ""


class CompanyContextPack(BaseModel):
    company_id: str
    task: str
    profile: dict[str, Any]
    pinned_facts: list[CompanyFact]
    retrieved_company_facts: list[CompanyFact] = Field(default_factory=list)
    open_questions: list[CompanyFact] = Field(default_factory=list)

    @field_validator("pinned_facts")
    @classmethod
    def require_high_importance_context(cls, value: list[CompanyFact]) -> list[CompanyFact]:
        if not value:
            raise ValueError("pinned_facts cannot be empty")
        return value


class PolicyDocument(BaseModel):
    policy_id: str
    title: str
    issuer: str = ""
    region_scope: list[str] = Field(default_factory=list)
    published_at: str = ""
    source_url: str = ""
    source_level: str = "L4"
    policy_type: list[str] = Field(default_factory=list)
    raw_text_path: str = ""
    text: str


class PolicyChunk(BaseModel):
    chunk_id: str
    doc_id: str
    policy_id: str
    title: str
    text: str
    issuer: str = ""
    published_at: str = ""
    source_url: str = ""


class EvidenceHit(PolicyChunk):
    score: float = 0.0
    compressed_text: str = ""


class PolicyClause(BaseModel):
    clause_id: str
    policy_id: str
    policy_title: str
    clause_type: str
    text: str
    source_url: str = ""


class CompanyEvidence(BaseModel):
    fact_id: str
    source_path: str | None = None
    value: str | None = None
    importance: int = Field(default=3, ge=1, le=5)
    overlap_terms: list[str] = Field(default_factory=list)


class PolicyMatch(BaseModel):
    match_id: str
    policy_id: str
    policy_title: str
    clause_id: str
    clause_type: str
    policy_evidence: dict[str, Any]
    company_evidence: list[CompanyEvidence]

    @field_validator("company_evidence")
    @classmethod
    def require_company_evidence(cls, value: list[CompanyEvidence]) -> list[CompanyEvidence]:
        if not value:
            raise ValueError("company_evidence cannot be empty")
        return value


class PolicyEvidenceSpan(BaseModel):
    span_id: str
    text: str
    source_url: str = ""
    verified: bool = True


class PolicyApplicability(BaseModel):
    policy_id: str
    policy_title: str
    applicability: ApplicabilityType
    binding_effect: BindingEffect = "unknown"
    effective_date: str = ""
    effective_status: EffectiveStatus = "unknown"
    as_of_date: str = ""
    confidence: float = Field(default=0.5, ge=0, le=1)
    scope_summary: str
    trigger_conditions: list[str] = Field(default_factory=list)
    exclusion_conditions: list[str] = Field(default_factory=list)
    matched_company_fact_ids: list[str] = Field(default_factory=list)
    missing_company_facts: list[str] = Field(default_factory=list)
    policy_evidence_spans: list[PolicyEvidenceSpan] = Field(default_factory=list)
    reason_codes: list[str]
    recommended_actions: list[str] = Field(default_factory=list)

    @field_validator("scope_summary")
    @classmethod
    def require_scope_summary(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("scope_summary cannot be empty")
        return value

    @field_validator("reason_codes")
    @classmethod
    def require_reason_codes(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("reason_codes cannot be empty")
        return value


class ImpactAssessment(BaseModel):
    assessment_id: str
    match_id: str
    policy_id: str
    policy_title: str
    impact_level: ImpactLevel
    impact_type: ImpactType
    relevance_score: int = Field(ge=0, le=100)
    reasoning: list[str]
    company_evidence: list[CompanyEvidence]
    policy_evidence: dict[str, Any]
    missing_fields: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    applicability: ApplicabilityType = "conditional"
    binding_effect: BindingEffect = "unknown"
    effective_date: str = ""
    effective_status: EffectiveStatus = "unknown"
    as_of_date: str = ""
    trigger_conditions: list[str] = Field(default_factory=list)
    exclusion_conditions: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)

    @field_validator("reasoning")
    @classmethod
    def require_reasoning(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("reasoning cannot be empty")
        return value


class ReviewResult(BaseModel):
    passed: bool
    downgraded_assessment_ids: list[str] = Field(default_factory=list)
    unsupported_claim_rate: float = Field(default=0.0, ge=0, le=1)
    applicability_violation_ids: list[str] = Field(default_factory=list)
    claim_review_audit_refs: dict[str, str] = Field(default_factory=dict)
    claim_review_details: list[dict[str, Any]] = Field(default_factory=list)


class CorrectionProposal(BaseModel):
    correction_id: str
    company_id: str
    target_file: str
    target_fact_id: str
    old_claim: str
    new_claim: str
    status: Literal["pending", "applied", "rejected"] = "pending"


class ToolCallAudit(BaseModel):
    call_id: str
    stage_name: str
    tool_name: str
    allowed: bool
    started_at: str
    finished_at: str | None = None
    status: str
    error: str | None = None
    argument_keys: list[str] = Field(default_factory=list)
    result_summary: str = ""


class RunArtifactEnvelope(BaseModel):
    run_id: str
    company_id: str
    current_stage: str
    company_context_pack: dict[str, Any]
    policy_documents: list[dict[str, Any]]
    policy_clauses: list[dict[str, Any]]
    impact_assessments: list[dict[str, Any]]
    review_result: dict[str, Any]
    report_paths: dict[str, str]
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    quality_metrics: dict[str, Any] = Field(default_factory=dict)
    errors: list[dict[str, Any]] = Field(default_factory=list)


def validation_errors(model: type[BaseModel], payloads: list[dict[str, Any]]) -> list[str]:
    issues: list[str] = []
    for idx, payload in enumerate(payloads):
        try:
            model.model_validate(payload)
        except ValidationError as exc:
            issues.append(f"{model.__name__}[{idx}]: {exc.errors()}")
    return issues


def validate_one(model: type[BaseModel], payload: dict[str, Any]) -> list[str]:
    try:
        model.model_validate(payload)
        return []
    except ValidationError as exc:
        return [f"{model.__name__}: {exc.errors()}"]
