from pydantic import BaseModel, ConfigDict, Field, model_validator

from policy_impact.runtime.execution_mode import ExecutionMode
from policy_impact.runtime.task_brief import DelegationEvidence


class CitationRef(BaseModel):
    model_config = ConfigDict(extra="allow")

    citation_type: str = ""
    title: str = ""
    url: str = ""
    source_ref: str = ""


class SkillResult(BaseModel):
    answer: str
    actual_execution_mode: ExecutionMode
    stop_reason: str
    citations: list[CitationRef] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)
    memory_proposals: list[str] = Field(default_factory=list)
    gate_decision_refs: list[str] = Field(default_factory=list)
    delegation_evidence: tuple[DelegationEvidence, ...] = ()
    resumable: bool = False

    @model_validator(mode="after")
    def validate_delegation_mode(self) -> "SkillResult":
        delegated = bool(self.delegation_evidence)
        if self.actual_execution_mode is ExecutionMode.SUBAGENT_WORKFLOW and not delegated:
            raise ValueError("subagent_workflow requires successful delegation evidence")
        if delegated and self.actual_execution_mode is not ExecutionMode.SUBAGENT_WORKFLOW:
            raise ValueError("successful delegation evidence requires subagent_workflow")
        return self
