from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from policy_impact.runtime.result import SkillResult


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_value(item) for item in value)
    if isinstance(value, bytearray):
        return bytes(value)
    return value


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("execution context values must be mappings")
    return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})


@dataclass(frozen=True)
class SkillExecutionContext:
    run_id: str
    session_id: str
    company_id: str
    message: str
    model_id: str
    context_manifest: Mapping[str, Any]
    prepared_context: Mapping[str, Any]
    services: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "context_manifest", _freeze_mapping(self.context_manifest))
        object.__setattr__(self, "prepared_context", _freeze_mapping(self.prepared_context))
        object.__setattr__(self, "services", _freeze_mapping(self.services))


@runtime_checkable
class SkillExecutor(Protocol):
    def execute(self, context: SkillExecutionContext) -> SkillResult:
        raise NotImplementedError
