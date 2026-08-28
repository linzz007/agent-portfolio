"""Task-scoped artifact access with no filesystem fallback."""

from __future__ import annotations

from collections.abc import Mapping
from math import isfinite
from typing import Any, Protocol

from policy_impact.runtime.task_brief import deep_freeze_json, deep_thaw_json


class TaskArtifactResolver(Protocol):
    def contains(self, artifact_ref: str) -> bool: ...

    def read(self, artifact_ref: str) -> Any: ...


class InMemoryTaskArtifactResolver:
    """Read only payloads explicitly registered by the trusted caller."""

    def __init__(self, payloads: Mapping[str, Any] | None = None) -> None:
        registered: dict[str, Any] = {}
        for raw_ref, payload in (payloads or {}).items():
            if not isinstance(raw_ref, str) or not raw_ref.strip():
                raise ValueError("artifact payload refs must be nonblank strings")
            artifact_ref = raw_ref.strip()
            if artifact_ref in registered:
                raise ValueError(f"duplicate artifact payload ref: {artifact_ref}")
            _validate_json(payload, path=f"artifact_payloads.{artifact_ref}")
            registered[artifact_ref] = deep_freeze_json(payload)
        self._payloads = deep_freeze_json(registered)

    def contains(self, artifact_ref: str) -> bool:
        return artifact_ref in self._payloads

    def read(self, artifact_ref: str) -> Any:
        if artifact_ref not in self._payloads:
            raise PermissionError(f"artifact ref {artifact_ref!r} is not registered")
        return deep_thaw_json(self._payloads[artifact_ref])


def _validate_json(value: Any, *, path: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError(f"{path} contains a non-finite number")
        return
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError(f"{path} must use string keys")
        for key, item in value.items():
            _validate_json(item, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_json(item, path=f"{path}[{index}]")
        return
    raise ValueError(f"{path} must contain JSON-compatible values")


__all__ = ["InMemoryTaskArtifactResolver", "TaskArtifactResolver"]
