import copy
import hashlib
import importlib
import json
from pathlib import Path
from typing import get_args

import pytest
from pydantic import TypeAdapter, ValidationError

from policy_impact.harness.agent_loop import AgentLoop, AgentLoopConfig, StopReason
from policy_impact.harness.context_manifest import build_context_manifest
from policy_impact.harness.model_gateway import ModelResponse, ScriptedModelAdapter
from policy_impact.harness.permissions import PermissionEngine, ToolPolicy
from policy_impact.harness.state import PolicyImpactState
from policy_impact.harness.tool_gateway import TOOL_REGISTRY, ToolGateway, register_tool
from policy_impact.runtime.execution_mode import ExecutionMode
from policy_impact.runtime.task_brief import SubagentResult
from policy_impact.runtime.subagent_roles import build_default_subagent_role_registry
from policy_impact.skills.manifest import SkillManifest
from policy_impact.skills.registry import SkillRegistry


ALLOWED_ACTIONS = {"final", "tool_call", "delegate", "request_approval"}
EXPECTED_STOP_REASONS = {
    "success",
    "deterministic_complete",
    "max_steps",
    "max_model_calls",
    "timeout",
    "model_error",
    "invalid_model_action",
    "tool_denied",
    "tool_failed",
    "tool_timeout",
    "unknown_outcome",
    "invalid_tool_output",
    "repeated_tool_failure",
    "context_budget_exceeded",
    "integrity_error",
    "persistence_error",
    "awaiting_approval",
    "gate_blocked",
    "cancelled",
    "delegate_failed",
}


class RecordingAdapter:
    def __init__(self, payloads, *, metadata=None):
        self.payloads = list(payloads)
        self.metadata = metadata or {}
        self.message_batches = []

    @property
    def call_count(self):
        return len(self.message_batches)

    def complete(self, messages, schema_name):
        self.message_batches.append(copy.deepcopy(messages))
        index = self.call_count - 1
        return ModelResponse(
            response_id=f"recording_{index + 1}",
            payload=self.payloads[index],
            metadata=copy.deepcopy(self.metadata),
        )


def _config(state: PolicyImpactState, **overrides) -> AgentLoopConfig:
    manifest = build_context_manifest(
        state=state,
        stage_name="agent_stage",
        agent_role="policy_agent",
        visible_fields=["company_context_pack"],
    )
    values = {
        "stage_name": "agent_stage",
        "agent_role": "policy_agent",
        "context_manifest": manifest,
        "output_key": "assessment",
        "max_steps": 4,
        "max_model_calls": 4,
        "timeout_seconds": 30,
        "max_calls_per_tool": 3,
        "max_repeated_failures": 2,
        "task_prompt": "Assess the policy impact.",
    }
    if "max_turns" in overrides and "max_steps" not in overrides:
        values.pop("max_steps")
    values.update(overrides)
    return AgentLoopConfig(**values)


def _actions_module():
    return importlib.import_module("policy_impact.runtime.actions")


def _contains_key(value, forbidden):
    if isinstance(value, dict):
        return any(key in forbidden or _contains_key(item, forbidden) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_key(item, forbidden) for item in value)
    return False


def _gateway(tool_name: str, decision: str = "allow") -> ToolGateway:
    return ToolGateway(
        permission_engine=PermissionEngine(
            [ToolPolicy("agent_stage", tool_name, decision, "test policy")]
        )
    )


def _caller_manifest(*, roles=("skeptic",)) -> SkillManifest:
    base = SkillRegistry().get_manifest("policy_weekly_impact")
    return SkillManifest.model_validate(
        {**base.model_dump(), "allowed_subagents": tuple(roles)}
    )


