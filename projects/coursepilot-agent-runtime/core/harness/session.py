"""Session identity for one Agent Harness run."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, Optional


class RunStatus:
    """String constants used by persisted run artifacts."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def _new_run_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return f"run_{stamp}_{uuid.uuid4().hex[:8]}"


@dataclass
class HarnessSession:
    """Stable identity and lifecycle metadata for one agent run."""

    run_id: str
    course_name: str
    mode: str
    skill_id: str
    user_message: str
    started_at: str
    ended_at: Optional[str] = None
    status: str = RunStatus.RUNNING
    trace_id: Optional[str] = None
    request_id: Optional[str] = None

    @classmethod
    def create(
        cls,
        *,
        course_name: str,
        mode: str,
        skill_id: str,
        user_message: str,
        trace_id: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> "HarnessSession":
        return cls(
            run_id=_new_run_id(),
            course_name=course_name,
            mode=mode,
            skill_id=skill_id,
            user_message=user_message,
            started_at=_now_iso(),
            trace_id=trace_id,
            request_id=request_id,
        )

    def complete(self) -> None:
        self.status = RunStatus.SUCCEEDED
        self.ended_at = _now_iso()

    def fail(self) -> None:
        self.status = RunStatus.FAILED
        self.ended_at = _now_iso()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
