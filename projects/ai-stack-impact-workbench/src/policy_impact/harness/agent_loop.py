"""Controlled model/tool loop for policy impact agents."""

from __future__ import annotations

import json
import time
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, is_dataclass
from hashlib import sha256
from math import isfinite
from os import PathLike
from typing import Any
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from policy_impact.harness.contracts import ContextManifestRecord
from policy_impact.harness.model_gateway import ModelAdapter, ModelResponse
from policy_impact.harness.state import PolicyImpactState, utc_now_iso
from policy_impact.harness.tool_gateway import ToolGateway
from policy_impact.runtime.actions import (
    ALLOWED_ACTION_NAMES,
    AgentAction,
    DelegateAction,
    FinalAction,
    RequestApprovalAction,
    StopReason,
    ToolCallAction,
)
from policy_impact.runtime.execution_mode import ExecutionMode
from policy_impact.runtime.subagent_roles import (
    SubagentRoleRegistry,
    build_default_subagent_role_registry,
)
from policy_impact.runtime.task_brief import DelegationEvidence, SubagentResult, TaskBrief
from policy_impact.skills.manifest import SkillManifest


_ACTION_ADAPTER = TypeAdapter(AgentAction)
_HIDDEN_REASONING_FIELDS = frozenset({"reasoning", "thinking", "analysis"})
_OMIT = object()


@dataclass(init=False)
class AgentLoopConfig:
    stage_name: str
    agent_role: str
    context_manifest: ContextManifestRecord
    output_key: str
    execution_mode: ExecutionMode
    max_steps: int
    max_model_calls: int
    timeout_seconds: float
    max_calls_per_tool: int
    max_repeated_failures: int
    task_prompt: str

    def __init__(
        self,
        stage_name: str,
        agent_role: str,
        context_manifest: ContextManifestRecord,
        output_key: str,
        max_steps: int | None = None,
        max_model_calls: int = 4,
        timeout_seconds: float = 120,
        max_calls_per_tool: int = 3,
        max_repeated_failures: int = 2,
        task_prompt: str = "",
        *,
        max_turns: int | None = None,
        execution_mode: ExecutionMode = ExecutionMode.AGENT_LOOP,
    ) -> None:
        if max_steps is not None and max_turns is not None and max_steps != max_turns:
            raise ValueError("max_steps and legacy max_turns must match when both are provided")
        self.stage_name = stage_name
        self.agent_role = agent_role
        self.context_manifest = context_manifest
        self.output_key = output_key
        self.execution_mode = ExecutionMode(execution_mode)
        self.max_steps = _positive_int(
            "max_steps",
            max_steps if max_steps is not None else max_turns if max_turns is not None else 4,
        )
        self.max_model_calls = _positive_int("max_model_calls", max_model_calls)
        self.timeout_seconds = _positive_number("timeout_seconds", timeout_seconds)
        self.max_calls_per_tool = _positive_int("max_calls_per_tool", max_calls_per_tool)
        self.max_repeated_failures = _positive_int(
            "max_repeated_failures", max_repeated_failures
        )
        self.task_prompt = task_prompt

    @property
    def max_turns(self) -> int:
        """Temporary read alias for callers migrating to ``max_steps``."""

        return self.max_steps


@dataclass(frozen=True)
class _LoopBudgets:
    max_steps: int
    max_model_calls: int
    timeout_seconds: float
    max_calls_per_tool: int
    max_repeated_failures: int


@dataclass
class AgentTurn:
    turn_index: int
    model_payload: dict[str, Any] = field(default_factory=dict)
    observation: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AgentLoopResult:
    stop_reason: StopReason
    final_output: dict[str, Any]
    turns: list[AgentTurn] = field(default_factory=list)
    actual_execution_mode: ExecutionMode = ExecutionMode.MODEL_ONCE
    delegation_evidence: tuple[DelegationEvidence, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "stop_reason": self.stop_reason,
            "final_output": _normalize_json_value(self.final_output),
            "turns": [turn.to_dict() for turn in self.turns],
            "actual_execution_mode": self.actual_execution_mode.value,
            "delegation_evidence": [
                evidence.model_dump(mode="json") for evidence in self.delegation_evidence
            ],
        }


class _InvalidJsonValue(ValueError):
    pass


