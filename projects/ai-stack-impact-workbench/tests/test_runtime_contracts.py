import pytest

from policy_impact.runtime.execution_context import SkillExecutionContext, SkillExecutor
from policy_impact.runtime.execution_mode import ExecutionMode
from policy_impact.runtime.gates import GateDefinition, GateDecision, GateRunner, GateRegistry
from policy_impact.runtime.hooks import HookDecision, HookManager
from policy_impact.runtime.result import SkillResult
from policy_impact.runtime.run_status import (
    LEGAL_RUN_TRANSITIONS,
    RunStatus,
    require_run_transition,
)
from pydantic import BaseModel, ValidationError


class GatePayload(BaseModel):
    evidence_refs: tuple[str, ...] = ()


class RecordingGateDecisionSink:
    def __init__(self):
        self.records = []

    def record(self, run_id, span_id, decision):
        self.records.append((run_id, span_id, decision))
        return f"gate-record-{len(self.records)}"


def test_execution_mode_values_are_stable():
    assert [item.value for item in ExecutionMode] == [
        "deterministic",
        "model_once",
        "agent_loop",
        "workflow",
        "subagent_workflow",
    ]


def test_run_status_and_recovery_transitions_are_frozen():
    assert {item.value for item in RunStatus} == {
        "created",
        "running",
        "awaiting_approval",
        "resuming",
        "done",
        "failed",
        "cancelled",
        "interrupted_recoverable",
        "interrupted_failed",
    }
    require_run_transition(RunStatus.RUNNING, RunStatus.INTERRUPTED_RECOVERABLE)
    with pytest.raises(ValueError, match="illegal Run transition"):
        require_run_transition(RunStatus.DONE, RunStatus.RUNNING)


def test_run_transition_table_and_values_are_read_only():
    with pytest.raises(TypeError):
        LEGAL_RUN_TRANSITIONS[RunStatus.DONE] = frozenset({RunStatus.RUNNING})

    running_targets = LEGAL_RUN_TRANSITIONS[RunStatus.RUNNING]
    assert isinstance(running_targets, frozenset)
    with pytest.raises(AttributeError):
        running_targets.add(RunStatus.CREATED)


def test_skill_result_rejects_unknown_execution_mode():
    try:
        SkillResult(
            answer="x",
            actual_execution_mode="pretend",
            stop_reason="success",
        )
    except ValueError:
        return
    raise AssertionError("unknown execution mode must fail closed")


def test_skill_executor_protocol_is_runtime_checkable():
    class Executor:
        def execute(self, context: SkillExecutionContext) -> SkillResult:
            return SkillResult(
                answer=context.message,
                actual_execution_mode=ExecutionMode.MODEL_ONCE,
                stop_reason="success",
            )

    assert isinstance(Executor(), SkillExecutor)


def test_skill_execution_context_mappings_are_defensive_read_only_snapshots():
    context_manifest = {"policy": {"allowed_tools": ["search"]}}
    prepared_context = {"evidence": [{"id": "e1"}]}
    services = {"gate": {"enabled": True}}
    context = SkillExecutionContext(
        run_id="r1",
        session_id="s1",
        company_id="c1",
        message="assess policy impact",
        model_id="model-1",
        context_manifest=context_manifest,
        prepared_context=prepared_context,
        services=services,
    )

    context_manifest["policy"]["allowed_tools"].append("write")
    context_manifest["new"] = "source mutation"
    prepared_context["evidence"][0]["id"] = "changed"
    services["gate"]["enabled"] = False

    assert context.context_manifest["policy"]["allowed_tools"] == ("search",)
    assert "new" not in context.context_manifest
    assert context.prepared_context["evidence"][0]["id"] == "e1"
    assert context.services["gate"]["enabled"] is True

    with pytest.raises(TypeError):
        context.context_manifest["new"] = "exposed mutation"
    with pytest.raises(TypeError):
        context.prepared_context["evidence"][0]["id"] = "exposed mutation"
    with pytest.raises(TypeError):
        context.services["gate"]["enabled"] = False
    with pytest.raises(AttributeError):
        context.context_manifest["policy"]["allowed_tools"].append("write")


def test_hook_manager_preserves_registration_order():
    hooks = HookManager()
    hooks.register("BeforeTurn", lambda payload: HookDecision(action="allow", reason="first"))
    hooks.register("BeforeTurn", lambda payload: HookDecision(action="emit_event", reason="second"))
    assert [item.reason for item in hooks.run("BeforeTurn", {})] == ["first", "second"]


def test_unknown_gate_fails_closed():
    sink = RecordingGateDecisionSink()
    decision = GateRunner(GateRegistry(), sink).evaluate(
        run_id="r1", span_id="s1", gate_id="missing", version="1.0.0", payload={}
    )
    assert decision.decision == "block"
    assert decision.reason == "unknown gate: missing@1.0.0"
    assert len(sink.records) == 1
    recorded = sink.records[0][2]
    assert sink.records[0][:2] == ("r1", "s1")
    assert recorded == decision.model_copy(update={"audit_ref": None})
    assert decision.audit_ref == "gate-record-1"