def _persist_child_records(state, brief, *, artifact_refs=(), **overrides):
    values = {
        "task_id": brief.task_id,
        "parent_run_id": brief.parent_run_id,
        "role": brief.role,
        "role_version": "1.0.0",
        "context_manifest_id": "ctx-child",
        "parent_span_id": brief.parent_span_id,
        "span_id": "span-child",
    }
    values.update(overrides)
    result = SubagentResult(
        **values,
        output={"counterexamples": [{"claim_ref": "claim-1"}]},
        stop_reason="success",
        artifact_refs=artifact_refs,
    )
    role = build_default_subagent_role_registry().get(values["role"], "1.0.0")
    caller_manifest = _caller_manifest()
    allowed_tools = [
        tool for tool in role.allowed_tools if tool in caller_manifest.allowed_tools
    ]
    visible_content = {
        "task_brief": brief.model_dump(mode="json"),
        "input_artifact_refs": list(brief.input_artifact_refs),
    }
    manifest_payload = {
        "brief": visible_content["task_brief"],
        "role_version": values["role_version"],
        "span_id": values["span_id"],
        "allowed_tools": allowed_tools,
    }
    checksum = hashlib.sha256(
        json.dumps(
            manifest_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    context_manifest_id = values.get("context_manifest_id")
    if context_manifest_id == "ctx-child":
        context_manifest_id = f"ctx_subagent_{checksum[:16]}"
        values["context_manifest_id"] = context_manifest_id
        result = SubagentResult(
            **values,
            output={"counterexamples": [{"claim_ref": "claim-1"}]},
            stop_reason="success",
            artifact_refs=artifact_refs,
        )
    state.add_context_manifest(
        {
            "record_id": context_manifest_id,
            "context_type": "subagent_context_manifest",
            "source_id": values["parent_run_id"],
            "checksum": checksum,
            "metadata": {
                "task_id": values["task_id"],
                "parent_run_id": values["parent_run_id"],
                "role": values["role"],
                "role_version": values["role_version"],
                "parent_span_id": values["parent_span_id"],
                "span_id": values["span_id"],
                "output_schema_name": brief.output_schema_name,
                "visible_content": visible_content,
                "allowed_tools": allowed_tools,
            },
        }
    )
    state.add_stage_trace(
        {
            "stage": "subagent_delegate",
            "status": "ok",
            "task_id": values["task_id"],
            "parent_run_id": values["parent_run_id"],
            "role": values["role"],
            "role_version": values["role_version"],
            "parent_span_id": values["parent_span_id"],
            "span_id": values["span_id"],
            "context_manifest_id": values["context_manifest_id"],
            "stop_reason": "success",
        }
    )
    state.add_delegation_evidence(result.evidence().model_dump(mode="json"))
    for ref in artifact_refs:
        state.add_artifact(ref, f"artifact://{ref}")
    return result


def test_agent_action_union_is_discriminated_frozen_and_extra_forbid():
    actions = _actions_module()
    adapter = TypeAdapter(actions.AgentAction)

    action = adapter.validate_python({"type": "final", "output": {"assessment": "low"}})

    assert isinstance(action, actions.FinalAction)
    assert action.model_config["frozen"] is True
    assert action.model_config["extra"] == "forbid"
    with pytest.raises(ValidationError, match="frozen"):
        action.output = {}
    with pytest.raises(ValidationError, match="extra_forbidden"):
        adapter.validate_python(
            {"type": "tool_call", "tool_name": "search", "arguments": {}, "analysis": "x"}
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "final", "output": "not-a-dict"},
        {"type": "tool_call", "tool_name": " ", "arguments": {}},
        {"type": "tool_call", "tool_name": "search", "arguments": []},
        {"type": "delegate", "role": " ", "task": "inspect", "input_artifact_refs": []},
        {"type": "delegate", "role": "reviewer", "task": " ", "input_artifact_refs": []},
        {
            "type": "request_approval",
            "tool_name": " ",
            "arguments": {},
            "reason": "needed",
        },
    ],
)
def test_agent_actions_reject_blank_identifiers_tasks_and_non_dict_payloads(payload):
    actions = _actions_module()

    with pytest.raises(ValidationError):
        TypeAdapter(actions.AgentAction).validate_python(payload)


def test_stop_reason_is_the_exact_runtime_taxonomy():
    assert set(get_args(StopReason)) == EXPECTED_STOP_REASONS


@pytest.mark.parametrize(
    "field",
    [
        "max_steps",
        "max_model_calls",
        "timeout_seconds",
        "max_calls_per_tool",
        "max_repeated_failures",
    ],
)
def test_agent_loop_config_rejects_non_positive_budgets(field):
    state = PolicyImpactState(run_id=f"run_invalid_{field}")

    with pytest.raises(ValueError, match=field):
        _config(state, **{field: 0})


def test_agent_loop_config_defaults_to_agent_loop_mode():
    state = PolicyImpactState(run_id="run_default_execution_mode")

    assert _config(state).execution_mode is ExecutionMode.AGENT_LOOP


def test_deterministic_mode_completes_without_model_or_tool_calls():
    state = PolicyImpactState(run_id="run_deterministic_mode")
    adapter = RecordingAdapter([{"type": "final", "output": {"assessment": "unused"}}])
    gateway = _gateway("unused_tool")

    result = AgentLoop(adapter, gateway).run(
        state,
        _config(state, execution_mode=ExecutionMode.DETERMINISTIC),
    )

    assert result.stop_reason == "deterministic_complete"
    assert result.final_output == {}
    assert result.turns == []
    assert adapter.call_count == 0
    assert gateway.calls == []
    assert state.model_calls == []
    assert result.actual_execution_mode is ExecutionMode.DETERMINISTIC
    assert result.delegation_evidence == ()


