from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, Field


class HookDecision(BaseModel):
    action: Literal["allow", "deny", "ask", "patch_context", "emit_event"]
    reason: str = ""
    context_patch: dict[str, Any] = Field(default_factory=dict)
    event_attributes: dict[str, Any] = Field(default_factory=dict)


HookHandler = Callable[[dict[str, Any]], HookDecision]


class HookManager:
    def __init__(self) -> None:
        self._handlers: dict[str, list[HookHandler]] = {}

    def register(self, hook_name: str, handler: HookHandler) -> None:
        self._handlers.setdefault(hook_name, []).append(handler)

    def run(self, hook_name: str, payload: dict[str, Any]) -> tuple[HookDecision, ...]:
        return tuple(handler(payload) for handler in self._handlers.get(hook_name, ()))
