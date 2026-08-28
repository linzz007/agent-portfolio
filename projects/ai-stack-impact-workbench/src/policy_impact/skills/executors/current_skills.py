"""Typed adapters for the current skill implementations."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, ClassVar

from pydantic import BaseModel

from policy_impact.runtime.execution_context import SkillExecutionContext
from policy_impact.runtime.execution_mode import ExecutionMode
from policy_impact.runtime.executor_registry import ExecutionOverride
from policy_impact.runtime.result import SkillResult
from policy_impact.runtime.task_brief import DelegationEvidence


SkillCallable = Callable[[SkillExecutionContext], Mapping[str, Any]]
OfficialReferenceResolverCallable = Callable[[str], Mapping[str, Any] | None]


class _CurrentSkillExecutor:
    execution_mode: ClassVar[ExecutionMode]

    def __init__(self, fn: SkillCallable, output_schema: type[BaseModel]) -> None:
        if not callable(fn):
            raise TypeError("skill executor callable must be callable")
        if not isinstance(output_schema, type) or not issubclass(output_schema, BaseModel):
            raise TypeError("output schema must be a Pydantic model class")
        self._fn = fn
        self._output_schema = output_schema

    def execute(self, context: SkillExecutionContext) -> SkillResult:
        raw = self._fn(context)
        if not isinstance(raw, Mapping):
            raise TypeError("skill executor callable must return a mapping")

        validated = self._output_schema.model_validate(raw)
        artifacts = dict(validated.artifacts)
        if "artifact_refs" in raw:
            artifact_refs = [
                str(value) for value in _read_collection_field(raw, "artifact_refs")
            ]
        else:
            artifact_refs = [str(value) for value in artifacts.values()]

        delegation_evidence = tuple(
            DelegationEvidence.model_validate(value)
            for value in _read_collection_field(raw, "delegation_evidence")
        )
        if any(
            evidence.parent_run_id != context.run_id
            for evidence in delegation_evidence
        ):
            raise ValueError("delegation evidence parent_run_id must match execution run_id")
        base_execution_mode = _coerce_execution_mode(
            raw.get("actual_execution_mode"),
            self.execution_mode,
        )
        actual_execution_mode = (
            ExecutionMode.SUBAGENT_WORKFLOW
            if delegation_evidence
            else ExecutionMode.WORKFLOW
            if base_execution_mode is ExecutionMode.SUBAGENT_WORKFLOW
            else base_execution_mode
        )

        return SkillResult(
            answer=validated.answer,
            actual_execution_mode=actual_execution_mode,
            stop_reason=str(raw.get("stop_reason") or "success"),
            citations=_normalize_citations(validated.citations),
            artifacts=artifacts,
            artifact_refs=artifact_refs,
            memory_proposals=_read_collection_field(raw, "memory_proposals"),
            gate_decision_refs=_read_collection_field(raw, "gate_decision_refs"),
            delegation_evidence=delegation_evidence,
            resumable=bool(raw.get("resumable", False)),
        )


class GeneralChatExecutor(_CurrentSkillExecutor):
    execution_mode = ExecutionMode.MODEL_ONCE


class PolicyWeeklyImpactExecutor(_CurrentSkillExecutor):
    execution_mode = ExecutionMode.SUBAGENT_WORKFLOW


class ExternalImpactReportExecutor(_CurrentSkillExecutor):
    execution_mode = ExecutionMode.SUBAGENT_WORKFLOW


class RecentNewsReportExecutor(_CurrentSkillExecutor):
    execution_mode = ExecutionMode.WORKFLOW


class ResearchReportExecutor(_CurrentSkillExecutor):
    execution_mode = ExecutionMode.MODEL_ONCE


class CompanyWikiBlueprintExecutor(_CurrentSkillExecutor):
    execution_mode = ExecutionMode.DETERMINISTIC


class OfficialLegalReferenceExecutor(_CurrentSkillExecutor):
    execution_mode = ExecutionMode.DETERMINISTIC


class OfficialLegalReferenceOverrideResolver:
    def __init__(self, fn: OfficialReferenceResolverCallable) -> None:
        if not callable(fn):
            raise TypeError("official reference resolver must be callable")
        self._fn = fn

    def resolve(self, message: str) -> ExecutionOverride | None:
        legal_reference = self._fn(message)
        if legal_reference is None:
            return None
        if not isinstance(legal_reference, Mapping):
            raise TypeError("official reference resolver must return a mapping or None")
        return ExecutionOverride(
            executor_id="official_legal_reference",
            services={"legal_reference": dict(legal_reference)},
        )


def _read_collection_field(raw: Mapping[str, Any], field_name: str) -> list[Any]:
    if field_name not in raw:
        return []
    value = raw[field_name]
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"{field_name} must be a list or tuple")
    return list(value)


def _coerce_execution_mode(value: Any, fallback: ExecutionMode) -> ExecutionMode:
    if value is None or value == "":
        return fallback
    try:
        return ExecutionMode(str(value))
    except ValueError:
        return fallback


def _normalize_citations(citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_citations = []
    for citation in citations:
        normalized = dict(citation)
        legacy_type = normalized.pop("type", "")
        normalized["citation_type"] = str(
            normalized.get("citation_type") or legacy_type or ""
        )
        normalized_citations.append(normalized)
    return normalized_citations