def test_model_once_blocks_non_final_action_after_exactly_one_model_call():
    state = PolicyImpactState(run_id="run_model_once_tool_block")
    tool_name = "agent_loop_model_once_tool"
    executions = []
    register_tool(tool_name, lambda: executions.append(True) or {"ok": True})
    adapter = RecordingAdapter(
        [
            {"type": "tool_call", "tool_name": tool_name, "arguments": {}},
            {"type": "final", "output": {"assessment": "must not run"}},
        ]
    )

    try:
        result = AgentLoop(adapter, _gateway(tool_name)).run(
            state,
            _config(
                state,
                execution_mode=ExecutionMode.MODEL_ONCE,
                max_model_calls=4,
            ),
            max_model_calls=8,
        )
    finally:
        TOOL_REGISTRY.pop(tool_name, None)

    assert result.stop_reason == "gate_blocked"
    assert adapter.call_count == 1
    assert executions == []
    assert result.turns[0].error == "gate_blocked"
    assert result.turns[0].observation == {
        "mode_error": {
            "action_type": "tool_call",
            "category": "gate_blocked",
            "execution_mode": "model_once",
        }
    }


def test_model_once_accepts_one_final_action():
    state = PolicyImpactState(run_id="run_model_once_final")
    adapter = RecordingAdapter(
        [
            {"type": "final", "output": {"assessment": "complete"}},
            {"type": "final", "output": {"assessment": "must not run"}},
        ]
    )

    result = AgentLoop(adapter, _gateway("unused_tool")).run(
        state,
        _config(state, execution_mode=ExecutionMode.MODEL_ONCE),
    )

    assert result.stop_reason == "success"
    assert result.final_output == {"assessment": "complete"}
    assert adapter.call_count == 1
    assert result.actual_execution_mode is ExecutionMode.MODEL_ONCE


def test_workflow_mode_is_rejected_before_any_model_or_tool_call():
    state = PolicyImpactState(run_id="run_workflow_rejected")
    adapter = RecordingAdapter([{"type": "final", "output": {"assessment": "unused"}}])
    gateway = _gateway("unused_tool")

    with pytest.raises(ValueError, match="WORKFLOW.*AgentLoop"):
        AgentLoop(adapter, gateway).run(
            state,
            _config(state, execution_mode=ExecutionMode.WORKFLOW),
        )

    assert adapter.call_count == 0
    assert gateway.calls == []


def test_planned_subagent_workflow_without_delegation_reports_model_once():
    state = PolicyImpactState(run_id="run_subagent_workflow_mode")
    adapter = RecordingAdapter(
        [{"type": "final", "output": {"assessment": "subagent complete"}}]
    )

    result = AgentLoop(adapter, _gateway("unused_tool")).run(
        state,
        _config(state, execution_mode=ExecutionMode.SUBAGENT_WORKFLOW),
    )

    assert result.stop_reason == "success"
    assert result.final_output == {"assessment": "subagent complete"}
    assert adapter.call_count == 1
    assert result.actual_execution_mode is ExecutionMode.MODEL_ONCE
    assert result.delegation_evidence == ()


def test_invalid_action_gets_one_sanitized_replan_then_stops():
    state = PolicyImpactState(run_id="run_invalid_action")
    adapter = RecordingAdapter(
        [
            {"type": "unknown", "analysis": "FIRST_SECRET"},
            {"type": "still_unknown", "thinking": "SECOND_SECRET"},
        ]
    )

    result = AgentLoop(adapter, _gateway("unused_tool")).run(state, _config(state))

    assert result.stop_reason == "invalid_model_action"
    assert adapter.call_count == 2
    validation_message = adapter.message_batches[1][-1]
    assert validation_message["role"] == "system"
    assert set(validation_message["content"]) == {"allowed_actions", "validation_errors"}
    assert set(validation_message["content"]["allowed_actions"]) == ALLOWED_ACTIONS
    persisted = json.dumps({"calls": state.model_calls, "turns": result.to_dict()})
    assert "FIRST_SECRET" not in persisted
    assert "SECOND_SECRET" not in persisted
    assert all(
        set(turn.model_payload) <= {"action_type", "validation_errors"}
        for turn in result.turns
    )


def test_reasoning_fields_are_rejected_and_never_persisted():
    state = PolicyImpactState(run_id="run_no_reasoning")
    adapter = RecordingAdapter(
        [
            {
                "type": "final",
                "output": {"assessment": "must not persist yet"},
                "reasoning": "PAYLOAD_SECRET",
            },
            {"type": "final", "output": {"assessment": "safe"}},
        ],
        metadata={"analysis": "METADATA_SECRET", "provider": "test"},
    )

    result = AgentLoop(adapter, _gateway("unused_tool")).run(state, _config(state))

    assert result.stop_reason == "success"
    assert result.final_output == {"assessment": "safe"}
    persisted = {"calls": state.model_calls, "turns": result.to_dict()}
    serialized = json.dumps(persisted)
    assert "PAYLOAD_SECRET" not in serialized
    assert "METADATA_SECRET" not in serialized
    assert not _contains_key(persisted, {"reasoning", "thinking", "analysis"})


