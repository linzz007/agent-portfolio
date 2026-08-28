"""Immutable contracts for isolated subagent delegation."""

from __future__ import annotations

from collections.abc import Mapping
from math import isfinite
from types import MappingProxyType
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_serializer,
    field_validator,
)

from policy_impact.runtime.actions import StopReason


_HIDDEN_REASONING_KEYS = frozenset({"analysis", "thinking", "reasoning"})
PositiveStrictInt = Annotated[StrictInt, Field(gt=0)]


class _FrozenContract(BaseModel):
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        frozen=True,
    )


class TaskBrief(_FrozenContract):
    task_id: str
    parent_run_id: str
    parent_span_id: str
    role: str
    task: str
    input_artifact_refs: tuple[str, ...] = ()
    output_schema_name: str
    max_steps: PositiveStrictInt
    max_model_calls: PositiveStrictInt
    timeout_seconds: PositiveStrictInt

    @field_validator(
        "task_id",
        "parent_run_id",
        "parent_span_id",
        "role",
        "task",
        "output_schema_name",
        mode="before",
    )
    @classmethod
    def validate_required_text(cls, value: object) -> object:
        return _nonblank_text(value)

    @field_validator("input_artifact_refs", mode="after")
    @classmethod
    def validate_artifact_refs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _normalize_refs(values, "input_artifact_refs")


class DelegationEvidence(_FrozenContract):
    """Public proof that a child span completed successfully."""

    task_id: str
    parent_run_id: str
    role: str
    role_version: str = "1.0.0"
    parent_span_id: str
    span_id: str
    context_manifest_id: str
    artifact_refs: tuple[str, ...] = ()

    @field_validator(
        "task_id",
        "parent_run_id",
        "role",
        "role_version",
        "parent_span_id",
        "span_id",
        "context_manifest_id",
        mode="before",
    )
    @classmethod
    def validate_required_text(cls, value: object) -> object:
        return _nonblank_text(value)

    @field_validator("artifact_refs", mode="after")
    @classmethod
    def validate_artifact_refs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _normalize_refs(values, "artifact_refs")


class SubagentResult(_FrozenContract):
    task_id: str
    parent_run_id: str
    role: str
    role_version: str = "1.0.0"
    output: Mapping[str, Any]
    stop_reason: StopReason
    context_manifest_id: str
    parent_span_id: str
    span_id: str
    artifact_refs: tuple[str, ...] = ()
    verifier_gate_audit_refs: tuple[str, ...] = ()

    @field_validator(
        "task_id",
        "parent_run_id",
        "role",
        "role_version",
        "context_manifest_id",
        "parent_span_id",
        "span_id",
        mode="before",
    )
    @classmethod
    def validate_required_text(cls, value: object) -> object:
        return _nonblank_text(value)

    @field_validator("output", mode="before")
    @classmethod
    def validate_public_output(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            raise ValueError("output must be a mapping")
        _validate_json_value(value, path="output")
        _reject_hidden_reasoning(value, path="output")
        return value

    @field_validator("output", mode="after")
    @classmethod
    def freeze_public_output(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        return deep_freeze_json(value)

    @field_serializer("output")
    def serialize_output(self, value: Mapping[str, Any]) -> dict[str, Any]:
        thawed = deep_thaw_json(value)
        if not isinstance(thawed, dict):  # pragma: no cover - guarded by field type
            raise TypeError("output must serialize as an object")
        return thawed

    @field_validator("artifact_refs", "verifier_gate_audit_refs", mode="after")
    @classmethod
    def validate_result_refs(cls, values: tuple[str, ...], info) -> tuple[str, ...]:
        return _normalize_refs(values, info.field_name)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def evidence(self) -> DelegationEvidence:
        if self.stop_reason != "success":
            raise ValueError("only a successful subagent result is delegation evidence")
        return DelegationEvidence(
            task_id=self.task_id,
            parent_run_id=self.parent_run_id,
            role=self.role,
            role_version=self.role_version,
            parent_span_id=self.parent_span_id,
            span_id=self.span_id,
            context_manifest_id=self.context_manifest_id,
            artifact_refs=self.artifact_refs,
        )


def _nonblank_text(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("value must be a string")
    stripped = value.strip()
    if not stripped:
        raise ValueError("value must not be blank")
    return stripped


def _normalize_refs(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    normalized = tuple(_nonblank_text(value) for value in values)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return normalized


def _validate_json_value(value: Any, *, path: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError(f"{path} must contain finite JSON numbers")
        return
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError(f"{path} must use string keys")
        for key, item in value.items():
            _validate_json_value(item, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_json_value(item, path=f"{path}[{index}]")
        return
    raise ValueError(f"{path} must contain JSON-compatible values")


def _reject_hidden_reasoning(value: Any, *, path: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = "".join(
                character for character in key.casefold() if character.isalnum()
            )
            if (
                normalized in {"analysis", "thinking", "chainofthought"}
                or "reasoning" in normalized
            ):
                raise ValueError(f"{path}.{key} contains hidden reasoning")
            _reject_hidden_reasoning(item, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_hidden_reasoning(item, path=f"{path}[{index}]")


def deep_freeze_json(value: Any) -> Any:
    """Return a recursively immutable representation of a validated JSON value."""

    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: deep_freeze_json(value[key]) for key in sorted(value)}
        )
    if isinstance(value, (list, tuple)):
        return tuple(deep_freeze_json(item) for item in value)
    return value


def deep_thaw_json(value: Any) -> Any:
    """Return a JSON-serializable copy of a frozen JSON value."""

    if isinstance(value, Mapping):
        return {key: deep_thaw_json(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [deep_thaw_json(item) for item in value]
    return value


__all__ = [
    "DelegationEvidence",
    "PositiveStrictInt",
    "SubagentResult",
    "TaskBrief",
    "deep_freeze_json",
    "deep_thaw_json",
]
