"""Structured action contracts for the controlled agent loop."""

from __future__ import annotations

from math import isfinite
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator


ALLOWED_ACTION_NAMES = ("final", "tool_call", "delegate", "request_approval")


def _validate_json_value(value: Any, *, path: str = "value") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError(f"{path} must contain finite JSON numbers")
        return
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValueError(f"{path} must use string keys")
        for key, item in value.items():
            _validate_json_value(item, path=f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, path=f"{path}[{index}]")
        return
    raise ValueError(f"{path} must contain JSON-compatible values")


class _ActionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FinalAction(_ActionModel):
    type: Literal["final"]
    output: dict[str, Any]

    @field_validator("output")
    @classmethod
    def validate_output(cls, value: dict[str, Any]) -> dict[str, Any]:
        _validate_json_value(value, path="output")
        return value


class ToolCallAction(_ActionModel):
    type: Literal["tool_call"]
    tool_name: str
    arguments: dict[str, Any]

    @field_validator("tool_name", mode="before")
    @classmethod
    def validate_tool_name(cls, value: object) -> object:
        return _nonblank_text(value, field_name="tool_name")

    @field_validator("arguments")
    @classmethod
    def validate_arguments(cls, value: dict[str, Any]) -> dict[str, Any]:
        _validate_json_value(value, path="arguments")
        return value


class DelegateAction(_ActionModel):
    type: Literal["delegate"]
    role: str
    task: str
    input_artifact_refs: tuple[str, ...] = ()

    @field_validator("role", "task", mode="before")
    @classmethod
    def validate_required_text(cls, value: object, info: Any) -> object:
        return _nonblank_text(value, field_name=info.field_name)

    @field_validator("input_artifact_refs", mode="after")
    @classmethod
    def validate_artifact_refs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(
            _nonblank_text(value, field_name="input_artifact_refs") for value in values
        )


class RequestApprovalAction(_ActionModel):
    type: Literal["request_approval"]
    tool_name: str
    arguments: dict[str, Any]
    reason: str

    @field_validator("tool_name", "reason", mode="before")
    @classmethod
    def validate_required_text(cls, value: object, info: Any) -> object:
        return _nonblank_text(value, field_name=info.field_name)

    @field_validator("arguments")
    @classmethod
    def validate_arguments(cls, value: dict[str, Any]) -> dict[str, Any]:
        _validate_json_value(value, path="arguments")
        return value


def _nonblank_text(value: object, *, field_name: str) -> object:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} must not be blank")
    return stripped


AgentAction = Annotated[
    Union[FinalAction, ToolCallAction, DelegateAction, RequestApprovalAction],
    Field(discriminator="type"),
]

StopReason = Literal[
    "success",
    "deterministic_complete",
    "max_steps",
    "max_model_calls",
    "timeout",
    "model_error",
    "invalid_model_action",
    "tool_denied",
    "tool_failed",
    "tool_timeout",
    "unknown_outcome",
    "invalid_tool_output",
    "repeated_tool_failure",
    "context_budget_exceeded",
    "integrity_error",
    "persistence_error",
    "awaiting_approval",
    "gate_blocked",
    "cancelled",
    "delegate_failed",
]


__all__ = [
    "ALLOWED_ACTION_NAMES",
    "AgentAction",
    "DelegateAction",
    "FinalAction",
    "RequestApprovalAction",
    "StopReason",
    "ToolCallAction",
]