def test_model_call_budget_is_enforced_before_an_extra_call():
    state = PolicyImpactState(run_id="run_model_budget")
    tool_name = "agent_loop_model_budget_tool"
    register_tool(tool_name, lambda: {"ok": True})
    adapter = RecordingAdapter(
        [
            {"type": "tool_call", "tool_name": tool_name, "arguments": {}},
            {"type": "final", "output": {"assessment": "too late"}},
        ]
    )

    try:
        result = AgentLoop(adapter, _gateway(tool_name)).run(
            state,
            _config(state),
            max_model_calls=1,
            max_steps=4,
        )
    finally:
        TOOL_REGISTRY.pop(tool_name, None)

    assert result.stop_reason == "max_model_calls"
    assert adapter.call_count == 1


def test_step_budget_is_enforced():
    state = PolicyImpactState(run_id="run_step_budget")
    tool_name = "agent_loop_step_budget_tool"
    register_tool(tool_name, lambda: {"ok": True})
    adapter = RecordingAdapter(
        [
            {"type": "tool_call", "tool_name": tool_name, "arguments": {}},
            {"type": "final", "output": {"assessment": "too late"}},
        ]
    )

    try:
        result = AgentLoop(adapter, _gateway(tool_name)).run(
            state, _config(state, max_steps=1, max_model_calls=3)
        )
    finally:
        TOOL_REGISTRY.pop(tool_name, None)

    assert result.stop_reason == "max_steps"
    assert adapter.call_count == 1


def test_monotonic_deadline_stops_before_model_call(monkeypatch):
    state = PolicyImpactState(run_id="run_timeout")
    adapter = RecordingAdapter([{"type": "final", "output": {"assessment": "late"}}])
    readings = iter([10.0, 12.0])
    monkeypatch.setattr("policy_impact.harness.agent_loop.time.monotonic", lambda: next(readings))

    result = AgentLoop(adapter, _gateway("unused_tool")).run(
        state, _config(state, timeout_seconds=1)
    )

    assert result.stop_reason == "timeout"
    assert adapter.call_count == 0


def test_per_tool_call_budget_prevents_extra_execution():
    state = PolicyImpactState(run_id="run_tool_budget")
    tool_name = "agent_loop_call_budget_tool"
    executions = []
    register_tool(tool_name, lambda query: executions.append(query) or {"query": query})
    adapter = RecordingAdapter(
        [
            {"type": "tool_call", "tool_name": tool_name, "arguments": {"query": "one"}},
            {"type": "tool_call", "tool_name": tool_name, "arguments": {"query": "two"}},
        ]
    )

    try:
        result = AgentLoop(adapter, _gateway(tool_name)).run(
            state, _config(state, max_calls_per_tool=1)
        )
    finally:
        TOOL_REGISTRY.pop(tool_name, None)

    assert result.stop_reason == "tool_denied"
    assert executions == ["one"]


def test_repeated_identical_tool_failure_uses_normalized_argument_hash():
    state = PolicyImpactState(run_id="run_repeated_failure")
    tool_name = "agent_loop_repeated_failure_tool"

    def fail(**kwargs):
        raise RuntimeError("PRIVATE_FAILURE_DETAIL")

    register_tool(tool_name, fail)
    adapter = RecordingAdapter(
        [
            {
                "type": "tool_call",
                "tool_name": tool_name,
                "arguments": {"company_id": "c1", "query": "missing"},
            },
            {
                "type": "tool_call",
                "tool_name": tool_name,
                "arguments": {"query": "missing", "company_id": "c1"},
            },
            {"type": "final", "output": {"assessment": "too late"}},
        ]
    )

    try:
        result = AgentLoop(adapter, _gateway(tool_name)).run(state, _config(state))
    finally:
        TOOL_REGISTRY.pop(tool_name, None)

    assert result.stop_reason == "repeated_tool_failure"
    assert adapter.call_count == 2
    assert "PRIVATE_FAILURE_DETAIL" not in json.dumps(result.to_dict())
    assert result.turns[0].observation["tool_error"]["category"] == "tool_failed"


