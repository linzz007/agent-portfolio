"""Turn orchestration and process-local Run execution ownership."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import threading
from types import MappingProxyType
from typing import Any
from uuid import uuid4

from policy_impact.memory.store import PolicyMemoryStore
from policy_impact.runtime.execution_context import SkillExecutionContext
from policy_impact.runtime.executor_registry import (
    ExecutionOverride,
    ExecutionOverrideRegistry,
    ExecutorRegistry,
)
from policy_impact.runtime.hooks import HookDecision, HookManager
from policy_impact.runtime.result import CitationRef, SkillResult
from policy_impact.runtime.run_status import RunStatus
from policy_impact.skills.manifest import SkillManifest
from policy_impact.skills.registry import SkillRegistry


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
        raise TypeError("PreparedTurn mappings must be mappings")
    return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_json_value(item) for item in value), key=repr)
    if isinstance(value, bytes):
        return value.hex()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"prepared execution plan contains non-JSON value: {type(value).__name__}")


def _fingerprint(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        _json_value(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PreparedTurn:
    run_id: str
    session_id: str
    company_id: str
    user_message_id: str
    message: str
    model_id: str
    mode: str
    manifest: SkillManifest


class PreparedTurnIntegrityError(ValueError):
    """Raised before claim when a PreparedTurn no longer matches its persisted plan."""


@dataclass(frozen=True)
class _PersistedExecutionPlan:
    manifest: SkillManifest
    executor_id: str
    services: Mapping[str, Any]
    requested_skill_id: str
    context_patch: Mapping[str, Any]
    before_turn_decisions: tuple[Mapping[str, Any], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "services", _freeze_mapping(self.services))
        object.__setattr__(self, "context_patch", _freeze_mapping(self.context_patch))
        object.__setattr__(
            self,
            "before_turn_decisions",
            tuple(_freeze_mapping(decision) for decision in self.before_turn_decisions),
        )


@dataclass(frozen=True)
class TurnOperations:
    validate_request: Callable[[str, str, str, str, str], None]
    parse_slash_command: Callable[[str], tuple[str | None, str]]
    normalize_requested_skill_id: Callable[[str, str | None, str], str]
    assign_first_turn_title: Callable[[str, str], None]
    add_intent_step: Callable[[str, str, str, SkillManifest], None]
    add_skill_selection_step: Callable[[str, SkillManifest, str], None]
    build_context: Callable[
        [str, str, str, SkillManifest, str],
        tuple[dict[str, Any], dict[str, Any]],
    ]
    after_skill_result: Callable[
        [str, str, SkillManifest, SkillResult],
        list[dict[str, Any]] | None,
    ]
    public_citations: Callable[[list[CitationRef]], list[dict[str, Any]]]


class RunExecutionConflict(RuntimeError):
    """Raised when a Run already has an active process-local executor."""

    def __init__(self, run_id: str, owner: str | None) -> None:
        owner_text = owner or "unknown owner"
        super().__init__(f"Run {run_id!r} is already executing under {owner_text}")
        self.run_id = run_id
        self.owner_identity = owner


@dataclass
class _RunExecutionLock:
    lock: threading.RLock = field(default_factory=threading.RLock)
    owner_identity: str | None = None
    owner_thread_id: int | None = None
    waiter_count: int = 0


class RunExecutionLockRegistry:
    """Own one process-local execution lock and owner record per Run."""

    def __init__(self) -> None:
        self._guard = threading.Condition()
        self._locks: dict[str, _RunExecutionLock] = {}

    @contextmanager
    def hold(self, run_id: str, blocking: bool = False) -> Iterator[None]:
        run_id = str(run_id or "").strip()
        if not run_id:
            raise ValueError("run_id is required")
        identity = self._current_owner_identity()
        thread_id = threading.get_ident()
        with self._guard:
            entry = self._locks.setdefault(run_id, _RunExecutionLock())
            if blocking:
                while entry.owner_identity is not None:
                    if entry.owner_thread_id == thread_id:
                        raise RunExecutionConflict(run_id, entry.owner_identity)
                    entry.waiter_count += 1
                    try:
                        self._guard.wait()
                    finally:
                        entry.waiter_count -= 1
            elif entry.owner_identity is not None:
                raise RunExecutionConflict(run_id, entry.owner_identity)
            acquired = entry.lock.acquire(blocking=False)
            if not acquired:
                raise RunExecutionConflict(run_id, entry.owner_identity)
            entry.owner_identity = identity
            entry.owner_thread_id = thread_id

        try:
            yield
        finally:
            with self._guard:
                if entry.owner_identity != identity:
                    raise RunExecutionConflict(run_id, entry.owner_identity)
                entry.owner_identity = None
                entry.owner_thread_id = None
                entry.lock.release()
                if entry.waiter_count == 0 and self._locks.get(run_id) is entry:
                    self._locks.pop(run_id, None)
                self._guard.notify_all()

    def owner(self, run_id: str) -> str | None:
        with self._guard:
            entry = self._locks.get(str(run_id))
            return entry.owner_identity if entry else None

    def tracked_run_count(self) -> int:
        with self._guard:
            return len(self._locks)

    @staticmethod
    def _current_owner_identity() -> str:
        thread = threading.current_thread()
        identity = f"thread:{thread.ident}:{thread.name}"
        try:
            task = asyncio.current_task()
        except RuntimeError:
            task = None
        if task is not None:
            identity += f":task:{id(task)}"
        return identity


class TurnCoordinator:
    """Coordinate one validated user turn through truthful Run terminalization."""

    def __init__(
        self,
        *,
        company_id: str,
        store: PolicyMemoryStore,
        registry: SkillRegistry,
        executor_registry: ExecutorRegistry,
        execution_override_registry: ExecutionOverrideRegistry,
        operations: TurnOperations,
        hooks: HookManager | None = None,
        run_locks: RunExecutionLockRegistry | None = None,
    ) -> None:
        self.company_id = company_id
        self.store = store
        self.registry = registry
        self.executor_registry = executor_registry
        self.execution_override_registry = execution_override_registry
        self.operations = operations
        self.hooks = hooks or HookManager()
        self.run_locks = run_locks or RunExecutionLockRegistry()

    def run_turn(
        self,
        session_id: str,
        message: str,
        mode: str,
        model_id: str,
        active_skill_id: str = "",
    ) -> dict[str, Any]:
        prepared = self.prepare_turn(
            session_id=session_id,
            message=message,
            mode=mode,
            model_id=model_id,
            active_skill_id=active_skill_id,
        )
        return self.execute_prepared_turn(prepared)

    def prepare_turn(
        self,
        session_id: str,
        message: str,
        mode: str,
        model_id: str,
        active_skill_id: str = "",
    ) -> PreparedTurn:
        self._validate_request(session_id, message, mode, model_id, active_skill_id)
        context_patch, before_turn_decisions = self._run_before_turn(
            session_id=session_id,
            message=message,
            mode=mode,
            model_id=model_id,
            active_skill_id=active_skill_id,
        )

        inline_skill_id, task_message = self.operations.parse_slash_command(message)
        requested_skill_id = active_skill_id if inline_skill_id is None else inline_skill_id
        requested_skill_id = self.operations.normalize_requested_skill_id(
            requested_skill_id,
            inline_skill_id,
            task_message,
        )
        execution_override = self.execution_override_registry.resolve(task_message)
        manifest = self.registry.resolve_skill(requested_skill_id or None, mode, task_message)
        execution = execution_override or ExecutionOverride(executor_id=manifest.executor_id)

        self.operations.assign_first_turn_title(session_id, task_message)
        user_message_id = self.store.save_chat_message(
            session_id=session_id,
            role="user",
            content=message,
            metadata={
                "mode": mode,
                "model_id": model_id,
                "active_skill_id": requested_skill_id,
                "inline_skill_command": inline_skill_id is not None,
            },
        )
        run_id = uuid4().hex
        execution_plan = {
            "executor_id": execution.executor_id,
            "services": _json_value(execution.services),
            "requested_skill_id": requested_skill_id,
            "before_turn_context_patch": _json_value(context_patch),
            "before_turn_decisions": _json_value(before_turn_decisions),
        }
        prepared_record = {
            "schema_version": "runtime.prepared_turn.v1",
            "run_id": run_id,
            "session_id": session_id,
            "company_id": self.company_id,
            "user_message_id": user_message_id,
            "user_message": message,
            "task_message": task_message,
            "model_id": model_id,
            "mode": mode,
            "manifest": {"id": manifest.id, "version": manifest.version},
            "execution_plan": execution_plan,
        }
        prepared_fingerprint = _fingerprint(prepared_record)
        self.store.create_agent_run(
            session_id=session_id,
            user_message_id=user_message_id,
            mode=mode,
            model_id=model_id,
            skill_id=manifest.id,
            metadata={
                "planned_execution_mode": manifest.execution_mode.value,
                "runtime": "agent_workbench",
                "prepared_turn": prepared_record,
                "prepared_turn_fingerprint": prepared_fingerprint,
            },
            status=RunStatus.CREATED,
            run_id=run_id,
        )
        return PreparedTurn(
            run_id=run_id,
            session_id=session_id,
            company_id=self.company_id,
            user_message_id=user_message_id,
            message=task_message,
            model_id=model_id,
            mode=mode,
            manifest=manifest,
        )

    def execute_prepared_turn(self, prepared: PreparedTurn) -> dict[str, Any]:
        if not isinstance(prepared, PreparedTurn):
            raise TypeError("prepared must be a PreparedTurn")

        with self.run_locks.hold(prepared.run_id, blocking=False):
            execution_plan = self._validate_prepared_turn(prepared)
            if not self.store.claim_agent_run(prepared.run_id):
                run = self.store.get_agent_run(prepared.run_id)
                owner = f"persisted status {run['status']!r}" if run else "missing Run"
                raise RunExecutionConflict(prepared.run_id, owner)
            return self._execute_locked(prepared, execution_plan)

    def _execute_locked(
        self,
        prepared: PreparedTurn,
        execution_plan: _PersistedExecutionPlan,
    ) -> dict[str, Any]:
        result: SkillResult | None = None
        artifacts: dict[str, str] = {}
        artifact_refs: list[str] = []
        citations: list[dict[str, Any]] = []
        assistant_message_id = ""
        failure: BaseException | None = None
        failure_stage = "lifecycle"
        candidate_status = RunStatus.FAILED
        stop_reason = "executor_error"

        try:
            failure_stage = "intent"
            self.operations.add_intent_step(
                prepared.run_id,
                prepared.message,
                prepared.mode,
                execution_plan.manifest,
            )
            self.operations.add_skill_selection_step(
                prepared.run_id,
                execution_plan.manifest,
                execution_plan.requested_skill_id,
            )
            failure_stage = "context"
            context_manifest, prepared_context = self.operations.build_context(
                prepared.run_id,
                prepared.session_id,
                prepared.message,
                execution_plan.manifest,
                prepared.model_id,
            )
            if execution_plan.context_patch:
                prepared_context = {
                    **prepared_context,
                    **dict(execution_plan.context_patch),
                }
            context_manifest = {
                **context_manifest,
                "before_turn": {
                    "context_patch_keys": sorted(execution_plan.context_patch),
                    "context_patch_sha256": _fingerprint(execution_plan.context_patch),
                    "decisions": _json_value(execution_plan.before_turn_decisions),
                },
            }
            self.store.add_agent_step(
                prepared.run_id,
                step_type="context_build",
                title="构建上下文清单",
                status="done",
                input_payload={
                    "message": prepared.message,
                    "skill_id": execution_plan.manifest.id,
                },
                output_payload={"context_manifest": context_manifest},
                metadata={
                    "token_budget": context_manifest.get("token_budget", {}),
                    "loop_phase": "think",
                },
            )
            failure_stage = "executor"
            context = SkillExecutionContext(
                run_id=prepared.run_id,
                session_id=prepared.session_id,
                company_id=prepared.company_id,
                message=prepared.message,
                model_id=prepared.model_id,
                context_manifest=context_manifest,
                prepared_context=prepared_context,
                services=execution_plan.services,
            )
            result = self.executor_registry.get(execution_plan.executor_id).execute(context)
            artifacts = dict(result.artifacts)
            artifact_refs = list(result.artifact_refs) or [
                value for value in artifacts.values() if isinstance(value, str)
            ]
            citations = self.operations.public_citations(result.citations)
            failure_stage = "assistant_staging"
            assistant_message_id = self.store.stage_assistant_message(
                run_id=prepared.run_id,
                session_id=prepared.session_id,
                content=result.answer,
                metadata={
                    "run_id": prepared.run_id,
                    "skill_id": execution_plan.manifest.id,
                    "artifacts": artifacts,
                    "citations": citations,
                },
            )
            candidate_status = RunStatus.DONE
            stop_reason = str(result.stop_reason or "success")
        except asyncio.CancelledError as exc:
            failure = exc
            candidate_status = RunStatus.CANCELLED
            stop_reason = "cancelled"
        except Exception as exc:  # noqa: BLE001
            failure = exc
            candidate_status = RunStatus.FAILED
            stop_reason = self._failure_stop_reason(failure_stage)

        hook_candidate_status = candidate_status
        hook_candidate_stop_reason = stop_reason
        stop_payload = {
            "run_id": prepared.run_id,
            "session_id": prepared.session_id,
            "company_id": prepared.company_id,
            "user_message_id": prepared.user_message_id,
            "assistant_message_id": assistant_message_id,
            "candidate_status": hook_candidate_status.value,
            "stop_reason": stop_reason,
            "artifact_refs": list(artifact_refs),
        }
        stop_decisions: tuple[HookDecision, ...] = ()
        stop_hook_error = ""
        try:
            stop_decisions = self.hooks.run("Stop", stop_payload)
        except Exception as exc:  # noqa: BLE001
            stop_hook_error = repr(exc)
            stop_decisions = (
                HookDecision(
                    action="deny",
                    reason=f"Stop hook error: {type(exc).__name__}",
                    event_attributes={"error_type": type(exc).__name__},
                ),
            )
            if failure is None:
                failure = exc
                candidate_status = RunStatus.FAILED
                stop_reason = "stop_hook_error"

        blocked_decision = next(
            (
                decision
                for decision in stop_decisions
                if decision.action in {"deny", "ask"}
            ),
            None,
        )
        if failure is None and blocked_decision is not None:
            candidate_status = RunStatus.FAILED
            stop_reason = "gate_blocked"
            failure = PermissionError(
                f"Stop {blocked_decision.action}: {blocked_decision.reason or 'blocked'}"
            )

        memory_writes: list[dict[str, Any]] = []
        if failure is None and result is not None:
            try:
                planned_writes = self.operations.after_skill_result(
                    prepared.run_id,
                    prepared.message,
                    execution_plan.manifest,
                    result,
                )
                if planned_writes:
                    memory_writes = list(planned_writes)
            except Exception as exc:  # noqa: BLE001
                failure = exc
                candidate_status = RunStatus.FAILED
                stop_reason = "post_gate_side_effect_error"

        metadata_patch: dict[str, Any] = {
            "planned_execution_mode": execution_plan.manifest.execution_mode.value,
            "stop_reason": stop_reason,
            "runtime": "agent_workbench",
        }
        if result is not None:
            metadata_patch["execution_mode"] = result.actual_execution_mode.value
            metadata_patch["delegation_count"] = len(result.delegation_evidence)
            metadata_patch["delegation_evidence"] = [
                item.model_dump(mode="json") for item in result.delegation_evidence
            ]
        if stop_hook_error:
            metadata_patch["stop_hook_error"] = stop_hook_error
        publish_assistant = (
            failure is None
            and result is not None
            and candidate_status is RunStatus.DONE
        )
        final_answer_step = None
        if result is not None:
            final_answer_step = {
                "step_type": "final_answer",
                "title": "保存最终回答",
                "status": "done",
                "input_payload": {},
                "output_payload": {
                    "answer_preview": result.answer[:300],
                    "artifact_keys": sorted(artifacts),
                },
                "metadata": {"loop_phase": "answer"},
            }
        run_stopped_step = self._build_run_stopped_step(
            prepared=prepared,
            candidate_status=hook_candidate_status,
            terminal_status=candidate_status,
            candidate_stop_reason=hook_candidate_stop_reason,
            terminal_stop_reason=stop_reason,
            artifact_refs=artifact_refs,
            decisions=stop_decisions,
        )
        assistant_message_id = self.store.commit_agent_run_terminal(
            prepared.run_id,
            candidate_status,
            publish_assistant=publish_assistant,
            final_answer_step=final_answer_step,
            run_stopped_step=run_stopped_step,
            metadata_patch=metadata_patch,
            metrics={
                "selected_skill_id": execution_plan.manifest.id,
                "artifact_count": len(artifacts),
                "delegation_count": len(result.delegation_evidence) if result else 0,
            },
            artifacts=[
                {"artifact_type": key, "path": value}
                for key, value in artifacts.items()
                if isinstance(value, str)
            ],
            error=repr(failure) if failure is not None else "",
            memory_writes=memory_writes if publish_assistant else [],
        )

        if failure is not None:
            raise failure
        if result is None:
            raise RuntimeError("executor completed without a SkillResult")
        return {
            "run_id": prepared.run_id,
            "session_id": prepared.session_id,
            "answer": result.answer,
            "selected_skill_id": execution_plan.manifest.id,
            "model_id": prepared.model_id,
            "artifacts": artifacts,
            "citations": citations,
        }

    def _validate_prepared_turn(
        self,
        prepared: PreparedTurn,
    ) -> _PersistedExecutionPlan:
        run = self.store.get_agent_run(prepared.run_id)
        if not run:
            raise PreparedTurnIntegrityError(
                f"PreparedTurn Run does not exist: {prepared.run_id}"
            )
        metadata = run.get("metadata") or {}
        record = metadata.get("prepared_turn")
        persisted_fingerprint = str(metadata.get("prepared_turn_fingerprint") or "")
        if not isinstance(record, dict) or not persisted_fingerprint:
            raise PreparedTurnIntegrityError("Run has no persisted prepared-turn binding")
        if not hmac.compare_digest(_fingerprint(record), persisted_fingerprint):
            raise PreparedTurnIntegrityError("persisted prepared-turn fingerprint mismatch")

        manifest_record = record.get("manifest")
        execution_record = record.get("execution_plan")
        if not isinstance(manifest_record, dict) or not isinstance(execution_record, dict):
            raise PreparedTurnIntegrityError("persisted prepared-turn plan is malformed")
        try:
            canonical_manifest = self.registry.get_manifest(str(manifest_record.get("id") or ""))
        except (KeyError, ValueError) as exc:
            raise PreparedTurnIntegrityError("persisted manifest is not registered") from exc

        persisted_checks = {
            "run_id": (record.get("run_id"), run.get("run_id")),
            "session_id": (record.get("session_id"), run.get("session_id")),
            "user_message_id": (
                record.get("user_message_id"),
                run.get("user_message_id"),
            ),
            "model_id": (record.get("model_id"), run.get("model_id")),
            "mode": (record.get("mode"), run.get("mode")),
            "manifest_id": (manifest_record.get("id"), run.get("skill_id")),
            "manifest_version": (
                manifest_record.get("version"),
                canonical_manifest.version,
            ),
        }
        for label, (bound_value, persisted_value) in persisted_checks.items():
            if bound_value != persisted_value:
                raise PreparedTurnIntegrityError(f"persisted {label} binding mismatch")

        persisted_user_message = self.store.get_chat_message(
            str(record.get("user_message_id") or "")
        )
        if (
            not persisted_user_message
            or persisted_user_message.get("session_id") != record.get("session_id")
            or persisted_user_message.get("role") != "user"
            or persisted_user_message.get("content") != record.get("user_message")
        ):
            raise PreparedTurnIntegrityError("persisted user message binding mismatch")

        caller_checks = {
            "run_id": (prepared.run_id, record.get("run_id")),
            "session_id": (prepared.session_id, record.get("session_id")),
            "company_id": (prepared.company_id, record.get("company_id")),
            "coordinator_company_id": (self.company_id, record.get("company_id")),
            "user_message_id": (
                prepared.user_message_id,
                record.get("user_message_id"),
            ),
            "message": (prepared.message, record.get("task_message")),
            "model_id": (prepared.model_id, record.get("model_id")),
            "mode": (prepared.mode, record.get("mode")),
        }
        for label, (caller_value, bound_value) in caller_checks.items():
            if caller_value != bound_value:
                raise PreparedTurnIntegrityError(f"PreparedTurn {label} mismatch")
        if prepared.manifest != canonical_manifest:
            raise PreparedTurnIntegrityError("PreparedTurn manifest is not canonical")

        executor_id = str(execution_record.get("executor_id") or "")
        services = execution_record.get("services")
        context_patch = execution_record.get("before_turn_context_patch")
        decisions = execution_record.get("before_turn_decisions")
        if not executor_id or not isinstance(services, dict):
            raise PreparedTurnIntegrityError("persisted executor plan is malformed")
        if not isinstance(context_patch, dict) or not isinstance(decisions, list):
            raise PreparedTurnIntegrityError("persisted BeforeTurn plan is malformed")

        resolved_override = self.execution_override_registry.resolve(prepared.message)
        resolved_execution = resolved_override or ExecutionOverride(
            executor_id=canonical_manifest.executor_id
        )
        if executor_id != resolved_execution.executor_id:
            raise PreparedTurnIntegrityError("persisted executor id is not canonical")
        if _json_value(services) != _json_value(resolved_execution.services):
            raise PreparedTurnIntegrityError("persisted executor services are not canonical")

        return _PersistedExecutionPlan(
            manifest=canonical_manifest,
            executor_id=executor_id,
            services=services,
            requested_skill_id=str(execution_record.get("requested_skill_id") or ""),
            context_patch=context_patch,
            before_turn_decisions=tuple(decisions),
        )

    def _validate_request(
        self,
        session_id: str,
        message: str,
        mode: str,
        model_id: str,
        active_skill_id: str,
    ) -> None:
        for value, label in (
            (session_id, "session_id"),
            (message, "message"),
            (mode, "mode"),
            (model_id, "model_id"),
            (active_skill_id, "active_skill_id"),
        ):
            if not isinstance(value, str):
                raise TypeError(f"{label} must be a string")
        for value, label in (
            (session_id, "session_id"),
            (message, "message"),
            (mode, "mode"),
            (model_id, "model_id"),
        ):
            if not value.strip():
                raise ValueError(f"{label} is required")
        if not self.store.get_chat_session(session_id):
            raise ValueError(f"chat session not found: {session_id}")
        self.operations.validate_request(
            session_id,
            message,
            mode,
            model_id,
            active_skill_id,
        )

    def _run_before_turn(
        self,
        **payload: Any,
    ) -> tuple[Mapping[str, Any], tuple[dict[str, Any], ...]]:
        context_patch: dict[str, Any] = {}
        persisted_decisions: list[dict[str, Any]] = []
        for decision in self.hooks.run("BeforeTurn", dict(payload, company_id=self.company_id)):
            if decision.action == "ask":
                raise ValueError(
                    f"BeforeTurn ask is invalid: {decision.reason or 'blocked'}"
                )
            if decision.action == "deny":
                raise PermissionError(
                    f"BeforeTurn deny: {decision.reason or 'blocked'}"
                )
            persisted_decisions.append(
                _json_value(decision.model_dump(mode="python"))
            )
            if decision.action == "patch_context":
                context_patch.update(decision.context_patch)
        return context_patch, tuple(persisted_decisions)

    def _build_run_stopped_step(
        self,
        *,
        prepared: PreparedTurn,
        candidate_status: RunStatus,
        terminal_status: RunStatus,
        candidate_stop_reason: str,
        terminal_stop_reason: str,
        artifact_refs: list[str],
        decisions: tuple[HookDecision, ...],
    ) -> dict[str, Any]:
        blocked = next(
            (decision for decision in decisions if decision.action in {"deny", "ask"}),
            None,
        )
        return {
            "step_type": "run_stopped",
            "title": "运行结束",
            "status": "done",
            "input_payload": {
                "candidate_status": candidate_status.value,
                "stop_reason": candidate_stop_reason,
                "artifact_refs": list(artifact_refs),
            },
            "output_payload": {
                "candidate_status": candidate_status.value,
                "terminal_status": terminal_status.value,
                "stop_reason": terminal_stop_reason,
                "artifact_refs": list(artifact_refs),
            },
            "gate_result": {
                "decision": blocked.action if blocked else "allow",
                "reason": blocked.reason if blocked else "",
                "policy_id": "hook.stop.v1",
            },
            "metadata": {
                "loop_phase": "stop",
                "hook_decision_count": len(decisions),
            },
        }

    @staticmethod
    def _failure_stop_reason(stage: str) -> str:
        if stage == "executor":
            return "executor_error"
        if stage == "context":
            return "context_error"
        if stage == "assistant_staging":
            return "assistant_staging_error"
        if stage == "result_persistence":
            return "result_persistence_error"
        return "runtime_error"
