"""Fail-closed registry for runtime skill executors."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import re
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from policy_impact.runtime.execution_context import SkillExecutor


_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")


def _require_identifier(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"invalid {label}: {value!r}")
    if not value:
        raise ValueError(f"empty {label}: {value!r}")
    if not _IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(f"invalid {label}: {value!r}")
    return value


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


def _freeze_services(services: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(services, Mapping):
        raise TypeError("execution override services must be a mapping")
    return MappingProxyType({key: _freeze_value(value) for key, value in services.items()})


@dataclass(frozen=True)
class ExecutionOverride:
    executor_id: str
    services: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_identifier(self.executor_id, "executor id")
        object.__setattr__(self, "services", _freeze_services(self.services))


@runtime_checkable
class ExecutionOverrideResolver(Protocol):
    def resolve(self, message: str) -> ExecutionOverride | None:
        raise NotImplementedError


class ExecutionOverrideRegistry:
    def __init__(self) -> None:
        self._resolvers: dict[str, ExecutionOverrideResolver] = {}

    def register(self, resolver_id: str, resolver: ExecutionOverrideResolver) -> None:
        _require_identifier(resolver_id, "override resolver id")
        if resolver_id in self._resolvers:
            raise ValueError(f"duplicate override resolver id: {resolver_id!r}")
        if not isinstance(resolver, ExecutionOverrideResolver) or not callable(resolver.resolve):
            raise TypeError("resolver must satisfy ExecutionOverrideResolver")
        self._resolvers[resolver_id] = resolver

    def resolve(self, message: str) -> ExecutionOverride | None:
        for resolver in self._resolvers.values():
            override = resolver.resolve(message)
            if override is None:
                continue
            if not isinstance(override, ExecutionOverride):
                raise TypeError("override resolver must return ExecutionOverride or None")
            return override
        return None


class ExecutorRegistry:
    def __init__(self) -> None:
        self._executors: dict[str, SkillExecutor] = {}

    def register(self, executor_id: str, executor: SkillExecutor) -> None:
        _require_identifier(executor_id, "executor id")
        if executor_id in self._executors:
            raise ValueError(f"duplicate executor id: {executor_id!r}")
        if not isinstance(executor, SkillExecutor) or not callable(executor.execute):
            raise TypeError("executor must satisfy SkillExecutor")
        self._executors[executor_id] = executor

    def get(self, executor_id: str) -> SkillExecutor:
        _require_identifier(executor_id, "executor id")
        try:
            return self._executors[executor_id]
        except KeyError as exc:
            raise KeyError(f"unknown executor: {executor_id}") from exc