def test_tool_failure_observation_allows_bounded_replan():
    state = PolicyImpactState(run_id="run_tool_replan")
    tool_name = "agent_loop_failure_replan_tool"
    register_tool(tool_name, lambda: (_ for _ in ()).throw(RuntimeError("PRIVATE_DETAIL")))
    adapter = RecordingAdapter(
        [
            {"type": "tool_call", "tool_name": tool_name, "arguments": {}},
            {"type": "final", "output": {"assessment": "replanned"}},
        ]
    )

    try:
        result = AgentLoop(adapter, _gateway(tool_name)).run(state, _config(state))
    finally:
        TOOL_REGISTRY.pop(tool_name, None)

    assert result.stop_reason == "success"
    assert result.final_output == {"assessment": "replanned"}
    assert result.turns[0].observation == {
        "tool_error": {"category": "tool_failed", "tool_name": tool_name}
    }
    assert "PRIVATE_DETAIL" not in json.dumps(result.to_dict())


def test_timeout_error_maps_to_tool_timeout():
    state = PolicyImpactState(run_id="run_tool_timeout")
    tool_name = "agent_loop_timeout_tool"
    register_tool(tool_name, lambda: (_ for _ in ()).throw(TimeoutError("PRIVATE_TIMEOUT")))
    adapter = RecordingAdapter(
        [{"type": "tool_call", "tool_name": tool_name, "arguments": {}}]
    )

    try:
        result = AgentLoop(adapter, _gateway(tool_name)).run(state, _config(state))
    finally:
        TOOL_REGISTRY.pop(tool_name, None)

    assert result.stop_reason == "tool_timeout"
    assert result.turns[0].observation["tool_error"]["category"] == "tool_timeout"
    assert "PRIVATE_TIMEOUT" not in json.dumps(result.to_dict())


def test_invalid_tool_output_fails_closed_without_stringifying_object():
    class UnsafeResult:
        def __str__(self):
            raise AssertionError("arbitrary result must not be stringified")

    state = PolicyImpactState(run_id="run_invalid_tool_output")
    tool_name = "agent_loop_invalid_output_tool"
    register_tool(tool_name, UnsafeResult)
    adapter = RecordingAdapter(
        [{"type": "tool_call", "tool_name": tool_name, "arguments": {}}]
    )

    try:
        result = AgentLoop(adapter, _gateway(tool_name)).run(state, _config(state))
    finally:
        TOOL_REGISTRY.pop(tool_name, None)

    assert result.stop_reason == "invalid_tool_output"
    assert result.turns[0].observation == {
        "tool_error": {"category": "invalid_tool_output", "tool_name": tool_name}
    }


def test_request_approval_stops_without_executing_tool():
    state = PolicyImpactState(run_id="run_request_approval")
    tool_name = "agent_loop_approval_tool"
    gateway = _gateway(tool_name)
    adapter = RecordingAdapter(
        [
            {
                "type": "request_approval",
                "tool_name": tool_name,
                "arguments": {"query": "policy"},
                "reason": "external write",
            }
        ]
    )

    result = AgentLoop(adapter, gateway).run(state, _config(state))

    assert result.stop_reason == "awaiting_approval"
    assert gateway.calls == []


def test_absent_delegate_capability_adds_denied_observation_then_replans():
    state = PolicyImpactState(run_id="run_delegate_denied")
    gateway = _gateway("unused_tool")
    adapter = RecordingAdapter(
        [
            {
                "type": "delegate",
                "role": "skeptic",
                "task": "Review the evidence",
                "input_artifact_refs": [],
            },
            {"type": "final", "output": {"assessment": "completed locally"}},
        ]
    )

    result = AgentLoop(adapter, gateway).run(state, _config(state))

    assert result.stop_reason == "success"
    assert gateway.calls == []
    assert result.turns[0].observation == {
        "delegate_error": {"category": "tool_denied", "role": "skeptic"}
    }
    assert adapter.call_count == 2
    assert result.actual_execution_mode is ExecutionMode.AGENT_LOOP
    assert result.delegation_evidence == ()


def test_successful_delegate_receives_task_brief_and_returns_only_public_result():
    state = PolicyImpactState(run_id="run-delegate-success")
    captured = []
    adapter = RecordingAdapter(
        [
            {
                "type": "delegate",
                "role": "skeptic",
                "task": "Challenge claim-1",
                "input_artifact_refs": ["claim-1"],
            },
            {"type": "final", "output": {"assessment": "verified after challenge"}},
        ]
    )

    def delegate(brief):
        captured.append(brief)
        return _persist_child_records(
            state,
            brief,
            artifact_refs=("counterexample-1",),
        )

    result = AgentLoop(
        adapter,
        _gateway("unused_tool"),
        delegate_fn=delegate,
        skill_manifest=_caller_manifest(),
    ).run(state, _config(state))

    assert len(captured) == 1
    brief = captured[0]
    assert brief.parent_run_id == state.run_id
    assert brief.role == "skeptic"
    assert brief.task == "Challenge claim-1"
    assert brief.input_artifact_refs == ("claim-1",)
    assert brief.output_schema_name == "counterexample_set.v1"
    assert result.actual_execution_mode is ExecutionMode.SUBAGENT_WORKFLOW
    assert len(result.delegation_evidence) == 1
    assert result.delegation_evidence[0].span_id == "span-child"
    observation = result.turns[0].observation["subagent_result"]
    assert {"role", "output", "artifact_refs"}.issubset(observation)
    assert observation["task_id"] == brief.task_id
    assert observation["stop_reason"] == "success"
    assert observation["context_manifest_id"].startswith("ctx_subagent_")
    assert observation["output"] == {"counterexamples": [{"claim_ref": "claim-1"}]}
    assert observation["artifact_refs"] == ["counterexample-1"]
    assert "ctx-child" not in json.dumps(adapter.message_batches[1])


