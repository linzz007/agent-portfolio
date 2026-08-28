"""Lifecycle hook bus for harness governance extensions."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from core.metrics import add_event
from core.harness.session import HarnessSession


HookHandler = Callable[["HookEvent"], None]


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


@dataclass
class HookEvent:
    name: str
    run_id: str
    ts: str
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class LifecycleHooks:
    """Small hook registry; handler errors are recorded but never break the run."""

    def __init__(self, handlers: Optional[List[HookHandler]] = None):
        self._handlers: List[HookHandler] = list(handlers or [])
        self.logger = logging.getLogger("harness.hooks")

    def register(self, handler: HookHandler) -> None:
        self._handlers.append(handler)

    def emit(
        self,
        name: str,
        session: HarnessSession,
        payload: Optional[Dict[str, Any]] = None,
    ) -> HookEvent:
        event = HookEvent(
            name=name,
            run_id=session.run_id,
            ts=_now_iso(),
            payload=dict(payload or {}),
        )
        for handler in list(self._handlers):
            try:
                handler(event)
            except Exception as exc:
                self.logger.warning(
                    "[harness.hook] handler_failed hook=%s run_id=%s err=%s",
                    name,
                    session.run_id,
                    str(exc),
                )
                add_event(
                    "harness_hook_error",
                    hook_name=name,
                    run_id=session.run_id,
                    error=str(exc),
                )
        return event


class MetricsHook:
    """Mirror lifecycle hook events into the existing metrics trace."""

    def __call__(self, event: HookEvent) -> None:
        add_event(
            "harness_hook",
            hook_name=event.name,
            run_id=event.run_id,
            **event.payload,
        )
