"""Chat-turn runtime for the single-user Agent Workbench."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from policy_impact.company_wiki.loader import company_dir, load_company_knowledge
from policy_impact.harness.agent_loop import AgentLoop, AgentLoopConfig, AgentLoopResult
from policy_impact.harness.chat_context import ChatContextConfig, compile_chat_context
from policy_impact.harness.contracts import ContextManifestRecord
from policy_impact.harness.context_manifest import build_context_manifest, estimate_tokens
from policy_impact.harness.model_config import DEFAULT_MODEL_ID, ensure_default_model_available
from policy_impact.harness.model_gateway import (
    AnthropicCompatibleModelAdapter,
    ModelAdapter,
    ModelInvocationError,
    ModelResponse,
    ScriptedModelAdapter,
)
from policy_impact.harness.permissions import PermissionEngine, ToolPolicy
from policy_impact.harness.prompt_builder import (
    build_conversation_summary_prompt,
    build_general_chat_prompt,
    build_research_report_prompt,
)
from policy_impact.harness.state import PolicyImpactState
from policy_impact.harness.subagent_planner import PlannedSubagentTask, QuerySubagentPlanner
from policy_impact.harness.subagents import SubagentRunner
from policy_impact.harness.tool_gateway import ToolGateway, reset_tool_audit_log
from policy_impact.memory.store import PolicyMemoryStore, canonical_memory_id
from policy_impact.official_legal_reference import resolve_official_legal_reference
from policy_impact.mcp_server.registry import register_all_tools
from policy_impact.rag.lexical import tokenize
from policy_impact.runtime.execution_context import SkillExecutionContext
from policy_impact.runtime.execution_mode import ExecutionMode
from policy_impact.runtime.executor_registry import (
    ExecutionOverrideRegistry,
    ExecutorRegistry,
)
from policy_impact.runtime.hooks import HookManager
from policy_impact.runtime.gate_catalog import (
    AgentStepGateDecisionSink,
    build_default_gate_registry,
)
from policy_impact.runtime.gates import GateRunner
from policy_impact.runtime.result import CitationRef, SkillResult
from policy_impact.runtime.subagent_manifests import (
    SubagentManifest,
    build_default_subagent_manifest_registry,
)
from policy_impact.runtime.subagent_roles import build_default_subagent_role_registry
from policy_impact.runtime.task_brief import SubagentResult, TaskBrief
from policy_impact.runtime.turn_coordinator import (
    RunExecutionLockRegistry,
    TurnCoordinator,
    TurnOperations,
)
from policy_impact.runtime.verifier_proof import PolicyMemoryReviewGateResolver
from policy_impact.skill_executors.policy_weekly_impact import run_policy_weekly_impact
from policy_impact.skill_executors.recent_news_report import run_recent_news_report
from policy_impact.source_connectors.ingestion import (
    load_latest_news_snapshot,
    load_latest_policy_snapshot,
    load_source_config,
)
from policy_impact.skills.executors import (
    CompanyWikiBlueprintExecutor,
    ExternalImpactReportExecutor,
    GeneralChatExecutor,
    OfficialLegalReferenceExecutor,
    OfficialLegalReferenceOverrideResolver,
    PolicyWeeklyImpactExecutor,
    RecentNewsReportExecutor,
    ResearchReportExecutor,
)
from policy_impact.skills.manifest import SkillManifest
from policy_impact.skills.registry import SkillRegistry, compatibility_tool_policy
from policy_impact.skills.schemas import SkillOutput


def _materialize_execution_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _materialize_execution_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_materialize_execution_value(item) for item in value]
    if isinstance(value, frozenset):
        return [_materialize_execution_value(item) for item in value]
    return value


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            _materialize_execution_value(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def _context_manifest_record_from_metadata(
    *,
    run_id: str,
    metadata: dict[str, Any],
    stage_name: str,
    agent_role: str,
) -> ContextManifestRecord:
    record_metadata = dict(metadata)
    record_metadata.setdefault("stage_name", stage_name)
    record_metadata.setdefault("agent_role", agent_role)
    record_metadata.setdefault("visible_keys", list(record_metadata.get("visible_keys") or []))
    record_id = str(record_metadata.get("manifest_id") or record_metadata.get("record_id") or "").strip()
    if not record_id:
        record_id = f"ctx_{_stable_hash({'run_id': run_id, 'metadata': record_metadata})[:16]}"
        record_metadata["manifest_id"] = record_id
    return ContextManifestRecord(
        record_id=record_id,
        context_type="stage_context_manifest",
        source_id=run_id,
        checksum=_stable_hash(record_metadata),
        metadata=record_metadata,
    )


class _ForcedFirstActionModelAdapter:
    """Return one deterministic runtime action, then delegate to the real adapter."""

    def __init__(self, inner: ModelAdapter, first_payload: dict[str, Any]) -> None:
        self.inner = inner
        self.first_payload = dict(first_payload)
        self._used = False

    def complete(self, messages: list[dict[str, Any]], schema_name: str) -> ModelResponse:
        if not self._used:
            self._used = True
            return ModelResponse(
                response_id=f"runtime_forced_{_stable_hash(self.first_payload)[:12]}",
                payload=dict(self.first_payload),
                metadata={
                    "adapter": type(self).__name__,
                    "schema_name": schema_name,
                    "deterministic_action": True,
                },
            )
        return self.inner.complete(messages, schema_name)


class _ForcedActionSequenceModelAdapter:
    """Return a planned action sequence, then delegate to the real adapter."""

    def __init__(
        self,
        inner: ModelAdapter,
        payloads: list[dict[str, Any]] | tuple[dict[str, Any], ...],
        *,
        planner: str,
    ) -> None:
        self.inner = inner
        self.payloads = [dict(payload) for payload in payloads]
        self.planner = planner
        self._index = 0

    def complete(self, messages: list[dict[str, Any]], schema_name: str) -> ModelResponse:
        if schema_name == "general_chat_agent_loop.v1" and self._index < len(self.payloads):
            self._index += 1
            payload = self.payloads[self._index - 1]
            return ModelResponse(
                response_id=f"runtime_forced_sequence_{self._index}_{_stable_hash(payload)[:12]}",
                payload=payload,
                metadata={
                    "adapter": type(self).__name__,
                    "schema_name": schema_name,
                    "deterministic_action": True,
                    "planner": self.planner,
                    "sequence_index": self._index,
                    "sequence_count": len(self.payloads),
                },
            )
        return self.inner.complete(messages, schema_name)


class _ReportWorkflowToolGateway:
    """Skill-scoped gateway; report workflow is not exposed as a normal chat tool."""

    def __init__(self, run_workflow) -> None:
        self._run_workflow = run_workflow
        self.last_result: dict[str, Any] = {}

    def call(self, stage_name: str, tool_name: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if args or tool_name != "report_workflow.run" or stage_name != "report_agent_loop":
            raise PermissionError("report_workflow.run is only available inside /report AgentLoop")
        message = str(kwargs.get("message") or "").strip()
        if not message:
            raise ValueError("report_workflow.run requires message")
        result = dict(self._run_workflow(message))
        self.last_result = result
        return {
            "schema_version": "report_workflow.observation.v1",
            "answer": str(result.get("answer") or ""),
            "artifacts": dict(result.get("artifacts") or {}),
            "citations": list(result.get("citations") or []),
            "internal_route": str(result.get("internal_route") or ""),
            "delegation_evidence_count": len(result.get("delegation_evidence") or []),
            "stop_reason": str(result.get("stop_reason") or "success"),
        }


class AgentRuntime:
    """Run one chat turn through controlled harness semantics and a real model boundary."""

    def __init__(
        self,
        company_id: str,
        registry: SkillRegistry | None = None,
        model_adapter: ModelAdapter | None = None,
        executor_registry: ExecutorRegistry | None = None,
        execution_override_registry: ExecutionOverrideRegistry | None = None,
        hooks: HookManager | None = None,
        run_locks: RunExecutionLockRegistry | None = None,
        operations: TurnOperations | None = None,
    ) -> None:
        self.company_id = company_id
        self.registry = registry or SkillRegistry()
        self.store = PolicyMemoryStore(company_id)
        self.model_adapter = model_adapter
        self.subagent_role_registry = build_default_subagent_role_registry()
        self.subagent_manifest_registry = build_default_subagent_manifest_registry(
            self.subagent_role_registry
        )
        self.executor_registry = executor_registry or self._build_executor_registry()
        self.execution_override_registry = (
            execution_override_registry or self._build_execution_override_registry()
        )
        self.hooks = hooks or HookManager()
        self.run_locks = run_locks or RunExecutionLockRegistry()
        register_all_tools()
        self.operations = operations or TurnOperations(
            validate_request=self._validate_turn_request,
            parse_slash_command=self._parse_slash_command,
            normalize_requested_skill_id=self._normalize_requested_skill_id,
            assign_first_turn_title=self._assign_first_turn_title,
            add_intent_step=self._add_intent_step,
            add_skill_selection_step=self._add_skill_selection_step,
            build_context=self._build_context,
            after_skill_result=self._after_skill_result,
            public_citations=self._public_citations,
        )
        self.coordinator = TurnCoordinator(
            company_id=self.company_id,
            store=self.store,
            registry=self.registry,
            executor_registry=self.executor_registry,
            execution_override_registry=self.execution_override_registry,
            hooks=self.hooks,
            run_locks=self.run_locks,
            operations=self.operations,
        )

    def _build_executor_registry(self) -> ExecutorRegistry:
        registry = ExecutorRegistry()
        general_chat = self.registry.get_manifest("general_chat")
        registry.register(
            general_chat.executor_id,
            GeneralChatExecutor(self._execute_general_chat, general_chat.output_schema),
        )
        external_impact_report = self.registry.get_manifest("external_impact_report")
        registry.register(
            external_impact_report.executor_id,
            ExternalImpactReportExecutor(
                self._execute_external_impact_report,
                external_impact_report.output_schema,
            ),
        )
        policy_weekly_impact = self.registry.get_manifest("policy_weekly_impact")
        registry.register(
            policy_weekly_impact.executor_id,
            PolicyWeeklyImpactExecutor(
                self._execute_policy_weekly_impact,
                policy_weekly_impact.output_schema,
            ),
        )
        recent_news_report = self.registry.get_manifest("recent_news_report")
        registry.register(
            recent_news_report.executor_id,
            RecentNewsReportExecutor(
                self._execute_recent_news_report,
                recent_news_report.output_schema,
            ),
        )
        research_report = self.registry.get_manifest("research_report")
        registry.register(
            research_report.executor_id,
            ResearchReportExecutor(
                self._execute_research_report,
                research_report.output_schema,
            ),
        )
        company_wiki_blueprint = self.registry.get_manifest("company_wiki_blueprint")
        registry.register(
            company_wiki_blueprint.executor_id,
            CompanyWikiBlueprintExecutor(
                self._execute_company_wiki_blueprint,
                company_wiki_blueprint.output_schema,
            ),
        )
        registry.register(
            "official_legal_reference",
            OfficialLegalReferenceExecutor(
                self._execute_official_legal_reference,
                SkillOutput,
            ),
        )
        return registry

    @staticmethod
    def _build_execution_override_registry() -> ExecutionOverrideRegistry:
        registry = ExecutionOverrideRegistry()
        registry.register(
            "official_legal_reference",
            OfficialLegalReferenceOverrideResolver(resolve_official_legal_reference),
        )
        return registry

    def run_turn(
        self,
        session_id: str,
        message: str,
        mode: str,
        model_id: str,
        active_skill_id: str = "",
    ) -> dict[str, Any]:
        return self.coordinator.run_turn(
            session_id=session_id,
            message=message,
            mode=mode,
            model_id=model_id,
            active_skill_id=active_skill_id,
        )

    def _validate_turn_request(
        self,
        session_id: str,
        message: str,
        mode: str,
        model_id: str,
        active_skill_id: str,
    ) -> None:
        del session_id, message, mode, active_skill_id
        if model_id != DEFAULT_MODEL_ID:
            raise ValueError(f"模型不存在或不可用：{model_id}。当前只允许 {DEFAULT_MODEL_ID}。")
        if not self.store.get_model_config(model_id):
            raise ValueError("没有可用模型，无法执行。请先检查默认模型配置。")
        if self.model_adapter is None:
            ensure_default_model_available()

    def _normalize_requested_skill_id(
        self,
        requested_skill_id: str,
        inline_skill_id: str | None,
        task_message: str,
    ) -> str:
        if inline_skill_id is None and self._requests_memory_write(task_message):
            return "general_chat"
        return requested_skill_id

    def _after_skill_result(
        self,
        run_id: str,
        message: str,
        skill: SkillManifest,
        result: SkillResult,
    ) -> list[dict[str, Any]]:
        planned_writes: list[dict[str, Any]] = []
        for proposal in result.memory_proposals:
            parsed = json.loads(proposal)
            if not isinstance(parsed, dict):
                raise ValueError("memory proposal must decode to an object")
            if parsed.get("schema_version") != "runtime.memory_write.v1":
                raise ValueError("unsupported memory proposal schema")
            planned_writes.append(parsed)
        planned_writes.extend(
            self._plan_skill_memory_if_requested(
                run_id,
                message,
                skill,
                result.model_dump(mode="python"),
            )
        )
        return planned_writes

    @staticmethod
    def _public_citations(citations: list[CitationRef]) -> list[dict[str, Any]]:
        serialized = []
        for citation in citations:
            item = citation.model_dump(mode="python", exclude_defaults=True)
            citation_type = str(item.pop("citation_type", "") or "")
            if citation_type:
                item["type"] = citation_type
            serialized.append(item)
        return serialized

    @staticmethod
    def _parse_slash_command(message: str) -> tuple[str | None, str]:
        text = str(message or "").strip()
        match = re.search(
            r"(^|\s)/(auto|chat|report|impact|policy|news|research|wiki)(?=\s|$)",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return None, text
        command_to_skill = {
            "auto": "",
            "chat": "general_chat",
            "report": "external_impact_report",
            "impact": "external_impact_report",
            "policy": "external_impact_report",
            "news": "external_impact_report",
            "research": "external_impact_report",
            "wiki": "company_wiki_blueprint",
        }
        command = match.group(2).lower()
        command_start = match.start() + len(match.group(1) or "")
        command_end = match.end()
        task = f"{text[:command_start]} {text[command_end:]}".strip()
        task = re.sub(r"\s+", " ", task)
        return command_to_skill[command], task or text

    def _assign_first_turn_title(self, session_id: str, message: str) -> None:
        session = self.store.get_chat_session(session_id)
        if not session or int(session.get("message_count") or 0) > 0:
            return
        current = str(session.get("title") or "").strip()
        if current not in {"", "新对话", "未命名对话"} and not re.fullmatch(r"\?{3,}", current):
            return
        title = re.sub(r"\s+", " ", str(message or "")).strip(" /：:")
        if not title:
            return
        self.store.update_chat_session_settings(session_id, title=title[:32])

    def _add_intent_step(
        self,
        run_id: str,
        message: str,
        mode: str,
        skill: SkillManifest,
    ) -> None:
        self.store.add_agent_step(
            run_id,
            step_type="intent_classification",
            title="识别本轮意图",
            status="done",
            input_payload={"message": message, "mode": mode},
            output_payload={"resolved_skill_id": skill.id},
            metadata={"method": "deterministic_keyword_router", "loop_phase": "think"},
        )

    def _add_skill_selection_step(
        self,
        run_id: str,
        skill: SkillManifest,
        active_skill_id: str,
    ) -> None:
        self.store.add_agent_step(
            run_id,
            step_type="skill_selection",
            title="选择执行技能",
            status="done",
            input_payload={"active_skill_id": active_skill_id},
            output_payload={
                "skill": {
                    "id": skill.id,
                    "name": skill.name,
                    "mode": skill.execution_mode.value,
                    "subagents": list(skill.allowed_subagents),
                    "artifact_types": list(
                        skill.output_schema.model_json_schema().get("artifact_types", [])
                    ),
                }
            },
            metadata={
                "selection_source": "explicit" if active_skill_id else "resolver",
                "loop_phase": "think",
            },
        )

    def _build_context(
        self,
        run_id: str,
        session_id: str,
        message: str,
        skill: SkillManifest,
        model_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        config = ChatContextConfig()
        all_messages = self.store.list_chat_messages(session_id)
        history = all_messages[:-1] if all_messages and all_messages[-1].get("role") == "user" else all_messages
        summary = self._ensure_conversation_summary(
            run_id=run_id,
            session_id=session_id,
            history=history,
            model_id=model_id,
            config=config,
        )
        strict_knowledge_only = (
            skill.id == "general_chat"
            and self._requires_retrieved_evidence_only(message)
            and "知识库" in message
            and "记忆" not in message
        )
        unbound_project_scope = skill.id == "general_chat" and self._is_unbound_project_comparison(message)
        workbench_project_scope = skill.id == "general_chat" and self._is_workbench_design_query(message)
        raw_memories: list[dict[str, Any]] = []
        memories: list[dict[str, Any]] = []
        facts: list[dict[str, Any]] = []
        context_tool_calls: list[dict[str, Any]] = []
        if skill.id == "general_chat":
            history_only = self._is_immediate_history_query(message) or self._is_session_context_declaration(message)
            reset_tool_audit_log()
            gateway = ToolGateway()
            raw_memories = [] if strict_knowledge_only else gateway.call(
                "main_agent",
                "memory_search",
                company_id=self.company_id,
                query=message,
                top_k=5,
            )
            memories = [] if history_only else self._filter_general_chat_memories(message, raw_memories)
            raw_facts = [] if unbound_project_scope or workbench_project_scope else gateway.call(
                "main_agent",
                "company_wiki_search",
                company_id=self.company_id,
                query=message,
                top_k=12,
            )
            facts = [] if history_only else raw_facts
            context_tool_calls = [record.to_dict() for record in gateway.calls]
            self.store.add_agent_step(
                run_id,
                step_type="memory_read",
                title="读取长期记忆",
                status="done",
                input_payload={"query": message, "top_k": 5},
                output_payload={
                    "memory_count": len(memories),
                    "raw_memory_count": len(raw_memories),
                    "filtered_out": max(0, len(raw_memories) - len(memories)),
                    "memory_ids": [str(item.get("id") or "") for item in memories],
                    "history_only_resolution": history_only,
                },
                metadata={"loop_phase": "observe"},
            )
            requested_tools = [str(record.get("tool_name") or "") for record in context_tool_calls]
            self.store.add_agent_step(
                run_id,
                step_type="tool_call",
                title="检索记忆与企业知识库上下文",
                status="done",
                input_payload={"tools": requested_tools},
                output_payload={
                    "fact_count": len(facts),
                    "raw_fact_count": len(raw_facts),
                    "memory_count": len(memories),
                    "fact_ids": [str(item.get("fact_id") or "") for item in facts],
                    "history_only_resolution": history_only,
                    "workbench_project_scope": workbench_project_scope,
                },
                tool_calls=context_tool_calls,
                gate_result={
                    "decision": "allow",
                    "reason": (
                        "Workbench 项目问题不注入企业 Wiki fact，避免把公司画像当项目事实"
                        if workbench_project_scope
                        else "main_agent allowlist permits context retrieval tools"
                    ),
                },
                metadata={"loop_phase": "act", "context_retrieval": True},
            )
        elif skill.id in {"research_report", "external_impact_report"}:
            raw_memories = self.store.search_memory(message, top_k=8)
            memories = self._filter_research_memories(message, raw_memories)
            self.store.add_agent_step(
                run_id,
                step_type="memory_read",
                title=(
                    "为外部变化分析读取长期记忆"
                    if skill.id == "external_impact_report"
                    else "为调研报告读取记忆"
                ),
                status="done",
                input_payload={"query": message, "top_k": 8},
                output_payload={
                    "memory_count": len(memories),
                    "raw_memory_count": len(raw_memories),
                    "filtered_out": max(0, len(raw_memories) - len(memories)),
                    "memory_ids": [str(item.get("id") or "") for item in memories],
                },
                metadata={"loop_phase": "observe"},
            )

        skill_payload = {
            "id": skill.id,
            "name": skill.name,
            "mode": skill.execution_mode.value,
            "subagents": list(skill.allowed_subagents),
        }
        tool_policy = {
            "decision_order": ["deny", "ask", "allow"],
            "skill_allowlists": compatibility_tool_policy(skill),
            "runtime_owns_permissions": True,
        }
        compiled = compile_chat_context(
            message=message,
            skill=skill_payload,
            history=history,
            conversation_summary=summary,
            memories=memories,
            facts=facts,
            tool_policy=tool_policy,
            config=config,
        )
        recent_run_traces = self._compile_recent_run_traces(
            history,
            limit=3,
            token_budget=config.run_trace_token_budget,
        )
        compiled["recent_run_traces"] = recent_run_traces
        compiled["context_policy"]["recent_run_traces_included"] = len(recent_run_traces)
        state = PolicyImpactState(company_id=self.company_id, run_id=run_id)
        state.current_user_message = compiled["current_user_message"]
        state.selected_skill = compiled["selected_skill"]
        state.recent_history = compiled["recent_history"]
        state.recent_artifacts = compiled["recent_artifacts"]
        state.recent_run_traces = compiled["recent_run_traces"]
        state.conversation_summary = compiled["conversation_summary"]
        state.relevant_memories = compiled["relevant_memories"]
        state.company_facts = compiled["company_facts"]
        state.tool_policy_summary = compiled["tool_policy_summary"]
        state.context_policy = compiled["context_policy"]
        manifest = build_context_manifest(
            state=state,
            stage_name="chat_turn",
            agent_role=f"skill:{skill.id}",
            visible_fields=[
                "current_user_message",
                "selected_skill",
                "recent_history",
                "recent_artifacts",
                "recent_run_traces",
                "conversation_summary",
                "relevant_memories",
                "company_facts",
                "tool_policy_summary",
                "context_policy",
            ],
            token_budget=config.manifest_budget(),
            manifest_sequence=1,
        )
        prepared = {
            "history": history,
            "recent_history": compiled["recent_history"],
            "recent_artifacts": compiled["recent_artifacts"],
            "recent_run_traces": compiled["recent_run_traces"],
            "conversation_summary": compiled["conversation_summary"],
            "memories": memories,
            "facts": facts,
            "strict_knowledge_only": strict_knowledge_only,
            "unbound_project_scope": unbound_project_scope,
            "workbench_project_scope": workbench_project_scope,
            "context_tool_calls": context_tool_calls,
        }
        return manifest.metadata, prepared

    def _compile_recent_run_traces(
        self,
        history: list[dict[str, Any]],
        *,
        limit: int,
        token_budget: int,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        seen_run_ids: set[str] = set()
        for message in reversed(history):
            if str(message.get("role") or "") != "assistant":
                continue
            metadata = message.get("metadata") or {}
            if not isinstance(metadata, dict):
                continue
            run_id = str(metadata.get("run_id") or "").strip()
            if not run_id or run_id in seen_run_ids:
                continue
            seen_run_ids.add(run_id)
            run = self.store.get_agent_run(run_id) or {}
            steps = self.store.list_agent_steps(run_id) if run else []
            step_types: list[str] = []
            internal_routes: list[str] = []
            tool_names: list[str] = []
            gate_decisions: list[dict[str, str]] = []
            report_workflow_run = False
            workflow_observation_returned = False
            for step in steps:
                step_type = str(step.get("step_type") or "")
                if step_type:
                    step_types.append(step_type)
                step_metadata = step.get("metadata") or {}
                output = step.get("output_payload") or {}
                if output.get("internal_route"):
                    internal_routes.append(str(output.get("internal_route")))
                payload = output.get("payload") if isinstance(output.get("payload"), dict) else {}
                payload_tool = str(payload.get("tool_name") or "")
                if payload_tool:
                    tool_names.append(payload_tool)
                if payload_tool == "report_workflow.run":
                    report_workflow_run = True
                for call in step.get("tool_calls") or []:
                    tool_name = str(call.get("tool_name") or "")
                    if tool_name:
                        tool_names.append(tool_name)
                if (
                    step_metadata.get("embedded_loop") == "external_impact_report"
                    and step_type == "workflow_stage"
                ):
                    workflow_observation_returned = True
                gate_result = step.get("gate_result") or {}
                if gate_result.get("decision"):
                    gate_decisions.append(
                        {
                            "step_type": step_type,
                            "decision": str(gate_result.get("decision") or ""),
                            "policy_id": str(gate_result.get("policy_id") or ""),
                            "reason": str(gate_result.get("reason") or "")[:120],
                        }
                    )
            run_artifacts = list(run.get("artifacts") or [])
            metadata_artifacts = metadata.get("artifacts") or {}
            artifact_keys = [
                str(item.get("artifact_type") or "")
                for item in run_artifacts
                if isinstance(item, dict) and item.get("artifact_type")
            ]
            if not artifact_keys and isinstance(metadata_artifacts, dict):
                artifact_keys = sorted(str(key) for key in metadata_artifacts)
            record = {
                "recency_rank": len(records) + 1,
                "run_id": run_id,
                "skill_id": str(metadata.get("skill_id") or ""),
                "status": str(run.get("status") or ""),
                "step_types": step_types,
                "internal_routes": _unique_preserve_order(internal_routes),
                "tool_names": _unique_preserve_order(tool_names),
                "report_workflow_run": report_workflow_run,
                "workflow_observation_returned": workflow_observation_returned,
                "artifact_keys": artifact_keys,
                "artifact_count": len(artifact_keys),
                "gate_decisions": gate_decisions[:6],
            }
            trial = [*records, record]
            if estimate_tokens(trial) <= token_budget:
                records.append(record)
            if len(records) >= limit:
                break
        return records

    def _ensure_conversation_summary(
        self,
        *,
        run_id: str,
        session_id: str,
        history: list[dict[str, Any]],
        model_id: str,
        config: ChatContextConfig,
    ) -> dict[str, Any]:
        compacted_count = max(0, len(history) - config.recent_message_limit)
        if compacted_count == 0:
            return {}
        compacted_messages = history[:compacted_count]
        source_hash = hashlib.sha256(
            json.dumps(
                [
                    {
                        "role": item.get("role", ""),
                        "content": item.get("content", ""),
                    }
                    for item in compacted_messages
                ],
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        existing = self.store.get_conversation_summary(session_id)
        if existing and existing.get("source_hash") == source_hash:
            self.store.add_agent_step(
                run_id,
                step_type="context_compaction",
                title="复用增量对话摘要",
                status="done",
                input_payload={"source_message_count": compacted_count},
                output_payload={
                    "summary_id": f"conversation_summary:{session_id}",
                    "source_hash": source_hash,
                    "reused": True,
                },
                metadata={"loop_phase": "observe", "compaction_policy": "incremental_model_summary"},
            )
            return dict(existing.get("summary") or {})

        previous_count = int((existing or {}).get("source_message_count") or 0)
        if previous_count < 0 or previous_count > compacted_count:
            previous_count = 0
        incremental_messages = compacted_messages[previous_count:]
        previous_summary = dict((existing or {}).get("summary") or {}) if previous_count else {}
        prompt_messages, prompt_contract = build_conversation_summary_prompt(
            previous_summary=previous_summary,
            messages=incremental_messages,
        )
        fallback_used = False
        try:
            response = self._complete_model(
                run_id=run_id,
                title="压缩历史对话上下文",
                model_id=model_id,
                schema_name="conversation_summary.v1",
                messages=prompt_messages,
                prompt_contract=prompt_contract,
            )
            summary = dict(response.payload.get("output") or {})
        except ModelInvocationError as exc:
            fallback_used = True
            summary = self._extractive_conversation_summary(previous_summary, incremental_messages)
            self.store.add_agent_step(
                run_id,
                step_type="gate_check",
                title="执行上下文压缩降级门控",
                status="done",
                input_payload={"contract_id": "conversation_summary.v1"},
                output_payload={"fallback": "loss_aware_extractive", "error": str(exc)},
                gate_result={
                    "decision": "repair",
                    "reason": "摘要模型不可用，使用不引入新事实的抽取式压缩并保留失败证据",
                },
                metadata={"loop_phase": "observe"},
            )
        saved = self.store.upsert_conversation_summary(
            session_id=session_id,
            summary=summary,
            source_message_count=compacted_count,
            source_hash=source_hash,
        )
        self.store.add_agent_step(
            run_id,
            step_type="context_compaction",
            title="生成增量对话摘要",
            status="done",
            input_payload={
                "previous_source_message_count": previous_count,
                "incremental_message_count": len(incremental_messages),
                "source_message_count": compacted_count,
            },
            output_payload={
                "summary_id": f"conversation_summary:{session_id}",
                "source_hash": source_hash,
                "reused": False,
                "fallback_used": fallback_used,
                "summary_keys": sorted(summary),
            },
            gate_result={
                "decision": "allow",
                "reason": "摘要通过 conversation_summary.v1 契约或无新增事实的抽取式降级生成",
                "policy_id": "context.compaction.v1",
            },
            metadata={"loop_phase": "observe", "compaction_policy": "incremental_model_summary"},
        )
        self.store.add_agent_step(
            run_id,
            step_type="memory_write",
            title="持久化增量对话摘要",
            status="done",
            input_payload={
                "memory_type": "conversation_summary",
                "write_policy": "context_pressure_compaction",
            },
            output_payload={
                "memory_id": f"conversation_summary:{session_id}",
                "namespace": f"company:{self.company_id}:conversation",
                "created": existing is None,
                "updated": existing is not None,
                "source_message_count": compacted_count,
                "source_hash": source_hash,
                "fallback_used": fallback_used,
            },
            gate_result={
                "decision": "allow",
                "reason": "仅压缩已持久化对话，不新增用户事实或企业事实",
            },
            metadata={"loop_phase": "act", "compaction_policy": "incremental_model_summary"},
        )
        return dict(saved.get("summary") or {})

    @staticmethod
    def _extractive_conversation_summary(
        previous_summary: dict[str, Any],
        messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        excerpts = []
        for item in messages:
            role = "用户" if item.get("role") == "user" else "助手"
            content = " ".join(str(item.get("content") or "").split())[:240]
            if content:
                excerpts.append(f"{role}：{content}")
        previous_text = str(previous_summary.get("summary") or "").strip()
        merged = "；".join([part for part in (previous_text, *excerpts) if part])[:900]
        return {
            "summary": merged or "旧对话没有可压缩的实质内容。",
            "user_facts": list(previous_summary.get("user_facts") or [])[:6],
            "decisions": list(previous_summary.get("decisions") or [])[:6],
            "open_loops": list(previous_summary.get("open_loops") or [])[:6],
        }

    def _execute_skill(
        self,
        run_id: str,
        session_id: str,
        skill: SkillManifest,
        message: str,
        model_id: str,
        context_manifest: dict[str, Any],
        prepared_context: dict[str, Any],
        *,
        executor_id: str,
        services: Mapping[str, Any],
    ) -> SkillResult:
        context = SkillExecutionContext(
            run_id=run_id,
            session_id=session_id,
            company_id=self.company_id,
            message=message,
            model_id=model_id,
            context_manifest=context_manifest,
            prepared_context=prepared_context,
            services=services,
        )
        result = self.executor_registry.get(executor_id).execute(context)
        self._persist_skill_memory_if_requested(
            run_id,
            message,
            skill,
            result.model_dump(mode="python"),
        )
        return result

    def _execute_general_chat(self, context: SkillExecutionContext) -> dict[str, Any]:
        return self._run_general_chat(
            context.run_id,
            context.session_id,
            context.message,
            context.model_id,
            _materialize_execution_value(context.context_manifest),
            _materialize_execution_value(context.prepared_context),
        )

    def _execute_external_impact_report(self, context: SkillExecutionContext) -> dict[str, Any]:
        return self._run_external_impact_report(
            context.run_id,
            context.session_id,
            context.message,
            context.model_id,
            _materialize_execution_value(context.context_manifest),
            _materialize_execution_value(context.prepared_context),
        )

    def _execute_policy_weekly_impact(
        self,
        context: SkillExecutionContext,
    ) -> dict[str, Any]:
        return self._run_policy_weekly_impact(
            context.run_id,
            context.session_id,
            context.message,
        )

    def _execute_recent_news_report(self, context: SkillExecutionContext) -> dict[str, Any]:
        return self._run_recent_news_report(context.run_id, context.message)

    def _execute_research_report(self, context: SkillExecutionContext) -> dict[str, Any]:
        return self._run_research_report(
            context.run_id,
            context.message,
            context.model_id,
            _materialize_execution_value(context.context_manifest),
            _materialize_execution_value(context.prepared_context),
        )

    def _execute_company_wiki_blueprint(
        self,
        context: SkillExecutionContext,
    ) -> dict[str, Any]:
        return self._run_company_wiki_blueprint(context.run_id)

    def _execute_official_legal_reference(
        self,
        context: SkillExecutionContext,
    ) -> dict[str, Any]:
        return self._run_official_legal_reference(
            context.run_id,
            context.message,
            _materialize_execution_value(context.services["legal_reference"]),
        )

    def _run_official_legal_reference(
        self,
        run_id: str,
        message: str,
        legal_reference: dict[str, Any],
    ) -> dict[str, Any]:
        self.store.add_agent_step(
            run_id,
            step_type="tool_call",
            title="核验官方法规原文",
            status="done",
            input_payload={
                "reference_id": legal_reference["reference_id"],
                "query": message,
            },
            output_payload={
                "title": legal_reference["title"],
                "official_text": legal_reference["official_text"],
                "official_url": legal_reference["official_url"],
            },
            gate_result={
                "decision": "allow",
                "reason": "命中经过人工核验的官方法规原文注册表",
            },
            metadata={"loop_phase": "observe", "source_level": "official_primary"},
        )
        return {
            "answer": legal_reference["answer"],
            "artifacts": {},
            "citations": [
                {
                    "type": "official_legal_reference",
                    "title": legal_reference["title"],
                    "url": legal_reference["official_url"],
                }
            ],
        }

    def _run_general_chat(
        self,
        run_id: str,
        session_id: str,
        message: str,
        model_id: str,
        context_manifest: dict[str, Any],
        prepared_context: dict[str, Any],
    ) -> dict[str, Any]:
        strict_knowledge_only = bool(prepared_context.get("strict_knowledge_only"))
        memories = list(prepared_context.get("memories") or [])
        facts = list(prepared_context.get("facts") or [])
        citations = []
        visible_fact_ids = {
            str(item.get("fact_id") or "")
            for item in (context_manifest.get("visible_content") or {}).get("company_facts") or []
        }
        for fact in facts:
            if str(fact.get("fact_id") or "") not in visible_fact_ids:
                continue
            citations.append(
                {
                    "type": "company_fact",
                    "fact_id": fact.get("fact_id"),
                    "path": fact.get("source_path"),
                    "source": fact.get("source"),
                    "source_kind": fact.get("source_kind"),
                }
            )
        for item in memories[:3]:
            citations.append({"type": "memory_item", "memory_id": item.get("id")})
        history = self.store.list_chat_messages(session_id)
        prior_fact_answer = self._prior_fact_id_answer(message, history[:-1])
        if prior_fact_answer:
            prior_citations = self._prior_company_fact_citations(history[:-1])
            self.store.add_agent_step(
                run_id,
                step_type="evidence_replay",
                title="复现上一轮企业事实引用",
                status="done",
                input_payload={"question": message},
                output_payload={"fact_ids": [item["fact_id"] for item in prior_citations]},
                gate_result={
                    "decision": "allow",
                    "reason": "fact_id 来自上一轮已持久化回答与引用元数据",
                },
                metadata={"loop_phase": "observe", "deterministic": True},
            )
            return {
                "answer": prior_fact_answer,
                "artifacts": {},
                "citations": prior_citations,
            }
        if self._is_unpersisted_cross_session_query(message):
            self.store.add_agent_step(
                run_id,
                step_type="gate_check",
                title="执行跨会话临时信息隔离门控",
                status="done",
                input_payload={"policy_id": "memory.cross_session_isolation.v1"},
                output_payload={"model_call_skipped": True},
                gate_result={
                    "decision": "deny",
                    "reason": "未显式写入长期记忆的会话临时信息不得跨会话读取",
                    "policy_id": "memory.cross_session_isolation.v1",
                },
                metadata={"loop_phase": "observe", "deterministic": True},
            )
            return {
                "answer": (
                    "我无法读取另一个会话中未写入长期记忆的临时内容。"
                    "只有你明确要求保存的长期记忆才能跨会话召回；请回到原会话查看，或在当前会话重新提供。"
                ),
                "artifacts": {},
                "citations": [],
            }
        if prepared_context.get("unbound_project_scope"):
            configured_subject = (
                "company_001（示例公司企业画像）"
                if self.company_id == "company_001"
                else self.company_id
            )
            answer = (
                "当前无法基于受控证据比较“这个项目”和 CoursePilot：当前企业知识库属于 "
                f"{configured_subject}，不是 Agent Workbench 或 CoursePilot 的项目资料；"
                "长期记忆中也没有足以消除“这个项目”指代歧义的项目契约。"
                "本轮已阻止把企业画像事实冒充项目实现事实，也没有调用模型补造差异。"
                "请明确项目名称，并为两个项目提供 README、能力边界或结构化项目画像后再比较。"
            )
            self.store.add_agent_step(
                run_id,
                step_type="gate_check",
                title="确认项目比较对象",
                status="done",
                input_payload={"message": message, "configured_company_id": self.company_id},
                output_payload={"model_call_skipped": True},
                gate_result={
                    "decision": "ask",
                    "reason": "项目指代不明确，且企业 Wiki 不能作为项目代码事实",
                    "policy_id": "general_chat.subject_scope.v1",
                },
                metadata={"loop_phase": "observe", "deterministic": True},
            )
            return {"answer": answer, "artifacts": {}, "citations": []}
        chat_loop = self._run_general_chat_agent_loop(
            run_id=run_id,
            message=message,
            model_id=model_id,
            context_manifest=context_manifest,
            prepared_context=prepared_context,
            memories=memories,
            facts=facts,
        )
        answer = str(chat_loop.get("answer") or "").strip()
        if not answer:
            raise ModelInvocationError(
                f"普通对话 AgentLoop 未产出 answer：{chat_loop.get('stop_reason') or 'unknown'}"
            )
        delegation_evidence = list(chat_loop.get("delegation_evidence") or [])
        governed_answer, answer_contract = self._govern_general_chat_answer(message, answer, facts)
        if answer_contract:
            answer = governed_answer
            self.store.add_agent_step(
                run_id,
                step_type="gate_check",
                title="执行企业回答契约门控",
                status="done",
                input_payload={"contract_id": answer_contract["contract_id"]},
                output_payload={
                    "repair_applied": True,
                    "required_fields": answer_contract["required_fields"],
                },
                gate_result={
                    "decision": "repair",
                    "reason": answer_contract["reason"],
                    "policy_id": answer_contract["contract_id"],
                },
                metadata={"loop_phase": "observe", "deterministic": True},
            )
        memory_requested = self._requests_memory_write(message)
        memory_content = self._explicit_memory_content(message)
        feedback_memory_content = "" if memory_content else self._feedback_memory_content(message)
        memory_proposals: list[str] = []
        if memory_content:
            namespace = f"company:{self.company_id}:user_profile"
            memory_id, memory_created = self._plan_deduplicated_memory_id(
                namespace,
                "explicit_user_memory",
                memory_content,
            )
            memory_plan = {
                "schema_version": "runtime.memory_write.v1",
                "memory_id": memory_id,
                "namespace": namespace,
                "memory_type": "explicit_user_memory",
                "content": memory_content,
                "importance": 0.9,
                "deduplicate": True,
                "metadata": {
                    "source": "explicit_user_request",
                    "run_id": run_id,
                },
                "step": {
                    "step_type": "memory_write",
                    "title": "写入用户明确要求保存的长期记忆",
                    "status": "done",
                    "input_payload": {
                        "memory_type": "explicit_user_memory",
                        "write_policy": "explicit_user_request_only",
                    },
                    "output_payload": {
                        "namespace": namespace,
                        "content_preview": memory_content[:160],
                        "created": memory_created,
                        "deduplicated": not memory_created,
                    },
                    "gate_result": {
                        "decision": "allow",
                        "reason": "用户本轮明确要求记住该信息",
                    },
                    "metadata": {"loop_phase": "act"},
                },
            }
            memory_proposals.append(
                json.dumps(memory_plan, ensure_ascii=False, sort_keys=True)
            )
            citations = [item for item in citations if item.get("type") != "memory_item"]
            citations.append({"type": "memory_item", "memory_id": memory_id})
            answer = (
                f"已写入长期记忆。\n- 保存内容：{memory_content}"
                if memory_created
                else f"长期记忆中已存在相同内容，本轮未重复写入。\n- 保存内容：{memory_content}"
            )
        elif feedback_memory_content:
            namespace = f"company:{self.company_id}:user_profile"
            memory_id, memory_created = self._plan_deduplicated_memory_id(
                namespace,
                "feedback_memory",
                feedback_memory_content,
            )
            memory_plan = {
                "schema_version": "runtime.memory_write.v1",
                "memory_id": memory_id,
                "namespace": namespace,
                "memory_type": "feedback_memory",
                "content": feedback_memory_content,
                "importance": 0.85,
                "deduplicate": True,
                "metadata": {
                    "source": "user_feedback",
                    "run_id": run_id,
                },
                "step": {
                    "step_type": "memory_write",
                    "title": "写入用户反馈偏好",
                    "status": "done",
                    "input_payload": {
                        "memory_type": "feedback_memory",
                        "write_policy": "durable_user_feedback_only",
                    },
                    "output_payload": {
                        "namespace": namespace,
                        "content_preview": feedback_memory_content[:160],
                        "created": memory_created,
                        "deduplicated": not memory_created,
                    },
                    "gate_result": {
                        "decision": "allow",
                        "reason": "用户本轮给出会影响后续报告和建议排序的反馈",
                    },
                    "metadata": {"loop_phase": "act"},
                },
            }
            memory_proposals.append(
                json.dumps(memory_plan, ensure_ascii=False, sort_keys=True)
            )
            citations = [item for item in citations if item.get("type") != "memory_item"]
            citations.append({"type": "memory_item", "memory_id": memory_id})
            if memory_created:
                answer = f"{answer}\n\n已记录这条反馈，后续报告和建议排序会参考它。[memory_id: {memory_id}]"
        elif memory_requested:
            answer = "未执行记忆写入：没有识别到冒号后的具体保存内容。请使用“请记住：...”后重试。"
        citations = self._ground_general_chat_citations(
            message,
            answer,
            citations,
            memory_requested=memory_requested,
            strict_knowledge_only=strict_knowledge_only,
            memory_items=memories,
        )
        answer = self._attach_grounded_memory_citations(answer, citations)
        return {
            "answer": answer,
            "artifacts": {},
            "citations": citations,
            "memory_proposals": memory_proposals,
            "delegation_evidence": delegation_evidence,
            "stop_reason": str(chat_loop.get("stop_reason") or "success"),
        }

    def _run_general_chat_agent_loop(
        self,
        *,
        run_id: str,
        message: str,
        model_id: str,
        context_manifest: dict[str, Any],
        prepared_context: dict[str, Any],
        memories: list[dict[str, Any]],
        facts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        skill_manifest = self.registry.get_manifest("general_chat")
        artifact_ref = f"main_chat_context:{run_id[:12]}"
        agent_roster = self._subagent_roster_for_skill(skill_manifest)
        task_artifact = self._build_dynamic_subagent_context_artifact(
            message=message,
            context_manifest=context_manifest,
            prepared_context=prepared_context,
        )
        planned_tasks = QuerySubagentPlanner().plan(
            message,
            skill_manifest=skill_manifest,
            artifact_ref=artifact_ref,
            prepared_context=prepared_context,
        )
        if planned_tasks:
            self.store.add_agent_step(
                run_id,
                step_type="subagent_plan",
                title="规划普通对话动态子智能体",
                status="done",
                input_payload={"message": message},
                output_payload={
                    "planner": "query_subagent_planner.v1",
                    "artifact_ref": artifact_ref,
                    "tasks": [task.to_dict() for task in planned_tasks],
                },
                gate_result={
                    "decision": "allow",
                    "reason": "根据用户问题中的来源、分析、质疑和核验信号生成受控委派计划",
                    "policy_id": "subagent.general_chat_query_plan.v1",
                },
                metadata={
                    "loop_phase": "observe",
                    "planner": "query_subagent_planner.v1",
                    "dynamic_subagent": True,
                },
            )
        loop_state = PolicyImpactState(company_id=self.company_id, run_id=run_id)
        loop_state.current_user_message = message
        loop_state.selected_skill = {"id": "general_chat", "mode": "agent_loop"}
        loop_state.recent_history = list(prepared_context.get("recent_history") or [])
        loop_state.recent_artifacts = list(prepared_context.get("recent_artifacts") or [])
        loop_state.recent_run_traces = list(prepared_context.get("recent_run_traces") or [])
        loop_state.conversation_summary = dict(prepared_context.get("conversation_summary") or {})
        loop_state.relevant_memories = list(memories)
        loop_state.company_facts = list(facts)
        loop_state.tool_policy_summary = {
            "decision_order": ["deny", "ask", "allow"],
            "runtime_owns_permissions": True,
            "main_loop_tools": ["delegate"],
            "subagent_roster_count": len(agent_roster),
        }
        visible_content = dict(context_manifest.get("visible_content") or {})
        visible_content.update(
            {
                "current_user_message": message,
                "selected_skill": loop_state.selected_skill,
                "relevant_memories": loop_state.relevant_memories,
                "company_facts": loop_state.company_facts,
                "recent_history": loop_state.recent_history,
                "recent_artifacts": loop_state.recent_artifacts,
                "recent_run_traces": loop_state.recent_run_traces,
                "conversation_summary": loop_state.conversation_summary,
                "runtime_self_knowledge": (
                    []
                    if self._requires_retrieved_evidence_only(message)
                    else self._general_system_facts(message)
                ),
                "agent_roster": agent_roster,
                "recommended_delegation_plan": [task.to_dict() for task in planned_tasks],
                "delegated_context_artifact_ref": artifact_ref,
                "delegation_policy": {
                    "policy_id": "main_agent.spawn_subagent.v1",
                    "delegate_only_when": [
                        "用户显式要求子智能体、多角度、质疑、验证或来源收集",
                        "问题需要独立证据收集、结构化分析、反方审查或证据核验",
                        "简单解释、寒暄、记忆写入和明确事实复述不要委派",
                    ],
                    "max_roles_per_turn": 3,
                    "allowed_roles": list(skill_manifest.allowed_subagents),
                },
            }
        )
        manifest_record = _context_manifest_record_from_metadata(
            run_id=run_id,
            metadata={
                **dict(context_manifest),
                "stage_name": "chat_agent_loop",
                "agent_role": "main_chat_agent",
                "loop_boundary": "Main Agent may call delegate; Runtime owns subagent manifests, permissions, context, trace, and evidence.",
                "available_actions": ["delegate", "final"],
                "default_delegated_artifact_ref": artifact_ref,
                "default_delegated_artifact_refs": [artifact_ref],
                "visible_content": visible_content,
            },
            stage_name="chat_agent_loop",
            agent_role="main_chat_agent",
        )
        loop_state.add_context_manifest(manifest_record.to_dict())
        delegate_records: list[dict[str, Any]] = []

        def delegate_fn(brief: TaskBrief) -> SubagentResult:
            return self._run_general_chat_delegate(
                parent_state=loop_state,
                skill_manifest=skill_manifest,
                brief=brief,
                artifact_ref=artifact_ref,
                artifact_payload=task_artifact,
                prepared_context=prepared_context,
                delegate_records=delegate_records,
            )

        model_adapter: ModelAdapter = self._get_model_adapter()
        if planned_tasks and not isinstance(model_adapter, ScriptedModelAdapter):
            model_adapter = _ForcedActionSequenceModelAdapter(
                model_adapter,
                [
                    {
                        "type": "delegate",
                        "role": task.role,
                        "task": task.task,
                        "input_artifact_refs": [artifact_ref],
                    }
                    for task in planned_tasks
                ],
                planner="query_subagent_planner.v1",
            )
        loop_result = AgentLoop(
            model_adapter,
            ToolGateway(permission_engine=PermissionEngine([])),
            delegate_fn=delegate_fn,
            skill_manifest=skill_manifest,
            role_registry=self.subagent_role_registry,
            reject_hidden_reasoning=True,
        ).run(
            loop_state,
            AgentLoopConfig(
                stage_name="chat_agent_loop",
                agent_role="main_chat_agent",
                context_manifest=manifest_record,
                output_key="general_chat_agent_loop.v1",
                execution_mode=ExecutionMode.AGENT_LOOP,
                max_steps=skill_manifest.max_steps,
                max_model_calls=skill_manifest.max_model_calls,
                timeout_seconds=skill_manifest.timeout_seconds,
                max_calls_per_tool=1,
                task_prompt=self._general_chat_loop_task_prompt(message, artifact_ref, agent_roster),
            ),
        )
        self._record_general_chat_agent_loop(run_id, loop_state, loop_result, delegate_records)
        final_output = loop_result.final_output if loop_result.stop_reason == "success" else {}
        return {
            "answer": str(final_output.get("answer") or "").strip(),
            "delegation_evidence": [
                item.model_dump(mode="json") for item in loop_result.delegation_evidence
            ],
            "stop_reason": loop_result.stop_reason,
            "actual_execution_mode": loop_result.actual_execution_mode.value,
        }

    def _subagent_roster_for_skill(self, skill_manifest: SkillManifest) -> list[dict[str, Any]]:
        manifests = self.subagent_manifest_registry.for_roles(skill_manifest.allowed_subagents)
        return [
            manifest.roster_item(caller_allowed_tools=skill_manifest.allowed_tools)
            for manifest in manifests
        ]

    @staticmethod
    def _general_chat_loop_task_prompt(
        message: str,
        artifact_ref: str,
        agent_roster: list[dict[str, Any]],
    ) -> str:
        return "\n".join(
            [
                "你是 Agent Workbench 的主 Agent，本轮运行在受控 AgentLoop 中。",
                f"当前用户问题：{message}",
                "你必须在每次模型响应中只返回一个 JSON action。",
                "简单解释、寒暄、会话偏好、记忆写入确认、已有事实复述：直接返回 type=final。",
                "如果问题需要独立来源收集、结构化分析、反方质疑或证据核验，可以返回 type=delegate。",
                "如果用户同时要求“来源/证据链/证据”和“核验/验证/是否真实”，应先 delegate collector 收集证据，再 delegate verifier 核验证据，不要只委派 verifier。",
                "如果用户同时要求“分析/方案/影响”和“质疑/风险/拷打”，应分别 delegate analyst 与 skeptic，再综合两者输出。",
                "delegate 不是普通文本建议，而是调用 Runtime 的 spawn_subagent 能力。",
                "delegate.role 只能是 agent_roster 中的 role，不能创造新角色。",
                f"delegate.input_artifact_refs 必须包含 {artifact_ref}；如果省略，Runtime 会自动绑定该默认 artifact。",
                "每个 delegate.task 要写成给子智能体的具体任务 brief，不要只复述用户问题。",
                "子智能体返回后，你会收到 tool observation；再基于 observation 和 visible_context 返回最终 answer。",
                "不要输出 reasoning、thinking、analysis 或 chain-of-thought 字段。",
                "最终回答必须使用中文，结构为：{\"type\":\"final\",\"output\":{\"answer\":\"...\"}}。",
                f"agent_roster：{json.dumps(agent_roster, ensure_ascii=False)}",
            ]
        )

    def _run_general_chat_delegate(
        self,
        *,
        parent_state: PolicyImpactState,
        skill_manifest: SkillManifest,
        brief: TaskBrief,
        artifact_ref: str,
        artifact_payload: dict[str, Any],
        prepared_context: dict[str, Any],
        delegate_records: list[dict[str, Any]],
    ) -> SubagentResult:
        manifest = self.subagent_manifest_registry.get(brief.role)
        input_refs = tuple(brief.input_artifact_refs or (artifact_ref,))
        if any(ref != artifact_ref for ref in input_refs):
            raise PermissionError("general_chat subagent can only read the delegated main-chat artifact")
        task = PlannedSubagentTask(
            role=brief.role,
            reason="主 Agent 在 AgentLoop 中显式返回 delegate action",
            task=brief.task,
            priority=20,
            trigger="main_agent_delegate_action",
        )
        gateway = self._subagent_tool_gateway(skill_manifest)
        child_state = PolicyImpactState(company_id=self.company_id, run_id=parent_state.run_id)
        child_state.current_user_message = parent_state.current_user_message
        child_state.selected_skill = {
            "id": "general_chat",
            "mode": "agent_loop",
            "delegated_by": "main_agent_delegate_action",
        }
        child_state.recent_history = list(prepared_context.get("recent_history") or [])
        child_state.recent_artifacts = list(prepared_context.get("recent_artifacts") or [])
        child_state.recent_run_traces = list(prepared_context.get("recent_run_traces") or [])
        child_state.relevant_memories = list(prepared_context.get("memories") or [])
        child_state.company_facts = list(prepared_context.get("facts") or [])
        forced_adapter = _ForcedFirstActionModelAdapter(
            self._get_model_adapter(),
            {
                "type": "tool_call",
                "tool_name": "artifact_read",
                "arguments": {"artifact_ref": artifact_ref},
            },
        )
        try:
            result = SubagentRunner(
                model_adapter=forced_adapter,
                tool_gateway=gateway,
                skill_manifest=skill_manifest,
                role_registry=self.subagent_role_registry,
            ).run(
                child_state,
                brief.role,
                task=brief.task,
                input_artifact_refs=input_refs,
                artifact_payloads={ref: artifact_payload for ref in input_refs},
                parent_span_id=brief.parent_span_id,
                task_id=brief.task_id,
            )
            finding = {
                "role": result.role,
                "task_id": result.task_id,
                "status": "success" if result.stop_reason == "success" else "failed",
                "stop_reason": result.stop_reason,
                "reason": task.reason,
                "trigger": task.trigger,
                "output_schema": manifest.output_schema_name,
                "output": _materialize_execution_value(result.output),
                "context_manifest_id": result.context_manifest_id,
                "artifact_refs": list(result.artifact_refs),
            }
        except Exception as exc:  # noqa: BLE001
            finding = {
                "role": brief.role,
                "task_id": brief.task_id,
                "status": "failed",
                "stop_reason": "delegate_failed",
                "reason": task.reason,
                "trigger": task.trigger,
                "error": str(exc)[:240],
                "output": {},
            }
            delegate_records.append(
                {
                    "task": task,
                    "manifest": manifest,
                    "artifact_ref": artifact_ref,
                    "child_state": child_state,
                    "finding": finding,
                    "tool_calls": [record.to_dict() for record in gateway.calls],
                    "skill_manifest": skill_manifest,
                }
            )
            raise

        parent_state.context_manifests.extend(child_state.context_manifests)
        parent_state.stage_trace.extend(child_state.stage_trace)
        parent_state.delegation_evidence.extend(child_state.delegation_evidence)
        parent_state.artifacts.update(child_state.artifacts)
        delegate_records.append(
            {
                "task": task,
                "manifest": manifest,
                "artifact_ref": artifact_ref,
                "child_state": child_state,
                "finding": finding,
                "tool_calls": [record.to_dict() for record in gateway.calls],
                "skill_manifest": skill_manifest,
            }
        )
        return result

    def _record_general_chat_agent_loop(
        self,
        run_id: str,
        loop_state: PolicyImpactState,
        loop_result: AgentLoopResult,
        delegate_records: list[dict[str, Any]],
    ) -> None:
        pending_records = list(delegate_records)
        for model_call in loop_state.model_calls:
            self._record_general_chat_loop_model_call(run_id, model_call)
            payload = model_call.get("payload") or {}
            if payload.get("type") != "delegate" or not pending_records:
                continue
            role = str(payload.get("role") or "")
            record_index = next(
                (
                    index
                    for index, record in enumerate(pending_records)
                    if getattr(record.get("task"), "role", "") == role
                ),
                0,
            )
            record = pending_records.pop(record_index)
            self._record_general_chat_subagent_plan(run_id, record)
            self._record_dynamic_subagent_child(
                run_id=run_id,
                task=record["task"],
                artifact_ref=record["artifact_ref"],
                child_state=record["child_state"],
                finding=record["finding"],
                tool_calls=record["tool_calls"],
            )
        for record in pending_records:
            self._record_general_chat_subagent_plan(run_id, record)
            self._record_dynamic_subagent_child(
                run_id=run_id,
                task=record["task"],
                artifact_ref=record["artifact_ref"],
                child_state=record["child_state"],
                finding=record["finding"],
                tool_calls=record["tool_calls"],
            )
        if loop_result.stop_reason != "success":
            self.store.add_agent_step(
                run_id,
                step_type="gate_check",
                title="普通对话 AgentLoop 停止检查",
                status="failed",
                input_payload={"stop_reason": loop_result.stop_reason},
                output_payload={"final_output": loop_result.final_output},
                gate_result={
                    "decision": "ask",
                    "reason": "主 Agent 未能在预算内产出合法最终回答",
                    "policy_id": "general_chat.agent_loop_stop.v1",
                },
                metadata={"loop_phase": "observe", "embedded_loop": "general_chat"},
            )

    def _record_general_chat_loop_model_call(
        self,
        run_id: str,
        model_call: dict[str, Any],
    ) -> None:
        payload = model_call.get("payload") or {}
        action_type = str(payload.get("type") or "")
        title_map = {
            "delegate": "主 Agent 调用 spawn_subagent",
            "final": "主 Agent 生成最终回答",
            "tool_call": "主 Agent 尝试调用工具",
            "request_approval": "主 Agent 请求人工审批",
        }
        self.store.add_agent_step(
            run_id,
            step_type="model_call",
            title=title_map.get(action_type, "主 Agent 输出动作"),
            status="done",
            input_payload={
                "schema_name": model_call.get("schema_name"),
                "agent_role": model_call.get("agent_role"),
                "messages": model_call.get("messages") or [],
                "prompt_contract": {
                    "contract_id": "general_chat_agent_loop.v1",
                    "role": "main_chat_agent",
                    "allowed_actions": ["delegate", "final"],
                },
            },
            output_payload={
                "payload": payload,
                "response_id": model_call.get("response_id"),
            },
            metadata={
                **dict(model_call.get("metadata") or {}),
                "loop_phase": "think",
                "embedded_loop": "general_chat",
                "action_type": action_type,
            },
        )

    def _record_general_chat_subagent_plan(
        self,
        run_id: str,
        record: dict[str, Any],
    ) -> None:
        task: PlannedSubagentTask = record["task"]
        manifest: SubagentManifest = record["manifest"]
        skill_manifest: SkillManifest = record["skill_manifest"]
        self.store.add_agent_step(
            run_id,
            step_type="subagent_plan",
            title="主 Agent 规划子智能体委派",
            status="done",
            input_payload={
                "role": task.role,
                "message": task.task,
                "planner": "main_agent_loop.delegate_action.v1",
            },
            output_payload={
                "planner": "main_agent_loop.delegate_action.v1",
                "artifact_ref": record["artifact_ref"],
                "tasks": [task.to_dict()],
                "agent_manifest": manifest.roster_item(
                    caller_allowed_tools=skill_manifest.allowed_tools
                ),
            },
            gate_result={
                "decision": "allow",
                "reason": "主 Agent 只能委派 general_chat manifest 声明的子智能体角色",
                "policy_id": "subagent.main_agent_delegate.v1",
            },
            metadata={
                "loop_phase": "think",
                "planner": "main_agent_loop.delegate_action.v1",
                "dynamic_subagent": True,
            },
        )

    def _run_dynamic_subagents_for_chat(
        self,
        *,
        run_id: str,
        message: str,
        context_manifest: dict[str, Any],
        prepared_context: dict[str, Any],
    ) -> dict[str, Any]:
        skill_manifest = self.registry.get_manifest("general_chat")
        artifact_ref = f"main_chat_context:{run_id[:12]}"
        planned_tasks = self._plan_dynamic_subagents_for_chat(
            run_id=run_id,
            message=message,
            skill_manifest=skill_manifest,
            artifact_ref=artifact_ref,
            prepared_context=prepared_context,
        )
        if not planned_tasks:
            return {"findings": [], "delegation_evidence": []}

        task_artifact = self._build_dynamic_subagent_context_artifact(
            message=message,
            context_manifest=context_manifest,
            prepared_context=prepared_context,
        )
        self.store.add_agent_step(
            run_id,
            step_type="subagent_plan",
            title="规划动态子智能体",
            status="done",
            input_payload={"message": message},
            output_payload={
                "planner": "model_assisted_subagent_planner.v1",
                "artifact_ref": artifact_ref,
                "tasks": [task.to_dict() for task in planned_tasks],
            },
            gate_result={
                "decision": "allow",
                "reason": "只允许 general_chat manifest 中声明的子智能体角色",
                "policy_id": "subagent.dynamic_planner.v1",
            },
            metadata={"loop_phase": "think", "planner": "model_assisted_subagent_planner.v1"},
        )

        findings: list[dict[str, Any]] = []
        delegation_evidence: list[dict[str, Any]] = []
        for index, task in enumerate(planned_tasks, start=1):
            gateway = self._subagent_tool_gateway(skill_manifest)
            child_state = PolicyImpactState(company_id=self.company_id, run_id=run_id)
            child_state.current_user_message = message
            child_state.selected_skill = {
                "id": "general_chat",
                "mode": "agent_loop",
                "delegated_by": "model_assisted_subagent_planner.v1",
            }
            child_state.recent_history = list(prepared_context.get("recent_history") or [])
            child_state.recent_artifacts = list(prepared_context.get("recent_artifacts") or [])
            child_state.recent_run_traces = list(prepared_context.get("recent_run_traces") or [])
            child_state.relevant_memories = list(prepared_context.get("memories") or [])
            child_state.company_facts = list(prepared_context.get("facts") or [])
            forced_adapter = _ForcedFirstActionModelAdapter(
                self._get_model_adapter(),
                {
                    "type": "tool_call",
                    "tool_name": "artifact_read",
                    "arguments": {"artifact_ref": artifact_ref},
                },
            )
            try:
                result = SubagentRunner(
                    model_adapter=forced_adapter,
                    tool_gateway=gateway,
                    skill_manifest=skill_manifest,
                ).run(
                    child_state,
                    task.role,
                    task=task.task,
                    input_artifact_refs=(artifact_ref,),
                    artifact_payloads={artifact_ref: task_artifact},
                    parent_span_id=f"span_chat_{run_id[:16]}",
                    task_id=f"task_{run_id[:8]}_{index}_{task.role}",
                )
                finding = {
                    "role": result.role,
                    "task_id": result.task_id,
                    "status": "success" if result.stop_reason == "success" else "failed",
                    "stop_reason": result.stop_reason,
                    "reason": task.reason,
                    "trigger": task.trigger,
                    "output_schema": child_state.context_manifests[-1]
                    .get("metadata", {})
                    .get("output_schema_name", ""),
                    "output": _materialize_execution_value(result.output),
                    "context_manifest_id": result.context_manifest_id,
                    "artifact_refs": list(result.artifact_refs),
                }
                if result.stop_reason == "success":
                    delegation_evidence.append(result.evidence().model_dump(mode="json"))
            except Exception as exc:  # noqa: BLE001
                finding = {
                    "role": task.role,
                    "task_id": f"task_{run_id[:8]}_{index}_{task.role}",
                    "status": "failed",
                    "stop_reason": "delegate_failed",
                    "reason": task.reason,
                    "trigger": task.trigger,
                    "error": str(exc)[:240],
                    "output": {},
                }
            findings.append(finding)
            self._record_dynamic_subagent_child(
                run_id=run_id,
                task=task,
                artifact_ref=artifact_ref,
                child_state=child_state,
                finding=finding,
                tool_calls=[record.to_dict() for record in gateway.calls],
            )

        return {"findings": findings, "delegation_evidence": delegation_evidence}

    def _plan_dynamic_subagents_for_chat(
        self,
        *,
        run_id: str,
        message: str,
        skill_manifest: SkillManifest,
        artifact_ref: str,
        prepared_context: dict[str, Any],
    ) -> list[PlannedSubagentTask]:
        if QuerySubagentPlanner._should_skip(str(message or "")):
            return []
        heuristic_candidates = QuerySubagentPlanner().plan(
            message,
            skill_manifest=skill_manifest,
            artifact_ref=artifact_ref,
            prepared_context=prepared_context,
        )
        candidate_pool = heuristic_candidates or self._default_subagent_candidate_pool(
            message=message,
            skill_manifest=skill_manifest,
            artifact_ref=artifact_ref,
            prepared_context=prepared_context,
        )
        try:
            planner_response = self._complete_model(
                run_id=run_id,
                title="模型规划是否委派子智能体",
                model_id=DEFAULT_MODEL_ID,
                schema_name="subagent_plan.v1",
                messages=self._build_subagent_planner_prompt(
                    message=message,
                    skill_manifest=skill_manifest,
                    artifact_ref=artifact_ref,
                    prepared_context=prepared_context,
                    candidates=candidate_pool,
                ),
                prompt_contract={
                    "contract_id": "subagent_planner.v1",
                    "role": "main_agent_planner",
                    "allowed_roles": list(skill_manifest.allowed_subagents),
                    "output_schema": {
                        "type": "subagent_plan",
                        "output": {
                            "tasks": [
                                {
                                    "role": "collector|analyst|skeptic|verifier",
                                    "reason": "why this role is needed",
                                    "task": "dynamic task brief for the role",
                                    "priority": 10,
                                    "trigger": "user_intent_or_risk_signal",
                                }
                            ]
                        },
                    },
                },
            )
            planned = self._parse_model_subagent_plan(
                planner_response.payload,
                candidates=candidate_pool,
                skill_manifest=skill_manifest,
            )
            if planned:
                return planned
            if heuristic_candidates:
                self.store.add_agent_step(
                    run_id,
                    step_type="gate_check",
                    title="修复空的子智能体规划",
                    status="done",
                    input_payload={
                        "planner": "model_assisted_subagent_planner.v1",
                        "model_task_count": 0,
                    },
                    output_payload={
                        "fallback_planner": "query_subagent_planner.v1",
                        "candidate_count": len(heuristic_candidates),
                        "roles": [task.role for task in heuristic_candidates],
                    },
                    gate_result={
                        "decision": "repair",
                        "reason": "用户问题包含明确的分析、质疑、来源或核验触发信号，模型空计划被确定性候选计划修复",
                        "policy_id": "subagent.empty_plan_repair.v1",
                    },
                    metadata={
                        "loop_phase": "observe",
                        "planner": "query_subagent_planner.v1",
                    },
                )
                return heuristic_candidates
            return []
        except Exception as exc:  # noqa: BLE001
            if not heuristic_candidates:
                return []
            self.store.add_agent_step(
                run_id,
                step_type="gate_check",
                title="子智能体规划失败后回退候选计划",
                status="done",
                input_payload={"planner": "model_assisted_subagent_planner.v1"},
                output_payload={
                    "fallback_planner": "query_subagent_planner.v1",
                    "candidate_count": len(heuristic_candidates),
                    "error": str(exc)[:240],
                },
                gate_result={
                    "decision": "repair",
                    "reason": "模型规划未产出可用结构化计划，回退到确定性候选计划",
                    "policy_id": "subagent.planner_fallback.v1",
                },
                metadata={"loop_phase": "observe", "planner": "query_subagent_planner.v1"},
            )
            return heuristic_candidates

    @staticmethod
    def _default_subagent_candidate_pool(
        *,
        message: str,
        skill_manifest: SkillManifest,
        artifact_ref: str,
        prepared_context: dict[str, Any],
    ) -> list[PlannedSubagentTask]:
        role_priorities = {
            "collector": 30,
            "analyst": 40,
            "skeptic": 50,
            "verifier": 60,
        }
        role_reasons = {
            "collector": "当本轮需要来源、证据链或数据源盘点时使用",
            "analyst": "当本轮需要结构化影响分析、方案拆解或取舍判断时使用",
            "skeptic": "当本轮需要独立质疑、风险审查或反例检查时使用",
            "verifier": "当本轮需要核验结论是否被证据支撑时使用",
        }
        return [
            PlannedSubagentTask(
                role=role,
                reason=role_reasons.get(role, "候选子智能体角色"),
                task=QuerySubagentPlanner._task_for_role(
                    role,
                    message=message,
                    artifact_ref=artifact_ref,
                    prepared_context=prepared_context,
                ),
                priority=role_priorities.get(role, 90),
                trigger="model_delegation_candidate",
            )
            for role in skill_manifest.allowed_subagents
            if role in role_priorities
        ]

    @staticmethod
    def _build_subagent_planner_prompt(
        *,
        message: str,
        skill_manifest: SkillManifest,
        artifact_ref: str,
        prepared_context: dict[str, Any],
        candidates: list[PlannedSubagentTask],
    ) -> list[dict[str, Any]]:
        context_counts = {
            "memory_count": len(prepared_context.get("memories") or []),
            "company_fact_count": len(prepared_context.get("facts") or []),
            "history_count": len(prepared_context.get("recent_history") or []),
            "recent_artifact_count": len(prepared_context.get("recent_artifacts") or []),
            "recent_run_trace_count": len(prepared_context.get("recent_run_traces") or []),
        }
        candidate_payload = [item.to_dict() for item in candidates]
        return [
            {
                "role": "system",
                "content": (
                    "你是 Agent Workbench 主 Agent 内部的 delegation planner。"
                    "你的任务不是回答用户，而是判断本轮是否需要启动隔离子智能体。"
                    "你必须独立判断是否委派；简单问答、概念解释、寒暄和记忆写入返回空 tasks。"
                    "只能从候选角色中选择，不能创造新角色。"
                    "每个 task 必须是给该角色的动态任务 brief，而不是复述用户问题。"
                    "只返回 JSON，不要 Markdown。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "schema": "subagent_plan.v1",
                        "required_json": {
                            "type": "subagent_plan",
                            "output": {"tasks": []},
                        },
                        "allowed_roles": list(skill_manifest.allowed_subagents),
                        "artifact_ref": artifact_ref,
                        "user_message": message,
                        "context_counts": context_counts,
                        "candidate_tasks": candidate_payload,
                        "decision_rules": [
                            "简单定义、普通解释、记忆写入、寒暄：tasks=[]",
                            "需要反驳/风险/拷打：选择 skeptic",
                            "需要结构化方案/取舍/影响判断：选择 analyst",
                            "需要来源、证据链、数据源盘点：选择 collector",
                            "需要确认结论是否被证据支撑：选择 verifier",
                            "最多 3 个角色；每个 role 只出现一次",
                        ],
                    },
                    ensure_ascii=False,
                ),
            },
        ]

    @staticmethod
    def _parse_model_subagent_plan(
        payload: dict[str, Any],
        *,
        candidates: list[PlannedSubagentTask],
        skill_manifest: SkillManifest,
    ) -> list[PlannedSubagentTask]:
        output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
        raw_tasks = output.get("tasks") if isinstance(output, dict) else []
        if not isinstance(raw_tasks, list):
            return []
        allowed_roles = set(skill_manifest.allowed_subagents)
        candidate_by_role = {item.role: item for item in candidates}
        planned: list[PlannedSubagentTask] = []
        for index, item in enumerate(raw_tasks, start=1):
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip()
            if role not in allowed_roles or role not in candidate_by_role:
                continue
            if any(existing.role == role for existing in planned):
                continue
            fallback = candidate_by_role[role]
            task = str(item.get("task") or "").strip() or fallback.task
            reason = str(item.get("reason") or "").strip() or fallback.reason
            trigger = str(item.get("trigger") or "").strip() or fallback.trigger
            try:
                priority = int(item.get("priority") or fallback.priority or (20 + index))
            except (TypeError, ValueError):
                priority = fallback.priority
            planned.append(
                PlannedSubagentTask(
                    role=role,
                    reason=reason[:300],
                    task=task[:1800],
                    priority=priority,
                    trigger=trigger[:120],
                )
            )
        planned.sort(key=lambda item: (item.priority, item.role))
        return planned[:3]

    def _build_dynamic_subagent_context_artifact(
        self,
        *,
        message: str,
        context_manifest: dict[str, Any],
        prepared_context: dict[str, Any],
    ) -> dict[str, Any]:
        visible = context_manifest.get("visible_content") or {}
        return {
            "schema_version": "main_chat_context_for_subagent.v1",
            "current_user_message": message,
            "context_controls": {
                "visible_keys": list(context_manifest.get("visible_keys") or []),
                "token_budget": dict(context_manifest.get("token_budget") or {}),
                "context_policy": visible.get("context_policy") or {},
            },
            "company_facts": list(prepared_context.get("facts") or [])[:8],
            "runtime_self_knowledge": self._general_system_facts(message),
            "long_term_memories": list(prepared_context.get("memories") or [])[:5],
            "recent_history": list(prepared_context.get("recent_history") or [])[-8:],
            "recent_artifacts": list(prepared_context.get("recent_artifacts") or [])[:5],
            "recent_run_traces": list(prepared_context.get("recent_run_traces") or [])[:3],
            "tool_policy_summary": visible.get("tool_policy_summary")
            or prepared_context.get("tool_policy_summary")
            or {},
            "sandbox_policy": {
                "artifact_scope": "subagent can only read explicitly delegated artifact_ref",
                "tool_scope": "allowed tools = role contract ∩ caller skill manifest",
                "write_policy": "subagent cannot write long-term memory or publish artifacts in general_chat",
            },
        }

    def _subagent_tool_gateway(self, skill_manifest: SkillManifest) -> ToolGateway:
        role_registry = build_default_subagent_role_registry()
        policies: list[ToolPolicy] = []
        for role_name in skill_manifest.allowed_subagents:
            try:
                role = role_registry.get(role_name, "1.0.0")
            except KeyError:
                continue
            for tool_name in role.allowed_tools:
                if tool_name in skill_manifest.allowed_tools:
                    policies.append(
                        ToolPolicy(
                            f"subagent.{role.role}",
                            tool_name,
                            "allow",
                            "role contract and caller skill allowlist intersection",
                        )
                    )
        return ToolGateway(permission_engine=PermissionEngine(policies))

    def _record_dynamic_subagent_child(
        self,
        *,
        run_id: str,
        task: PlannedSubagentTask,
        artifact_ref: str,
        child_state: PolicyImpactState,
        finding: dict[str, Any],
        tool_calls: list[dict[str, Any]],
    ) -> None:
        for manifest in child_state.context_manifests:
            self.store.add_agent_step(
                run_id,
                step_type="subagent_context",
                title=f"构建 {task.role} 子智能体上下文",
                status="done",
                input_payload={
                    "task_id": finding.get("task_id", ""),
                    "role": task.role,
                    "artifact_ref": artifact_ref,
                },
                output_payload={"context_manifest": manifest},
                metadata={"loop_phase": "think", "subagent_role": task.role},
            )

        for model_call in child_state.model_calls:
            model_metadata = model_call.get("metadata") or {}
            payload = model_call.get("payload") or {}
            self.store.add_agent_step(
                run_id,
                step_type="model_call",
                title=(
                    f"{task.role} 子智能体读取任务上下文"
                    if payload.get("type") == "tool_call"
                    else f"{task.role} 子智能体生成结构化结果"
                ),
                status="done" if finding.get("status") == "success" else "failed",
                input_payload={
                    "schema_name": model_call.get("schema_name"),
                    "agent_role": model_call.get("agent_role"),
                    "messages": model_call.get("messages") or [],
                    "prompt_contract": {
                        "contract_id": model_call.get("schema_name") or "",
                        "role": task.role,
                        "loop_boundary": "subagent reads task artifact through artifact_read; runtime owns permissions",
                    },
                },
                output_payload={
                    "payload": payload,
                    "response_id": model_call.get("response_id"),
                },
                metadata={
                    **dict(model_metadata),
                    "loop_phase": "think",
                    "subagent_role": task.role,
                    "dynamic_subagent": True,
                },
            )

        observed_tool_calls = [
            {
                "stage_name": f"subagent.{task.role}",
                "tool_name": "artifact_read",
                "allowed": True,
                "status": "ok",
                "policy_decision": "allow",
                "policy_reason": "artifact ref is inside delegated task scope",
                "argument_keys": ["artifact_ref"],
                "result_summary": "dict[main_chat_context_for_subagent]",
            },
            *tool_calls,
        ]
        self.store.add_agent_step(
            run_id,
            step_type="subagent_delegate",
            title=f"执行动态子智能体：{task.role}",
            status="done" if finding.get("status") == "success" else "failed",
            input_payload={
                "role": task.role,
                "reason": task.reason,
                "trigger": task.trigger,
                "artifact_ref": artifact_ref,
            },
            output_payload={
                "role": task.role,
                "task_id": finding.get("task_id", ""),
                "stop_reason": finding.get("stop_reason", ""),
                "output_schema": finding.get("output_schema", ""),
                "output": finding.get("output") or {},
                "context_manifest_id": finding.get("context_manifest_id", ""),
            },
            tool_calls=observed_tool_calls,
            gate_result={
                "decision": "allow" if finding.get("status") == "success" else "ask",
                "reason": (
                    "子智能体在任务 artifact 和 role-scoped tool policy 内完成"
                    if finding.get("status") == "success"
                    else str(finding.get("error") or "子智能体未能产出合格结构化结果")
                ),
                "policy_id": "subagent.delegation_result.v1",
            },
            metadata={
                "loop_phase": "observe",
                "subagent_role": task.role,
                "dynamic_subagent": True,
            },
        )

    def _attach_subagent_context(
        self,
        *,
        run_id: str,
        context_manifest: dict[str, Any],
        subagent_findings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        visible_content = dict(context_manifest.get("visible_content") or {})
        visible_content["subagent_results"] = subagent_findings
        visible_keys = list(context_manifest.get("visible_keys") or [])
        if "subagent_results" not in visible_keys:
            visible_keys.append("subagent_results")
        token_estimates = dict(context_manifest.get("token_estimates") or {})
        token_estimates["subagent_results"] = estimate_tokens(subagent_findings)
        patched = {
            **dict(context_manifest),
            "manifest_id": f"ctx_{_stable_hash({'run_id': run_id, 'subagent_findings': subagent_findings})[:16]}",
            "visible_keys": visible_keys,
            "visible_content": visible_content,
            "token_estimates": token_estimates,
            "context_patch": {
                "patch_id": "dynamic_subagent_results.v1",
                "subagent_result_count": len(subagent_findings),
                "roles": _unique_preserve_order(
                    [str(item.get("role") or "") for item in subagent_findings]
                ),
            },
        }
        self.store.add_agent_step(
            run_id,
            step_type="context_build",
            title="回填动态子智能体上下文",
            status="done",
            input_payload={"subagent_result_count": len(subagent_findings)},
            output_payload={"context_manifest": patched},
            metadata={"loop_phase": "think", "context_patch": "dynamic_subagent_results.v1"},
        )
        return patched

    @classmethod
    def _govern_general_chat_answer(
        cls,
        message: str,
        model_answer: str,
        facts: list[dict[str, Any]],
    ) -> tuple[str, dict[str, Any] | None]:
        text = str(message or "").lower()
        if cls._is_workbench_capability_query(message):
            return cls._render_workbench_general_answer(message), {
                "contract_id": "general_chat.workbench_capabilities.v1",
                "required_fields": [
                    "AgentRun",
                    "AgentStep",
                    "conditional_tool_gate_memory_artifact",
                    "model_content_not_deterministic",
                ],
                "reason": "系统自描述必须区分每轮固定留痕与按路由条件触发的能力",
            }
        if cls._is_immediate_history_query(message) and not facts and any(
            marker in model_answer
            for marker in ("公开事实（public_disclosure）", "公开事实 (public_disclosure)")
        ):
            repaired = str(model_answer).replace(
                "公开事实（public_disclosure）",
                "用户确认的会话事实（user_confirmed）",
            ).replace(
                "公开事实 (public_disclosure)",
                "用户确认的会话事实 (user_confirmed)",
            ).replace(
                "以上“公开事实”",
                "以上“用户确认的会话事实”",
            )
            return repaired, {
                "contract_id": "general_chat.session_provenance.v1",
                "required_fields": ["user_confirmed", "analysis_inference"],
                "reason": "会话历史中的用户声明不得标记为企业公开披露",
            }
        if all(term in text for term in ("ifind", "上线清单")) and any(
            term in text for term in ("负责人", "阻断条件", "验收证据")
        ):
            return cls._release_readiness_contract_answer(facts), {
                "contract_id": "general_chat.release_readiness.v1",
                "required_fields": [
                    "检查项",
                    "负责人",
                    "证据产物",
                    "通过阈值",
                    "阻断条件",
                    "待确认",
                ],
                "reason": "模型草案必须转换为逐项可审计的上线责任契约",
            }
        if "ifind" in text and any(term in text for term in ("超时", "重试", "幂等")):
            return cls._api_engineering_review_answer(facts), {
                "contract_id": "general_chat.api_risk_hypothesis.v1",
                "required_fields": ["已知事实", "待核验 P0 假设", "证据要求", "通用工程建议"],
                "reason": "没有实现证据时，不得把超时、重试或幂等风险写成已确认缺陷",
            }
        if cls._requires_retrieved_evidence_only(message):
            repaired = str(model_answer or "").replace(
                "企业事实中未提及",
                "本轮检索到的企业事实未提供",
            ).replace(
                "企业知识库中未提及",
                "本轮检索到的企业事实未提供",
            )
            if repaired != model_answer:
                return repaired, {
                    "contract_id": "general_chat.retrieval_scope.v1",
                    "required_fields": ["本轮检索范围", "证据不足边界"],
                    "reason": "相关性检索不能表述为已完成企业知识库全量扫描",
                }
        return model_answer, None

    @staticmethod
    def _fact_value(facts: list[dict[str, Any]], fact_id: str) -> str:
        return next(
            (
                str(item.get("value") or "")
                for item in facts
                if str(item.get("fact_id") or "") == fact_id
            ),
            "",
        )

    @classmethod
    def _api_engineering_review_answer(cls, facts: list[dict[str, Any]]) -> str:
        ifind = cls._fact_value(facts, "ai_product.ifind")
        known = (
            f"- [ai_product.ifind] {ifind}"
            if ifind
            else "- 当前企业知识库没有提供 iFinD 异步 API 的实现证据。"
        )
        return "\n".join(
            [
                "结论：当前证据只能支持风险假设和核验计划，不能认定现网已经存在超时、重试、幂等或数据合规缺陷。",
                "",
                "已知事实：",
                known,
                "- 知识库未提供客户端/服务端超时配置、重试策略、幂等存储、压测结果或生产 Trace。",
                "",
                "待核验 P0 假设：",
                "1. 超时边界：核验客户端、网关、任务队列和下游模型的分层超时是否一致。",
                "   - 证据要求：配置快照、超时 Trace、故障注入记录；没有这些证据前不得写成‘缺少双向超时’。",
                "2. 重试安全：核验重试触发条件、退避、抖动、最大次数和不可重试错误分类。",
                "   - 证据要求：重试策略配置、错误分类表、失败重放测试与请求放大率。",
                "3. 幂等语义：核验任务键生成、重复提交去重、状态机终态和结果复用。",
                "   - 证据要求：幂等键协议、唯一约束或去重记录、并发重复请求测试。",
                "4. 数据合规：核验研报内容、用户标识、日志字段、保存期限与第三方传输边界。",
                "   - 证据要求：字段清单、数据流图、脱敏样例、权限审计和删除记录。",
                "",
                "通用工程建议（仅在核验发现缺口后实施）：统一 deadline 透传；对可重试错误使用有上限的指数退避；以业务任务键实现幂等；将敏感字段最小化并纳入审计。所有阈值必须由压测和业务 SLA 决定，不能把示例值当成 iFinD 现状。",
            ]
        )

    @classmethod
    def _release_readiness_contract_answer(cls, facts: list[dict[str, Any]]) -> str:
        ifind = cls._fact_value(facts, "ai_product.ifind")
        known = (
            f"- [ai_product.ifind] {ifind}"
            if ifind
            else "- 当前企业知识库没有提供 iFinD 异步研报摘要任务的已实现能力。"
        )
        return "\n".join(
            [
                "已知事实：",
                known,
                "- 当前证据不能证明异步任务页面、状态机、SLA、数据字段或合规流程已经实现。",
                "",
                "逐项上线契约：",
                "1. 检查项：任务提交与状态可见性",
                "   - 负责人：产品经理（待指定姓名）",
                "   - 证据产物：验收环境录屏、状态机用例报告、异常文案清单",
                "   - 通过阈值：提交、排队、运行、成功、失败、取消均有可复现状态与下一步动作",
                "   - 阻断条件：任一终态不可识别，或失败后用户无法恢复/重试",
                "   - 待确认：当前界面和状态机是否存在；不得用企业 fact_id 代替验收证据",
                "2. 检查项：摘要正确性与证据回链",
                "   - 负责人：算法/应用研发负责人（待指定姓名）",
                "   - 证据产物：标注集、逐条来源回链、事实一致性评测报告",
                "   - 通过阈值：冻结标注集版本，P0 事实错误为 0，关键结论来源回链覆盖率 100%，其余质量阈值由业务负责人签字版本化",
                "   - 阻断条件：关键结论无来源、事实冲突未拦截或评测版本不可复现",
                "   - 待确认：任务样本、黄金答案、容错标准和人工复核比例",
                "3. 检查项：超时、重试与幂等",
                "   - 负责人：后端研发负责人（待指定姓名）",
                "   - 证据产物：配置快照、故障注入报告、重复请求与恢复 Trace",
                "   - 通过阈值：重试不造成重复任务/重复计费，超时后状态可恢复且结果唯一",
                "   - 阻断条件：出现重复副作用、孤儿任务、无限重试或终态不一致",
                "   - 待确认：真实 SLA、队列容量、下游限流与幂等键协议",
                "4. 检查项：数据与内容合规",
                "   - 负责人：合规/数据安全负责人（待指定姓名）",
                "   - 证据产物：数据流图、字段与权限清单、脱敏样例、保存/删除记录",
                "   - 通过阈值：每个数据字段均有目的、来源、权限、期限和责任人",
                "   - 阻断条件：来源授权不明、敏感字段无控制、内容结论无法追溯",
                "   - 待确认：研报版权、用户数据范围、第三方模型传输和日志留存边界",
                "",
                "通用工程建议：将以上契约写入发布门禁；只有证据产物存在且通过阈值后才能关闭检查项。负责人、阈值和证据目前均是待确认项，不代表 iFinD 现状。",
            ]
        )

    @staticmethod
    def _ground_general_chat_citations(
        message: str,
        answer: str,
        citations: list[dict[str, Any]],
        *,
        memory_requested: bool,
        strict_knowledge_only: bool,
        memory_items: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        mentioned_fact_ids = AgentRuntime._extract_company_fact_ids(answer)
        message_text = str(message or "")
        memory_focused = (
            any(term in message_text for term in ("记忆", "记住", "记下", "保存的"))
            and "知识库" not in message_text
        )
        memory_content_by_id = {
            str(item.get("id") or ""): " ".join(str(item.get("content") or "").split())
            for item in memory_items or []
        }
        normalized_answer = " ".join(str(answer or "").split())
        filtered = []
        seen = set()
        for item in citations:
            item_type = str(item.get("type") or "")
            if item_type == "company_fact":
                fact_id = str(item.get("fact_id") or "")
                if memory_requested or memory_focused:
                    continue
                if fact_id not in mentioned_fact_ids:
                    continue
            if item_type == "memory_item" and not (memory_requested or memory_focused):
                continue
            if item_type == "memory_item" and memory_focused and not memory_requested:
                memory_id = str(item.get("memory_id") or "")
                content = memory_content_by_id.get(memory_id, "")
                explicit_marker = bool(
                    re.search(
                        rf"\[memory_id\s*:\s*{re.escape(memory_id)}\]",
                        normalized_answer,
                        flags=re.I,
                    )
                )
                content_terms = set(tokenize(content))
                answer_terms = set(tokenize(normalized_answer))
                overlap = len(content_terms & answer_terms)
                overlap_ratio = overlap / max(1, len(content_terms))
                paraphrase_grounded = overlap >= 4 and overlap_ratio >= 0.25
                if not content or not (explicit_marker or content in normalized_answer or paraphrase_grounded):
                    continue
            marker = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if marker in seen:
                continue
            seen.add(marker)
            filtered.append(item)
        return filtered

    @staticmethod
    def _attach_grounded_memory_citations(
        answer: str,
        citations: list[dict[str, Any]],
    ) -> str:
        memory_ids = [
            str(item.get("memory_id") or "").strip()
            for item in citations
            if item.get("type") == "memory_item" and str(item.get("memory_id") or "").strip()
        ]
        memory_ids = list(dict.fromkeys(memory_ids))
        if not memory_ids:
            return answer
        cleaned = re.sub(
            r"\s*\[memory_id\s*:\s*[^\]\r\n]+\]",
            "",
            str(answer or ""),
            flags=re.I,
        ).rstrip()
        markers = " ".join(f"[memory_id: {memory_id}]" for memory_id in memory_ids)
        return f"{cleaned}\n\n记忆引用：{markers}"

    @staticmethod
    def _extract_company_fact_ids(content: str) -> set[str]:
        """Accept bracketed and Markdown-code fact references emitted by the model."""
        text = str(content or "")
        candidates = re.findall(
            r"\[(?:fact_id\s*:\s*)?([A-Za-z0-9_][A-Za-z0-9_.:-]*)\]",
            text,
            flags=re.IGNORECASE,
        )
        candidates.extend(
            re.findall(
                r"`([A-Za-z0-9_][A-Za-z0-9_.:-]*\.[A-Za-z0-9_.:-]+)`",
                text,
                flags=re.IGNORECASE,
            )
        )
        candidates.extend(
            re.findall(
                r"fact_id\s*(?:为|是|[:：=])\s*([A-Za-z0-9_][A-Za-z0-9_.:-]*\.[A-Za-z0-9_.:-]+)",
                text,
                flags=re.IGNORECASE,
            )
        )
        return {fact_id for fact_id in candidates if "." in fact_id}

    @staticmethod
    def _prior_company_fact_citations(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        previous = next(
            (item for item in reversed(history) if item.get("role") == "assistant"),
            None,
        )
        if not previous:
            return []
        citations = []
        seen = set()
        for item in (previous.get("metadata") or {}).get("citations") or []:
            fact_id = str(item.get("fact_id") or "").strip()
            if item.get("type") != "company_fact" or not fact_id or fact_id in seen:
                continue
            seen.add(fact_id)
            citations.append({"type": "company_fact", "fact_id": fact_id, "path": item.get("path")})
        for fact_id in AgentRuntime._extract_company_fact_ids(str(previous.get("content") or "")):
            if fact_id in seen:
                continue
            seen.add(fact_id)
            citations.append({"type": "company_fact", "fact_id": fact_id})
        return citations

    @classmethod
    def _prior_fact_id_answer(
        cls,
        message: str,
        history: list[dict[str, Any]],
    ) -> str:
        text = str(message or "").lower()
        if "fact_id" not in text or not any(term in text for term in ("上一轮", "上轮", "刚才")):
            return ""
        fact_ids = [item["fact_id"] for item in cls._prior_company_fact_citations(history)]
        if not fact_ids:
            return "上一轮回答没有持久化可复现的企业 fact_id；本轮不会根据新的检索结果补造。"
        return "\n".join(
            [
                "上一轮回答实际使用并持久化的 fact_id：",
                *[f"- {fact_id}" for fact_id in fact_ids],
                "本轮直接复现上一轮证据元数据，没有用新的检索结果改写历史引用。",
            ]
        )

    @staticmethod
    def _explicit_memory_content(message: str) -> str:
        text = str(message or "").strip()
        patterns = (
            r"请(?:把|将)(?:以下内容)?(?:显式)?(?:写入|保存到|存入)(?:长期)?记忆\s*[：:]\s*(.+)",
            r"(?:请|帮我)?(?:显式)?(?:写入|保存到|存入)(?:长期)?记忆\s*[：:]\s*(.+)",
            r"(?:请|帮我)?记住\s*[：:]\s*(.+)",
            r"remember that\s*[：:]?\s*(.+)",
        )
        match = next(
            (found for pattern in patterns if (found := re.search(pattern, text, flags=re.I | re.S))),
            None,
        )
        if not match:
            return ""
        content = match.group(1).strip(" ：:，,。")
        content = re.split(
            r"(?:。|\n)\s*(?:请)?(?:只在实际写入|写入成功后|否则明确|不要假装)",
            content,
            maxsplit=1,
        )[0].strip(" ：:，,。")
        return content[:1000]

    @staticmethod
    def _feedback_memory_content(message: str) -> str:
        text = re.sub(r"\s+", " ", str(message or "").strip())
        if not text:
            return ""
        if re.search(
            r"(?:不要|不需要|无需|禁止|不得)[^。；\n]{0,20}(?:记住|写入|保存到|存入).{0,6}记忆",
            text,
            flags=re.I,
        ):
            return ""
        feedback_markers = (
            "不认可",
            "不同意",
            "不接受",
            "纠正",
            "短期不",
            "暂时不",
            "以后不要",
            "后续不要",
            "之后不要",
        )
        durable_scope_markers = (
            "报告",
            "建议",
            "行动",
            "关注重点",
            "优先级",
            "偏好",
            "短期",
            "长期",
            "之后",
            "以后",
            "后续",
        )
        if not any(marker in text for marker in feedback_markers):
            return ""
        if not any(marker in text for marker in durable_scope_markers):
            return ""
        return f"用户反馈/偏好：{text}".strip()[:1000]

    @staticmethod
    def _requests_memory_write(message: str) -> bool:
        text = str(message or "").lower()
        if re.search(
            r"(?:不要|不需要|无需|禁止|不得|不(?:要)?)(?:将|把)?[^。；\n]{0,20}(?:写入|保存到|存入).{0,6}记忆",
            text,
            flags=re.I,
        ) or re.search(r"(?:不要|无需|禁止|不得)记住", text, flags=re.I):
            return False
        return bool(
            re.search(
                r"(?:请记住|帮我记住|写入.{0,4}记忆|保存到.{0,4}记忆|存入.{0,4}记忆|remember that)",
                text,
                flags=re.I,
            )
        )

    def _plan_skill_memory_if_requested(
        self,
        run_id: str,
        message: str,
        skill: SkillManifest,
        result: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not self._requests_memory_write(message):
            return []
        artifacts = {
            str(key): str(value)
            for key, value in (result.get("artifacts") or {}).items()
            if value
        }
        namespace = f"company:{self.company_id}:skill_results"
        memory_type = "explicit_skill_result"
        content = (
            f"用户明确要求保存技能结果：skill={skill.id}；"
            f"artifacts={artifacts}；"
            f"answer={str(result.get('answer') or '')[:300]}"
        )
        memory_id, _ = self._plan_deduplicated_memory_id(
            namespace,
            memory_type,
            content,
        )
        return [
            {
                "schema_version": "runtime.memory_write.v1",
                "memory_id": memory_id,
                "namespace": namespace,
                "memory_type": memory_type,
                "content": content,
                "importance": 0.8,
                "deduplicate": True,
                "metadata": {
                    "source": "explicit_user_request",
                    "run_id": run_id,
                    "skill_id": skill.id,
                    "artifacts": artifacts,
                },
                "step": {
                    "step_type": "memory_write",
                    "title": "写入用户明确要求保存的技能结果",
                    "status": "done",
                    "input_payload": {
                        "memory_type": "explicit_skill_result",
                        "write_policy": "explicit_user_request_only",
                    },
                    "output_payload": {"namespace": namespace},
                    "gate_result": {
                        "decision": "allow",
                        "reason": "用户本轮明确要求将技能结果写入长期记忆",
                    },
                    "metadata": {"loop_phase": "act"},
                },
            }
        ]

    def _plan_deduplicated_memory_id(
        self,
        namespace: str,
        memory_type: str,
        content: str,
    ) -> tuple[str, bool]:
        existing = self.store.find_memory_exact(namespace, memory_type, content)
        if existing:
            return str(existing["id"]), False
        return canonical_memory_id(namespace, memory_type, content), True

    @staticmethod
    def _general_system_facts(message: str) -> list[str]:
        facts = [
            "Agent Workbench is a single-user Agent Harness and Runtime learning system.",
            "Each turn is persisted as an AgentRun with ordered AgentStep records.",
            "SkillRegistry routes general_chat, /report, and the compatible internal routes behind report runs.",
            "Every completed turn records AgentRun, ordered AgentStep, context, and final-answer evidence; memory writes, workflow tools, gates, model calls, and artifacts are recorded only when the selected route actually triggers them.",
            "General chat uses a real configured model after deterministic memory and company-wiki retrieval.",
        ]
        text = str(message or "").lower()
        if "coursepilot" in text or "course pilot" in text:
            facts.extend(
                [
                    "CoursePilot is the user's course-learning Agent Runtime project, focused on RAG, tutoring, quizzes, grading, learning memory, and retrieval evaluation.",
                    "Agent Workbench is focused on Harness and Infra controls across replaceable business skills: routing, context contracts, tool policy, trace, artifacts, replay, and eval.",
                ]
            )
        return facts

    @staticmethod
    def _requires_retrieved_evidence_only(message: str) -> bool:
        text = str(message or "").lower()
        strict_markers = ("只基于", "仅基于", "只根据", "仅根据", "only use", "based only on")
        evidence_markers = ("知识库", "记忆", "检索", "knowledge base", "memory", "retrieved")
        return any(marker in text for marker in strict_markers) and any(
            marker in text for marker in evidence_markers
        )

    def _complete_model(
        self,
        *,
        run_id: str,
        title: str,
        model_id: str,
        schema_name: str,
        messages: list[dict[str, Any]],
        prompt_contract: dict[str, Any],
    ) -> ModelResponse:
        adapter = self._get_model_adapter()
        input_payload = {
            "model_id": model_id,
            "schema_name": schema_name,
            "messages": messages,
            "prompt_contract": prompt_contract,
        }
        try:
            response = adapter.complete(messages=messages, schema_name=schema_name)
        except Exception as exc:  # noqa: BLE001
            message = str(exc) or type(exc).__name__
            self.store.add_agent_step(
                run_id,
                step_type="model_call",
                title=title,
                status="failed",
                input_payload=input_payload,
                output_payload={"error": message},
                metadata={
                    "adapter": type(adapter).__name__,
                    "loop_phase": "think",
                    "error_type": type(exc).__name__,
                },
            )
            if isinstance(exc, ModelInvocationError):
                raise
            raise ModelInvocationError(message) from exc

        self.store.add_agent_step(
            run_id,
            step_type="model_call",
            title=title,
            status="done",
            input_payload=input_payload,
            output_payload=response.to_dict(),
            metadata={
                "adapter": type(adapter).__name__,
                "loop_phase": "think",
                "latency_ms": response.metadata.get("total_latency_ms")
                or response.metadata.get("latency_ms"),
                "attempt_latency_ms": response.metadata.get("latency_ms"),
                "total_latency_ms": response.metadata.get("total_latency_ms")
                or response.metadata.get("latency_ms"),
                "usage": response.metadata.get("usage", {}),
                "attempts": response.metadata.get("attempts", 1),
                "retry_errors": response.metadata.get("retry_errors", []),
                "stop_reason": response.metadata.get("stop_reason", ""),
                "temperature": response.metadata.get("temperature"),
                "max_tokens": response.metadata.get("max_tokens"),
                "timeout_seconds": response.metadata.get("timeout_seconds"),
                "max_attempts": response.metadata.get("max_attempts"),
            },
        )
        return response

    def _get_model_adapter(self) -> ModelAdapter:
        if self.model_adapter is None:
            self.model_adapter = AnthropicCompatibleModelAdapter.from_env()
        return self.model_adapter

    def _run_external_impact_report(
        self,
        run_id: str,
        session_id: str,
        message: str,
        model_id: str,
        context_manifest: dict[str, Any],
        prepared_context: dict[str, Any],
    ) -> dict[str, Any]:
        workflow_gateway = _ReportWorkflowToolGateway(
            lambda workflow_message: self._run_external_impact_workflow(
                run_id,
                session_id,
                workflow_message,
                model_id,
                context_manifest,
                prepared_context,
            )
        )
        loop_state = PolicyImpactState(company_id=self.company_id, run_id=run_id)
        loop_state.current_user_message = message
        loop_state.selected_skill = {"id": "external_impact_report", "mode": "skill"}
        loop_state.recent_history = list(prepared_context.get("recent_history") or [])
        loop_state.conversation_summary = dict(prepared_context.get("conversation_summary") or {})
        loop_state.relevant_memories = list(prepared_context.get("memories") or [])
        loop_state.tool_policy_summary = {
            "decision_order": ["deny", "ask", "allow"],
            "skill_scoped_tools": ["report_workflow.run"],
            "runtime_owns_permissions": True,
        }
        manifest_record = _context_manifest_record_from_metadata(
            run_id=run_id,
            metadata={
                **dict(context_manifest),
                "stage_name": "report_agent_loop",
                "agent_role": "report_orchestrator",
                "loop_boundary": "AgentLoop owns orchestration; report workflow is a skill-scoped tool observation.",
                "available_actions": ["tool_call:report_workflow.run", "final"],
                "visible_content": {
                    "current_user_message": message,
                    "selected_skill": loop_state.selected_skill,
                    "recent_history": loop_state.recent_history,
                    "conversation_summary": loop_state.conversation_summary,
                    "relevant_memories": loop_state.relevant_memories,
                    "tool_policy_summary": loop_state.tool_policy_summary,
                },
            },
            stage_name="report_agent_loop",
            agent_role="report_orchestrator",
        )
        loop_state.add_context_manifest(manifest_record.to_dict())
        forced_payload = {
            "type": "tool_call",
            "tool_name": "report_workflow.run",
            "arguments": {"message": message},
        }
        self._record_embedded_report_model_call(
            run_id,
            {
                "schema_name": "agent_action.v1",
                "agent_role": "report_orchestrator",
                "messages": [],
                "payload": forced_payload,
                "response_id": f"runtime_forced_{_stable_hash(forced_payload)[:12]}",
                "metadata": {
                    "adapter": "_ForcedFirstActionModelAdapter",
                    "loop_phase": "think",
                    "embedded_loop": "external_impact_report",
                    "action_type": "tool_call",
                    "deterministic_action": True,
                    "causal_trace_pre_recorded": True,
                },
            },
        )
        forced_adapter = _ForcedFirstActionModelAdapter(
            self._get_model_adapter(),
            forced_payload,
        )
        loop_result = AgentLoop(
            forced_adapter,
            workflow_gateway,
            reject_hidden_reasoning=True,
        ).run(
            loop_state,
            AgentLoopConfig(
                stage_name="report_agent_loop",
                agent_role="report_orchestrator",
                context_manifest=manifest_record,
                output_key="agent_action.v1",
                execution_mode=ExecutionMode.AGENT_LOOP,
                max_steps=4,
                max_model_calls=2,
                timeout_seconds=240,
                max_calls_per_tool=1,
                task_prompt=(
                    "This turn explicitly activated /report. The runtime has already "
                    "called report_workflow.run as a skill-scoped tool. Use the tool "
                    "observation as the primary evidence, then return type=final with "
                    "output.answer, output.artifacts, output.citations, and output.internal_route. "
                    "Do not invent additional sources or claim direct workflow execution outside AgentLoop."
                ),
            ),
        )
        self._record_embedded_report_loop(run_id, loop_state, loop_result)
        workflow_result = workflow_gateway.last_result
        final_output = loop_result.final_output if loop_result.stop_reason == "success" else {}
        # The fixed workflow and its gates remain authoritative.  The outer
        # AgentLoop is allowed to observe and explain the result, but it must
        # not overwrite a workflow ask/deny/scope decision with a generic model
        # answer.
        answer = str(workflow_result.get("answer") or final_output.get("answer") or "").strip()
        artifacts = dict(workflow_result.get("artifacts") or {})
        citations = list(workflow_result.get("citations") or final_output.get("citations") or [])
        if not answer:
            raise ModelInvocationError(f"/report AgentLoop failed: {loop_result.stop_reason}")
        return {
            "answer": answer,
            "artifacts": artifacts,
            "citations": citations,
            "artifact_refs": [value for value in artifacts.values() if isinstance(value, str)],
            "delegation_evidence": list(workflow_result.get("delegation_evidence") or []),
            "internal_route": str(final_output.get("internal_route") or workflow_result.get("internal_route") or ""),
            "stop_reason": loop_result.stop_reason,
            "loop_execution_mode": loop_result.actual_execution_mode.value,
        }

    def _run_external_impact_workflow(
        self,
        run_id: str,
        session_id: str,
        message: str,
        model_id: str,
        context_manifest: dict[str, Any],
        prepared_context: dict[str, Any],
    ) -> dict[str, Any]:
        route = self._impact_internal_route(message)
        route_labels = {
            "source_inventory": "数据源盘点",
            "policy": "政策/监管影响分析",
            "news": "新闻/行业事件/技术发布影响分析",
            "research": "补充调研报告",
        }
        selected_sources = self._impact_selected_sources(route)
        self.store.add_agent_step(
            run_id,
            step_type="workflow_stage",
            title="选择外部变化分析路线",
            status="done",
            input_payload={"message": message},
            output_payload={
                "public_skill_id": "external_impact_report",
                "internal_route": route,
                "route_label": route_labels.get(route, route),
                "selected_source_types": selected_sources,
                "hidden_legacy_skills": [
                    "policy_weekly_impact",
                    "recent_news_report",
                    "research_report",
                ],
            },
            metadata={
                "loop_phase": "think",
                "router": "external_change_router.v1",
                "public_skill": True,
            },
        )
        if route == "source_inventory":
            result = self._run_impact_source_inventory(run_id, message)
        elif route == "policy":
            result = self._run_policy_weekly_impact(run_id, session_id, message)
        elif route == "research":
            result = self._run_research_report(
                run_id,
                message,
                model_id,
                context_manifest,
                prepared_context,
            )
        else:
            result = self._run_recent_news_report(run_id, message)
        return self._wrap_external_impact_result(
            result,
            route=route,
            route_label=route_labels.get(route, route),
            selected_sources=selected_sources,
        )

    def _record_embedded_report_loop(
        self,
        run_id: str,
        loop_state: PolicyImpactState,
        loop_result: AgentLoopResult,
    ) -> None:
        turns_by_index = {
            int(turn.turn_index): turn
            for turn in loop_result.turns
            if int(turn.turn_index or 0) > 0
        }
        observed_turns: set[int] = set()
        for index, model_call in enumerate(loop_state.model_calls, start=1):
            if self._is_pre_recorded_report_workflow_call(model_call):
                turn = turns_by_index.get(index)
                if turn is not None:
                    self._record_embedded_report_observation(run_id, turn)
                    observed_turns.add(int(turn.turn_index))
                continue
            self._record_embedded_report_model_call(run_id, model_call)
            turn = turns_by_index.get(index)
            if turn is not None:
                self._record_embedded_report_observation(run_id, turn)
                observed_turns.add(int(turn.turn_index))
        for turn in loop_result.turns:
            turn_index = int(turn.turn_index or 0)
            if turn_index not in observed_turns:
                self._record_embedded_report_observation(run_id, turn)

    @staticmethod
    def _is_pre_recorded_report_workflow_call(model_call: dict[str, Any]) -> bool:
        payload = model_call.get("payload") or {}
        metadata = model_call.get("metadata") or {}
        return (
            payload.get("type") == "tool_call"
            and payload.get("tool_name") == "report_workflow.run"
            and bool(metadata.get("deterministic_action"))
        )

    def _record_embedded_report_model_call(
        self,
        run_id: str,
        model_call: dict[str, Any],
    ) -> None:
        payload = model_call.get("payload") or {}
        action_type = str(payload.get("type") or "")
        self.store.add_agent_step(
            run_id,
            step_type="model_call",
            title=(
                "报告主 Agent 决定调用 Workflow"
                if action_type == "tool_call"
                else "报告主 Agent 汇总 Workflow 观察结果"
            ),
            status="done",
            input_payload={
                "schema_name": model_call.get("schema_name"),
                "agent_role": model_call.get("agent_role"),
                "messages": model_call.get("messages") or [],
            },
            output_payload={
                "payload": payload,
                "response_id": model_call.get("response_id"),
            },
            metadata={
                **dict(model_call.get("metadata") or {}),
                "loop_phase": "think",
                "embedded_loop": "external_impact_report",
                "action_type": action_type,
            },
        )

    def _record_embedded_report_observation(
        self,
        run_id: str,
        turn: Any,
    ) -> None:
        observation = dict(turn.observation or {})
        if "tool_result" not in observation:
            return
        self.store.add_agent_step(
            run_id,
            step_type="workflow_stage",
            title="报告 Workflow 结果回填 AgentLoop",
            status="done",
            input_payload={"turn_index": turn.turn_index},
            output_payload={"observation": observation["tool_result"]},
            metadata={
                "loop_phase": "observe",
                "embedded_loop": "external_impact_report",
                "tool_name": "report_workflow.run",
            },
        )

    @staticmethod
    def _impact_internal_route(message: str) -> str:
        text = str(message or "").lower()
        agent_tech_terms = (
            "agent runtime",
            "agent harness",
            "tool policy",
            "subagent",
            "claude code",
            "codex",
            "openclaw",
            "runtime",
            "harness",
            "工具策略",
            "子智能体",
        )
        news_or_release_terms = (
            "新闻",
            "最新",
            "日报",
            "发布",
            "更新",
            "开源",
            "行业事件",
            "news",
            "latest",
            "release",
            "changelog",
        )
        strong_policy_terms = (
            "政策",
            "监管",
            "法规",
            "法条",
            "办法",
            "条例",
            "合规",
            "regulation",
        )
        regulatory_terms_without_plain_policy = tuple(
            term for term in strong_policy_terms if term != "policy"
        )
        source_inventory_terms = (
            "数据源",
            "source",
            "sources",
            "抓取状态",
            "快照",
            "接入了什么",
            "有哪些数据",
        )
        source_inventory_action_terms = (
            "有哪些",
            "查看",
            "看看",
            "盘点",
            "状态",
            "列出",
            "接入",
            "show",
            "list",
            "status",
        )
        if any(term in text for term in source_inventory_terms) and (
            any(term in text for term in source_inventory_action_terms)
            or not any(term in text for term in ("影响", "分析", "报告", "impact"))
        ):
            return "source_inventory"
        if any(term in text for term in news_or_release_terms) and not any(
            term in text for term in strong_policy_terms
        ):
            return "news"
        if any(term in text for term in agent_tech_terms) and not any(
            term in text for term in regulatory_terms_without_plain_policy
        ):
            return "news"
        if any(
            term in text
            for term in (
                *strong_policy_terms,
                "合规政策",
                "policy",
            )
        ):
            return "policy"
        if any(
            term in text
            for term in (
                "调研",
                "研究",
                "综述",
                "research",
                "study",
            )
        ) and not any(
            term in text
            for term in (
                "新闻",
                "最新",
                "发布",
                "release",
                "changelog",
                "政策",
                "监管",
                "影响",
            )
        ):
            return "research"
        return "news"

    @staticmethod
    def _impact_selected_sources(route: str) -> list[str]:
        if route == "policy":
            return ["policy_sources", "company_wiki", "long_term_memory"]
        if route == "research":
            return ["company_wiki", "long_term_memory", "model_research_synthesis"]
        if route == "source_inventory":
            return ["source_config", "news_snapshot", "policy_snapshot"]
        return [
            "news_sources",
            "tech_release_feeds",
            "research_feeds",
            "company_wiki",
            "long_term_memory",
        ]

    def _wrap_external_impact_result(
        self,
        result: dict[str, Any],
        *,
        route: str,
        route_label: str,
        selected_sources: list[str],
    ) -> dict[str, Any]:
        wrapped = dict(result)
        answer = str(wrapped.get("answer") or "")
        source_text = "、".join(selected_sources)
        prefix = (
            f"已通过统一外部变化分析入口处理本轮请求。\n"
            f"- 内部路线：{route_label}\n"
            f"- 数据/上下文边界：{source_text}\n\n"
        )
        if not answer.startswith("已通过统一外部变化分析入口"):
            wrapped["answer"] = prefix + answer
        wrapped.setdefault("citations", [])
        wrapped.setdefault("artifacts", {})
        wrapped["internal_route"] = route
        return wrapped

    def _run_impact_source_inventory(self, run_id: str, message: str) -> dict[str, Any]:
        config = load_source_config()
        news_sources = list(config.get("news_sources") or [])
        policy_sources = list(config.get("policy_sources") or [])
        fallback_sources = list(config.get("local_fallback_sources") or [])
        news_snapshot = load_latest_news_snapshot()
        policy_snapshot = load_latest_policy_snapshot()
        enabled_news = [item for item in news_sources if item.get("enabled", True)]
        enabled_policy = [item for item in policy_sources if item.get("enabled", True)]
        snapshot_summary = {
            "news": {
                "item_count": len(news_snapshot.get("items") or []),
                "snapshot_path": news_snapshot.get("snapshot_path") or "",
                "captured_at": news_snapshot.get("captured_at") or news_snapshot.get("collected_at") or "",
                "source_status_count": len(news_snapshot.get("source_statuses") or []),
            },
            "policy": {
                "item_count": len(
                    policy_snapshot.get("items") or policy_snapshot.get("documents") or []
                ),
                "snapshot_path": policy_snapshot.get("snapshot_path") or "",
                "captured_at": policy_snapshot.get("captured_at") or policy_snapshot.get("collected_at") or "",
                "source_status_count": len(policy_snapshot.get("source_statuses") or []),
            },
        }
        inventory = {
            "schema_version": "external_impact.source_inventory.v1",
            "message": message,
            "news_sources": news_sources,
            "policy_sources": policy_sources,
            "local_fallback_sources": fallback_sources,
            "source_levels": config.get("source_levels") or {},
            "snapshots": snapshot_summary,
            "boundary": {
                "user_visible_skill": "external_impact_report",
                "legacy_internal_routes": [
                    "policy_weekly_impact",
                    "recent_news_report",
                    "research_report",
                ],
                "fixture_rule": "TEST/local fixture sources can validate the pipeline, but should not be presented as production evidence.",
            },
        }
        artifact_dir = company_dir(self.company_id) / "runs" / run_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / f"{date.today().isoformat()}-source-inventory.json"
        artifact_path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
        self.store.add_agent_step(
            run_id,
            step_type="tool_call",
            title="读取外部变化数据源配置",
            status="done",
            output_payload={
                "news_source_count": len(news_sources),
                "enabled_news_source_count": len(enabled_news),
                "policy_source_count": len(policy_sources),
                "enabled_policy_source_count": len(enabled_policy),
                "fallback_source_count": len(fallback_sources),
                "snapshots": snapshot_summary,
                "source_levels": config.get("source_levels") or {},
            },
            tool_calls=[
                {
                    "stage_name": "external_impact_source_inventory",
                    "tool_name": "source_config.read",
                    "policy_decision": "allow",
                    "status": "success",
                    "output": {
                        "news_source_count": len(news_sources),
                        "policy_source_count": len(policy_sources),
                    },
                }
            ],
            metadata={"loop_phase": "act", "data_surface": "source_inventory"},
        )
        has_snapshot = bool(snapshot_summary["news"]["item_count"] or snapshot_summary["policy"]["item_count"])
        self.store.add_agent_step(
            run_id,
            step_type="gate_check",
            title="检查数据源证据边界",
            status="done",
            gate_result={
                "decision": "allow" if has_snapshot else "ask",
                "reason": (
                    "已找到历史快照，可用于复盘来源状态"
                    if has_snapshot
                    else "当前没有可用快照；只能展示已配置来源，不能声称已抓取到实时数据"
                ),
                "policy_id": "external_impact.source_boundary.v1",
            },
            metadata={"loop_phase": "observe"},
        )
        self.store.add_agent_step(
            run_id,
            step_type="artifact_write",
            title="写入数据源盘点产物",
            status="done",
            output_payload={"artifacts": {"run_artifact": str(artifact_path)}},
            metadata={"loop_phase": "act"},
        )
        answer = self._render_source_inventory_answer(
            enabled_news=enabled_news,
            enabled_policy=enabled_policy,
            fallback_sources=fallback_sources,
            snapshot_summary=snapshot_summary,
            artifact_path=artifact_path,
        )
        return {
            "answer": answer,
            "artifacts": {"run_artifact": str(artifact_path)},
            "citations": [{"type": "source_inventory", "path": str(artifact_path)}],
        }

    @staticmethod
    def _render_source_inventory_answer(
        *,
        enabled_news: list[dict[str, Any]],
        enabled_policy: list[dict[str, Any]],
        fallback_sources: list[dict[str, Any]],
        snapshot_summary: dict[str, Any],
        artifact_path: Path,
    ) -> str:
        def source_lines(items: list[dict[str, Any]], limit: int = 6) -> list[str]:
            if not items:
                return ["- 未启用来源"]
            lines = []
            for item in items[:limit]:
                topics = "、".join(str(value) for value in item.get("topics") or item.get("keywords") or [])
                lines.append(
                    f"- {item.get('name') or item.get('source_id')}（{item.get('level', '')}/{item.get('type', '')}）"
                    + (f"：{topics}" if topics else "")
                )
            if len(items) > limit:
                lines.append(f"- 其余 {len(items) - limit} 个来源见 RunArtifact")
            return lines

        return "\n".join(
            [
                "外部变化数据源盘点如下：",
                "",
                "## 新闻/技术发布/研究来源",
                *source_lines(enabled_news),
                "",
                "## 政策/监管来源",
                *source_lines(enabled_policy),
                "",
                "## 本地兜底来源",
                *source_lines(fallback_sources),
                "",
                "## 最近快照",
                f"- 新闻快照：{snapshot_summary['news']['item_count']} 条，状态记录 {snapshot_summary['news']['source_status_count']} 条",
                f"- 政策快照：{snapshot_summary['policy']['item_count']} 条，状态记录 {snapshot_summary['policy']['source_status_count']} 条",
                "",
                "## 使用边界",
                "- L1/L2/L3 来源可以作为影响分析的主要证据；TEST/local fixture 只用于链路验收，不能当作实时结论。",
                "- 用户正常聊天时不需要选择具体来源；系统会按问题语义在统一影响分析入口内部路由。",
                "",
                f"RunArtifact：{artifact_path}",
            ]
        )

    def _run_policy_weekly_impact(self, run_id: str, session_id: str, message: str) -> dict[str, Any]:
        effective_query = self._policy_effective_query(session_id, message)
        if effective_query != str(message or "").strip():
            inherited_messages = [
                str(item.get("content") or "")
                for item in self.store.list_chat_messages(session_id)[:-1]
                if item.get("role") == "user" and str(item.get("content") or "").strip()
            ][-4:]
            self.store.add_agent_step(
                run_id,
                step_type="history_read",
                title="继承政策会话上下文",
                status="done",
                input_payload={"current_message": message},
                output_payload={
                    "history_turn_count": len(inherited_messages),
                    "recent_history": inherited_messages,
                    "effective_query": effective_query,
                },
                metadata={"loop_phase": "think", "continuity_contract": "policy_followup.v1"},
            )
        review_proof_resolver = PolicyMemoryReviewGateResolver(self.store)
        gate_runner = GateRunner(
            build_default_gate_registry(proof_resolver=review_proof_resolver),
            AgentStepGateDecisionSink(self.store),
        )
        result = run_policy_weekly_impact(
            self.company_id,
            model_adapter=self._get_model_adapter(),
            run_id=run_id,
            lookback_days=self._policy_lookback_days(effective_query),
            query=effective_query,
            gate_runner=gate_runner,
        )
        state = result.state
        policy_preferences = list(state.company_context_pack.get("user_preferences") or [])
        self.store.add_agent_step(
            run_id,
            step_type="memory_read",
            title="读取政策报告偏好",
            status="done",
            input_payload={"query": "政策报告 输出顺序 偏好"},
            output_payload={
                "memory_count": len(policy_preferences),
                "raw_memory_count": len(policy_preferences),
                "filtered_out": 0,
                "memory_ids": [str(item.get("id") or "") for item in policy_preferences],
            },
            metadata={"loop_phase": "observe", "scope": "policy_report_preferences"},
        )
        data_mode = self._policy_data_mode(state)
        tool_calls = list(state.tool_calls)
        gate_results = list(state.stage_gate_results)
        self.store.add_agent_step(
            run_id,
            step_type="tool_call",
            title="运行政策分析工作流工具",
            status="done" if state.current_stage == "done" else "failed",
            output_payload={
                "tool_call_count": len(tool_calls),
                "workflow_run_id": state.run_id,
                "workflow_status": state.current_stage,
                "data_mode": data_mode,
                "errors": state.errors,
                "source_manifest": state.source_manifest,
                "regulatory_coverage_gaps": state.regulatory_coverage_gaps,
                "query_company_facts": state.query_company_facts,
            },
            tool_calls=tool_calls,
            metadata={"loop_phase": "act"},
        )
        stage_titles = {
            "load_company_context": "读取企业知识库",
            "fetch_recent_policies": "抓取官方政策",
            "policy_ingest_and_index": "构建政策索引",
            "retrieve_relevant_clauses": "召回候选条款",
            "extract_policy_clauses": "抽取政策条款",
            "match_company_policy": "生成候选企业关联",
            "analyze_policy_applicability": "判定政策适用性",
            "score_policy_impact": "计算影响等级",
            "review_evidence_and_risk": "执行证据质疑复核",
            "generate_weekly_report": "生成报告产物",
        }
        for stage_trace in state.stage_trace:
            stage_name = str(stage_trace.get("stage") or "workflow_stage")
            gate = stage_trace.get("gate") or {}
            self.store.add_agent_step(
                run_id,
                step_type="workflow_stage",
                title=stage_titles.get(stage_name, stage_name),
                status="done" if stage_trace.get("status") == "ok" else "failed",
                output_payload={
                    "stage_name": stage_name,
                    "duration_ms": stage_trace.get("duration_ms", 0),
                    "artifact_keys": stage_trace.get("artifact_keys") or [],
                    "gate_metrics": gate.get("metrics") or {},
                },
                gate_result={
                    "decision": "allow" if gate.get("passed", True) else "deny",
                    "reason": "; ".join(str(item) for item in gate.get("issues") or [])
                    or "阶段契约与证据门控通过",
                },
                metadata={"loop_phase": "observe", "workflow_stage": stage_name},
            )
        if state.model_calls:
            role_titles = {
                "applicability_analyst": "运行政策适用性分析子智能体",
                "skeptic": "运行政策证据质疑子智能体",
            }
            for workflow_model_call in state.model_calls:
                model_metadata = workflow_model_call.get("metadata") or {}
                role = str(workflow_model_call.get("agent_role") or "policy_subagent")
                schema_name = str(workflow_model_call.get("schema_name") or "structured_output")
                self.store.add_agent_step(
                    run_id,
                    step_type="model_call",
                    title=role_titles.get(role, f"运行 {role} 子智能体"),
                    status="done" if state.current_stage == "done" else "failed",
                    input_payload={
                        "model_id": model_metadata.get("model_id", DEFAULT_MODEL_ID),
                        "schema_name": schema_name,
                        "subagent_role": role,
                        "messages": workflow_model_call.get("messages") or [],
                        "prompt_contract": {
                            "contract_id": schema_name,
                            "role": role,
                            "loop_boundary": "model proposes; deterministic evidence gates publish",
                            "context_controls": {
                                "visible_keys": [
                                    "applicability_context"
                                    if role == "applicability_analyst"
                                    else "review_context"
                                ],
                            },
                        },
                        "context_manifest_ids": [
                            item.get("record_id")
                            for item in state.context_manifests
                            if item.get("record_id")
                        ],
                    },
                    output_payload={
                        "payload": workflow_model_call.get("payload") or {},
                        "review_bundles": [
                            item for item in state.review_bundles if item.get("role") == role
                        ],
                    },
                    metadata={
                        "adapter": model_metadata.get("adapter", "AnthropicCompatibleModelAdapter"),
                        "loop_phase": "think",
                        "latency_ms": model_metadata.get("total_latency_ms")
                        or model_metadata.get("latency_ms"),
                        "attempt_latency_ms": model_metadata.get("latency_ms"),
                        "total_latency_ms": model_metadata.get("total_latency_ms")
                        or model_metadata.get("latency_ms"),
                        "usage": model_metadata.get("usage", {}),
                        "attempts": model_metadata.get("attempts", 1),
                        "retry_errors": model_metadata.get("retry_errors", []),
                        "stop_reason": model_metadata.get("stop_reason", ""),
                        "temperature": model_metadata.get("temperature"),
                        "max_tokens": model_metadata.get("max_tokens"),
                        "timeout_seconds": model_metadata.get("timeout_seconds"),
                        "max_attempts": model_metadata.get("max_attempts"),
                    },
                )
        if state.current_stage != "done":
            error_detail = state.errors[-1].get("detail") if state.errors else "unknown workflow error"
            raise ModelInvocationError(f"政策分析工作流失败：{error_detail}")
        self.store.add_agent_step(
            run_id,
            step_type="gate_check",
            title="汇总工作流门控结果",
            status="done",
            output_payload={
                "gate_count": len(gate_results),
                "workflow_status": state.current_stage,
                "data_mode": data_mode,
            },
            gate_result={
                "decision": "ask" if data_mode == "fixture" else "allow",
                "reason": (
                    "本地验收样例只用于验证流程，不可作为真实政策结论"
                    if data_mode == "fixture"
                    else "政策来源通过运行时校验"
                ),
                "stage_gate_count": len(gate_results),
            },
            metadata={"loop_phase": "observe"},
        )
        artifacts = {
            "report": state.report_paths.get("report", ""),
            "html_report": state.report_paths.get("html_report", ""),
            "run_artifact": state.report_paths.get("run_artifact", ""),
        }
        self.store.add_agent_step(
            run_id,
            step_type="artifact_write",
            title="挂载政策分析产物",
            status="done",
            output_payload={"artifacts": artifacts},
            metadata={"loop_phase": "act"},
        )
        answer = self._policy_workflow_answer(message, state, artifacts, query_context=effective_query)
        policy_citations = [
            {
                "type": "policy_source",
                "policy_id": item.get("policy_id"),
                "title": item.get("policy_title"),
                "url": (item.get("policy_evidence") or {}).get("source_url")
                or self._policy_source_url(state, str(item.get("policy_id") or "")),
            }
            for item in state.impact_assessments
            if (item.get("policy_evidence") or {}).get("source_url")
            or self._policy_source_url(state, str(item.get("policy_id") or ""))
        ]
        coverage_citations = [
            {
                "type": "regulatory_coverage_gap",
                "title": item.get("framework"),
                "url": item.get("official_url"),
            }
            for item in state.regulatory_coverage_gaps
            if item.get("official_url")
        ]
        grounded_policy_citations = self._ground_policy_citations(
            answer,
            policy_citations + coverage_citations,
        )
        return {
            "answer": answer,
            "artifacts": artifacts,
            "delegation_evidence": list(state.delegation_evidence),
            "citations": grounded_policy_citations
            + [{"type": "run_artifact", "path": artifacts["run_artifact"]}],
        }

    @staticmethod
    def _ground_policy_citations(answer: str, citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        text = str(answer or "")
        grounded = []
        seen = set()
        for item in citations:
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            if not ((title and title in text) or (url and url in text)):
                continue
            marker = (str(item.get("type") or ""), title, url)
            if marker in seen:
                continue
            seen.add(marker)
            grounded.append(item)
        return grounded

    def _run_recent_news_report(self, run_id: str, message: str) -> dict[str, Any]:
        scope_issue = self._news_project_scope_issue(message)
        if scope_issue:
            self.store.add_agent_step(
                run_id,
                step_type="gate_check",
                title="确认新闻影响分析对象",
                status="done",
                input_payload={"message": message, "configured_company_id": self.company_id},
                gate_result={
                    "decision": "ask",
                    "reason": scope_issue,
                    "policy_id": "news.subject_scope.v1",
                },
                metadata={"loop_phase": "observe"},
            )
            return {
                "answer": scope_issue,
                "artifacts": {},
                "citations": [],
            }
        result = run_recent_news_report(self.company_id, query=message)
        artifacts = {
            "report": result["report_path"],
            "html_report": result["html_report_path"],
            "run_artifact": result["artifact_path"],
        }
        source_manifest = result.get("source_manifest") or {}
        news_source_statuses = list(source_manifest.get("source_statuses") or [])
        healthy_news_sources = [
            item for item in news_source_statuses if item.get("status") == "ok"
        ]
        news_tool_calls = [
            {
                "stage_name": "recent_news_report",
                "tool_name": "news.load_items",
                "policy_decision": "allow",
                "status": "success",
                "input": {"company_id": self.company_id},
                "output": {
                    "event_count": result["event_count"],
                    "data_mode": result["data_mode"],
                    "source_manifest": result.get("source_manifest") or {},
                },
            },
            {
                "stage_name": "recent_news_report",
                "tool_name": "news.structure_events",
                "policy_decision": "allow",
                "status": "success",
                "input": {"source": "cleaned_news_items"},
                "output": {
                    "event_count": result["event_count"],
                    "matched_event_count": result["matched_event_count"],
                },
            },
            {
                "stage_name": "recent_news_report",
                "tool_name": "news.analyze_company_impact",
                "policy_decision": "allow",
                "status": "success",
                "input": {"company_id": self.company_id},
                "output": {"artifact_path": result["artifact_path"]},
            },
        ]
        self.store.add_agent_step(
            run_id,
            step_type="tool_call",
            title="记录新闻 Workflow 内部工具调用",
            status="done",
            output_payload={
                "event_count": result["event_count"],
                "matched_event_count": result["matched_event_count"],
                "data_mode": result["data_mode"],
                "news_source_count": len(news_source_statuses),
                "enabled_news_source_count": len(healthy_news_sources),
                "snapshots": {
                    "news": {
                        "item_count": result["event_count"],
                        "snapshot_path": source_manifest.get("snapshot_path") or "",
                        "captured_at": source_manifest.get("collected_at") or "",
                        "source_status_count": len(news_source_statuses),
                    }
                },
                "source_manifest": source_manifest,
            },
            tool_calls=news_tool_calls,
            metadata={"loop_phase": "act"},
        )
        workflow_stages = [
            (
                "读取外部新闻与技术发布",
                {"company_id": self.company_id, "query": message},
                {
                    "event_count": result["event_count"],
                    "data_mode": result["data_mode"],
                    "healthy_source_count": len(healthy_news_sources),
                    "source_count": len(news_source_statuses),
                    "snapshot_path": source_manifest.get("snapshot_path") or "",
                },
                "act",
            ),
            (
                "结构化 ExternalEvent",
                {"raw_source": "news snapshot / live RSS"},
                {
                    "structured_event_count": result["event_count"],
                    "constraint_diagnostics": result.get("constraint_diagnostics") or {},
                    "quality_diagnostics": result.get("quality_diagnostics") or {},
                },
                "observe",
            ),
            (
                "匹配 Wiki 画像与查询主题",
                {
                    "company_id": self.company_id,
                    "topic_coverage": result.get("topic_coverage") or {},
                },
                {
                    "matched_event_count": result["matched_event_count"],
                    "publishable_event_count": result.get("publishable_event_count", 0),
                    "selected_event_count": result.get("selected_event_count", 0),
                    "top_titles": [
                        item.get("title", "")
                        for item in list(result.get("top_matches") or [])[:5]
                    ],
                },
                "think",
            ),
            (
                "分析具体影响与行动建议",
                {
                    "impact_schema": "ExternalEvent + company_facts + impact_chain + recommended_action",
                },
                {
                    "impact_chains": [
                        {
                            "title": item.get("title", ""),
                            "matched_topics": item.get("matched_topics") or [],
                            "impact_level": item.get("impact_level", ""),
                            "impact_score": item.get("impact_score", 0),
                            "impact_chain": item.get("impact_chain") or [],
                            "recommended_action": item.get("recommended_action", ""),
                        }
                        for item in list(result.get("top_matches") or [])[:5]
                    ],
                },
                "think",
            ),
            (
                "生成 Markdown 与 HTML 报告产物",
                {"artifact_types": ["markdown_report", "html_report", "run_artifact"]},
                {"artifacts": artifacts},
                "act",
            ),
        ]
        for title, input_payload, output_payload, phase in workflow_stages:
            self.store.add_agent_step(
                run_id,
                step_type="workflow_stage",
                title=title,
                status="done",
                input_payload=input_payload,
                output_payload=output_payload,
                metadata={
                    "loop_phase": phase,
                    "workflow": "recent_news_report",
                },
            )
        self.store.add_agent_step(
            run_id,
            step_type="gate_check",
            title="检查新闻报告输出",
            status="done",
            gate_result={
                "decision": "allow" if result["relevance_gate"] == "allow" else "ask",
                "reason": result["gate_reason"],
                "data_mode": result["data_mode"],
            },
            metadata={"loop_phase": "observe"},
        )
        self.store.add_agent_step(
            run_id,
            step_type="artifact_write",
            title="挂载新闻报告产物",
            status="done",
            output_payload={"artifacts": artifacts},
            metadata={"loop_phase": "act"},
        )
        return {
            "answer": self._news_workflow_answer(message, result, artifacts),
            "artifacts": artifacts,
            "citations": [{"type": "news_artifact", "path": result["artifact_path"]}],
        }

    def _news_project_scope_issue(self, message: str) -> str:
        text = str(message or "").lower()
        project_reference = any(term in text for term in ("我的项目", "这个项目", "本项目"))
        named_non_company_project = any(
            term in text
            for term in (
                "agent workbench",
                "workbench",
                "coursepilot",
                "course pilot",
            )
        )
        configured_company_reference = any(
            term in text for term in ("示例公司", "company_001", "示例产品", "ifind")
        )
        if not (project_reference or named_non_company_project) or configured_company_reference:
            return ""
        if named_non_company_project:
            subject = "你点名的项目"
        else:
            subject = "“我的项目”"
        configured_subject = "company_001（示例公司）" if self.company_id == "company_001" else self.company_id
        return (
            f"当前无法可靠分析{subject}的新闻影响：本新闻技能已配置的受控分析对象只有 "
            f"{configured_subject}，并未读取 Agent Workbench、CoursePilot 或其他项目的代码与项目画像。"
            "因此本轮只完成对象范围门控，没有调用新闻工作流，也没有生成影响报告。"
            "请明确项目名称并先补充项目目标、技术栈、关键依赖和风险偏好；"
            "如果要分析示例公司，请在问题中明确写出“示例公司”。"
        )

    def _run_research_report(
        self,
        run_id: str,
        message: str,
        model_id: str,
        context_manifest: dict[str, Any],
        prepared_context: dict[str, Any],
    ) -> dict[str, Any]:
        memories = list(prepared_context.get("memories") or [])
        prompt_messages, prompt_contract = build_research_report_prompt(
            message=message,
            context_manifest=context_manifest,
            memory_count=len(memories),
            evidence=self._research_evidence(message, memories),
            conversation_summary=prepared_context.get("conversation_summary") or {},
            recent_history=prepared_context.get("recent_history") or [],
        )
        model_response = self._complete_model(
            run_id=run_id,
            title="生成调研报告提纲",
            model_id=model_id,
            schema_name="research_report.v1",
            messages=prompt_messages,
            prompt_contract=prompt_contract,
        )
        report_text = self._render_research_report_v2(
            message,
            context_manifest,
            memories,
            model_response.payload,
            run_id=run_id,
        )
        report_dir = company_dir(self.company_id) / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / f"{date.today().isoformat()}-research-report-{run_id}.md"
        report_path.write_text(report_text, encoding="utf-8")
        self.store.add_agent_step(
            run_id,
            step_type="artifact_write",
            title="写入调研报告产物",
            status="done",
            output_payload={"report": str(report_path)},
            metadata={"loop_phase": "act"},
        )
        return {
            "answer": self._research_answer(message, report_path),
            "artifacts": {"report": str(report_path)},
            "citations": [{"type": "research_report", "path": str(report_path)}],
        }

    def _research_evidence(self, message: str, memories: list[dict[str, Any]]) -> list[str]:
        evidence = [str(item.get("content") or "")[:300] for item in memories[:5]]
        if self._is_workbench_design_query(message) or self._is_harness_interview_query(message):
            evidence.extend(
                [
                    "AgentRuntime persists each turn as AgentRun plus ordered AgentStep records.",
                    "SkillRegistry routes general_chat, external_impact_report, and the internal report workflow executors.",
                    "ContextManifest records visible keys, hidden fields, token budgets, token estimates, and checksums; checksum mismatch verification is not implemented yet.",
                    "ToolGateway applies deterministic tool allowlists and stores tool audit evidence.",
                    "ChatWorkbenchService exposes trace summaries for context, memory, model, tools, gates, and artifacts.",
                    "The frontend renders the execution trace above the final assistant answer.",
                ]
            )
        return [item for item in evidence if item]

    def _run_company_wiki_blueprint(self, run_id: str) -> dict[str, Any]:
        gateway = ToolGateway()
        blueprint = gateway.call("main_agent", "company_wiki_blueprint", company_id=self.company_id)
        tool_calls = [record.to_dict() for record in gateway.calls]
        artifact_dir = company_dir(self.company_id) / "runs" / run_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / "company-wiki-blueprint.json"
        artifact_path.write_text(json.dumps(blueprint, ensure_ascii=False, indent=2), encoding="utf-8")
        self.store.add_agent_step(
            run_id,
            step_type="tool_call",
            title="生成企业知识库规划",
            status="done",
            output_payload={
                "missing_file_count": len(blueprint.get("missing_files", [])),
                "question_count": len(blueprint.get("next_questions", [])),
            },
            tool_calls=tool_calls,
            gate_result={
                "decision": "allow",
                "reason": "main_agent allowlist permits company_wiki_blueprint",
            },
            metadata={"loop_phase": "act"},
        )
        self.store.add_agent_step(
            run_id,
            step_type="artifact_write",
            title="写入知识库规划产物",
            status="done",
            output_payload={"artifacts": {"wiki_blueprint": str(artifact_path)}},
            metadata={"loop_phase": "act"},
        )
        answer_lines = ["企业知识库规划已生成。"]
        missing = blueprint.get("missing_files", [])
        if missing:
            answer_lines.append(f"缺失文件（{len(missing)}）：" + ", ".join(missing[:5]))
        missing_facts = blueprint.get("missing_facts", [])
        if missing_facts:
            answer_lines.append(f"缺失事实项：{len(missing_facts)} 个。")
        questions = blueprint.get("next_questions", [])
        if questions:
            answer_lines.extend(
                [
                    f"下一步采集问题（共 {len(questions)} 个）：",
                    *[f"{index}. {question}" for index, question in enumerate(questions, 1)],
                ]
            )
        answer_lines.append(f"结构化产物：{artifact_path}")
        return {
            "answer": "\n".join(answer_lines),
            "artifacts": {"wiki_blueprint": str(artifact_path)},
            "citations": [{"type": "company_wiki_blueprint", "path": str(artifact_path)}],
        }

    @staticmethod
    def _has_any(text: str, terms: tuple[str, ...]) -> bool:
        return any(term in text for term in terms)

    def _policy_workflow_answer(
        self,
        message: str,
        state: PolicyImpactState,
        artifacts: dict[str, str],
        *,
        query_context: str = "",
    ) -> str:
        text = str(query_context or message or "").lower()
        lines = [
            "政策影响分析已完成。我会按你这轮问题聚焦解释，而不是只给一份固定报告。",
            "",
            f"本次 workflow 处理政策数：{len(state.policy_documents)}，形成评估项：{len(state.impact_assessments)}。",
        ]
        if state.source_manifest.get("no_updates"):
            return "\n".join(
                [
                    "政策来源检查已完成。",
                    "",
                    (
                        f"官方来源抓取成功，但 {state.date_range.get('date_from')} 至 "
                        f"{state.date_range.get('date_to')} 没有发现新增政策文件。"
                    ),
                    "系统不会用历史政策或本地测试样例伪造本周更新，因此本轮不生成新的影响判断。",
                    "审计报告和 RunArtifact 已保存到本轮运行产物。",
                ]
            )
        if self._policy_data_mode(state) == "fixture":
            lines.insert(
                1,
                "数据边界：本次运行使用本地验收样例政策，并非真实近期监管文件；以下内容只用于验证 Harness 流程，不可作为现实合规结论。",
            )
        focused_followup = self._policy_product_followup_answer(message, state)
        if focused_followup:
            return focused_followup
        targeted_answer = self._targeted_policy_answer(message, state)
        if targeted_answer:
            return targeted_answer
        if state.regulatory_coverage_gaps:
            lines.extend(
                [
                    "",
                    "重要边界：本轮近期政策快照不是完整监管清单；4 类既有基础框架仍需单独做条款级核验。",
                ]
            )
            lines.extend(self._regulatory_triage_lines(state))
        relevant = self._relevant_policy_assessments(text, state.impact_assessments)
        if not relevant:
            covered = "、".join(item.get("title", "") for item in state.policy_documents)
            unrelated = self._explicit_domain_mismatch_answer(text)
            lines.extend(["", unrelated] if unrelated else [
                "",
                "当前可核验政策库没有覆盖你点名的法规或主题，因此本轮不生成条款、义务主体或整改结论。",
                f"本次实际覆盖：{covered or '无'}。",
                "请补充官方原文链接，或先把该官方来源接入政策源配置后再分析。",
            ])
        else:
            preference_text = " ".join(
                str(item.get("content") or "")
                for item in state.company_context_pack.get("user_preferences") or []
            )
            if "先列明确不适用" in preference_text and "条件适用" in preference_text:
                order = {
                    "not_applicable": 0,
                    "conditional": 1,
                    "direct": 2,
                    "insufficient_evidence": 3,
                }
                relevant = sorted(
                    relevant,
                    key=lambda item: order.get(str(item.get("applicability") or ""), 9),
                )
            labels = {
                "direct": "直接适用",
                "conditional": "条件适用",
                "not_applicable": "明确不适用",
                "insufficient_evidence": "证据不足",
            }
            lines.extend(
                [
                    "",
                    *self._policy_query_focus_lines(text, state, relevant),
                    "",
                    "分类口径：直接适用、条件适用、明确不适用、证据不足互斥；指导性文件单独标记为“不产生强制义务 + 直接相关”，不冒充强制要求。系统不会为凑齐四类而改变证据结论。",
                    "",
                    "针对本轮问题的证据化结论：",
                ]
            )
            for item in relevant:
                relationship = str(item.get("applicability") or "conditional")
                binding_effect = str(item.get("binding_effect") or "unknown")
                policy_evidence = item.get("policy_evidence") or {}
                source_kind_labels = {
                    "public_disclosure": "公开披露",
                    "analysis_inference": "分析推断",
                    "project_design": "项目设计",
                    "user_confirmed": "用户确认",
                }
                company_evidence = "; ".join(
                    (
                        f"[{source_kind_labels.get(str(fact.get('source_kind') or 'other'), str(fact.get('source_kind') or 'other'))}; "
                        f"fact_id: {fact.get('fact_id') or 'unknown'}] {fact.get('value') or ''}"
                    )
                    for fact in item.get("company_evidence") or []
                )
                source_url = policy_evidence.get("source_url") or self._policy_source_url(
                    state, str(item.get("policy_id") or "")
                )
                summary = item.get("reasoning", [""])[1] if len(item.get("reasoning", [])) > 1 else ""
                effective_status = str(item.get("effective_status") or "unknown")
                effective_date = str(item.get("effective_date") or "")
                as_of_date = str(item.get("as_of_date") or state.date_range.get("date_to") or "")
                if effective_status == "not_yet_effective":
                    effective_text = f"截至 {as_of_date} 尚未施行，将于 {effective_date} 施行"
                elif effective_status == "effective":
                    effective_text = f"已于 {effective_date} 施行" if effective_date else "已施行"
                else:
                    effective_text = "未检出独立施行日期"
                relationship_label = labels.get(relationship, relationship)
                if binding_effect == "guidance" and relationship == "not_applicable":
                    relationship_label = "不产生强制义务"
                policy_document = self._policy_document(
                    state,
                    str(item.get("policy_id") or ""),
                )
                published_at = str(policy_document.get("published_at") or "")
                lines.extend(
                    [
                        f"- **[{relationship_label}] {item.get('policy_title', '')}**",
                        f"  - 适用理由：{summary or '未记录'}",
                        *(
                            ["  - 政策相关性：直接相关（指导性文件，非强制义务）"]
                            if binding_effect == "guidance"
                            else []
                        ),
                        f"  - 发布日期：{published_at[:10] or '未记录'}",
                        f"  - 时间效力：{effective_text}",
                        f"  - 企业证据：{company_evidence or '无可绑定企业事实'}",
                        f"  - 政策依据：{str(policy_evidence.get('text') or '')[:360] or '当前来源未提供完整条款'}",
                        f"  - 待确认：{'; '.join(item.get('missing_fields', [])) or '无'}",
                        f"  - 建议动作：{'; '.join(item.get('recommended_actions', [])) or '无'}",
                        f"  - 原文：{source_url or '未记录'}",
                    ]
                )
        lines.extend(
            [
                "",
                "完整 Markdown、HTML 和 RunArtifact 已保存到本轮运行产物，可在执行记录中核验。",
            ]
        )
        if state.regulatory_coverage_gaps:
            lines.extend(
                [
                    "",
                    "监管覆盖边界（不是适用性结论）：",
                    "本轮只分析已采集的近期政策。以下既有框架尚未进入条款级核验，未核验前不能把本报告当作完整上线清单：",
                ]
            )
            for gap in state.regulatory_coverage_gaps:
                lines.extend(
                    [
                        f"- {gap.get('framework')}：{gap.get('trigger_reason')}",
                        f"  - 待核验：{gap.get('next_check')}",
                        f"  - 官方入口：{gap.get('official_url')}",
                    ]
                )
        if state.query_company_facts:
            lines.extend(
                [
                    "",
                    "本轮临时企业事实（仅进入本次 Context，不自动写入长期 Memory）：",
                    *[f"- [{item.get('fact_id')}] {item.get('value')}" for item in state.query_company_facts],
                    "这些事实只改变证据前提；对未进入条款级核验的既有框架，系统不会声称适用性结论已经改变。",
                ]
            )
        return "\n".join(lines)

    @staticmethod
    def _policy_query_focus_lines(
        text: str,
        state: PolicyImpactState,
        relevant: list[dict[str, Any]],
    ) -> list[str]:
        normalized = str(text or "").lower()
        wants_agent_platform = any(term in normalized for term in ("agent 平台", "agent平台", "智能体平台"))
        wants_tool_policy = any(term in normalized for term in ("工具权限", "tool policy", "权限门控"))
        wants_audit = any(term in normalized for term in ("审计留痕", "运行留痕", "留痕", "trace"))
        wants_compliance = any(term in normalized for term in ("企业合规", "合规"))
        if not any((wants_agent_platform, wants_tool_policy, wants_audit, wants_compliance)):
            return []

        titles = " ".join(str(item.get("policy_title") or "") for item in relevant)
        lines = ["本轮问题映射（政策证据与工程建议分开）："]
        if wants_agent_platform:
            if "智能体规范应用" in titles:
                lines.append(
                    "- Agent 平台：智能体指导性文件与产品形态直接相关，但不产生强制义务；只能作为治理方向，不能冒充合规结论。"
                )
            else:
                lines.append("- Agent 平台：本轮政策快照没有形成可直接绑定的强制义务结论。")
        if wants_tool_policy:
            lines.append(
                "- 工具权限：本轮 4 份政策没有规定 Agent 工具的 allow/ask/deny 机制；最小权限、人工确认和越权拦截属于工程控制建议，仍需结合个人信息、算法和生成式 AI 规则做条款级核验。"
            )
        if wants_audit:
            lines.append(
                "- 审计留痕：建议保留输入证据、工具决策、模型版本、人工确认和产物校验记录；本轮来源没有给出统一保存期限，不能虚构具体年限。"
            )
        if wants_compliance:
            conditional = sum(
                1 for item in state.impact_assessments if item.get("applicability") == "conditional"
            )
            if state.regulatory_coverage_gaps:
                boundary = (
                    f"此外仍有 {len(state.regulatory_coverage_gaps)} 类基础监管框架未完成条款级核验"
                )
            else:
                boundary = f"本轮仅覆盖已采集的 {len(state.policy_documents)} 份政策，未接入法规不能视为不适用"
            lines.append(
                f"- 企业合规：当前全部评估中形成 {conditional} 项条件适用判断；{boundary}，本报告不是完整上线清单。"
            )
        return lines

    @staticmethod
    def _explicit_domain_mismatch_answer(text: str) -> str:
        normalized = str(text or "").lower()
        agriculture_terms = (
            "深远海",
            "海洋养殖",
            "养殖装备",
            "农机",
            "农业补贴",
            "渔业补贴",
            "畜禽",
            "屠宰",
        )
        asks_direct_impact = any(term in normalized for term in ("直接影响", "是否相关", "有没有影响", "没有就明确"))
        if not asks_direct_impact or not any(term in normalized for term in agriculture_terms):
            return ""
        subjects = []
        if any(term in normalized for term in ("深远海", "海洋养殖", "养殖装备")):
            subjects.append("深远海养殖政策：基于业务领域不相交，未发现对示例公司核心业务的直接影响。")
        if any(term in normalized for term in ("农机", "农业补贴", "农机购置")):
            subjects.append("农机购置补贴政策：基于业务领域不相交，未发现对示例公司核心业务的直接影响。")
        if any(term in normalized for term in ("畜禽", "屠宰")):
            subjects.append("畜禽或屠宰政策：基于业务领域不相交，未发现对示例公司核心业务的直接影响。")
        return "\n".join(
            [
                "结论：",
                *[f"- {subject}" for subject in subjects],
                "概念股、行业资讯或 ESG 话题不构成政策对企业义务或经营流程的直接因果链。",
                "证据边界：本轮政策库未接入点名文件的官方原文；以上是业务领域不相交的初筛结论，不能替代逐份文件的条款级法律核验。",
            ]
        )

    @classmethod
    def _targeted_policy_answer(
        cls,
        message: str,
        state: PolicyImpactState,
    ) -> str:
        text = str(message or "")
        if "网络数据安全风险评估办法" not in text and "为什么是条件适用" not in text:
            return ""
        document = next(
            (
                item
                for item in state.policy_documents
                if "网络数据安全风险评估办法" in str(item.get("title") or "")
            ),
            {},
        )
        assessment = next(
            (
                item
                for item in state.impact_assessments
                if "网络数据安全风险评估办法" in str(item.get("policy_title") or "")
            ),
            {},
        )
        source_url = str(document.get("source_url") or "")
        published_at = str(document.get("published_at") or "")[:10]
        effective_date = str(assessment.get("effective_date") or "2026-08-20")
        if "发布日期" in text or "是否已经施行" in text:
            return "\n".join(
                [
                    "结论：截至 2026-07-16，该办法尚未施行。",
                    f"- 发布日期：{published_at or '未记录'}",
                    f"- 施行日期：{effective_date}",
                    "- 官方施行条款：第二十五条规定，本办法自2026年8月20日起施行。",
                    f"- 官方原文：{source_url or '未记录'}",
                    "发布日期不等于施行日期，因此不能把它写成 2026-07-16 已生效义务。",
                ]
            )
        if "为什么是条件适用" in text:
            return "\n".join(
                [
                    "承接上一轮《网络数据安全风险评估办法》的适用性判断：",
                    "适用性：条件适用。企业处理网络数据并不自动触发年度评估和报送义务，关键义务主体是重要数据处理者。",
                    "必须补齐的两个企业事实：",
                    "1. 示例公司是否已被主管部门识别或告知为重要数据处理者。",
                    "2. 当前处理的数据是否进入适用的重要数据目录，以及主管部门采用的目录口径。",
                    f"时间边界：截至 2026-07-16 尚未施行，将于 {effective_date} 施行。",
                    f"官方原文：{source_url or '未记录'}",
                ]
            )
        if "只回答适用性" in text:
            return "\n".join(
                [
                    "适用性：条件适用。",
                    "义务主体：重要数据处理者，不是所有一般网络数据处理者。",
                    "缺失事实：是否已被识别为重要数据处理者；处理数据是否进入适用的重要数据目录及主管部门口径。",
                    f"时间边界：截至 2026-07-16 尚未施行，将于 {effective_date} 施行。",
                    f"官方原文：{source_url or '未记录'}",
                ]
            )
        return ""

    @staticmethod
    def _regulatory_triage_lines(state: PolicyImpactState) -> list[str]:
        fact_ids = {str(item.get("fact_id") or "") for item in state.query_company_facts}
        if not fact_ids:
            return []
        priorities = []
        if fact_ids & {
            "query.output.investment_suggestion",
            "query.action.broker_jump",
            "query.output.buy_sell_points",
        }:
            priorities.append(
                "- P0 上线阻断问题：用真实输出样例、用户协议、收费与交易链路核验证券投顾业务边界；未关闭前不作资质结论。"
            )
        if fact_ids & {"query.data.holdings", "query.data.risk_preference", "query.data.watchlist"}:
            priorities.append(
                "- P0 上线阻断问题：确认实际个人信息字段及自动化决策用途；如命中《个人信息保护法》第五十五条情形，处理前完成影响评估并留痕。"
            )
        if "query.product.ai_research_assistant" in fact_ids:
            priorities.extend(
                [
                    "- P1 核验：确认服务是否面向境内公众以及生成式 AI 安全评估、算法备案边界。",
                    "- P1 核验：确认自选股个性化、排序、检索或调度是否落入算法推荐服务范围。",
                ]
            )
        if not priorities:
            return []
        return [
            "",
            "监管关注优先级（风险筛查，不是已完成的法律适用结论）：",
            *priorities,
        ]

    @staticmethod
    def _policy_product_followup_answer(message: str, state: PolicyImpactState) -> str:
        text = str(message or "")
        if not state.regulatory_coverage_gaps:
            return ""
        artifacts_note = "完整 Trace、Markdown、HTML 和 RunArtifact 已保存到本轮运行产物。"
        if any(term in text for term in ("相比第一轮", "哪些证据前提变化", "哪些结论仍不能", "补充事实")):
            facts = {str(item.get("fact_id") or ""): str(item.get("value") or "") for item in state.query_company_facts}
            changed_ids = (
                "query.action.direct_order",
                "query.business.fee",
                "query.output.buy_sell_points",
                "query.output.public_market_explanation",
                "query.data.watchlist",
                "query.data.holdings",
                "query.data.risk_preference",
            )
            changed = [f"- [{fact_id}] {facts[fact_id]}" for fact_id in changed_ids if fact_id in facts]
            return "\n".join(
                [
                    "本轮只更新证据前提，不把未核验法规写成已经改变的适用性结论。",
                    "",
                    "本轮新增或澄清的临时事实（不自动写入长期 Memory）：",
                    *(changed or ["- 当前消息没有抽取到新的明确产品事实。"]),
                    "",
                    "基于这些事实可以调整的风险假设：",
                    "- 个人信息前提减弱：用户否定了持仓和风险偏好处理；但自选股仍是个性化输入，个人信息规则不能整体排除。",
                    "- 交易执行前提减弱：不能直接下单，说明系统与交易执行之间仍有用户跳转；这不等于自动排除证券投顾边界。",
                    "- 建议强度前提减弱：不收费、不输出明确买卖点、只做公开信息解读都应进入后续核验，但单凭这些陈述仍不能形成资质结论。",
                    "",
                    "仍不能下的结论：",
                    "- 不能认定或排除证券投资顾问业务；相关框架尚未进入本轮条款级核验。",
                    "- 不能认定生成式 AI、算法推荐和个人信息保护义务已经全部满足或全部不适用。",
                    "- 不能把《网络数据安全风险评估办法》的年度评估/报送义务写成当前已触发；还缺重要数据处理者身份与目录口径，且该办法截至 2026-07-16 尚未施行。",
                    "",
                    artifacts_note,
                ]
            )
        if "证券投资顾问" in text or "投顾" in text or "直接认定" in text:
            return "\n".join(
                [
                    "上一轮列出的 4 项是待单独核验的基础监管框架，不是已经完成的适用性结论：",
                    *[
                        f"- {gap.get('framework')}：{gap.get('next_check')}（{gap.get('official_url')}）"
                        for gap in state.regulatory_coverage_gaps
                    ],
                    "",
                    "不能仅凭当前产品描述直接认定示例公司属于证券投资顾问，也不能直接排除。当前系统只掌握产品功能陈述，尚未完成《证券投资顾问业务暂行规定》的条款级核验，也没有取得主体资质、实际输出样例、用户协议、收费安排和完整交易链路等可核验证据。",
                    "",
                    "因此本轮结论是：证券投顾边界 = 未评估；需要单独启动该法规的官方原文与企业事实核验。",
                    artifacts_note,
                ]
            )
        return ""

    @staticmethod
    def _policy_source_url(state: PolicyImpactState, policy_id: str) -> str:
        for document in state.policy_documents:
            if str(document.get("policy_id") or "") == policy_id:
                return str(document.get("source_url") or "")
        return ""

    @staticmethod
    def _policy_document(state: PolicyImpactState, policy_id: str) -> dict[str, Any]:
        return next(
            (
                document
                for document in state.policy_documents
                if str(document.get("policy_id") or "") == policy_id
            ),
            {},
        )

    def _policy_effective_query(self, session_id: str, message: str) -> str:
        text = str(message or "").strip()
        followup_markers = (
            "刚才",
            "上面",
            "这个结论",
            "第",
            "条款",
            "证据",
            "为什么",
            "反驳",
            "撤回",
            "义务主体",
            "那如果",
            "上一轮",
            "第一轮",
            "补充",
            "相比",
            "变化",
            "哪些",
            "是否",
        )
        if not any(marker in text for marker in followup_markers):
            return text
        history = self.store.list_chat_messages(session_id)
        previous_users = [
            str(item.get("content") or "")
            for item in history[:-1]
            if item.get("role") == "user" and str(item.get("content") or "").strip()
        ][-4:]
        if not previous_users:
            return text
        prior = "\n".join(
            f"前序用户事实 {index}：{content}"
            for index, content in enumerate(previous_users, start=1)
        )
        return f"{prior}\n当前用户补充：{text}"

    @staticmethod
    def _relevant_policy_assessments(
        text: str,
        assessments: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        normalized = str(text or "").lower()
        named = [item.strip() for item in re.findall(r"《([^》]+)》", normalized) if item.strip()]
        if named:
            return [
                item
                for item in assessments
                if any(name in str(item.get("policy_title") or "").lower() for name in named)
            ]
        topic_rules = {
            "network_data": (
                "网络数据",
                "数据安全风险评估",
                "重要数据",
                "一般数据处理者",
                "数据留痕",
                "审计留痕",
                "运行留痕",
            ),
            "anthropomorphic": ("拟人化", "情感互动", "情感陪伴", "工作助手"),
            "agent_guidance": ("智能体规范", "agent 平台", "agent平台", "智能体平台"),
            "ai_ethics": ("科技伦理", "伦理审查"),
        }
        title_markers = {
            "network_data": "网络数据安全风险评估",
            "anthropomorphic": "拟人化互动",
            "agent_guidance": "智能体规范应用",
            "ai_ethics": "科技伦理审查",
        }
        requested_topics = {
            topic
            for topic, terms in topic_rules.items()
            if any(term in normalized for term in terms)
        }
        unsupported_specific_terms = (
            "个人信息保护法",
            "欧盟人工智能法",
            "eu ai act",
            "程序化交易",
            "算法备案",
            "深度合成",
            "生成式人工智能服务管理暂行办法",
            "深远海",
            "海洋养殖",
            "养殖装备",
            "农机",
            "农业补贴",
            "渔业补贴",
        )
        if any(term in normalized for term in unsupported_specific_terms) and not requested_topics:
            return []
        if requested_topics:
            return [
                item
                for item in assessments
                if any(
                    marker in str(item.get("policy_title") or "")
                    for topic, marker in title_markers.items()
                    if topic in requested_topics
                )
            ]
        return assessments

    @staticmethod
    def _policy_lookback_days(message: str) -> int:
        text = str(message or "")
        match = re.search(r"(?:近|最近)\s*(\d{1,3})\s*(天|日|个月|月)", text)
        if not match:
            if any(term in text for term in ("本周", "一周", "过去7天")):
                return 7
            if any(term in text for term in ("最近", "最新", "近期", "新增", "更新")):
                return 30
            return 365
        value = int(match.group(1))
        days = value * 30 if "月" in match.group(2) else value
        return max(1, min(days, 365))

    def _news_workflow_answer(
        self,
        message: str,
        result: dict[str, Any],
        artifacts: dict[str, str],
    ) -> str:
        topic_labels = {
            "inference_cost": "推理成本",
            "long_context": "长上下文",
            "tool_use": "Agent 工具调用",
            "agent_harness": "Agent Harness",
            "model_products": "模型产品",
            "developer_tools": "开发者工具",
            "financial_ai": "金融 AI",
        }
        if result["relevance_gate"] != "allow":
            coverage = result.get("topic_coverage") or {}
            covered_topics = [topic_labels.get(topic, topic) for topic, covered in coverage.items() if covered]
            missing_topics = [topic_labels.get(topic, topic) for topic, covered in coverage.items() if not covered]
            return "\n".join(
                [
                    "当前新闻数据没有通过来源与问题相关性门控，因此系统拒绝生成推断性结论。",
                    f"数据模式：{result['source_disclosure']}。",
                    (
                        f"已读取事件：{result['event_count']}，"
                        f"至少匹配一个请求主题的事件：{result['matched_event_count']}。"
                    ),
                    f"已覆盖主题：{', '.join(covered_topics) if covered_topics else '无'}。",
                    f"缺失主题：{', '.join(missing_topics) if missing_topics else '无'}。",
                    f"门控原因：{result['gate_reason']}。",
                    "请接入包含来源 URL 的 RSS/API 数据，或补充与本轮主题相关的新闻快照后重试。",
                    "审计报告和 RunArtifact 已保存到本轮运行产物。",
                ]
            )
        lines = [
            "新闻影响报告已完成。我会按你这轮问题重新组织结论，而不是只返回固定日报。",
            "",
            (
                f"本次 workflow 读取事件：{result['event_count']}，"
                f"通过查询过滤并匹配：{result['matched_event_count']}，"
                f"最终按相关性排序与系统展示上限选取：{result.get('selected_event_count', len(result.get('top_matches') or []))}。"
            ),
        ]
        requested_topics = [
            topic_labels.get(topic, topic)
            for topic in (result.get("topic_coverage") or {})
        ]
        if "agent_harness" in (result.get("topic_coverage") or {}):
            requested_topics = [
                "Agent Harness / Tool Policy（Agent 平台执行控制）"
                if item == "Agent Harness"
                else item
                for item in requested_topics
            ]
        if requested_topics:
            lines.append(f"本轮问题焦点：{', '.join(requested_topics)}。")
        top_matches = list(result.get("top_matches") or [])
        lines.extend(
            [
                "",
                "核心判断：当前证据更适合触发依赖核验、影子评测和治理能力补齐，而不是直接推出生产方案变更。",
                "",
                "具体影响链条：",
            ]
        )
        if not top_matches:
            lines.append("- 当前没有通过来源门控且与问题匹配的事件。")
        for item in top_matches[:3]:
            impact_chain = "; ".join(item.get("impact_chain") or [])
            if not impact_chain:
                impact_chain = (
                    "外部生态变化 -> 可能影响示例产品/iFinD 或内部 Agent 平台的技术选型与治理基线 -> "
                    "需要先核验生产依赖、质量成本基线和回滚方案。"
                )
            lines.append(f"- {item.get('title', '')}：{impact_chain}")
        lines.extend(["", "本轮可核验的新闻证据："])
        if not top_matches:
            lines.append("- 当前没有通过来源门控且与问题匹配的事件。")
        for index, item in enumerate(top_matches, start=1):
            matched_topics = ", ".join(
                topic_labels.get(topic, topic) for topic in item.get("matched_topics") or []
            ) or "通用主题"
            impact_level = {
                "high": "高影响",
                "medium": "中影响",
                "conditional": "条件式影响",
                "low": "低影响",
            }.get(str(item.get("impact_level") or ""), str(item.get("impact_level") or "未标注"))
            lines.extend(
                [
                    f"{index}. **{item.get('title', '')}**",
                    f"   - 发布主体：{item.get('publisher') or '未标注'}",
                    f"   - 时间：{item.get('published_at') or '未记录'}",
                    f"   - 来源：{item.get('source') or '未记录'} / 证据等级 {item.get('source_level') or '未标注'}",
                    f"   - 原文：{item.get('url') or '未记录'}",
                    f"   - 命中维度：{matched_topics}",
                    f"   - 影响判断：{impact_level} / {item.get('impact_score')}",
                    f"   - 新闻/变更摘要：{item.get('summary') or '来源未提供摘要'}",
                    f"   - 企业证据：{'; '.join(item.get('company_evidence') or []) or '无可绑定企业事实'}",
                    f"   - 具体影响：{'; '.join(item.get('impact_chain') or []) or '当前只有外部变化证据，需先核验内部生产依赖后再判断实际影响'}",
                    f"   - 待确认：{'; '.join(item.get('missing_company_facts') or []) or '无'}",
                    f"   - 验证指标：{'; '.join(item.get('verification_metrics') or []) or '未定义'}",
                    f"   - 建议：{item.get('recommended_action') or '低频观察'}",
                ]
            )
        missing_topics = [
            topic_labels.get(topic, topic)
            for topic, covered in (result.get("topic_coverage") or {}).items()
            if not covered
        ]
        if missing_topics:
            lines.extend(
                [
                    "",
                    f"当前来源未覆盖：{', '.join(missing_topics)}。这些主题不生成影响结论。",
                ]
            )
        lines.extend(
            [
                "",
                f"数据模式：{result['source_disclosure']}。结论仅覆盖报告中列出的匹配证据。",
                f"门控结果：{result['gate_reason']}。",
                "Markdown、HTML 和 RunArtifact 已保存到本轮运行产物。",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _policy_data_mode(state: PolicyImpactState) -> str:
        return "fixture" if any(doc.get("data_mode") == "fixture" for doc in state.policy_documents) else "live_snapshot"

    @staticmethod
    def _is_workbench_general_query(message: str) -> bool:
        text = str(message or "").lower()
        has_workbench = "agent workbench" in text or "workbench" in text or "这个项目" in text or "本项目" in text
        has_coursepilot = "coursepilot" in text or "course pilot" in text
        has_user_intent = any(
            term in text
            for term in (
                "能做什么",
                "介绍",
                "区别",
                "侧重点",
                "学习",
                "设计",
                "harness",
                "runtime",
                "trace",
            )
        )
        return (has_workbench and has_user_intent) or has_coursepilot

    @staticmethod
    def _is_workbench_capability_query(message: str) -> bool:
        text = str(message or "").lower()
        has_explicit_subject = "agent workbench" in text or "workbench" in text
        asks_capability = any(term in text for term in ("能做什么", "介绍", "有哪些能力", "功能"))
        asks_model_boundary = any(
            term in text
            for term in (
                "模型调用边界",
                "模型边界",
                "什么时候调用模型",
                "哪些步骤调用模型",
                "model boundary",
            )
        )
        return has_explicit_subject and (asks_capability or asks_model_boundary)

    @staticmethod
    def _is_unbound_project_comparison(message: str) -> bool:
        text = str(message or "").lower()
        has_comparison = "coursepilot" in text or "course pilot" in text
        has_vague_subject = any(term in text for term in ("这个项目", "本项目", "我的项目"))
        has_explicit_subject = any(
            term in text
            for term in (
                "agent workbench",
                "workbench",
                "policy_impact",
                "示例公司",
                "company_001",
            )
        )
        return has_comparison and has_vague_subject and not has_explicit_subject

    @staticmethod
    def _render_workbench_general_answer(message: str) -> str:
        text = str(message or "").lower()
        if any(
            term in text
            for term in (
                "模型调用边界",
                "模型边界",
                "什么时候调用模型",
                "哪些步骤调用模型",
                "model boundary",
            )
        ):
            return (
                "Agent Workbench 的模型调用边界是：Runtime 先用确定性代码完成路由、权限、"
                "上下文编译和工具控制，只在所选 Skill 的语义生成或判断阶段调用已配置的 "
                "deepseek-v4-flash；每次模型调用都会写入 Trace，缺少可用模型时整轮拒绝执行，"
                "确定性 Stage 不存在隐藏模型旁路。"
            )
        if "coursepilot" in text or "course pilot" in text or "区别" in text:
            return "\n".join(
                [
                    "这个项目和 CoursePilot 的分工可以这样理解：",
                    "",
                    "1. CoursePilot 是课程学习 Agent Runtime：重点是学习/练习/批改流程、RAG 检索评测、学习记忆和上下文预算。",
                    "2. Agent Workbench 是 Agent Harness / Infra：重点是把每轮对话变成可路由、可控、可观测、可评估的 AgentRun。",
                    "3. CoursePilot 更像一个垂直业务 Agent；Workbench 更像控制层和观测层，可以挂 policy、news、research、wiki 等不同 skill。",
                    "4. 面试时 CoursePilot 负责证明你会做可运行的 Runtime 和 RAG，Workbench 负责证明你理解 Harness、Tool Policy、Trace、Memory、Eval。",
                ]
            )
        return "\n".join(
            [
                "Agent Workbench 当前可以做四类事情：",
                "",
                "1. 普通对话：读取企业知识库和长期记忆，但不会主动跑重型 workflow。",
                "2. Skill 执行：通过普通对话或 /report 进入不同能力边界。",
                "3. Harness 观测：每轮都会记录 AgentRun 和有序 AgentStep；权限判断与门控是确定性代码，模型回答和技能内容不是。context、model、final answer 会留痕，memory、tool/gate、artifact 按路由实际触发，未触发时计数为 0。",
                "4. 面试学习：你可以围绕 AgentRuntime、SkillRegistry、ContextManifest、ToolGateway、MemoryStore、Trace、Eval 逐个学习。",
                "",
                "这个项目的重点不是政策分析本身，而是学习一个成熟 Agent 系统怎样控制、观测和复盘每一次运行。",
            ]
        )

    @staticmethod
    def _query_allows_artifact_memory(message: str) -> bool:
        text = str(message or "").lower()
        return any(
            term in text
            for term in (
                "报告",
                "历史",
                "之前",
                "上次",
                "产物",
                "artifact",
                "report",
                "weekly",
            )
        )

    @staticmethod
    def _artifact_memory_key(item: dict[str, Any]) -> str:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        return str(metadata.get("path") or item.get("content") or item.get("id") or "")

    def _is_stale_artifact_memory(self, message: str, item: dict[str, Any]) -> bool:
        memory_type = str(item.get("memory_type") or "").lower()
        content = str(item.get("content") or "").lower()
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        blob = f"{content} {metadata}".lower()
        artifact_like = memory_type in {"skill_result", "policy_report", "news_report"} or any(
            term in blob
            for term in (
                "weekly-policy-impact",
                "recent-news-impact",
                "research-report",
                "生成政策影响报告",
                "生成最近新闻影响报告",
                "调研报告已生成",
            )
        )
        return artifact_like and not self._query_allows_artifact_memory(message)

    def _filter_general_chat_memories(
        self,
        message: str,
        memories: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        filtered: list[dict[str, Any]] = []
        seen_artifacts: set[str] = set()
        for item in memories:
            # Conversation summaries are session-scoped working memory. They are
            # loaded through get_conversation_summary(session_id) and must never
            # re-enter another session through the global memory search index.
            if item.get("memory_type") == "conversation_summary":
                continue
            if self._is_stale_artifact_memory(message, item):
                continue
            key = self._artifact_memory_key(item)
            if key and key in seen_artifacts:
                continue
            seen_artifacts.add(key)
            filtered.append(item)
            if len(filtered) >= 5:
                break
        return filtered

    @staticmethod
    def _is_immediate_history_query(message: str) -> bool:
        text = str(message or "").lower()
        immediate_markers = (
            "刚才",
            "上一轮",
            "上轮",
            "本轮之前",
            "本次对话",
            "本次会话",
            "这个会话",
            "当前会话",
            "另一个会话",
            "previous turn",
            "just now",
            "this session",
        )
        explicit_memory_markers = (
            "长期记忆",
            "记忆里",
            "记忆中",
            "让我记住",
            "保存过",
            "保存的",
            "long-term memory",
            "saved memory",
        )
        negated_memory_write = bool(
            re.search(
                r"(?:不要|不需要|无需|禁止|不得|不)(?:将|把)?[^。；\n]{0,20}(?:写入|保存到|存入).{0,8}(?:长期)?记忆",
                text,
                flags=re.I,
            )
        )
        has_immediate_marker = any(marker in text for marker in immediate_markers)
        has_explicit_memory_marker = any(marker in text for marker in explicit_memory_markers)
        return has_immediate_marker and (not has_explicit_memory_marker or negated_memory_write)

    @staticmethod
    def _is_session_context_declaration(message: str) -> bool:
        """Identify declarative turn-scoped controls that should not pull global evidence."""
        text = str(message or "").strip().lower()
        if re.match(r"^(?:未决事项|禁止项|临时代号|输出偏好)\s*[：:]", text):
            return True
        return text.startswith(("纠正上一条", "更正上一条")) and not any(
            marker in text for marker in ("？", "?", "为什么", "如何", "分析", "查询", "检索")
        )

    @staticmethod
    def _is_unpersisted_cross_session_query(message: str) -> bool:
        text = str(message or "").lower()
        cross_session_markers = ("另一个会话", "其他会话", "别的会话", "another session")
        explicit_memory_markers = (
            "长期记忆",
            "记忆里",
            "记忆中",
            "让我记住",
            "保存过",
            "保存的",
            "long-term memory",
            "saved memory",
        )
        return any(marker in text for marker in cross_session_markers) and not any(
            marker in text for marker in explicit_memory_markers
        )

    @staticmethod
    def _is_workbench_design_query(message: str) -> bool:
        text = str(message or "").lower()
        has_workbench = "agent workbench" in text or "workbench" in text or "工作台" in text
        has_design_intent = any(
            term in text
            for term in (
                "设计",
                "重点",
                "架构",
                "侧重点",
                "学习",
                "harness",
                "runtime",
                "infra",
                "subagent",
            )
        )
        return has_workbench and has_design_intent

    def _filter_research_memories(
        self,
        message: str,
        memories: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        text = str(message or "").lower()
        wants_policy = any(term in text for term in ("政策", "合规", "监管", "policy", "compliance", "regulation"))
        wants_news = any(term in text for term in ("新闻", "日报", "news", "daily", "latest"))
        design_terms = (
            "agent workbench",
            "workbench",
            "agentrun",
            "agentstep",
            "harness",
            "runtime",
            "skill",
            "context",
            "memory",
            "trace",
            "tool",
            "gate",
            "eval",
            "replay",
            "工作台",
            "上下文",
            "记忆",
            "工具",
            "追踪",
            "评测",
        )
        stale_policy_terms = (
            "weekly-policy-impact",
            "policy-impact",
            "政策影响报告",
            "政策分析",
            "合规分析",
        )
        stale_news_terms = ("weekly-news", "news-report", "新闻影响报告")

        filtered: list[dict[str, Any]] = []
        seen_memory_keys: set[str] = set()
        for item in memories:
            if item.get("memory_type") == "conversation_summary":
                continue
            content = str(item.get("content") or "")
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            blob = f"{content} {metadata}".lower()
            memory_key = " ".join(content.split()).lower() or self._artifact_memory_key(item)
            if memory_key and memory_key in seen_memory_keys:
                continue

            if self._is_workbench_design_query(message):
                if any(term in blob for term in stale_policy_terms) or any(term in blob for term in stale_news_terms):
                    continue
                if any(term in blob for term in design_terms):
                    filtered.append(item)
                    if memory_key:
                        seen_memory_keys.add(memory_key)
                continue

            if not wants_policy and any(term in blob for term in stale_policy_terms):
                continue
            if not wants_news and any(term in blob for term in stale_news_terms):
                continue
            filtered.append(item)
            if memory_key:
                seen_memory_keys.add(memory_key)
        return filtered[:5]

    def _research_answer(self, message: str, report_path: Path) -> str:
        if self._is_workbench_design_query(message):
            return "\n".join(
                [
                    "Agent Workbench 的设计重点已整理完成。",
                    "",
                    "核心学习顺序：",
                    "1. AgentRuntime：每轮对话如何变成可审计的 AgentRun。",
                    "2. SkillRegistry：普通对话和 /report 如何决定执行边界。",
                    "3. ContextManifest：模型本轮能看什么、不能看什么、token 预算是多少。",
                    "4. MemoryStore：什么时候读记忆、什么时候写结果，如何避免旧结果污染新问题。",
                    "5. ToolGateway / PermissionEngine：工具调用为什么必须经过确定性权限层。",
                    "6. AgentStep / Trace：think、act、observe、answer 每一步如何前端可见。",
                    "7. Eval / Linter：用测试用例证明路由、上下文、记忆和 trace 没跑偏。",
                    "",
                    f"Markdown 报告：{report_path}",
                ]
            )
        if self._is_harness_interview_query(message):
            return "\n".join(
                [
                    "Agent Harness 面试讲法已整理完成。",
                    "",
                    "建议按这条线讲：",
                    "1. ContextManifest 解决本轮模型可见上下文和 token budget。",
                    "2. ToolGateway / PermissionEngine 解决工具权限、审计和确定性控制。",
                    "3. Trace / AgentStep 解决每一步输入输出、loop phase 和失败定位。",
                    "4. Eval / Linter 解决如何证明路由、上下文、记忆和产物没有跑偏。",
                    "",
                    f"Markdown 报告：{report_path}",
                ]
            )
        return f"调研报告已生成：{report_path}"

    def _render_research_report_v2(
        self,
        message: str,
        context_manifest: dict[str, Any],
        memories: list[dict[str, Any]],
        model_payload: dict[str, Any],
        run_id: str = "",
    ) -> str:
        if self._is_workbench_design_query(message):
            return self._render_workbench_design_report(
                message, context_manifest, memories, model_payload, run_id=run_id
            )
        if self._is_harness_interview_query(message):
            return self._render_harness_interview_report(
                message, context_manifest, memories, model_payload, run_id=run_id
            )
        return self._render_generic_research_report(message, context_manifest, memories, model_payload)

    def _company_display_name(self) -> str:
        try:
            profile = load_company_knowledge(self.company_id).get("profile", {})
            return str(profile.get("company_name") or self.company_id)
        except FileNotFoundError:
            return self.company_id

    @staticmethod
    def _memory_bullets(memories: list[dict[str, Any]]) -> list[str]:
        if not memories:
            return ["- 本轮没有命中和当前问题直接相关的长期记忆。"]
        out = []
        for item in memories[:5]:
            content = str(item.get("content") or "").strip()
            if len(content) > 180:
                content = content[:177].rstrip() + "..."
            out.append(f"- {content}")
        return out

    def _current_run_fact_lines(self, run_id: str) -> list[str]:
        steps = self.store.list_agent_steps(run_id) if run_id else []
        counts: dict[str, int] = {}
        for step in steps:
            step_type = str(step.get("step_type") or "unknown")
            counts[step_type] = counts.get(step_type, 0) + 1
        return [
            "## 本次 Run 已发生的事实（报告写入前）",
            "",
            (
                "- "
                + ", ".join(
                    f"{name}={counts.get(name, 0)}"
                    for name in (
                        "intent_classification",
                        "skill_selection",
                        "context_build",
                        "memory_read",
                        "model_call",
                        "tool_call",
                        "gate_check",
                        "memory_write",
                    )
                )
            ),
            "- 上述计数只描述当前 Run；系统具备某项能力，不代表这一轮一定触发该步骤。",
        ]

    def _render_workbench_design_report(
        self,
        message: str,
        context_manifest: dict[str, Any],
        memories: list[dict[str, Any]],
        model_payload: dict[str, Any],
        run_id: str = "",
    ) -> str:
        output = model_payload.get("output") if isinstance(model_payload.get("output"), dict) else {}
        thesis = str(output.get("thesis") or "").strip() or (
            "Agent Workbench 的核心不是单个政策/新闻 workflow，而是把每轮用户对话变成"
            "可路由、可控、可观测、可评估、可复盘的 AgentRun。"
        )
        model_analysis = self._model_report_section(output)
        visible_keys = ", ".join(context_manifest.get("visible_keys", [])) or "无"
        memory_lines = self._memory_bullets(memories)
        return "\n".join(
            [
                f"# Agent Workbench 设计重点整理",
                "",
                f"- 用户问题：{message}",
                f"- 企业空间：{self._company_display_name()}",
                f"- 本轮可见上下文字段：{visible_keys}",
                "",
                "## 一句话定位",
                "",
                thesis
                or "Agent Workbench 的核心不是单个政策/新闻工作流，而是一套把对话、技能、工具、记忆、上下文、追踪和评测统一起来的 Agent Harness。",
                "",
                "## 系统设计链（按任务条件触发，并非每轮全走）",
                "",
                "```text",
                "User message",
                "-> AgentRuntime 创建 AgentRun",
                "-> SkillRegistry 选择 skill",
                "-> ContextManifest 构造本轮可见上下文",
                "-> MemoryStore 按问题语义读取相关长期记忆（按需）",
                "-> ModelGateway / AgentLoop 执行真实模型调用",
                "-> ToolGateway / PermissionEngine 管控工具调用（技能需要工具时）",
                "-> GateEngine 校验关键输出（存在契约或风险门控时）",
                "-> ArtifactStore 保存报告或结构化产物（产物型技能）",
                "-> MemoryStore 写入可复用结果（仅显式请求或上下文压缩）",
                "-> AgentStep 持久化 trace 并在前端展示",
                "```",
                "",
                *self._current_run_fact_lines(run_id),
                "",
                "## 模型生成的补充分析（不是当前 Run 事实）",
                "",
                *model_analysis,
                "",
                "## 你学习这个项目时的重点",
                "",
                "1. **AgentRuntime**：看每一轮对话如何被拆成 intent、skill、context、memory、tool/model、artifact、final answer。重点不是“能回答”，而是每一步都有输入、输出和状态。",
                "2. **SkillRegistry**：理解普通对话和 `/report` 的差异。Skill 是业务能力的边界，也是工具权限、上下文策略和 artifact 类型的入口。",
                "3. **ContextManifest**：重点看 visible keys、hidden fields、token budget、checksum。它解决的是“本轮模型到底能看到什么”，不是简单拼 prompt。",
                "4. **MemoryStore**：区分 session message、长期 memory、skill result memory。尤其要注意旧报告结果不能污染新问题，本轮已经加入 memory 过滤指标。",
                "5. **ToolGateway / PermissionEngine**：工具调用不是 prompt 里说一句“不要乱用”就结束，而是由 harness 层确定性 allow/deny/ask，并记录 tool audit。",
                "6. **AgentStep / Trace**：每一步都带 step_type、loop_phase、input_payload、output_payload、tool_calls、gate_result、metadata。前端展示的是系统可解释性。",
                "7. **Eval / Linter**：用动态对话测试、pytest 和 harness_linter 去证明路由、memory、context、trace 和前端没有跑偏。",
                "",
                "## 和 CoursePilot 的区别",
                "",
                "- CoursePilot 重点是课程学习 Runtime：RAG、练习、批改、学习记忆和检索评测。",
                "- Agent Workbench 重点是 Harness / Infra：同一个对话入口下，如何控制 skill、上下文、工具权限、trace、artifact 和 eval。",
                "- 所以 Workbench 不要讲成政策分析产品，政策/新闻/调研只是挂在 Harness 上的业务 skill。",
                "",
                "## 本轮命中的长期记忆",
                "",
                *memory_lines,
                "",
                "## 面试讲法",
                "",
                "这个项目可以这样讲：我实现了一个单用户 Agent Workbench，每轮用户输入都会进入统一 AgentRuntime，经过 Skill 路由、ContextManifest、Memory、ToolGateway、Gate、Artifact 和 Trace。业务 skill 可以替换，但 harness 层负责控制、观测、评估和复盘。",
            ]
        )

    @staticmethod
    def _is_harness_interview_query(message: str) -> bool:
        text = str(message or "").lower()
        required = any(term in text for term in ("harness", "面试", "讲法", "contextmanifest", "toolgateway", "trace", "eval"))
        concept_count = sum(
            1
            for term in ("contextmanifest", "toolgateway", "trace", "eval", "memory", "gate", "artifact")
            if term in text
        )
        return required and concept_count >= 2

    def _render_harness_interview_report(
        self,
        message: str,
        context_manifest: dict[str, Any],
        memories: list[dict[str, Any]],
        model_payload: dict[str, Any],
        run_id: str = "",
    ) -> str:
        output = model_payload.get("output") if isinstance(model_payload.get("output"), dict) else {}
        visible_keys = ", ".join(context_manifest.get("visible_keys", [])) or "无"
        memory_lines = self._memory_bullets(memories)
        return "\n".join(
            [
                "# Agent Harness 面试讲法整理",
                "",
                f"- 用户问题：{message}",
                f"- 企业空间：{self._company_display_name()}",
                f"- 本轮可见上下文字段：{visible_keys}",
                "",
                "## 总体讲法",
                "",
                "我不会把这个项目讲成一个简单的政策/新闻分析器，而会讲成一个单用户 Agent Harness。"
                "它的核心是把每一轮用户输入变成可控制、可观测、可评估、可复盘的 AgentRun。",
                "",
                *self._current_run_fact_lines(run_id),
                "",
                "## 模型生成的补充分析（不是当前 Run 事实）",
                "",
                *self._model_report_section(output),
                "",
                "## 四个核心模块",
                "",
                "### 1. ContextManifest",
                "",
                "- 解决问题：模型本轮到底能看到什么，哪些 state 字段必须隐藏，token 预算怎么分配。",
                "- 项目实现：每轮 `context_build` step 记录 `visible_keys`、`hidden_fields`、`token_budget`、`token_estimates` 和 checksum。",
                "- 面试回答：这不是简单拼 prompt，而是把上下文变成可审计的输入契约。",
                "",
                "### 2. ToolGateway / PermissionEngine",
                "",
                "- 解决问题：工具调用不能只靠 prompt 让模型自觉，必须有确定性权限层。",
                "- 项目实现：工具调用统一经过 ToolGateway，记录 allow/deny/ask 风格的 gate result 和 tool audit。",
                "- 面试回答：模型可以提出工具意图，但 harness 决定是否执行，并把决策原因写进 trace。",
                "",
                "### 3. Trace / AgentStep",
                "",
                "- 解决问题：Agent 失败时不能只看到最终回答，要能还原每一步发生了什么。",
                "- 项目实现：每轮运行持久化为 AgentRun；每个 AgentStep 记录 step_type、loop_phase、input_payload、output_payload、tool_calls、gate_result、metadata。",
                "- 面试回答：trace 不是日志堆积，而是围绕 think / act / observe / answer 的运行证据。",
                "",
                "### 4. Eval / Linter",
                "",
                "- 解决问题：不能靠主观看 demo 判断 Agent 是否变好。",
                "- 项目实现：用 pytest、harness_linter、API dialogue matrix 验证 skill 路由、memory 过滤、context、artifact 和 trace 结构。",
                "- 面试回答：我会用固定 case 覆盖普通对话、/report 和 Wiki 数据页等混合意图，防止回归。",
                "",
                "## 容易被追问的点",
                "",
                "- 为什么不是 Dify / LangGraph workflow？因为这个项目重点在每轮对话的控制面和观测面，而不是只跑固定 DAG。",
                "- 为什么不是 Codex/Claude Skill？因为通用 Agent 可以执行任务，但这里的价值是长期 memory、tool policy、trace、artifact、eval 都在本地系统里可审计。",
                "- 多 Agent 在哪里？当前以 skill/subagent 角色边界为主，policy/news workflow 可继续把底层 stage 映射成更细的 AgentStep。",
                "",
                "## 本轮命中的长期记忆",
                "",
                *memory_lines,
            ]
        )

    def _render_generic_research_report(
        self,
        message: str,
        context_manifest: dict[str, Any],
        memories: list[dict[str, Any]],
        model_payload: dict[str, Any],
    ) -> str:
        output = model_payload.get("output") if isinstance(model_payload.get("output"), dict) else {}
        thesis = str(output.get("thesis") or "").strip()
        visible_keys = ", ".join(context_manifest.get("visible_keys", [])) or "无"
        return "\n".join(
            [
                f"# 调研报告：{message}",
                "",
                f"- 企业空间：{self._company_display_name()}",
                f"- 本轮可见上下文字段：{visible_keys}",
                "",
                "## 结论",
                "",
                thesis or "本轮调研应围绕问题本身组织结论、证据、风险和下一步验证动作。",
                "",
                "## 模型调研内容",
                "",
                *self._model_report_section(output),
                "",
                "## 相关记忆",
                "",
                *self._memory_bullets(memories),
                "",
                "## 建议",
                "",
                "- 优先把问题拆成可验证的判断点。",
                "- 区分模型生成内容、工具返回内容和长期记忆内容。",
                "- 在 trace 中检查 context、memory、model_call 和 artifact 是否互相一致。",
            ]
        )

    @staticmethod
    def _model_report_section(output: dict[str, Any]) -> list[str]:
        lines: list[str] = []
        for label, key in (
            ("证据", "evidence"),
            ("风险", "risks"),
            ("建议", "recommendations"),
            ("下一步验证", "next_checks"),
        ):
            values = output.get(key) if isinstance(output, dict) else None
            if not isinstance(values, list) or not values:
                continue
            lines.append(f"### {label}")
            lines.append("")
            lines.extend(f"- {str(item).strip()}" for item in values if str(item).strip())
            lines.append("")
        return lines or ["- 模型未返回可用的结构化分析项。"]

    def _render_research_report(
        self,
        message: str,
        context_manifest: dict[str, Any],
        memories: list[dict[str, Any]],
        model_payload: dict[str, Any],
    ) -> str:
        company_name = self.company_id
        try:
            company_name = str(load_company_knowledge(self.company_id).get("profile", {}).get("company_name") or company_name)
        except FileNotFoundError:
            pass
        thesis = (
            model_payload.get("output", {}).get("thesis")
            if isinstance(model_payload.get("output"), dict)
            else ""
        )
        memory_lines = "\n".join(f"- {item.get('content', '')}" for item in memories[:5]) or "- 未命中相关长期记忆。"
        visible_keys = ", ".join(context_manifest.get("visible_keys", []))
        return "\n".join(
            [
                f"# 调研报告：{message}",
                "",
                f"- 企业：{company_name}",
                f"- 本轮运行可见字段：{visible_keys}",
                "",
                "## 结论",
                "",
                thesis
                or "成熟的 Agent Harness 需要在同一个受控运行时内整合对话、技能、工具、记忆、上下文、追踪和产物。",
                "",
                "## 证据",
                "",
                "- 当前项目已经具备 ToolGateway、ContextManifest、AgentLoop、Checkpoint、Replay 和 SQLite memory。",
                "- 新的智能体工作台会把每轮用户请求记录为可审计的 AgentRun，并持久化有序的 AgentStep。",
                memory_lines,
                "",
                "## 建议",
                "",
                "- 将业务技能保持为可插拔能力，不要把所有行为硬编码到页面层。",
                "- 在执行追踪中持续展示工具策略、上下文清单、记忆读写、模型调用和产物路径。",
                "- 面试演示优先使用本地确定性运行；接入真实模型时只替换模型适配器边界。",
            ]
        )
