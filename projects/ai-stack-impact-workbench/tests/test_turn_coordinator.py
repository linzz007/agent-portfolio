from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, fields, replace
import hashlib
import json
from pathlib import Path
import threading
from typing import Any

import pytest

from policy_impact.company_wiki import loader as wiki_loader
from policy_impact.harness.agent_runtime import AgentRuntime
from policy_impact.harness.model_config import DEFAULT_MODEL_ID
from policy_impact.harness.model_gateway import ScriptedModelAdapter
from policy_impact.memory.store import PolicyMemoryStore
from policy_impact.runtime.execution_context import SkillExecutionContext
from policy_impact.runtime.execution_mode import ExecutionMode
from policy_impact.runtime.hooks import HookDecision, HookManager
from policy_impact.runtime.result import SkillResult
from policy_impact.runtime.run_status import RunStatus
from policy_impact.runtime.turn_coordinator import (
    PreparedTurn,
    PreparedTurnIntegrityError,
    RunExecutionConflict,
    RunExecutionLockRegistry,
)
from policy_impact.skills.manifest import SkillManifest
from policy_impact.skills.schemas import SkillInput, SkillOutput


class BrokenExecutor:
    def execute(self, context: SkillExecutionContext) -> SkillResult:
        raise RuntimeError("planned executor failure")


class CancelledExecutor:
    def execute(self, context: SkillExecutionContext) -> SkillResult:
        raise asyncio.CancelledError("planned cancellation")


class DeterministicExecutor:
    def __init__(self) -> None:
        self.contexts: list[SkillExecutionContext] = []

    def execute(self, context: SkillExecutionContext) -> SkillResult:
        self.contexts.append(context)
        return SkillResult(
            answer="deterministic answer",
            actual_execution_mode=ExecutionMode.DETERMINISTIC,
            stop_reason="success",
            artifact_refs=["artifact://report-1"],
            artifacts={"report": "artifacts/report-1.md"},
        )


class BlockingExecutor(DeterministicExecutor):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def execute(self, context: SkillExecutionContext) -> SkillResult:
        self.started.set()
        if not self.release.wait(timeout=5):
            raise RuntimeError("blocking executor timed out")
        return super().execute(context)


def _test_skill_manifest(skill_id: str, executor_id: str) -> SkillManifest:
    return SkillManifest(
        id=skill_id,
        version="1.0.0",
        name=f"{skill_id} test skill",
        description="Only used to verify TurnCoordinator lifecycle behavior.",
        execution_mode=ExecutionMode.DETERMINISTIC,
        executor_id=executor_id,
        input_schema=SkillInput,
        output_schema=SkillOutput,
        context_policy_id="context.test.v1",
        memory_policy_id="memory.deny.v1",
        permission_policy_id="tools.deny.v1",
        max_steps=1,
        max_model_calls=1,
        timeout_seconds=5,
    )


