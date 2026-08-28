from collections.abc import Mapping
from enum import Enum
from types import MappingProxyType


class RunStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    RESUMING = "resuming"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED_RECOVERABLE = "interrupted_recoverable"
    INTERRUPTED_FAILED = "interrupted_failed"


LEGAL_RUN_TRANSITIONS: Mapping[RunStatus, frozenset[RunStatus]] = MappingProxyType({
    RunStatus.CREATED: frozenset(
        {
            RunStatus.RUNNING,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.INTERRUPTED_FAILED,
        }
    ),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.AWAITING_APPROVAL,
            RunStatus.DONE,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.INTERRUPTED_RECOVERABLE,
            RunStatus.INTERRUPTED_FAILED,
        }
    ),
    RunStatus.AWAITING_APPROVAL: frozenset(
        {RunStatus.RESUMING, RunStatus.CANCELLED, RunStatus.FAILED}
    ),
    RunStatus.RESUMING: frozenset(
        {
            RunStatus.RUNNING,
            RunStatus.AWAITING_APPROVAL,
            RunStatus.DONE,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.INTERRUPTED_RECOVERABLE,
            RunStatus.INTERRUPTED_FAILED,
        }
    ),
    RunStatus.INTERRUPTED_RECOVERABLE: frozenset(
        {RunStatus.RESUMING, RunStatus.CANCELLED, RunStatus.FAILED}
    ),
    RunStatus.DONE: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
    RunStatus.INTERRUPTED_FAILED: frozenset(),
})


def require_run_transition(current: RunStatus, target: RunStatus) -> None:
    if target not in LEGAL_RUN_TRANSITIONS[current]:
        raise ValueError(f"illegal Run transition: {current.value} -> {target.value}")
