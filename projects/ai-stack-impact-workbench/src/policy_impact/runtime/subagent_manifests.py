"""Manifest contracts for subagents exposed to the main Agent loop.

These manifests are the user-facing delegation surface: the main agent sees a
small roster built from them, while the runtime still enforces the underlying
role contract, tool boundary, and output schema.
"""

from __future__ import annotations

from collections.abc import Iterable
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from policy_impact.runtime.plugin_config import read_plugin_json
from policy_impact.runtime.subagent_roles import SubagentRoleRegistry


PositiveStrictInt = Annotated[StrictInt, Field(gt=0)]


class SubagentManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    role: str
    description: str
    use_when: tuple[str, ...]
    avoid_when: tuple[str, ...] = ()
    model: str = "inherit"
    reasoning_effort: Literal["low", "medium", "high"] = "medium"
    tools: tuple[str, ...]
    disallowed_tools: tuple[str, ...] = ()
    output_schema_name: str
    max_turns: PositiveStrictInt
    max_model_calls: PositiveStrictInt
    timeout_seconds: PositiveStrictInt
    context_policy_id: str
    memory_scope: Literal["none", "read_only"] = "read_only"
    can_publish: bool = False

    @field_validator(
        "name",
        "role",
        "description",
        "model",
        "output_schema_name",
        "context_policy_id",
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

    @field_validator("use_when", mode="after")
    @classmethod
    def validate_use_when(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _normalize_unique_text_tuple(values, field_name="use_when", allow_empty=False)

    @field_validator("avoid_when", "tools", "disallowed_tools", mode="after")
    @classmethod
    def validate_tuple_fields(cls, values: tuple[str, ...], info) -> tuple[str, ...]:
        return _normalize_unique_text_tuple(
            values,
            field_name=str(info.field_name),
            allow_empty=info.field_name != "tools",
        )

    def roster_item(self, *, caller_allowed_tools: tuple[str, ...]) -> dict[str, object]:
        caller_tools = set(caller_allowed_tools)
        visible_tools = tuple(tool for tool in self.tools if tool in caller_tools)
        return {
            "name": self.name,
            "role": self.role,
            "description": self.description,
            "use_when": list(self.use_when),
            "avoid_when": list(self.avoid_when),
            "tools": list(visible_tools),
            "disallowed_tools": list(self.disallowed_tools),
            "output_schema_name": self.output_schema_name,
            "max_turns": self.max_turns,
            "max_model_calls": self.max_model_calls,
            "timeout_seconds": self.timeout_seconds,
            "context_policy_id": self.context_policy_id,
            "memory_scope": self.memory_scope,
            "can_publish": self.can_publish,
        }


class SubagentManifestRegistry:
    __slots__ = ("_by_role", "_ordered")

    def __init__(self, manifests: Iterable[SubagentManifest]) -> None:
        ordered = tuple(manifests)
        by_role: dict[str, SubagentManifest] = {}
        for manifest in ordered:
            if not isinstance(manifest, SubagentManifest):
                raise TypeError("manifests must be SubagentManifest instances")
            if manifest.role in by_role:
                raise ValueError(f"duplicate subagent manifest role: {manifest.role!r}")
            by_role[manifest.role] = manifest
        self._ordered = ordered
        self._by_role = MappingProxyType(by_role)

    def get(self, role: str) -> SubagentManifest:
        key = str(role or "").strip()
        try:
            return self._by_role[key]
        except KeyError as exc:
            raise KeyError(f"unknown subagent manifest role: {key!r}") from exc

    def for_roles(self, roles: Iterable[str]) -> tuple[SubagentManifest, ...]:
        result = []
        for role in roles:
            try:
                result.append(self.get(role))
            except KeyError:
                continue
        return tuple(result)

    def manifests(self) -> tuple[SubagentManifest, ...]:
        return self._ordered


def build_default_subagent_manifest_registry(
    role_registry: SubagentRoleRegistry,
) -> SubagentManifestRegistry:
    payload = read_plugin_json("subagents", "manifests.json")
    if not isinstance(payload, list):
        raise ValueError("plugins/subagents/manifests.json must contain a list")
    manifests = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("subagent manifest entries must be objects")
        descriptor = dict(item)
        role = role_registry.get(str(descriptor.get("role") or ""), "1.0.0")
        descriptor.update(
            {
                "tools": role.allowed_tools,
                "output_schema_name": role.output_schema_name,
                "max_turns": role.max_steps,
                "max_model_calls": role.max_model_calls,
                "timeout_seconds": role.timeout_seconds,
                "can_publish": role.can_publish,
            }
        )
        manifests.append(SubagentManifest.model_validate(descriptor))
    return SubagentManifestRegistry(manifests)


def _normalize_unique_text_tuple(
    values: tuple[str, ...],
    *,
    field_name: str,
    allow_empty: bool,
) -> tuple[str, ...]:
    normalized = tuple(str(value).strip() for value in values)
    if any(not value for value in normalized):
        raise ValueError(f"{field_name} must not contain blank values")
    if not allow_empty and not normalized:
        raise ValueError(f"{field_name} must not be empty")
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return normalized


__all__ = [
    "SubagentManifest",
    "SubagentManifestRegistry",
    "build_default_subagent_manifest_registry",
]
