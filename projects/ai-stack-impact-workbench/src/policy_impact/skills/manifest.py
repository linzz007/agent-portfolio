"""Executable contract for one registered skill."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    WithJsonSchema,
    field_serializer,
    field_validator,
)

from policy_impact.runtime.execution_mode import ExecutionMode
from policy_impact.skills.schemas import (
    SkillInput,
    SkillOutput,
    register_schema_model,
    resolve_schema_model,
    schema_model_ref,
)


_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
_SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_MODEL_REF_SCHEMA = {"type": "string", "format": "pydantic-model-ref"}
PydanticModelType = Annotated[type[BaseModel], WithJsonSchema(_MODEL_REF_SCHEMA)]


def _default_input_schema() -> type[BaseModel]:
    return SkillInput


def _default_output_schema() -> type[BaseModel]:
    return SkillOutput


class SkillManifest(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    id: str
    version: str
    name: str
    description: str
    execution_mode: ExecutionMode
    executor_id: str
    input_schema: PydanticModelType = Field(default_factory=_default_input_schema)
    output_schema: PydanticModelType = Field(default_factory=_default_output_schema)
    allowed_tools: tuple[str, ...] = ()
    allowed_subagents: tuple[str, ...] = ()
    context_policy_id: str
    memory_policy_id: str
    permission_policy_id: str
    max_steps: int = Field(gt=0)
    max_model_calls: int = Field(gt=0)
    timeout_seconds: int = Field(gt=0)

    @field_validator(
        "id",
        "version",
        "name",
        "description",
        "executor_id",
        "context_policy_id",
        "memory_policy_id",
        "permission_policy_id",
        mode="before",
    )
    @classmethod
    def strip_required_text(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be blank")
        return stripped

    @field_validator(
        "id",
        "executor_id",
        "context_policy_id",
        "memory_policy_id",
        "permission_policy_id",
    )
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        if not _IDENTIFIER_PATTERN.fullmatch(value):
            raise ValueError("value must be a lowercase dotted identifier")
        return value

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if not _SEMVER_PATTERN.fullmatch(value):
            raise ValueError("version must use semantic version syntax")
        return value

    @field_validator("input_schema", "output_schema", mode="before")
    @classmethod
    def resolve_model_ref(cls, value: object) -> type[BaseModel]:
        return resolve_schema_model(value)

    @field_validator("allowed_tools", "allowed_subagents")
    @classmethod
    def validate_identifier_tuple(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip() for value in values)
        if any(not value or not _IDENTIFIER_PATTERN.fullmatch(value) for value in normalized):
            raise ValueError("entries must be lowercase dotted identifiers")
        if len(normalized) != len(set(normalized)):
            raise ValueError("entries must not contain duplicates")
        return normalized

    @field_serializer("input_schema", "output_schema", when_used="json")
    def serialize_model_ref(self, value: type[BaseModel]) -> str:
        register_schema_model(value)
        return schema_model_ref(value)
