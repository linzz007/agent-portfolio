"""Versioned, immutable role contracts for delegated agents."""

from __future__ import annotations

from collections.abc import Iterable
from types import MappingProxyType

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from policy_impact.runtime.plugin_config import import_ref, read_plugin_json


PositiveStrictInt = Annotated[StrictInt, Field(gt=0)]


class SubagentRoleDefinition(BaseModel):
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        frozen=True,
    )

    role: str
    version: str
    prompt_template_id: str
    prompt_template_version: str
    output_schema_name: str
    output_schema: type[BaseModel]
    allowed_tools: tuple[str, ...]
    max_steps: PositiveStrictInt
    max_model_calls: PositiveStrictInt
    timeout_seconds: PositiveStrictInt
    can_publish: bool = False

    @field_validator(
        "role",
        "version",
        "prompt_template_id",
        "prompt_template_version",
        "output_schema_name",
        mode="before",
    )
    @classmethod
    def validate_required_text(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be blank")
        return stripped

    @field_validator("allowed_tools", mode="after")
    @classmethod
    def validate_allowed_tools(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip() for value in values)
        if any(not value for value in normalized):
            raise ValueError("allowed_tools must not contain blank values")
        if len(normalized) != len(set(normalized)):
            raise ValueError("allowed_tools must not contain duplicates")
        return normalized


class SubagentRoleRegistry:
    """Read-only lookup keyed by exact role and version."""

    __slots__ = ("_definitions", "_ordered")

    def __init__(self, definitions: Iterable[SubagentRoleDefinition]) -> None:
        ordered = tuple(definitions)
        by_key: dict[tuple[str, str], SubagentRoleDefinition] = {}
        for definition in ordered:
            if not isinstance(definition, SubagentRoleDefinition):
                raise TypeError("role definitions must be SubagentRoleDefinition instances")
            key = (definition.role, definition.version)
            if key in by_key:
                raise ValueError(f"duplicate subagent role contract: {key!r}")
            by_key[key] = definition
        self._ordered = ordered
        self._definitions = MappingProxyType(by_key)

    def get(self, role: str, version: str = "1.0.0") -> SubagentRoleDefinition:
        key = (str(role).strip(), str(version).strip())
        try:
            return self._definitions[key]
        except KeyError as exc:
            raise KeyError(f"unknown subagent role contract: {key[0]!r}@{key[1]}") from exc

    def definitions(self) -> tuple[SubagentRoleDefinition, ...]:
        return self._ordered


def load_default_subagent_role_definitions() -> tuple[SubagentRoleDefinition, ...]:
    payload = read_plugin_json("subagents", "roles.json")
    if not isinstance(payload, list):
        raise ValueError("plugins/subagents/roles.json must contain a list")
    definitions = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("subagent role entries must be objects")
        descriptor = dict(item)
        descriptor["output_schema"] = import_ref(str(descriptor.get("output_schema") or ""))
        definitions.append(SubagentRoleDefinition.model_validate(descriptor))
    return tuple(definitions)


def build_default_subagent_role_registry() -> SubagentRoleRegistry:
    return SubagentRoleRegistry(load_default_subagent_role_definitions())


__all__ = [
    "SubagentRoleDefinition",
    "SubagentRoleRegistry",
    "build_default_subagent_role_registry",
    "load_default_subagent_role_definitions",
]