def test_delegate_exception_is_sanitized_and_allows_one_replan():
    state = PolicyImpactState(run_id="run-delegate-error")
    adapter = RecordingAdapter(
        [
            {
                "type": "delegate",
                "role": "skeptic",
                "task": "SENSITIVE_DELEGATE_TASK",
                "input_artifact_refs": ["SECRET_ARTIFACT_REF"],
            },
            {"type": "final", "output": {"assessment": "completed locally"}},
        ]
    )

    def fail_delegate(brief):
        raise RuntimeError(f"PRIVATE_EXCEPTION::{brief.task}::{brief.input_artifact_refs[0]}")

    result = AgentLoop(
        adapter,
        _gateway("unused_tool"),
        delegate_fn=fail_delegate,
        skill_manifest=_caller_manifest(),
    ).run(state, _config(state))

    assert result.stop_reason == "success"
    assert result.actual_execution_mode is ExecutionMode.AGENT_LOOP
    assert result.delegation_evidence == ()
    assert result.turns[0].observation == {
        "delegate_error": {"category": "delegate_failed", "role": "skeptic"}
    }
    persisted = json.dumps(result.to_dict())
    assert "PRIVATE_EXCEPTION" not in persisted
    assert "SENSITIVE_DELEGATE_TASK" not in persisted
    assert "SECRET_ARTIFACT_REF" not in persisted


def test_delegate_fn_is_not_called_when_caller_manifest_disallows_role():
    state = PolicyImpactState(run_id="run-delegate-role-denied")
    called = []
    adapter = RecordingAdapter(
        [
            {
                "type": "delegate",
                "role": "skeptic",
                "task": "Challenge claim",
                "input_artifact_refs": [],
            },
            {"type": "final", "output": {"assessment": "local fallback"}},
        ]
    )

    def delegate(brief):
        called.append(brief)
        raise AssertionError("disallowed delegate must not run")

    result = AgentLoop(
        adapter,
        _gateway("unused_tool"),
        delegate_fn=delegate,
        skill_manifest=_caller_manifest(roles=("collector",)),
    ).run(state, _config(state))

    assert called == []
    assert result.actual_execution_mode is ExecutionMode.AGENT_LOOP
    assert result.delegation_evidence == ()
    assert result.turns[0].observation == {
        "delegate_error": {"category": "tool_denied", "role": "skeptic"}
    }


@pytest.mark.parametrize(
    ("overrides", "mutation"),
    [
        ({"parent_run_id": "forged-run"}, None),
        ({"role_version": "9.9.9"}, None),
        ({"context_manifest_id": "ctx-forged"}, "drop_context"),
        ({"span_id": "span-forged"}, "drop_trace"),
        ({}, "drop_evidence"),
    ],
)
def test_forged_or_incomplete_child_records_never_enable_subagent_mode(overrides, mutation):
    state = PolicyImpactState(run_id="run-forged-delegation")
    adapter = RecordingAdapter(
        [
            {
                "type": "delegate",
                "role": "skeptic",
                "task": "Challenge claim",
                "input_artifact_refs": [],
            },
            {"type": "final", "output": {"assessment": "local fallback"}},
        ]
    )

    def delegate(brief):
        result = _persist_child_records(state, brief, **overrides)
        if mutation == "drop_context":
            state.context_manifests.clear()
        elif mutation == "drop_trace":
            state.stage_trace.clear()
        elif mutation == "drop_evidence":
            state.delegation_evidence.clear()
        return result

    result = AgentLoop(
        adapter,
        _gateway("unused_tool"),
        delegate_fn=delegate,
        skill_manifest=_caller_manifest(),
    ).run(state, _config(state))

    assert result.stop_reason == "success"
    assert result.actual_execution_mode is ExecutionMode.AGENT_LOOP
    assert result.delegation_evidence == ()
    assert result.turns[0].observation["delegate_error"]["category"] == "delegate_failed"