@pytest.fixture
def turn_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    company_dir = tmp_path / "data" / "companies" / "company_test"
    wiki_dir = company_dir / "wiki"
    wiki_dir.mkdir(parents=True)
    (company_dir / "company.yaml").write_text(
        '{"company_id": "company_test", "company_name": "Demo AI Co"}',
        encoding="utf-8",
    )
    (wiki_dir / "product.md").write_text(
        """---
domain: product
importance: 5
confidence: 0.9
---
## FACT: fact_runtime
- value: Builds an Agent Workbench runtime.
- source: internal wiki
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(wiki_loader, "project_root", lambda: tmp_path, raising=False)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("ANTHROPIC_MODEL", DEFAULT_MODEL_ID)

    store = PolicyMemoryStore("company_test")
    store.seed_model_configs()
    session_id = store.create_chat_session(
        title="Turn coordinator",
        mode="auto",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="",
    )

    def runtime(*, hooks: HookManager | None = None) -> AgentRuntime:
        return AgentRuntime(
            company_id="company_test",
            model_adapter=ScriptedModelAdapter([]),
            hooks=hooks,
        )

    return {"store": store, "session_id": session_id, "runtime": runtime}


def _run(runtime: AgentRuntime, session_id: str, **overrides: Any) -> dict[str, Any]:
    request = {
        "session_id": session_id,
        "message": "run deterministic test skill",
        "mode": "auto",
        "model_id": DEFAULT_MODEL_ID,
        "active_skill_id": "deterministic_test",
    }
    request.update(overrides)
    return runtime.run_turn(**request)


def _register_test_executor(
    runtime: AgentRuntime,
    *,
    skill_id: str,
    executor_id: str,
    executor: Any,
) -> None:
    runtime.registry.register_for_test(_test_skill_manifest(skill_id, executor_id))
    runtime.executor_registry.register(executor_id, executor)


def test_run_records_actual_deterministic_wiki_mode(turn_env: dict[str, Any]) -> None:
    runtime = turn_env["runtime"]()
    result = runtime.run_turn(
        session_id=turn_env["session_id"],
        message="/wiki 规划公司知识库",
        mode="auto",
        model_id=DEFAULT_MODEL_ID,
    )

    run = turn_env["store"].get_agent_run(result["run_id"])

    assert run["metadata"]["planned_execution_mode"] == "deterministic"
    assert run["metadata"]["execution_mode"] == "deterministic"
    assert run["metadata"]["stop_reason"] == "success"
    assert run["metadata"]["runtime"] == "agent_workbench"
    assert run["status"] == RunStatus.DONE.value


def test_failed_run_has_structured_stop_reason(turn_env: dict[str, Any]) -> None:
    runtime = turn_env["runtime"]()
    _register_test_executor(
        runtime,
        skill_id="broken_skill",
        executor_id="broken",
        executor=BrokenExecutor(),
    )

    with pytest.raises(RuntimeError, match="planned executor failure"):
        _run(
            runtime,
            turn_env["session_id"],
            active_skill_id="broken_skill",
        )

    run = turn_env["store"].latest_agent_run(turn_env["session_id"])
    assert run["status"] == RunStatus.FAILED.value
    assert run["metadata"]["stop_reason"] == "executor_error"
    assert run["metadata"]["runtime"] == "agent_workbench"


def test_result_persistence_failure_stops_with_known_artifact_refs(
    turn_env: dict[str, Any],
) -> None:
    payloads: list[dict[str, Any]] = []
    hooks = HookManager()
    hooks.register(
        "Stop",
        lambda payload: payloads.append(payload) or HookDecision(action="allow"),
    )
    runtime = turn_env["runtime"](hooks=hooks)
    _register_test_executor(
        runtime,
        skill_id="deterministic_test",
        executor_id="deterministic_test",
        executor=DeterministicExecutor(),
    )

    def fail_result_persistence(*args: Any) -> None:
        raise RuntimeError("planned result persistence failure")

    runtime.coordinator.operations = replace(
        runtime.coordinator.operations,
        after_skill_result=fail_result_persistence,
    )

    with pytest.raises(RuntimeError, match="planned result persistence failure"):
        _run(runtime, turn_env["session_id"])

    assert payloads[0]["artifact_refs"] == ["artifact://report-1"]
    run = turn_env["store"].latest_agent_run(turn_env["session_id"])
    assert run["status"] == RunStatus.FAILED.value
    assert run["metadata"]["stop_reason"] == "post_gate_side_effect_error"


def test_cancelled_executor_stops_once_and_persists_cancelled(
    turn_env: dict[str, Any],
) -> None:
    payloads: list[dict[str, Any]] = []
    hooks = HookManager()
    hooks.register(
        "Stop",
        lambda payload: payloads.append(payload) or HookDecision(action="allow"),
    )
    runtime = turn_env["runtime"](hooks=hooks)
    _register_test_executor(
        runtime,
        skill_id="cancelled_skill",
        executor_id="cancelled",
        executor=CancelledExecutor(),
    )

    with pytest.raises(asyncio.CancelledError, match="planned cancellation"):
        _run(
            runtime,
            turn_env["session_id"],
            active_skill_id="cancelled_skill",
        )

    assert len(payloads) == 1
    assert payloads[0]["candidate_status"] == RunStatus.CANCELLED.value
    assert payloads[0]["stop_reason"] == "cancelled"
    run = turn_env["store"].latest_agent_run(turn_env["session_id"])
    assert run["status"] == RunStatus.CANCELLED.value
    assert run["metadata"]["stop_reason"] == "cancelled"


def test_same_run_cannot_have_two_active_executors() -> None:
    run_locks = RunExecutionLockRegistry()

    with run_locks.hold("run-1"):
        owner = run_locks.owner("run-1")
        assert owner
        with pytest.raises(RunExecutionConflict, match="run-1"):
            with run_locks.hold("run-1", blocking=False):
                pass

    assert run_locks.owner("run-1") is None


def test_same_run_cannot_execute_in_two_tasks_on_one_thread() -> None:
    run_locks = RunExecutionLockRegistry()

    async def exercise() -> None:
        acquired = asyncio.Event()
        release = asyncio.Event()

        async def first_owner() -> None:
            with run_locks.hold("run-1"):
                acquired.set()
                await release.wait()

        first = asyncio.create_task(first_owner())
        await acquired.wait()
        try:
            with pytest.raises(RunExecutionConflict, match="run-1"):
                with run_locks.hold("run-1", blocking=False):
                    pass
        finally:
            release.set()
            await first

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("action", "expected_exception"),
    [("deny", PermissionError), ("ask", ValueError)],
)
def test_before_turn_deny_or_ask_has_no_persistence(
    turn_env: dict[str, Any],
    action: str,
    expected_exception: type[Exception],
) -> None:
    hooks = HookManager()
    hooks.register(
        "BeforeTurn",
        lambda payload: HookDecision(action=action, reason=f"blocked by {action}"),
    )
    runtime = turn_env["runtime"](hooks=hooks)

    with pytest.raises(expected_exception, match="BeforeTurn"):
        runtime.run_turn(
            session_id=turn_env["session_id"],
            message="must not persist",
            mode="auto",
            model_id=DEFAULT_MODEL_ID,
        )

    assert turn_env["store"].list_chat_messages(turn_env["session_id"]) == []
    assert turn_env["store"].latest_agent_run(turn_env["session_id"]) is None


@pytest.mark.parametrize("fails", [False, True])
def test_stop_runs_exactly_once_for_success_and_failure(
    turn_env: dict[str, Any],
    fails: bool,
) -> None:
    payloads: list[dict[str, Any]] = []
    hooks = HookManager()

    def record_stop(payload: dict[str, Any]) -> HookDecision:
        payloads.append(payload)
        return HookDecision(action="allow", reason="audit recorded")

    hooks.register("Stop", record_stop)
    runtime = turn_env["runtime"](hooks=hooks)
    executor = BrokenExecutor() if fails else DeterministicExecutor()
    skill_id = "broken_skill" if fails else "deterministic_test"
    executor_id = "broken" if fails else "deterministic_test"
    _register_test_executor(
        runtime,
        skill_id=skill_id,
        executor_id=executor_id,
        executor=executor,
    )

    if fails:
        with pytest.raises(RuntimeError, match="planned executor failure"):
            _run(runtime, turn_env["session_id"], active_skill_id=skill_id)
    else:
        _run(runtime, turn_env["session_id"], active_skill_id=skill_id)

    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["candidate_status"] == (
        RunStatus.FAILED.value if fails else RunStatus.DONE.value
    )
    assert payload["stop_reason"] == ("executor_error" if fails else "success")
    assert set(payload) == {
        "run_id",
        "session_id",
        "company_id",
        "user_message_id",
        "assistant_message_id",
        "candidate_status",
        "stop_reason",
        "artifact_refs",
    }
    assert payload["artifact_refs"] == ([] if fails else ["artifact://report-1"])

    run = turn_env["store"].latest_agent_run(turn_env["session_id"])
    stopped = [
        step
        for step in turn_env["store"].list_agent_steps(run["run_id"])
        if step["step_type"] == "run_stopped"
    ]
    assert len(stopped) == 1
    assert stopped[0]["output_payload"]["candidate_status"] == payload["candidate_status"]


@pytest.mark.parametrize("action", ["deny", "ask"])
def test_stop_deny_or_ask_fails_closed_and_does_not_return_answer(
    turn_env: dict[str, Any],
    action: str,
) -> None:
    hooks = HookManager()
    hooks.register(
        "Stop",
        lambda payload: HookDecision(action=action, reason="release gate blocked"),
    )
    runtime = turn_env["runtime"](hooks=hooks)
    _register_test_executor(
        runtime,
        skill_id="deterministic_test",
        executor_id="deterministic_test",
        executor=DeterministicExecutor(),
    )

    with pytest.raises(PermissionError, match="Stop"):
        _run(runtime, turn_env["session_id"])

    run = turn_env["store"].latest_agent_run(turn_env["session_id"])
    assert run["status"] == RunStatus.FAILED.value
    assert run["metadata"]["stop_reason"] == "gate_blocked"
    assert run["assistant_message_id"] == ""
    assert [item["role"] for item in turn_env["store"].list_chat_messages(turn_env["session_id"])] == [
        "user"
    ]


def test_before_turn_cannot_bypass_request_validation(turn_env: dict[str, Any]) -> None:
    calls: list[dict[str, Any]] = []
    hooks = HookManager()

    def record(payload: dict[str, Any]) -> HookDecision:
        calls.append(payload)
        return HookDecision(action="allow")

    hooks.register("BeforeTurn", record)
    runtime = turn_env["runtime"](hooks=hooks)

    with pytest.raises(ValueError):
        runtime.run_turn(
            session_id=turn_env["session_id"],
            message="invalid request",
            mode="auto",
            model_id="unsupported-model",
        )

    assert calls == []
    assert turn_env["store"].list_chat_messages(turn_env["session_id"]) == []
    assert turn_env["store"].latest_agent_run(turn_env["session_id"]) is None


def test_prepare_turn_has_exact_public_contract_and_persists_execution_override(
    turn_env: dict[str, Any],
) -> None:
    runtime = turn_env["runtime"]()
    prepared = runtime.coordinator.prepare_turn(
        session_id=turn_env["session_id"],
        message="请核验个人信息保护法 article 55",
        mode="auto",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="research_report",
    )

    assert isinstance(prepared, PreparedTurn)
    assert [field.name for field in fields(PreparedTurn)] == [
        "run_id",
        "session_id",
        "company_id",
        "user_message_id",
        "message",
        "model_id",
        "mode",
        "manifest",
    ]
    assert prepared.manifest.id == "research_report"
    run = turn_env["store"].get_agent_run(prepared.run_id)
    execution_plan = run["metadata"]["prepared_turn"]["execution_plan"]
    assert execution_plan["executor_id"] == "official_legal_reference"
    assert execution_plan["services"]["legal_reference"]["reference_id"] == "pipl_article_55"
    assert run["metadata"]["prepared_turn_fingerprint"]
    with pytest.raises(FrozenInstanceError):
        prepared.run_id = "changed"


def test_prepared_fingerprint_binds_original_and_task_messages(
    turn_env: dict[str, Any],
) -> None:
    runtime = turn_env["runtime"]()
    prepared = runtime.coordinator.prepare_turn(
        session_id=turn_env["session_id"],
        message="/wiki build a release map",
        mode="auto",
        model_id=DEFAULT_MODEL_ID,
    )
    run = turn_env["store"].get_agent_run(prepared.run_id)
    record = run["metadata"]["prepared_turn"]

    assert record["user_message"] == "/wiki build a release map"
    assert record["task_message"] == "build a release map"
    assert prepared.message == record["task_message"]

    with turn_env["store"]._conn() as conn:
        conn.execute(
            "UPDATE chat_messages SET content = ? WHERE message_id = ?",
            ("tampered persisted user message", prepared.user_message_id),
        )

    with pytest.raises(PreparedTurnIntegrityError, match="user message"):
        runtime.coordinator.execute_prepared_turn(prepared)

    assert turn_env["store"].get_agent_run(prepared.run_id)["status"] == "created"


@pytest.mark.parametrize(
    "tamper",
    [
        "run_id",
        "session_id",
        "company_id",
        "user_message_id",
        "message",
        "model_id",
        "mode",
        "manifest_id",
        "manifest_executor",
    ],
)
def test_tampered_prepared_turn_fails_before_claim_or_execution(
    turn_env: dict[str, Any],
    tamper: str,
) -> None:
    stop_payloads: list[dict[str, Any]] = []
    hooks = HookManager()
    hooks.register(
        "Stop",
        lambda payload: stop_payloads.append(payload) or HookDecision(action="allow"),
    )
    runtime = turn_env["runtime"](hooks=hooks)
    executor = DeterministicExecutor()
    _register_test_executor(
        runtime,
        skill_id="deterministic_test",
        executor_id="deterministic_test",
        executor=executor,
    )
    prepared = runtime.coordinator.prepare_turn(
        session_id=turn_env["session_id"],
        message="integrity-bound task",
        mode="auto",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="deterministic_test",
    )
    other_session_id = turn_env["store"].create_chat_session(
        title="Other",
        mode="auto",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="",
    )
    replacements: dict[str, Any] = {
        "run_id": {"run_id": "missing-run"},
        "session_id": {"session_id": other_session_id},
        "company_id": {"company_id": "company_other"},
        "user_message_id": {"user_message_id": "other-message"},
        "message": {"message": "tampered task"},
        "model_id": {"model_id": "tampered-model"},
        "mode": {"mode": "skill"},
        "manifest_id": {
            "manifest": runtime.registry.get_manifest("general_chat"),
        },
        "manifest_executor": {
            "manifest": prepared.manifest.model_copy(update={"executor_id": "broken"}),
        },
    }
    tampered = replace(prepared, **replacements[tamper])

    with pytest.raises(PreparedTurnIntegrityError):
        runtime.coordinator.execute_prepared_turn(tampered)

    run = turn_env["store"].get_agent_run(prepared.run_id)
    assert run["status"] == RunStatus.CREATED.value
    assert executor.contexts == []
    assert stop_payloads == []


def test_atomic_claim_allows_only_one_coordinator_to_execute(
    turn_env: dict[str, Any],
) -> None:
    stop_payloads: list[dict[str, Any]] = []
    hooks = HookManager()
    hooks.register(
        "Stop",
        lambda payload: stop_payloads.append(payload) or HookDecision(action="allow"),
    )
    first_runtime = turn_env["runtime"](hooks=hooks)
    executor = BlockingExecutor()
    _register_test_executor(
        first_runtime,
        skill_id="deterministic_test",
        executor_id="deterministic_test",
        executor=executor,
    )
    second_runtime = AgentRuntime(
        company_id="company_test",
        registry=first_runtime.registry,
        executor_registry=first_runtime.executor_registry,
        execution_override_registry=first_runtime.execution_override_registry,
        model_adapter=ScriptedModelAdapter([]),
        hooks=hooks,
        run_locks=RunExecutionLockRegistry(),
    )
    prepared = first_runtime.coordinator.prepare_turn(
        session_id=turn_env["session_id"],
        message="claim once",
        mode="auto",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="deterministic_test",
    )
    winner_result: list[dict[str, Any]] = []
    winner_errors: list[BaseException] = []

    def execute_winner() -> None:
        try:
            winner_result.append(first_runtime.coordinator.execute_prepared_turn(prepared))
        except BaseException as exc:  # noqa: BLE001
            winner_errors.append(exc)

    winner = threading.Thread(target=execute_winner)
    winner.start()
    assert executor.started.wait(timeout=5)
    try:
        with pytest.raises(RunExecutionConflict):
            second_runtime.coordinator.execute_prepared_turn(prepared)
    finally:
        executor.release.set()
        winner.join(timeout=5)

    assert winner_errors == []
    assert len(winner_result) == 1
    assert len(executor.contexts) == 1
    assert len(stop_payloads) == 1
    run = turn_env["store"].get_agent_run(prepared.run_id)
    assert run["status"] == RunStatus.DONE.value


def test_assistant_is_staged_and_hidden_until_stop_allows_publication(
    turn_env: dict[str, Any],
) -> None:
    observed: dict[str, Any] = {}
    hooks = HookManager()

    def inspect_stop(payload: dict[str, Any]) -> HookDecision:
        observed["roles"] = [
            item["role"]
            for item in turn_env["store"].list_chat_messages(turn_env["session_id"])
        ]
        observed["staged"] = turn_env["store"].get_staged_assistant_message(
            payload["run_id"]
        )
        observed["assistant_message_id"] = payload["assistant_message_id"]
        return HookDecision(action="allow")

    hooks.register("Stop", inspect_stop)
    runtime = turn_env["runtime"](hooks=hooks)
    _register_test_executor(
        runtime,
        skill_id="deterministic_test",
        executor_id="deterministic_test",
        executor=DeterministicExecutor(),
    )

    result = _run(runtime, turn_env["session_id"])

    assert observed["roles"] == ["user"]
    assert observed["staged"]["content"] == "deterministic answer"
    assert observed["staged"]["message_id"] == observed["assistant_message_id"]
    assert turn_env["store"].get_staged_assistant_message(result["run_id"]) is None
    assert [
        item["role"]
        for item in turn_env["store"].list_chat_messages(turn_env["session_id"])
    ] == ["user", "assistant"]


def test_assistant_staging_failure_stops_and_terminalizes_without_output_leaks(
    turn_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop_payloads: list[dict[str, Any]] = []
    hooks = HookManager()
    hooks.register(
        "Stop",
        lambda payload: stop_payloads.append(payload) or HookDecision(action="allow"),
    )
    runtime = turn_env["runtime"](hooks=hooks)
    _register_test_executor(
        runtime,
        skill_id="deterministic_test",
        executor_id="deterministic_test",
        executor=DeterministicExecutor(),
    )

    def fail_staging(*args: Any, **kwargs: Any) -> str:
        raise RuntimeError("planned assistant staging failure")

    monkeypatch.setattr(runtime.store, "stage_assistant_message", fail_staging)

    with pytest.raises(RuntimeError, match="planned assistant staging failure"):
        _run(runtime, turn_env["session_id"])

    run = turn_env["store"].latest_agent_run(turn_env["session_id"])
    assert len(stop_payloads) == 1
    assert stop_payloads[0]["assistant_message_id"] == ""
    assert stop_payloads[0]["artifact_refs"] == ["artifact://report-1"]
    assert run["status"] == RunStatus.FAILED.value
    assert run["metadata"]["stop_reason"] == "assistant_staging_error"
    assert run["artifacts"] == []
    assert turn_env["store"].get_staged_assistant_message(run["run_id"]) is None
    assert [
        item["role"]
        for item in turn_env["store"].list_chat_messages(turn_env["session_id"])
    ] == ["user"]


@pytest.mark.parametrize("failing_step_type", ["final_answer", "run_stopped"])
def test_terminal_commit_rolls_back_every_publication_on_step_failure(
    turn_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    failing_step_type: str,
) -> None:
    stop_calls = 0
    hooks = HookManager()

    def allow_stop(payload: dict[str, Any]) -> HookDecision:
        nonlocal stop_calls
        stop_calls += 1
        return HookDecision(action="allow")

    hooks.register("Stop", allow_stop)
    runtime = turn_env["runtime"](hooks=hooks)
    _register_test_executor(
        runtime,
        skill_id="deterministic_test",
        executor_id="deterministic_test",
        executor=DeterministicExecutor(),
    )
    original_insert = PolicyMemoryStore._insert_agent_step_conn

    def fail_terminal_step(conn: Any, run_id: str, step: dict[str, Any]) -> str:
        if step.get("step_type") == failing_step_type:
            raise RuntimeError(f"planned {failing_step_type} commit failure")
        return original_insert(conn, run_id, step)

    monkeypatch.setattr(runtime.store, "_insert_agent_step_conn", fail_terminal_step)

    with pytest.raises(RuntimeError, match=f"planned {failing_step_type} commit failure"):
        _run(runtime, turn_env["session_id"])

    run = turn_env["store"].latest_agent_run(turn_env["session_id"])
    steps = turn_env["store"].list_agent_steps(run["run_id"])
    assert stop_calls == 1
    assert run["status"] == RunStatus.RUNNING.value
    assert run["assistant_message_id"] == ""
    assert run["artifacts"] == []
    assert turn_env["store"].get_staged_assistant_message(run["run_id"]) is not None
    assert not any(
        step["step_type"] in {"final_answer", "run_stopped"} for step in steps
    )
    assert [
        item["role"]
        for item in turn_env["store"].list_chat_messages(turn_env["session_id"])
    ] == ["user"]


def test_stop_denial_discards_staged_artifacts_and_post_gate_memory(
    turn_env: dict[str, Any],
) -> None:
    hooks = HookManager()
    hooks.register("Stop", lambda payload: HookDecision(action="deny", reason="blocked"))
    runtime = turn_env["runtime"](hooks=hooks)
    _register_test_executor(
        runtime,
        skill_id="deterministic_test",
        executor_id="deterministic_test",
        executor=DeterministicExecutor(),
    )
    post_gate_calls: list[str] = []

    def plan_memory(*args: Any) -> list[dict[str, Any]]:
        post_gate_calls.append("called")
        return [
            {
                "namespace": "company:company_test:test",
                "memory_type": "blocked_result",
                "content": "must remain absent",
            }
        ]

    runtime.coordinator.operations = replace(
        runtime.coordinator.operations,
        after_skill_result=plan_memory,
    )

    with pytest.raises(PermissionError, match="Stop deny"):
        _run(runtime, turn_env["session_id"])

    run = turn_env["store"].latest_agent_run(turn_env["session_id"])
    assert post_gate_calls == []
    assert run["status"] == RunStatus.FAILED.value
    assert run["artifacts"] == []
    assert turn_env["store"].get_staged_assistant_message(run["run_id"]) is None
    assert turn_env["store"].search_memory("must remain absent", top_k=5) == []


@pytest.mark.parametrize("executor_fails", [False, True])
def test_stop_hook_exception_fails_closed_and_preserves_primary_failure(
    turn_env: dict[str, Any],
    executor_fails: bool,
) -> None:
    hooks = HookManager()

    def fail_stop(payload: dict[str, Any]) -> HookDecision:
        raise LookupError("planned Stop hook failure")

    hooks.register("Stop", fail_stop)
    runtime = turn_env["runtime"](hooks=hooks)
    executor = BrokenExecutor() if executor_fails else DeterministicExecutor()
    skill_id = "broken_skill" if executor_fails else "deterministic_test"
    executor_id = "broken" if executor_fails else "deterministic_test"
    _register_test_executor(
        runtime,
        skill_id=skill_id,
        executor_id=executor_id,
        executor=executor,
    )

    expected_error = RuntimeError if executor_fails else LookupError
    expected_message = (
        "planned executor failure" if executor_fails else "planned Stop hook failure"
    )
    with pytest.raises(expected_error, match=expected_message):
        _run(runtime, turn_env["session_id"], active_skill_id=skill_id)

    run = turn_env["store"].latest_agent_run(turn_env["session_id"])
    stopped = next(
        step
        for step in turn_env["store"].list_agent_steps(run["run_id"])
        if step["step_type"] == "run_stopped"
    )
    expected_reason = "executor_error" if executor_fails else "stop_hook_error"
    expected_candidate = RunStatus.FAILED if executor_fails else RunStatus.DONE
    assert run["status"] == RunStatus.FAILED.value
    assert run["metadata"]["stop_reason"] == expected_reason
    assert "planned Stop hook failure" in run["metadata"]["stop_hook_error"]
    assert run["assistant_message_id"] == ""
    assert run["artifacts"] == []
    assert stopped["input_payload"] == {
        "candidate_status": expected_candidate.value,
        "stop_reason": "executor_error" if executor_fails else "success",
        "artifact_refs": [] if executor_fails else ["artifact://report-1"],
    }
    assert stopped["output_payload"]["terminal_status"] == RunStatus.FAILED.value
    assert stopped["output_payload"]["stop_reason"] == expected_reason
    assert stopped["gate_result"]["decision"] == "deny"
    assert "Stop hook error" in stopped["gate_result"]["reason"]


def test_before_turn_patch_is_bound_to_executed_context_and_trace(
    turn_env: dict[str, Any],
) -> None:
    patch = {"strict_knowledge_only": True, "nested": {"limit": 3}}
    hooks = HookManager()
    hooks.register(
        "BeforeTurn",
        lambda payload: HookDecision(
            action="patch_context",
            reason="test patch",
            context_patch=patch,
        ),
    )
    runtime = turn_env["runtime"](hooks=hooks)
    executor = DeterministicExecutor()
    _register_test_executor(
        runtime,
        skill_id="deterministic_test",
        executor_id="deterministic_test",
        executor=executor,
    )

    result = _run(runtime, turn_env["session_id"])

    run = turn_env["store"].get_agent_run(result["run_id"])
    context_step = next(
        step
        for step in turn_env["store"].list_agent_steps(result["run_id"])
        if step["step_type"] == "context_build"
    )
    audit = context_step["output_payload"]["context_manifest"]["before_turn"]
    persisted_plan = run["metadata"]["prepared_turn"]["execution_plan"]
    assert executor.contexts[0].prepared_context["strict_knowledge_only"] is True
    assert executor.contexts[0].prepared_context["nested"] == {"limit": 3}
    assert persisted_plan["before_turn_context_patch"] == patch
    assert persisted_plan["before_turn_decisions"][0]["action"] == "patch_context"
    assert audit["context_patch_keys"] == ["nested", "strict_knowledge_only"]
    assert audit["context_patch_sha256"] == hashlib.sha256(
        json.dumps(patch, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert audit["decisions"] == persisted_plan["before_turn_decisions"]


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("session_id", "   "),
        ("message", "\t\n"),
        ("mode", " "),
        ("model_id", ""),
    ],
)
def test_blank_required_request_fields_fail_before_before_turn(
    turn_env: dict[str, Any],
    field_name: str,
    value: str,
) -> None:
    before_calls: list[dict[str, Any]] = []
    hooks = HookManager()
    hooks.register(
        "BeforeTurn",
        lambda payload: before_calls.append(payload) or HookDecision(action="allow"),
    )
    runtime = turn_env["runtime"](hooks=hooks)
    request = {
        "session_id": turn_env["session_id"],
        "message": "valid request",
        "mode": "auto",
        "model_id": DEFAULT_MODEL_ID,
    }
    request[field_name] = value

    with pytest.raises(ValueError, match=f"{field_name} is required"):
        runtime.run_turn(**request)

    assert before_calls == []
    assert turn_env["store"].list_chat_messages(turn_env["session_id"]) == []
    assert turn_env["store"].latest_agent_run(turn_env["session_id"]) is None


def test_released_unique_run_locks_are_reclaimed() -> None:
    run_locks = RunExecutionLockRegistry()

    for index in range(250):
        with run_locks.hold(f"run-{index}"):
            assert run_locks.owner(f"run-{index}")

    assert run_locks.tracked_run_count() == 0


def test_blocking_waiter_acquires_before_lock_entry_is_reclaimed() -> None:
    run_locks = RunExecutionLockRegistry()
    waiter_started = threading.Event()
    waiter_acquired = threading.Event()
    waiter_errors: list[BaseException] = []

    def wait_for_run() -> None:
        waiter_started.set()
        try:
            with run_locks.hold("shared-run", blocking=True):
                waiter_acquired.set()
        except BaseException as exc:  # noqa: BLE001
            waiter_errors.append(exc)

    with run_locks.hold("shared-run"):
        waiter = threading.Thread(target=wait_for_run)
        waiter.start()
        assert waiter_started.wait(timeout=2)
        assert not waiter_acquired.wait(timeout=0.05)

    waiter.join(timeout=2)
    assert waiter_errors == []
    assert waiter_acquired.is_set()
    assert run_locks.tracked_run_count() == 0


def test_created_run_transitions_running_then_done(turn_env: dict[str, Any]) -> None:
    runtime = turn_env["runtime"]()
    _register_test_executor(
        runtime,
        skill_id="deterministic_test",
        executor_id="deterministic_test",
        executor=DeterministicExecutor(),
    )
    transitions: list[RunStatus] = []
    original_claim = runtime.store.claim_agent_run
    original_commit = runtime.store.commit_agent_run_terminal

    def record_claim(run_id: str) -> bool:
        claimed = original_claim(run_id)
        if claimed:
            transitions.append(RunStatus.RUNNING)
        return claimed

    def record_terminal(run_id: str, status: RunStatus, **updates: Any) -> str:
        transitions.append(RunStatus(status))
        return original_commit(run_id, status, **updates)

    runtime.store.claim_agent_run = record_claim
    runtime.store.commit_agent_run_terminal = record_terminal

    _run(runtime, turn_env["session_id"])

    assert transitions == [RunStatus.RUNNING, RunStatus.DONE]
