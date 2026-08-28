"""Role-isolated subagent execution with explicit caller capabilities."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from policy_impact.harness.agent_loop import AgentLoop, AgentLoopConfig
from policy_impact.harness.contracts import ContextManifestRecord
from policy_impact.harness.model_gateway import ModelAdapter
from policy_impact.harness.state import PolicyImpactState, utc_now_iso
from policy_impact.harness.tool_gateway import ToolGateway
from policy_impact.runtime.subagent_roles import (
    SubagentRoleDefinition,
    SubagentRoleRegistry,
    build_default_subagent_role_registry,
)
from policy_impact.runtime.task_artifacts import (
    InMemoryTaskArtifactResolver,
    TaskArtifactResolver,
)
from policy_impact.runtime.task_brief import SubagentResult, TaskBrief
from policy_impact.runtime.verifier_proof import VerifierGateProof, VerifierGateResolver
from policy_impact.skills.manifest import SkillManifest


@dataclass(frozen=True)
class SubagentDefinition:
    """Compatibility descriptor; it never grants role or tool capabilities."""

    role: str
    stage_name: str
    visible_fields: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    output_key: str
    max_turns: int = 4
    task_prompt: str = ""

    def __post_init__(self) -> None:
        for field_name in ("role", "stage_name", "output_key"):
            value = str(getattr(self, field_name) or "").strip()
            if not value:
                raise ValueError(f"{field_name} is required")
            object.__setattr__(self, field_name, value)
        object.__setattr__(
            self,
            "visible_fields",
            tuple(str(value).strip() for value in self.visible_fields if str(value).strip()),
        )
        object.__setattr__(
            self,
            "allowed_tools",
            tuple(str(value).strip() for value in self.allowed_tools if str(value).strip()),
        )
        if isinstance(self.max_turns, bool) or not isinstance(self.max_turns, int) or self.max_turns <= 0:
            raise ValueError("max_turns must be a positive integer")
        object.__setattr__(self, "task_prompt", str(self.task_prompt or "").strip())


class SubagentRunner:
    def __init__(
        self,
        definitions: list[SubagentDefinition] | tuple[SubagentDefinition, ...] | None = None,
        model_adapter: ModelAdapter | None = None,
        tool_gateway: ToolGateway | None = None,
        *,
        skill_manifest: SkillManifest,
        role_registry: SubagentRoleRegistry | None = None,
        verifier_gate_resolver: VerifierGateResolver | None = None,
    ) -> None:
        if model_adapter is None:
            raise TypeError("model_adapter is required")
        if not isinstance(skill_manifest, SkillManifest):
            raise TypeError("skill_manifest must be a SkillManifest")
        self.model_adapter = model_adapter
        self.tool_gateway = tool_gateway or ToolGateway()
        self.skill_manifest = skill_manifest
        self.role_registry = role_registry or build_default_subagent_role_registry()
        self.verifier_gate_resolver = verifier_gate_resolver

        legacy_definitions = tuple(definitions or ())
        self.definitions = {definition.role: definition for definition in legacy_definitions}
        if len(self.definitions) != len(legacy_definitions):
            raise ValueError("duplicate legacy subagent role")
        for definition in legacy_definitions:
            # Legacy descriptors can customize the prompt only for a registered role.
            self.role_registry.get(definition.role, "1.0.0")

    def run(
        self,
        state: PolicyImpactState,
        role: str,
        *,
        task: str | None = None,
        input_artifact_refs: tuple[str, ...] | list[str] = (),
        artifact_payloads: Mapping[str, Any] | None = None,
        artifact_resolver: TaskArtifactResolver | None = None,
        output_artifact_refs: tuple[str, ...] | list[str] = (),
        parent_span_id: str | None = None,
        task_id: str | None = None,
    ) -> SubagentResult:
        normalized_role = str(role or "").strip()
        self._authorize_role(normalized_role)
        definition = self._definition_for(normalized_role)
        legacy = self.definitions.get(normalized_role)
        task_text = str(
            task
            if task is not None
            else legacy.task_prompt
            if legacy is not None and legacy.task_prompt
            else f"Complete the isolated {normalized_role} task."
        ).strip()
        parent_span = str(
            parent_span_id or self._default_parent_span(state, normalized_role)
        ).strip()
        brief = TaskBrief(
            task_id=task_id or f"task_{uuid4().hex}",
            parent_run_id=state.run_id,
            parent_span_id=parent_span,
            role=normalized_role,
            task=task_text,
            input_artifact_refs=tuple(input_artifact_refs),
            output_schema_name=definition.output_schema_name,
            max_steps=definition.max_steps,
            max_model_calls=definition.max_model_calls,
            timeout_seconds=definition.timeout_seconds,
        )
        proofs = self._editor_proofs(definition, brief)
        task_artifacts = _resolve_task_artifacts(artifact_payloads, artifact_resolver)
        controlled_output_refs = _validate_controlled_output_refs(
            output_artifact_refs,
            brief=brief,
            resolver=task_artifacts,
        )

        span_id = f"span_{uuid4().hex}"
        allowed_tools = self._allowed_tools(definition)
        manifest = _build_subagent_context_manifest(
            brief=brief,
            definition=definition,
            span_id=span_id,
            allowed_tools=allowed_tools,
        )
        state.add_context_manifest(manifest.to_dict())
        stage_name = f"subagent.{normalized_role}"
        loop_result = AgentLoop(
            self.model_adapter,
            _RoleScopedToolGateway(
                normalized_role,
                allowed_tools,
                self.tool_gateway,
                brief=brief,
                artifact_resolver=task_artifacts,
            ),
            reject_hidden_reasoning=True,
        ).run(
            state,
            AgentLoopConfig(
                stage_name=stage_name,
                agent_role=normalized_role,
                context_manifest=manifest,
                output_key=definition.output_schema_name,
                max_steps=brief.max_steps,
                max_model_calls=brief.max_model_calls,
                timeout_seconds=brief.timeout_seconds,
                task_prompt=brief.task,
            ),
        )

        output: Mapping[str, Any] = loop_result.final_output
        stop_reason = loop_result.stop_reason
        if stop_reason == "success":
            try:
                if normalized_role == "collector":
                    _reject_collector_publishing_semantics(output)
                output = definition.output_schema.model_validate(output).model_dump(
                    mode="python"
                )
                _validate_role_output_refs(
                    definition.role,
                    output,
                    input_artifact_refs=brief.input_artifact_refs,
                )
            except PermissionError:
                output = {}
                stop_reason = "gate_blocked"
            except ValidationError:
                output = {}
                stop_reason = "invalid_model_action"

        result = SubagentResult(
            task_id=brief.task_id,
            parent_run_id=brief.parent_run_id,
            role=normalized_role,
            role_version=definition.version,
            output=output,
            stop_reason=stop_reason,
            context_manifest_id=manifest.record_id,
            parent_span_id=brief.parent_span_id,
            span_id=span_id,
            artifact_refs=controlled_output_refs if stop_reason == "success" else (),
            verifier_gate_audit_refs=(
                tuple(proof.audit_ref for proof in proofs)
                if stop_reason == "success"
                else ()
            ),
        )
        state.add_stage_trace(
            {
                "stage": "subagent_delegate",
                "status": "ok" if result.stop_reason == "success" else "failed",
                "task_id": result.task_id,
                "parent_run_id": result.parent_run_id,
                "role": result.role,
                "role_version": result.role_version,
                "parent_span_id": result.parent_span_id,
                "span_id": result.span_id,
                "context_manifest_id": result.context_manifest_id,
                "stop_reason": result.stop_reason,
                "finished_at": utc_now_iso(),
            }
        )
        if result.stop_reason == "success":
            state.add_delegation_evidence(result.evidence().model_dump(mode="json"))
        return result

    def _definition_for(self, role: str) -> SubagentRoleDefinition:
        return self.role_registry.get(role, "1.0.0")

    def _authorize_role(self, role: str) -> None:
        if role not in self.skill_manifest.allowed_subagents:
            raise PermissionError(f"subagent role {role!r} is not allowed by caller skill")

    def _allowed_tools(self, definition: SubagentRoleDefinition) -> tuple[str, ...]:
        caller_tools = frozenset(self.skill_manifest.allowed_tools)
        return tuple(tool for tool in definition.allowed_tools if tool in caller_tools)

    def _editor_proofs(
        self,
        definition: SubagentRoleDefinition,
        brief: TaskBrief,
    ) -> tuple[VerifierGateProof, ...]:
        if definition.role != "editor":
            return ()
        if self.verifier_gate_resolver is None:
            raise PermissionError("editor input requires persisted verified claim proof")
        proofs = self.verifier_gate_resolver.resolve(
            brief.parent_run_id,
            brief.input_artifact_refs,
        )
        verified = {
            claim_ref
            for proof in proofs
            if proof.run_id == brief.parent_run_id
            for claim_ref in proof.verified_claim_refs
        }
        if not brief.input_artifact_refs or any(
            claim_ref not in verified for claim_ref in brief.input_artifact_refs
        ):
            raise PermissionError("editor input requires persisted verified claim proof")
        return proofs

    @staticmethod
    def _default_parent_span(state: PolicyImpactState, role: str) -> str:
        digest = hashlib.sha256(
            f"{state.run_id}:{role}:{len(state.delegation_evidence)}".encode("utf-8")
        ).hexdigest()[:16]
        return f"span_parent_{digest}"


class _RoleScopedToolGateway:
    def __init__(
        self,
        role: str,
        allowed_tools: tuple[str, ...],
        tool_gateway: ToolGateway,
        *,
        brief: TaskBrief,
        artifact_resolver: TaskArtifactResolver | None,
    ) -> None:
        self.role = role
        self.allowed_tools = frozenset(allowed_tools)
        self.tool_gateway = tool_gateway
        self.brief = brief
        self.artifact_resolver = artifact_resolver

    def call(self, stage_name: str, tool_name: str, *args: Any, **kwargs: Any) -> Any:
        if tool_name not in self.allowed_tools:
            raise PermissionError(
                f"subagent role {self.role!r} is not allowed to use tool {tool_name!r}"
            )
        if tool_name == "artifact_read":
            return self._read_artifact(args, kwargs)
        return self.tool_gateway.call(stage_name, tool_name, *args, **kwargs)

    def _read_artifact(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        if args or set(kwargs) != {"artifact_ref"}:
            raise PermissionError("artifact_read requires exactly one artifact_ref")
        artifact_ref = kwargs.get("artifact_ref")
        if (
            not isinstance(artifact_ref, str)
            or artifact_ref not in self.brief.input_artifact_refs
            or self.artifact_resolver is None
            or not self.artifact_resolver.contains(artifact_ref)
        ):
            raise PermissionError("artifact_read ref is outside the delegated task")
        return self.artifact_resolver.read(artifact_ref)


def _resolve_task_artifacts(
    payloads: Mapping[str, Any] | None,
    resolver: TaskArtifactResolver | None,
) -> TaskArtifactResolver | None:
    if payloads is not None and resolver is not None:
        raise ValueError("provide artifact_payloads or artifact_resolver, not both")
    if resolver is not None:
        if not callable(getattr(resolver, "contains", None)) or not callable(
            getattr(resolver, "read", None)
        ):
            raise TypeError("artifact_resolver must implement contains/read")
        return resolver
    return InMemoryTaskArtifactResolver(payloads) if payloads is not None else None


def _validate_controlled_output_refs(
    refs: tuple[str, ...] | list[str],
    *,
    brief: TaskBrief,
    resolver: TaskArtifactResolver | None,
) -> tuple[str, ...]:
    normalized = tuple(str(ref).strip() for ref in refs)
    if any(not ref for ref in normalized) or len(normalized) != len(set(normalized)):
        raise ValueError("output_artifact_refs must be unique nonblank strings")
    for artifact_ref in normalized:
        if artifact_ref in brief.input_artifact_refs:
            continue
        if resolver is not None and resolver.contains(artifact_ref):
            continue
        raise PermissionError(f"output artifact ref {artifact_ref!r} is not registered")
    return normalized


def _build_subagent_context_manifest(
    *,
    brief: TaskBrief,
    definition: SubagentRoleDefinition,
    span_id: str,
    allowed_tools: tuple[str, ...],
) -> ContextManifestRecord:
    visible_content = {
        "task_brief": brief.model_dump(mode="json"),
        "input_artifact_refs": list(brief.input_artifact_refs),
    }
    payload = {
        "brief": visible_content["task_brief"],
        "role_version": definition.version,
        "span_id": span_id,
        "allowed_tools": list(allowed_tools),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    checksum = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    manifest_id = f"ctx_subagent_{checksum[:16]}"
    return ContextManifestRecord(
        record_id=manifest_id,
        context_type="subagent_context_manifest",
        source_id=brief.parent_run_id,
        checksum=checksum,
        metadata={
            "manifest_id": manifest_id,
            "stage_name": f"subagent.{brief.role}",
            "agent_role": brief.role,
            "role": brief.role,
            "role_version": definition.version,
            "prompt_template_id": definition.prompt_template_id,
            "prompt_template_version": definition.prompt_template_version,
            "output_schema_name": definition.output_schema_name,
            "visible_keys": ["task_brief", "input_artifact_refs"],
            "visible_content": visible_content,
            "allowed_tools": list(allowed_tools),
            "task_id": brief.task_id,
            "parent_run_id": brief.parent_run_id,
            "parent_span_id": brief.parent_span_id,
            "span_id": span_id,
        },
    )


def _reject_collector_publishing_semantics(output: Mapping[str, Any]) -> None:
    forbidden = {
        "conclusion",
        "decision",
        "finaldecision",
        "publicationdecision",
        "publishdecision",
        "recommendation",
        "recommendations",
        "verdict",
        "verdicts",
    }

    def visit(value: Any) -> bool:
        if isinstance(value, Mapping):
            for key, item in value.items():
                normalized = "".join(
                    character
                    for character in str(key).strip().casefold()
                    if character.isalnum()
                )
                if normalized in forbidden or normalized.endswith("decision"):
                    return True
                if visit(item):
                    return True
        elif isinstance(value, (list, tuple)):
            return any(visit(item) for item in value)
        return False

    if visit(output):
        raise PermissionError("collector output contains publishing semantics")


def _validate_role_output_refs(
    role: str,
    output: Mapping[str, Any],
    *,
    input_artifact_refs: tuple[str, ...],
) -> None:
    allowed = frozenset(input_artifact_refs)
    referenced: list[str] = []
    if role in {"analyst", "skeptic"}:
        collection_key = "claims" if role == "analyst" else "counterexamples"
        for item in output.get(collection_key, ()):
            if isinstance(item, Mapping):
                referenced.extend(str(ref) for ref in item.get("evidence_refs", ()))
    elif role == "verifier":
        for item in output.get("verdicts", ()):
            if isinstance(item, Mapping):
                referenced.extend(str(ref) for ref in item.get("evidence_refs", ()))
    elif role == "editor":
        referenced.extend(str(ref) for ref in output.get("verified_claim_refs", ()))
    if any(ref not in allowed for ref in referenced):
        raise PermissionError("subagent output references artifacts outside the delegated task")


__all__ = ["SubagentDefinition", "SubagentResult", "SubagentRunner"]