@pytest.mark.parametrize(
    "malformed_return",
    [
        None,
        {
            "gate_id": "evidence_binding",
            "gate_version": "1.0.0",
            "decision": "pass",
            "reason": "dicts are not decisions",
        },
    ],
    ids=["none", "dict"],
)
def test_malformed_gate_return_fails_closed_and_is_recorded(malformed_return):
    registry = GateRegistry()
    registry.register(
        GateDefinition(
            gate_id="evidence_binding",
            version="1.0.0",
            description="requires evidence refs",
            input_schema=GatePayload,
                hard_constraint=True,
                repairable=True,
                allowed_repair_actions=("retry",),
        ),
        lambda payload: malformed_return,
    )
    sink = RecordingGateDecisionSink()

    decision = GateRunner(registry, sink).evaluate(
        run_id="r1",
        span_id="s1",
        gate_id="evidence_binding",
        version="1.0.0",
        payload={},
    )

    assert decision.decision == "block"
    assert decision.reason == "gate evaluation failed: TypeError"
    assert sink.records == [
        ("r1", "s1", decision.model_copy(update={"audit_ref": None}))
    ]


def test_gate_decision_is_frozen():
    decision = GateDecision(
        gate_id="evidence_binding",
        gate_version="1.0.0",
        decision="block",
        reason="missing evidence",
    )

    with pytest.raises(ValidationError, match="frozen"):
        decision.reason = "changed"


def test_gate_decision_repair_action_is_a_defensive_read_only_snapshot():
    source = {
        "action": "retry",
        "arguments": {"evidence_refs": ["e1"]},
    }
    decision = GateDecision(
        gate_id="evidence_binding",
        gate_version="1.0.0",
        decision="repair",
        reason="attach evidence",
        repair_action=source,
    )

    source["action"] = "bypass"
    source["arguments"]["evidence_refs"].append("untrusted")

    assert decision.repair_action["action"] == "retry"
    assert decision.repair_action["arguments"]["evidence_refs"] == ("e1",)
    with pytest.raises(TypeError):
        decision.repair_action["action"] = "bypass"
    with pytest.raises(TypeError):
        decision.repair_action["arguments"]["new"] = True
    with pytest.raises(AttributeError):
        decision.repair_action["arguments"]["evidence_refs"].append("untrusted")

    assert decision.model_dump(mode="python")["repair_action"] == {
        "action": "retry",
        "arguments": {"evidence_refs": ["e1"]},
    }
    assert '"repair_action":{"action":"retry"' in decision.model_dump_json()


@pytest.mark.parametrize(
    "invalid_value",
    [
        {"unordered": {"a", "b"}},
        {"binary": b"not-json"},
        {"number": float("inf")},
        {"nested": {1: "non-string-key"}},
    ],
    ids=["set", "bytes", "infinite-float", "non-string-key"],
)
def test_gate_decision_repair_action_rejects_non_deterministic_json(invalid_value):
    with pytest.raises(ValidationError, match="repair_action"):
        GateDecision(
            gate_id="evidence_binding",
            gate_version="1.0.0",
            decision="repair",
            reason="invalid repair action",
            repair_action=invalid_value,
        )


def test_gate_decision_repair_action_round_trip_is_stable():
    decision = GateDecision(
        gate_id="evidence_binding",
        gate_version="1.0.0",
        decision="repair",
        reason="attach evidence",
        repair_action={"z": [2, 1], "a": {"nested": True}},
    )

    dumped = decision.model_dump(mode="python")
    rebuilt = GateDecision.model_validate(dumped)

    assert rebuilt == decision
    assert rebuilt.model_dump_json() == decision.model_dump_json()
    assert list(dumped["repair_action"]) == ["a", "z"]


@pytest.mark.parametrize(
    ("decision", "repair_action"),
    [
        ("repair", None),
        ("repair", {}),
        ("pass", {"action": "retry"}),
        ("block", {"action": "retry"}),
    ],
)
def test_gate_decision_repair_action_invariants(decision, repair_action):
    with pytest.raises(ValidationError, match="repair_action"):
        GateDecision(
            gate_id="evidence_binding",
            gate_version="1.0.0",
            decision=decision,
            reason="invalid repair state",
            repair_action=repair_action,
        )


def test_invalid_gate_decision_instance_fails_closed_and_is_recorded():
    invalid_decision = GateDecision.model_construct(
        gate_id="evidence_binding",
        gate_version="1.0.0",
        decision="pass",
        reason="invalid repair state",
        repair_action={"action": "retry"},
    )
    registry = GateRegistry()
    registry.register(
        GateDefinition(
            gate_id="evidence_binding",
            version="1.0.0",
            description="requires evidence refs",
            input_schema=GatePayload,
                hard_constraint=True,
                repairable=True,
                allowed_repair_actions=("retry",),
        ),
        lambda payload: invalid_decision,
    )
    sink = RecordingGateDecisionSink()

    decision = GateRunner(registry, sink).evaluate(
        run_id="r1",
        span_id="s1",
        gate_id="evidence_binding",
        version="1.0.0",
        payload={},
    )

    assert decision.decision == "block"
    assert decision.reason == "gate evaluation failed: ValidationError"
    assert decision.repair_action is None
    assert sink.records == [
        ("r1", "s1", decision.model_copy(update={"audit_ref": None}))
    ]


def test_non_repairable_gate_cannot_return_repair():
    registry = GateRegistry()
    registry.register(
        GateDefinition(
            gate_id="evidence_binding",
            version="1.0.0",
            description="requires evidence refs",
            input_schema=GatePayload,
            hard_constraint=True,
            repairable=False,
        ),
        lambda payload: GateDecision(
            gate_id="evidence_binding",
            gate_version="1.0.0",
            decision="repair",
            reason="attach evidence",
            repair_action={"action": "attach_evidence"},
        ),
    )
    sink = RecordingGateDecisionSink()
    decision = GateRunner(registry, sink).evaluate(
        run_id="r1",
        span_id="s1",
        gate_id="evidence_binding",
        version="1.0.0",
        payload={},
    )
    assert decision.decision == "block"
    assert decision.reason == "gate is not repairable"
    assert decision.repair_action is None
    assert len(sink.records) == 1