class AgentLoop:
    def __init__(
        self,
        model_adapter: ModelAdapter,
        tool_gateway: ToolGateway,
        *,
        delegate_fn: Callable[[TaskBrief], SubagentResult] | None = None,
        skill_manifest: SkillManifest | None = None,
        role_registry: SubagentRoleRegistry | None = None,
        reject_hidden_reasoning: bool = False,
    ) -> None:
        if delegate_fn is not None and not callable(delegate_fn):
            raise TypeError("delegate_fn must be callable")
        self.model_adapter = model_adapter
        self.tool_gateway = tool_gateway
        self.delegate_fn = delegate_fn
        if skill_manifest is not None and not isinstance(skill_manifest, SkillManifest):
            raise TypeError("skill_manifest must be a SkillManifest")
        self.skill_manifest = skill_manifest
        self.role_registry = role_registry or build_default_subagent_role_registry()
        if not isinstance(reject_hidden_reasoning, bool):
            raise TypeError("reject_hidden_reasoning must be a bool")
        self.reject_hidden_reasoning = reject_hidden_reasoning

    def run(
        self,
        state: PolicyImpactState,
        config: AgentLoopConfig,
        *,
        max_steps: int | None = None,
        max_model_calls: int | None = None,
        timeout_seconds: float | None = None,
        max_calls_per_tool: int | None = None,
        max_repeated_failures: int | None = None,
    ) -> AgentLoopResult:
        if config.execution_mode is ExecutionMode.DETERMINISTIC:
            # Deterministic executors own their output; this loop only records the mode guard.
            return AgentLoopResult(
                "deterministic_complete",
                {},
                [],
                actual_execution_mode=ExecutionMode.DETERMINISTIC,
            )
        if config.execution_mode is ExecutionMode.WORKFLOW:
            raise ValueError("ExecutionMode.WORKFLOW is not supported by AgentLoop")

        budgets = _resolve_budgets(
            config,
            max_steps=max_steps,
            max_model_calls=max_model_calls,
            timeout_seconds=timeout_seconds,
            max_calls_per_tool=max_calls_per_tool,
            max_repeated_failures=max_repeated_failures,
        )
        if config.execution_mode is ExecutionMode.MODEL_ONCE:
            budgets = _LoopBudgets(
                max_steps=budgets.max_steps,
                max_model_calls=1,
                timeout_seconds=budgets.timeout_seconds,
                max_calls_per_tool=budgets.max_calls_per_tool,
                max_repeated_failures=budgets.max_repeated_failures,
            )
        deadline = time.monotonic() + budgets.timeout_seconds
        messages = [
            _initial_message(config),
            {
                "role": "user",
                "content": {
                    "task": config.task_prompt
                    or f"Complete the {config.agent_role} review using only visible_context.",
                    "output_key": config.output_key,
                    "required_response": {"type": "final", "output": "structured object"},
                },
            },
        ]
        turns: list[AgentTurn] = []
        model_call_count = 0
        step_count = 0
        invalid_repair_count = 0
        delegate_denial_count = 0
        nonfinal_action_count = 0
        delegation_evidence: list[DelegationEvidence] = []
        tool_call_counts: dict[str, int] = defaultdict(int)
        failed_call_counts: dict[str, int] = defaultdict(int)

        while True:
            budget_stop = _budget_stop_reason(
                deadline=deadline,
                step_count=step_count,
                model_call_count=model_call_count,
                budgets=budgets,
            )
            if budget_stop is not None:
                return _loop_result(
                    budget_stop,
                    {},
                    turns,
                    config=config,
                    model_call_count=model_call_count,
                    nonfinal_action_count=nonfinal_action_count,
                    delegation_evidence=delegation_evidence,
                )

            model_call_count += 1
            try:
                response = self.model_adapter.complete(messages, schema_name=config.output_key)
                response = _normalize_legacy_loop_response(response, config.output_key)
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
                turn = AgentTurn(turn_index=step_count + 1, error=error)
                turns.append(turn)
                state.add_error(config.stage_name, "model_error", error)
                return _loop_result(
                    "model_error",
                    {},
                    turns,
                    config=config,
                    model_call_count=model_call_count,
                    nonfinal_action_count=nonfinal_action_count,
                    delegation_evidence=delegation_evidence,
                )

            step_count += 1
            try:
                action = _ACTION_ADAPTER.validate_python(response.payload)
            except ValidationError as exc:
                validation_errors = _concise_validation_errors(exc)
                audit_payload = {
                    "action_type": _audit_action_type(response.payload),
                    "validation_errors": validation_errors,
                }
                turn = AgentTurn(
                    turn_index=step_count,
                    model_payload=audit_payload,
                    created_at=response.created_at,
                )
                turns.append(turn)
                if not _persist_model_call(
                    state,
                    config,
                    response,
                    messages=messages,
                    payload=audit_payload,
                ):
                    turn.error = "persistence_error"
                    return _loop_result(
                        "persistence_error",
                        {},
                        turns,
                        config=config,
                        model_call_count=model_call_count,
                        nonfinal_action_count=nonfinal_action_count,
                        delegation_evidence=delegation_evidence,
                    )
                if (
                    invalid_repair_count >= 1
                    or config.execution_mode is ExecutionMode.MODEL_ONCE
                ):
                    return _loop_result(
                        "invalid_model_action",
                        {},
                        turns,
                        config=config,
                        model_call_count=model_call_count,
                        nonfinal_action_count=nonfinal_action_count,
                        delegation_evidence=delegation_evidence,
                    )
                invalid_repair_count += 1
                messages.append(
                    {
                        "role": "system",
                        "content": {
                            "allowed_actions": list(ALLOWED_ACTION_NAMES),
                            "validation_errors": validation_errors,
                        },
                    }
                )
                continue

            raw_payload = action.model_dump(mode="json")
            hidden_reasoning_detected = bool(
                isinstance(action, FinalAction)
                and _contains_hidden_reasoning(raw_payload["output"])
            )
            if isinstance(action, FinalAction):
                raw_payload["output"] = _remove_hidden_reasoning(raw_payload["output"])
            payload = _normalize_json_value(raw_payload)
            audit_payload = _audit_action_payload(action, payload)
            turn = AgentTurn(
                turn_index=step_count,
                model_payload=audit_payload,
                created_at=response.created_at,
            )
            turns.append(turn)
            if not _persist_model_call(
                state,
                config,
                response,
                messages=messages,
                payload=audit_payload,
            ):
                turn.error = "persistence_error"
                return _loop_result(
                    "persistence_error",
                    {},
                    turns,
                    config=config,
                    model_call_count=model_call_count,
                    nonfinal_action_count=nonfinal_action_count,
                    delegation_evidence=delegation_evidence,
                )

            if time.monotonic() >= deadline:
                return _loop_result(
                    "timeout",
                    {},
                    turns,
                    config=config,
                    model_call_count=model_call_count,
                    nonfinal_action_count=nonfinal_action_count,
                    delegation_evidence=delegation_evidence,
                )

            if hidden_reasoning_detected and self.reject_hidden_reasoning:
                turn.error = "invalid_model_action"
                turn.observation = {
                    "output_error": {"category": "hidden_reasoning_rejected"}
                }
                return _loop_result(
                    "invalid_model_action",
                    {},
                    turns,
                    config=config,
                    model_call_count=model_call_count,
                    nonfinal_action_count=nonfinal_action_count,
                    delegation_evidence=delegation_evidence,
                )

            if isinstance(action, FinalAction):
                return _loop_result(
                    "success",
                    payload["output"],
                    turns,
                    config=config,
                    model_call_count=model_call_count,
                    nonfinal_action_count=nonfinal_action_count,
                    delegation_evidence=delegation_evidence,
                )

            nonfinal_action_count += 1

            if config.execution_mode is ExecutionMode.MODEL_ONCE:
                turn.error = "gate_blocked"
                turn.observation = {
                    "mode_error": {
                        "action_type": action.type,
                        "category": "gate_blocked",
                        "execution_mode": config.execution_mode.value,
                    }
                }
                return _loop_result(
                    "gate_blocked",
                    {},
                    turns,
                    config=config,
                    model_call_count=model_call_count,
                    nonfinal_action_count=nonfinal_action_count,
                    delegation_evidence=delegation_evidence,
                )

            if isinstance(action, RequestApprovalAction):
                turn.observation = {
                    "approval": {
                        "status": "awaiting_approval",
                        "tool_name": action.tool_name,
                    }
                }
                return _loop_result(
                    "awaiting_approval",
                    {},
                    turns,
                    config=config,
                    model_call_count=model_call_count,
                    nonfinal_action_count=nonfinal_action_count,
                    delegation_evidence=delegation_evidence,
                )

            if isinstance(action, DelegateAction):
                delegate_stop = self._run_delegate_action(
                    action=action,
                    turn=turn,
                    messages=messages,
                    state=state,
                    config=config,
                    delegation_evidence=delegation_evidence,
                )
                if delegate_stop is None:
                    if not _has_replan_budget(
                        deadline=deadline,
                        step_count=step_count,
                        model_call_count=model_call_count,
                        budgets=budgets,
                    ):
                        return _loop_result(
                            "max_model_calls",
                            {},
                            turns,
                            config=config,
                            model_call_count=model_call_count,
                            nonfinal_action_count=nonfinal_action_count,
                            delegation_evidence=delegation_evidence,
                        )
                    continue
                if delegate_denial_count >= 1:
                    return _loop_result(
                        delegate_stop,
                        {},
                        turns,
                        config=config,
                        model_call_count=model_call_count,
                        nonfinal_action_count=nonfinal_action_count,
                        delegation_evidence=delegation_evidence,
                    )
                delegate_denial_count += 1
                if not _has_replan_budget(
                    deadline=deadline,
                    step_count=step_count,
                    model_call_count=model_call_count,
                    budgets=budgets,
                ):
                    return _loop_result(
                        delegate_stop,
                        {},
                        turns,
                        config=config,
                        model_call_count=model_call_count,
                        nonfinal_action_count=nonfinal_action_count,
                        delegation_evidence=delegation_evidence,
                    )
                continue

            if isinstance(action, ToolCallAction):
                stop_reason = self._run_tool_action(
                    action=action,
                    response=response,
                    turn=turn,
                    messages=messages,
                    config=config,
                    budgets=budgets,
                    deadline=deadline,
                    step_count=step_count,
                    model_call_count=model_call_count,
                    tool_call_counts=tool_call_counts,
                    failed_call_counts=failed_call_counts,
                )
                if stop_reason is not None:
                    return _loop_result(
                        stop_reason,
                        {},
                        turns,
                        config=config,
                        model_call_count=model_call_count,
                        nonfinal_action_count=nonfinal_action_count,
                        delegation_evidence=delegation_evidence,
                    )

    def _run_delegate_action(
        self,
        *,
        action: DelegateAction,
        turn: AgentTurn,
        messages: list[dict[str, Any]],
        state: PolicyImpactState,
        config: AgentLoopConfig,
        delegation_evidence: list[DelegationEvidence],
    ) -> StopReason | None:
        if (
            self.delegate_fn is None
            or self.skill_manifest is None
            or action.role not in self.skill_manifest.allowed_subagents
        ):
            category: StopReason = "tool_denied"
            _append_delegate_error(turn, messages, action.role, category)
            return category

        try:
            role = self.role_registry.get(action.role, "1.0.0")
            input_artifact_refs = action.input_artifact_refs or _default_delegate_refs(
                config.context_manifest
            )
            brief = TaskBrief(
                task_id=f"task_{uuid4().hex}",
                parent_run_id=state.run_id,
                parent_span_id=_parent_span_id(state.run_id, config.stage_name),
                role=action.role,
                task=action.task,
                input_artifact_refs=input_artifact_refs,
                output_schema_name=role.output_schema_name,
                max_steps=role.max_steps,
                max_model_calls=role.max_model_calls,
                timeout_seconds=role.timeout_seconds,
            )
            raw_result = self.delegate_fn(brief)
            result = (
                raw_result
                if isinstance(raw_result, SubagentResult)
                else SubagentResult.model_validate(raw_result)
            )
            evidence = _validate_delegation_integrity(
                state=state,
                brief=brief,
                role_version=role.version,
                allowed_tools=tuple(
                    tool
                    for tool in role.allowed_tools
                    if tool in self.skill_manifest.allowed_tools
                ),
                result=result,
            )
        except Exception:  # noqa: BLE001
            category = "delegate_failed"
            _append_delegate_error(turn, messages, action.role, category)
            return category

        delegation_evidence.append(evidence)
        turn.observation = {
            "subagent_result": {
                "role": result.role,
                "task_id": result.task_id,
                "stop_reason": result.stop_reason,
                "context_manifest_id": result.context_manifest_id,
                "output": _normalize_json_value(result.output),
                "artifact_refs": list(result.artifact_refs),
            }
        }
        messages.append(
            {
                "role": "tool",
                "tool_name": f"subagent.{result.role}",
                "content": turn.observation,
            }
        )
        return None

    def _run_tool_action(
        self,
        *,
        action: ToolCallAction,
        response: ModelResponse,
        turn: AgentTurn,
        messages: list[dict[str, Any]],
        config: AgentLoopConfig,
        budgets: _LoopBudgets,
        deadline: float,
        step_count: int,
        model_call_count: int,
        tool_call_counts: dict[str, int],
        failed_call_counts: dict[str, int],
    ) -> StopReason | None:
        arguments = _normalize_json_value(action.arguments)
        messages.append(
            _assistant_tool_call_message(
                response_id=response.response_id,
                tool_name=action.tool_name,
                arguments=arguments,
            )
        )
        if tool_call_counts[action.tool_name] >= budgets.max_calls_per_tool:
            _append_tool_error(turn, messages, action.tool_name, "tool_denied")
            return "tool_denied"
        if time.monotonic() >= deadline:
            return "timeout"

        tool_call_counts[action.tool_name] += 1
        call_hash = _tool_call_hash(action.tool_name, arguments)
        try:
            result = self.tool_gateway.call(config.stage_name, action.tool_name, **arguments)
        except PermissionError:
            _append_tool_error(turn, messages, action.tool_name, "tool_denied")
            return "tool_denied"
        except TimeoutError:
            _append_tool_error(turn, messages, action.tool_name, "tool_timeout")
            return "tool_timeout"
        except Exception:  # noqa: BLE001
            failed_call_counts[call_hash] += 1
            _append_tool_error(turn, messages, action.tool_name, "tool_failed")
            if failed_call_counts[call_hash] >= budgets.max_repeated_failures:
                return "repeated_tool_failure"
            if not _has_replan_budget(
                deadline=deadline,
                step_count=step_count,
                model_call_count=model_call_count,
                budgets=budgets,
            ):
                return "tool_failed"
            return None

        try:
            normalized_result = _normalize_json_value(result)
        except _InvalidJsonValue:
            _append_tool_error(turn, messages, action.tool_name, "invalid_tool_output")
            return "invalid_tool_output"

        failed_call_counts.pop(call_hash, None)
        turn.observation = {"tool_result": normalized_result}
        messages.append(
            {
                "role": "tool",
                "tool_name": action.tool_name,
                "content": turn.observation,
            }
        )
        if time.monotonic() >= deadline:
            return "timeout"
        return None