def test_delegate_rejects_model_reported_unknown_artifact_refs():
    state = PolicyImpactState(run_id="run-forged-artifact-ref")
    adapter = RecordingAdapter(
        [
            {
                "type": "delegate",
                "role": "skeptic",
                "task": "Challenge claim",
                "input_artifact_refs": [],
            },
            {"type": "final", "output": {"assessment": "local fallback"}},
        ]
    )

    def delegate(brief):
        result = _persist_child_records(
            state,
            brief,
            artifact_refs=("unknown-artifact",),
        )
        state.artifacts.clear()
        return result

    result = AgentLoop(
        adapter,
        _gateway("unused_tool"),
        delegate_fn=delegate,
        skill_manifest=_caller_manifest(),
    ).run(state, _config(state))

    assert result.actual_execution_mode is ExecutionMode.AGENT_LOOP
    assert result.delegation_evidence == ()


def test_final_action_recursively_removes_hidden_reasoning_from_output_and_audit():
    state = PolicyImpactState(run_id="run-final-reasoning-sanitize")
    adapter = RecordingAdapter(
        [
            {
                "type": "final",
                "output": {
                    "answer": "public",
                    "nested": {
                        "reasoning": "PRIVATE_REASONING",
                        "items": [{"analysis": "PRIVATE_ANALYSIS", "value": 1}],
                    },
                },
            }
        ]
    )

    result = AgentLoop(adapter, _gateway("unused_tool")).run(state, _config(state))

    serialized = json.dumps({"result": result.to_dict(), "calls": state.model_calls})
    assert "PRIVATE_REASONING" not in serialized
    assert "PRIVATE_ANALYSIS" not in serialized
    assert result.final_output == {"answer": "public", "nested": {"items": [{"value": 1}]}}


@pytest.mark.parametrize(
    "hidden_key",
    ["chain_of_thought", "private_reasoning", "reasoning_text"],
)
def test_final_action_removes_hidden_reasoning_aliases(hidden_key):
    state = PolicyImpactState(run_id=f"run-hidden-{hidden_key}")
    adapter = RecordingAdapter(
        [{"type": "final", "output": {"answer": "public", hidden_key: "SECRET"}}]
    )

    result = AgentLoop(adapter, _gateway("unused_tool")).run(state, _config(state))

    assert result.final_output == {"answer": "public"}
    assert "SECRET" not in json.dumps({"result": result.to_dict(), "calls": state.model_calls})


def test_delegate_denial_does_not_consume_invalid_action_repair():
    state = PolicyImpactState(run_id="run_delegate_then_invalid")
    adapter = RecordingAdapter(
        [
            {
                "type": "delegate",
                "role": "skeptic",
                "task": "Review the evidence",
                "input_artifact_refs": [],
            },
            {"type": "unknown", "analysis": "PRIVATE_INVALID_VALUE"},
            {"type": "final", "output": {"assessment": "repaired"}},
        ]
    )

    result = AgentLoop(adapter, _gateway("unused_tool")).run(
        state,
        _config(state, max_steps=4, max_model_calls=4),
    )

    assert result.stop_reason == "success"
    assert result.final_output == {"assessment": "repaired"}
    assert adapter.call_count == 3
    assert adapter.message_batches[2][-1]["role"] == "system"
    assert set(adapter.message_batches[2][-1]["content"]) == {
        "allowed_actions",
        "validation_errors",
    }
    assert "PRIVATE_INVALID_VALUE" not in json.dumps(result.to_dict())


def test_agent_loop_calls_allowed_tool_then_finishes_successfully():
    state = PolicyImpactState(run_id="run_agent_loop")
    tool_name = "agent_loop_test_tool"
    register_tool(tool_name, lambda query: {"answer": f"observed {query}"})
    adapter = ScriptedModelAdapter(
        [
            {"type": "tool_call", "tool_name": tool_name, "arguments": {"query": "policy"}},
            {"type": "final", "output": {"assessment": "low impact"}},
        ]
    )

    try:
        result = AgentLoop(adapter, _gateway(tool_name)).run(state, _config(state))
    finally:
        TOOL_REGISTRY.pop(tool_name, None)

    assert result.stop_reason == "success"
    assert result.final_output == {"assessment": "low impact"}
    assert len(result.turns) == 2
    assert result.turns[0].observation == {"tool_result": {"answer": "observed policy"}}
    assert [call["response_id"] for call in state.model_calls] == ["scripted_1", "scripted_2"]
    assert all(call["stage_name"] == "agent_stage" for call in state.model_calls)
    assert all(call["agent_role"] == "policy_agent" for call in state.model_calls)
    assert result.actual_execution_mode is ExecutionMode.AGENT_LOOP


def test_agent_loop_sends_assistant_tool_call_before_tool_observation():
    class RecordingAdapter:
        def __init__(self):
            self.message_batches = []
            self.payloads = [
                {"type": "tool_call", "tool_name": tool_name, "arguments": {"query": "policy"}},
                {"type": "final", "output": {"assessment": "low impact"}},
            ]

        def complete(self, messages, schema_name):
            self.message_batches.append(copy.deepcopy(messages))
            response_index = len(self.message_batches)
            return ModelResponse(
                response_id=f"recording_{response_index}",
                payload=self.payloads[response_index - 1],
            )

    state = PolicyImpactState(run_id="run_message_contract")
    tool_name = "agent_loop_message_contract_tool"
    register_tool(tool_name, lambda query: {"answer": query})
    adapter = RecordingAdapter()

    try:
        result = AgentLoop(adapter, _gateway(tool_name)).run(state, _config(state))
    finally:
        TOOL_REGISTRY.pop(tool_name, None)

    assert result.stop_reason == "success"
    second_call_messages = adapter.message_batches[1]
    assert [message["role"] for message in second_call_messages] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]
    assert second_call_messages[2] == {
        "role": "assistant",
        "response_id": "recording_1",
        "content": {
            "type": "tool_call",
            "tool_name": tool_name,
            "arguments": {"query": "policy"},
        },
    }
    assert second_call_messages[3] == {
        "role": "tool",
        "tool_name": tool_name,
        "content": {"tool_result": {"answer": "policy"}},
    }


def test_agent_loop_normalizes_tool_observation_to_json_safe_content():
    state = PolicyImpactState(run_id="run_json_safe_observation")
    tool_name = "agent_loop_json_safe_tool"
    register_tool(tool_name, lambda: {"ids": {2, 1}, "path": Path("policy.txt")})
    adapter = ScriptedModelAdapter(
        [
            {"type": "tool_call", "tool_name": tool_name, "arguments": {}},
            {"type": "final", "output": {"assessment": "serialized"}},
        ]
    )

    try:
        result = AgentLoop(adapter, _gateway(tool_name)).run(state, _config(state))
    finally:
        TOOL_REGISTRY.pop(tool_name, None)

    assert result.turns[0].observation == {
        "tool_result": {"ids": [1, 2], "path": "policy.txt"}
    }
    assert json.loads(json.dumps(result.to_dict()))["turns"][0]["observation"] == {
        "tool_result": {"ids": [1, 2], "path": "policy.txt"}
    }


def test_agent_loop_stops_with_tool_denied_when_permission_blocks_tool():
    state = PolicyImpactState(run_id="run_tool_denied")
    tool_name = "agent_loop_denied_tool"
    adapter = ScriptedModelAdapter(
        [{"type": "tool_call", "tool_name": tool_name, "arguments": {"query": "policy"}}]
    )

    result = AgentLoop(adapter, _gateway(tool_name, decision="deny")).run(state, _config(state))

    assert result.stop_reason == "tool_denied"
    assert result.final_output == {}
    assert len(result.turns) == 1
    assert result.turns[0].error == "tool_denied"
    assert result.turns[0].observation == {
        "tool_error": {"category": "tool_denied", "tool_name": tool_name}
    }
    assert len(state.model_calls) == 1


def test_legacy_max_turns_constructor_maps_to_max_steps_stop_reason():
    state = PolicyImpactState(run_id="run_max_turns")
    tool_name = "agent_loop_repeat_tool"
    register_tool(tool_name, lambda query: {"answer": query})
    adapter = ScriptedModelAdapter(
        [
            {"type": "tool_call", "tool_name": tool_name, "arguments": {"query": "one"}},
            {"type": "tool_call", "tool_name": tool_name, "arguments": {"query": "two"}},
        ]
    )

    try:
        result = AgentLoop(adapter, _gateway(tool_name)).run(state, _config(state, max_turns=2))
    finally:
        TOOL_REGISTRY.pop(tool_name, None)

    assert result.stop_reason == "max_steps"
    assert result.final_output == {}
    assert len(result.turns) == 2
    assert len(state.model_calls) == 2


def test_agent_loop_returns_model_error_when_adapter_raises():
    class RaisingAdapter:
        def complete(self, messages, schema_name):
            raise RuntimeError("model unavailable")

    state = PolicyImpactState(run_id="run_model_error")

    result = AgentLoop(RaisingAdapter(), _gateway("unused_tool")).run(state, _config(state))

    assert result.stop_reason == "model_error"
    assert result.final_output == {}
    assert len(result.turns) == 1
    assert result.turns[0].error == "model unavailable"
    assert state.errors[-1]["message"] == "model_error"