def _validate_delegation_integrity(
    *,
    state: PolicyImpactState,
    brief: TaskBrief,
    role_version: str,
    allowed_tools: tuple[str, ...],
    result: SubagentResult,
) -> DelegationEvidence:
    if (
        result.stop_reason != "success"
        or result.task_id != brief.task_id
        or result.parent_run_id != state.run_id
        or result.parent_run_id != brief.parent_run_id
        or result.role != brief.role
        or result.role_version != role_version
        or result.parent_span_id != brief.parent_span_id
    ):
        raise ValueError("subagent result does not match delegated task")

    expected_manifest_fields = {
        "task_id": result.task_id,
        "parent_run_id": result.parent_run_id,
        "role": result.role,
        "role_version": result.role_version,
        "parent_span_id": result.parent_span_id,
        "span_id": result.span_id,
        "output_schema_name": brief.output_schema_name,
    }
    manifest_matches = False
    for record in state.context_manifests:
        if (
            not isinstance(record, Mapping)
            or record.get("record_id") != result.context_manifest_id
            or record.get("context_type") != "subagent_context_manifest"
            or record.get("source_id") != state.run_id
        ):
            continue
        metadata = record.get("metadata")
        if not isinstance(metadata, Mapping) or not all(
            metadata.get(key) == value for key, value in expected_manifest_fields.items()
        ):
            continue
        visible_content = metadata.get("visible_content")
        if not isinstance(visible_content, Mapping):
            continue
        manifest_payload = {
            "brief": visible_content.get("task_brief"),
            "role_version": result.role_version,
            "span_id": result.span_id,
            "allowed_tools": list(allowed_tools),
        }
        canonical = json.dumps(
            manifest_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        checksum = sha256(canonical.encode("utf-8")).hexdigest()
        if (
            record.get("checksum") == checksum
            and record.get("record_id") == f"ctx_subagent_{checksum[:16]}"
            and metadata.get("allowed_tools") == list(allowed_tools)
            and visible_content.get("task_brief") == brief.model_dump(mode="json")
            and visible_content.get("input_artifact_refs")
            == list(brief.input_artifact_refs)
        ):
            manifest_matches = True
            break
    if not manifest_matches:
        raise ValueError("subagent context manifest evidence is missing or inconsistent")

    trace_matches = any(
        isinstance(trace, Mapping)
        and trace.get("stage") == "subagent_delegate"
        and trace.get("status") == "ok"
        and trace.get("stop_reason") == "success"
        and trace.get("task_id") == result.task_id
        and trace.get("parent_run_id") == result.parent_run_id
        and trace.get("role") == result.role
        and trace.get("role_version") == result.role_version
        and trace.get("parent_span_id") == result.parent_span_id
        and trace.get("span_id") == result.span_id
        and trace.get("context_manifest_id") == result.context_manifest_id
        for trace in state.stage_trace
    )
    if not trace_matches:
        raise ValueError("successful subagent child trace is missing or inconsistent")

    evidence = result.evidence()
    persisted_evidence = []
    for raw_evidence in state.delegation_evidence:
        try:
            persisted_evidence.append(DelegationEvidence.model_validate(raw_evidence))
        except (TypeError, ValueError):
            continue
    if evidence not in persisted_evidence:
        raise ValueError("delegation evidence is missing from current state")

    for artifact_ref in result.artifact_refs:
        if artifact_ref in brief.input_artifact_refs:
            continue
        artifact_value = state.artifacts.get(artifact_ref)
        if not isinstance(artifact_value, str) or not artifact_value.strip():
            raise ValueError("delegated artifact ref is not registered")
    return evidence


def _positive_int(field_name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _default_delegate_refs(context_manifest: ContextManifestRecord) -> tuple[str, ...]:
    metadata = context_manifest.metadata
    refs = metadata.get("default_delegated_artifact_refs")
    if refs is None:
        ref = metadata.get("default_delegated_artifact_ref")
        refs = [ref] if ref else []
    if not isinstance(refs, (list, tuple)):
        return ()
    normalized: list[str] = []
    seen: set[str] = set()
    for ref in refs:
        value = str(ref or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return tuple(normalized)


def _normalize_legacy_loop_response(response: ModelResponse, output_key: str) -> ModelResponse:
    if output_key != "general_chat_agent_loop.v1":
        return response
    payload = response.payload
    if (
        isinstance(payload, Mapping)
        and isinstance(payload.get("output"), Mapping)
        and payload.get("type") in {"general_chat", "research_report"}
    ):
        return ModelResponse(
            response_id=response.response_id,
            payload={"type": "final", "output": dict(payload["output"])},
            metadata=dict(response.metadata),
            created_at=response.created_at,
        )
    return response


def _positive_number(field_name: str, value: Any) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{field_name} must be positive")
    return float(value)


def _resolve_budgets(
    config: AgentLoopConfig,
    *,
    max_steps: int | None,
    max_model_calls: int | None,
    timeout_seconds: float | None,
    max_calls_per_tool: int | None,
    max_repeated_failures: int | None,
) -> _LoopBudgets:
    return _LoopBudgets(
        max_steps=_positive_int(
            "max_steps", config.max_steps if max_steps is None else max_steps
        ),
        max_model_calls=_positive_int(
            "max_model_calls",
            config.max_model_calls if max_model_calls is None else max_model_calls,
        ),
        timeout_seconds=_positive_number(
            "timeout_seconds",
            config.timeout_seconds if timeout_seconds is None else timeout_seconds,
        ),
        max_calls_per_tool=_positive_int(
            "max_calls_per_tool",
            config.max_calls_per_tool
            if max_calls_per_tool is None
            else max_calls_per_tool,
        ),
        max_repeated_failures=_positive_int(
            "max_repeated_failures",
            config.max_repeated_failures
            if max_repeated_failures is None
            else max_repeated_failures,
        ),
    )


def _budget_stop_reason(
    *,
    deadline: float,
    step_count: int,
    model_call_count: int,
    budgets: _LoopBudgets,
) -> StopReason | None:
    if time.monotonic() >= deadline:
        return "timeout"
    if step_count >= budgets.max_steps:
        return "max_steps"
    if model_call_count >= budgets.max_model_calls:
        return "max_model_calls"
    return None


def _has_replan_budget(
    *,
    deadline: float,
    step_count: int,
    model_call_count: int,
    budgets: _LoopBudgets,
) -> bool:
    return (
        time.monotonic() < deadline
        and step_count < budgets.max_steps
        and model_call_count < budgets.max_model_calls
    )


def _persist_model_call(
    state: PolicyImpactState,
    config: AgentLoopConfig,
    response: ModelResponse,
    *,
    messages: list[dict[str, Any]],
    payload: dict[str, Any],
) -> bool:
    try:
        safe_messages = _normalize_json_value(messages)
        safe_payload = _normalize_json_value(payload)
        safe_metadata = _sanitize_metadata(response.metadata)
        state.add_model_call(
            {
                "response_id": response.response_id,
                "stage_name": config.stage_name,
                "agent_role": config.agent_role,
                "schema_name": config.output_key,
                "messages": safe_messages,
                "payload": safe_payload,
                "metadata": safe_metadata,
                "created_at": response.created_at,
            }
        )
    except Exception:  # noqa: BLE001
        return False
    return True


def _concise_validation_errors(exc: ValidationError) -> list[str]:
    errors: list[str] = []
    for item in exc.errors(include_url=False, include_context=False, include_input=False)[:8]:
        location = ".".join(str(part) for part in item.get("loc", ())) or "action"
        errors.append(f"{location}: {item.get('type', 'invalid')}")
    return errors or ["action: invalid"]


def _audit_action_type(payload: Any) -> str:
    if isinstance(payload, dict) and isinstance(payload.get("type"), str):
        action_type = payload["type"].strip()
        if action_type in ALLOWED_ACTION_NAMES:
            return action_type
    return "invalid"


def _sanitize_metadata(value: Any) -> dict[str, Any]:
    sanitized = _sanitize_metadata_value(value)
    return sanitized if isinstance(sanitized, dict) else {}


def _sanitize_metadata_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or key.casefold() in _HIDDEN_REASONING_FIELDS:
                continue
            safe_item = _sanitize_metadata_value(item)
            if safe_item is not _OMIT:
                sanitized[key] = safe_item
        return sanitized
    if isinstance(value, (list, tuple)):
        items = []
        for item in value:
            safe_item = _sanitize_metadata_value(item)
            if safe_item is not _OMIT:
                items.append(safe_item)
        return items
    try:
        return _normalize_json_value(value)
    except _InvalidJsonValue:
        return _OMIT


def _remove_hidden_reasoning(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _remove_hidden_reasoning(item)
            for key, item in value.items()
            if isinstance(key, str)
            and not _is_hidden_reasoning_key(key)
        }
    if isinstance(value, (list, tuple)):
        return [_remove_hidden_reasoning(item) for item in value]
    return value


def _contains_hidden_reasoning(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            (
                isinstance(key, str)
                and _is_hidden_reasoning_key(key)
            )
            or _contains_hidden_reasoning(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_hidden_reasoning(item) for item in value)
    return False


def _is_hidden_reasoning_key(key: str) -> bool:
    normalized = "".join(character for character in key.casefold() if character.isalnum())
    return (
        normalized in {"analysis", "thinking", "chainofthought"}
        or "reasoning" in normalized
    )


def _initial_message(config: AgentLoopConfig) -> dict[str, Any]:
    metadata = config.context_manifest.metadata
    return {
        "role": "system",
        "content": {
            "agent_role": config.agent_role,
            "output_key": config.output_key,
            "manifest_id": metadata.get("manifest_id", config.context_manifest.record_id),
            "visible_keys": metadata.get("visible_keys", []),
            "visible_context": metadata.get("visible_content", {}),
            "output_contract": _output_contract(config.output_key),
            "instructions": [
                "Use only visible_context and allowed tool observations.",
                "Do not invent evidence or hidden state.",
                "Do not include reasoning, thinking, analysis, or chain-of-thought fields.",
                "Return exactly one action: final, tool_call, delegate, or request_approval.",
                "Return type=final with a structured output object when the task is complete.",
            ],
        },
    }


def _output_contract(output_key: str) -> dict[str, Any]:
    contracts = {
        "evidence_collection.v1": {
            "final_shape": {
                "type": "final",
                "output": {
                    "evidence": [
                        {
                            "source_ref": "string",
                            "summary": "string",
                            "source_url": "string",
                        }
                    ]
                },
            }
        },
        "claim_set.v1": {
            "final_shape": {
                "type": "final",
                "output": {
                    "claims": [
                        {
                            "claim_ref": "string",
                            "statement": "string",
                            "evidence_refs": ["input_artifact_ref"],
                            "confidence": 0.0,
                        }
                    ]
                },
            }
        },
        "counterexample_set.v1": {
            "final_shape": {
                "type": "final",
                "output": {
                    "counterexamples": [
                        {
                            "claim_ref": "string",
                            "issue": "string",
                            "evidence_refs": ["input_artifact_ref"],
                        }
                    ],
                    "notes": ["string"],
                },
            }
        },
        "claim_verdict_set.v1": {
            "final_shape": {
                "type": "final",
                "output": {
                    "verdicts": [
                        {
                            "claim_ref": "string",
                            "decision": "pass",
                            "evidence_refs": ["input_artifact_ref"],
                            "reason": "string",
                        }
                    ]
                },
            }
        },
        "published_answer.v1": {
            "final_shape": {
                "type": "final",
                "output": {
                    "answer": "string",
                    "verified_claim_refs": ["input_artifact_ref"],
                },
            }
        },
        "general_chat_agent_loop.v1": {
            "final_shape": {
                "type": "final",
                "output": {
                    "answer": "string",
                    "citations": ["optional citation refs"],
                    "notes": ["optional uncertainty notes"],
                },
            },
            "delegate_shape": {
                "type": "delegate",
                "role": "collector|analyst|skeptic|verifier",
                "task": "bounded task brief for the selected subagent",
                "input_artifact_refs": ["main_chat_context:<run>"],
            },
        },
    }
    return contracts.get(
        output_key,
        {"final_shape": {"type": "final", "output": "structured object"}},
    )


def _assistant_tool_call_message(
    response_id: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    return {
        "role": "assistant",
        "response_id": response_id,
        "content": {
            "type": "tool_call",
            "tool_name": tool_name,
            "arguments": arguments,
        },
    }


def _append_tool_error(
    turn: AgentTurn,
    messages: list[dict[str, Any]],
    tool_name: str,
    category: str,
) -> None:
    turn.error = category
    turn.observation = {
        "tool_error": {
            "category": category,
            "tool_name": tool_name,
        }
    }
    messages.append(
        {
            "role": "tool",
            "tool_name": tool_name,
            "content": turn.observation,
        }
    )


def _append_delegate_error(
    turn: AgentTurn,
    messages: list[dict[str, Any]],
    role: str,
    category: StopReason,
) -> None:
    turn.error = category
    turn.observation = {
        "delegate_error": {
            "category": category,
            "role": role,
        }
    }
    messages.append({"role": "system", "content": turn.observation})


def _audit_action_payload(action: AgentAction, payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(action, DelegateAction):
        return {
            "type": "delegate",
            "role": action.role,
            "input_artifact_ref_count": len(action.input_artifact_refs),
        }
    return payload


def _parent_span_id(run_id: str, stage_name: str) -> str:
    digest = sha256(f"{run_id}:{stage_name}".encode("utf-8")).hexdigest()[:16]
    return f"span_parent_{digest}"


def _loop_result(
    stop_reason: StopReason,
    final_output: dict[str, Any],
    turns: list[AgentTurn],
    *,
    config: AgentLoopConfig,
    model_call_count: int,
    nonfinal_action_count: int,
    delegation_evidence: list[DelegationEvidence],
) -> AgentLoopResult:
    if delegation_evidence:
        actual_mode = ExecutionMode.SUBAGENT_WORKFLOW
    elif model_call_count == 1 and nonfinal_action_count == 0:
        actual_mode = ExecutionMode.MODEL_ONCE
    elif model_call_count == 0 and config.execution_mode is ExecutionMode.MODEL_ONCE:
        actual_mode = ExecutionMode.MODEL_ONCE
    else:
        actual_mode = ExecutionMode.AGENT_LOOP
    return AgentLoopResult(
        stop_reason=stop_reason,
        final_output=final_output,
        turns=turns,
        actual_execution_mode=actual_mode,
        delegation_evidence=tuple(delegation_evidence),
    )


def _tool_call_hash(tool_name: str, arguments: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"arguments": arguments, "tool_name": tool_name},
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise _InvalidJsonValue("non-finite number")
        return value
    if isinstance(value, PathLike):
        return str(value)
    if is_dataclass(value):
        return _normalize_json_value(asdict(value))
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise _InvalidJsonValue("mapping keys must be strings")
        return {
            key: _normalize_json_value(value[key])
            for key in sorted(value)
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        normalized = [_normalize_json_value(item) for item in value]
        return sorted(
            normalized,
            key=lambda item: json.dumps(
                item,
                allow_nan=False,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
    raise _InvalidJsonValue(f"unsupported JSON value type: {type(value).__name__}")


__all__ = [
    "AgentLoop",
    "AgentLoopConfig",
    "AgentLoopResult",
    "AgentTurn",
    "StopReason",
]
