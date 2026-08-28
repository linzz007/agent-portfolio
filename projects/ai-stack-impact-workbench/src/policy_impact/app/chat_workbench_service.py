"""Application service for the single-user Agent Workbench."""

from __future__ import annotations

from collections import Counter
from typing import Any, Callable

from policy_impact.harness.agent_runtime import AgentRuntime
from policy_impact.harness.model_config import (
    DEFAULT_MODEL_ID,
    default_model_config,
    ensure_default_model_available,
    model_runtime_status,
)
from policy_impact.harness.model_gateway import ModelAdapter
from policy_impact.company_wiki.loader import load_company_knowledge, update_wiki_page_body
from policy_impact.memory.store import PolicyMemoryStore
from policy_impact.runtime.turn_coordinator import RunExecutionLockRegistry
from policy_impact.skills.registry import SkillRegistry, compatibility_tool_policy


PUBLIC_WORKBENCH_SKILL_IDS = (
    "general_chat",
    "external_impact_report",
)


class ChatWorkbenchService:
    def __init__(
        self,
        registry: SkillRegistry | None = None,
        model_adapter_factory: Callable[[], ModelAdapter] | None = None,
    ) -> None:
        self.registry = registry or SkillRegistry()
        self.model_adapter_factory = model_adapter_factory
        self.run_locks = RunExecutionLockRegistry()

    def create_session(
        self,
        company_id: str,
        title: str,
        mode: str,
        model_id: str,
        active_skill_id: str,
    ) -> dict[str, Any]:
        self._validate_skill(active_skill_id)
        store = PolicyMemoryStore(company_id)
        store.seed_model_configs()
        model_id = self._resolve_model_id(store, model_id)
        session_id = store.create_chat_session(
            title=title,
            mode=mode,
            model_id=model_id,
            active_skill_id=active_skill_id,
        )
        session = store.get_chat_session(session_id)
        if not session:
            raise RuntimeError(f"failed to create chat session: {session_id}")
        return session

    def list_sessions(self, company_id: str) -> list[dict[str, Any]]:
        return PolicyMemoryStore(company_id).list_chat_sessions(status="active")

    def get_session(self, company_id: str, session_id: str) -> dict[str, Any]:
        session = PolicyMemoryStore(company_id).get_chat_session(session_id)
        if not session:
            raise KeyError(f"chat session not found: {session_id}")
        return session

    def update_session(
        self,
        company_id: str,
        session_id: str,
        title: str | None = None,
        mode: str | None = None,
        model_id: str | None = None,
        active_skill_id: str | None = None,
    ) -> dict[str, Any]:
        if active_skill_id is not None:
            self._validate_skill(active_skill_id)
        store = PolicyMemoryStore(company_id)
        if model_id is not None:
            model_id = self._resolve_model_id(store, model_id)
        store.update_chat_session_settings(
            session_id,
            title=title,
            mode=mode,
            model_id=model_id,
            active_skill_id=active_skill_id,
        )
        session = store.get_chat_session(session_id)
        if not session:
            raise KeyError(f"chat session not found: {session_id}")
        return session

    def list_messages(self, company_id: str, session_id: str) -> list[dict[str, Any]]:
        return PolicyMemoryStore(company_id).list_chat_messages(session_id)

    def send_message(self, company_id: str, session_id: str, message: str) -> dict[str, Any]:
        session = self.get_session(company_id, session_id)
        store = PolicyMemoryStore(company_id)
        model_id = self._resolve_model_id(store, str(session.get("model_id") or ""))
        if self.model_adapter_factory:
            adapter = self.model_adapter_factory()
        else:
            ensure_default_model_available()
            adapter = None
        runtime = AgentRuntime(
            company_id=company_id,
            registry=self.registry,
            model_adapter=adapter,
            run_locks=self.run_locks,
        )
        return runtime.run_turn(
            session_id=session_id,
            message=message,
            mode=session["mode"],
            model_id=model_id,
            active_skill_id=session["active_skill_id"],
        )

    def latest_trace(self, company_id: str, session_id: str) -> dict[str, Any]:
        store = PolicyMemoryStore(company_id)
        run = store.latest_agent_run(session_id)
        if not run:
            return {
                "run_id": "",
                "session_id": session_id,
                "steps": [],
                "observability_schema_version": "workbench.trace.v2",
                "trace_summary": self._empty_trace_summary(),
                "structured_events": [],
            }
        return self._trace_for_run(store, run)

    def trace_for_run(
        self,
        company_id: str,
        session_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        store = PolicyMemoryStore(company_id)
        run = store.get_agent_run(run_id)
        if not run or str(run.get("session_id") or "") != session_id:
            raise KeyError(f"agent run not found in session: {run_id}")
        return self._trace_for_run(store, run)

    @classmethod
    def _trace_for_run(
        cls,
        store: PolicyMemoryStore,
        run: dict[str, Any],
    ) -> dict[str, Any]:
        steps = store.list_agent_steps(run["run_id"])
        run["steps"] = steps
        run.update(cls._build_observability_view(run, steps))
        return run

    def list_models(self, company_id: str) -> list[dict[str, Any]]:
        status = model_runtime_status()
        model = self._redact_model_config(default_model_config())
        metadata = dict(model.get("metadata") or {})
        metadata.update(
            {
                "available": status["available"],
                "auth_configured": status["auth_configured"],
                "configured_model": status["configured_model"],
                "missing": status["missing"],
            }
        )
        model["metadata"] = metadata
        return [model]

    def save_model_config(self, company_id: str, config: dict[str, Any]) -> dict[str, Any]:
        raise ValueError(
            f"模型管理已禁用。当前 Workbench 固定使用 {DEFAULT_MODEL_ID}，"
            "请通过启动环境变量配置模型鉴权。"
        )

    def list_skills(self) -> list[dict[str, Any]]:
        return [
            {
                "id": skill.id,
                "name": skill.name,
                "description": skill.description,
                "mode": skill.execution_mode.value,
                "tool_policy": compatibility_tool_policy(skill),
                "artifact_types": list(
                    skill.output_schema.model_json_schema().get("artifact_types", [])
                ),
                "subagents": list(skill.allowed_subagents),
            }
            for skill in self.registry.list_manifests()
            if skill.id in PUBLIC_WORKBENCH_SKILL_IDS
        ]

    def wiki_tree(self, company_id: str) -> dict[str, Any]:
        knowledge = load_company_knowledge(company_id)
        PolicyMemoryStore(company_id).index_company_knowledge(
            dict(knowledge.get("profile") or {}),
            list(knowledge.get("pages") or []),
        )
        profile = dict(knowledge.get("profile") or {})
        pages = []
        for page in knowledge.get("pages") or []:
            meta = dict(page.get("meta") or {})
            facts = list(page.get("facts") or [])
            path = str(page.get("path") or "")
            title = str(meta.get("title") or path.rsplit("/", 1)[-1].removesuffix(".md") or path)
            pages.append(
                {
                    "path": path,
                    "parts": [part for part in path.split("/") if part],
                    "title": title,
                    "domain": meta.get("domain", ""),
                    "owner": meta.get("owner", ""),
                    "freshness": meta.get("freshness", ""),
                    "last_updated": meta.get("last_updated", ""),
                    "source": meta.get("source", ""),
                    "body": page.get("body") or "",
                    "fact_count": len(facts),
                    "facts": facts,
                }
            )
        facts = [fact for page in pages for fact in page["facts"]]
        return {
            "schema_version": "workbench.wiki_tree.v1",
            "company_id": company_id,
            "profile": profile,
            "pages": pages,
            "facts": facts,
            "stats": {
                "page_count": len(pages),
                "fact_count": len(facts),
                "high_importance_fact_count": sum(
                    1 for fact in facts if int(fact.get("importance") or 0) >= 4
                ),
                "open_question_count": sum(
                    1
                    for fact in facts
                    if any(
                        marker in str(fact.get("fact_id") or "").lower()
                        for marker in ("open", "question", "todo")
                    )
                ),
            },
        }

    def update_wiki_page(self, company_id: str, page_path: str, body: str) -> dict[str, Any]:
        page = update_wiki_page_body(company_id, page_path, body)
        knowledge = load_company_knowledge(company_id)
        PolicyMemoryStore(company_id).index_company_knowledge(
            dict(knowledge.get("profile") or {}),
            list(knowledge.get("pages") or []),
        )
        return {
            "schema_version": "workbench.wiki_update.v1",
            "company_id": company_id,
            "page": {
                "path": page.get("path", ""),
                "title": (page.get("meta") or {}).get("title", "")
                or str(page.get("path") or "").rsplit("/", 1)[-1].removesuffix(".md"),
                "fact_count": len(page.get("facts") or []),
            },
            "wiki": self.wiki_tree(company_id),
        }

    def _validate_skill(self, active_skill_id: str) -> None:
        if active_skill_id:
            self.registry.get_manifest(active_skill_id)

    @staticmethod
    def _resolve_model_id(store: PolicyMemoryStore, model_id: str) -> str:
        selected = str(model_id or "").strip()
        if selected and selected != DEFAULT_MODEL_ID:
            raise ValueError(f"模型不存在或不可用：{selected}。当前只允许 {DEFAULT_MODEL_ID}。")
        if not store.get_model_config(DEFAULT_MODEL_ID):
            raise ValueError(f"没有可用模型，无法执行。当前只允许 {DEFAULT_MODEL_ID}。")
        return DEFAULT_MODEL_ID

    @staticmethod
    def _redact_model_config(config: dict[str, Any]) -> dict[str, Any]:
        redacted = dict(config)
        metadata = dict(redacted.get("metadata") or {})
        has_api_key = bool(metadata.pop("api_key", ""))
        if has_api_key:
            metadata["has_api_key"] = True
        redacted["metadata"] = metadata
        return redacted

    @classmethod
    def _build_observability_view(cls, run: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, Any]:
        tool_calls = [call for step in steps for call in (step.get("tool_calls") or [])]
        gate_steps = [step for step in steps if step.get("gate_result")]
        context_summary = cls._summarize_context(steps)
        memory_summary = cls._summarize_memory(steps)
        model_summary = cls._summarize_model_calls(steps)
        source_summary = cls._summarize_sources(steps)
        subagent_summary = cls._summarize_subagents(run, steps)
        tool_summary = cls._summarize_tools(tool_calls)
        gate_summary = cls._summarize_gates(gate_steps)
        artifact_summary = cls._summarize_artifacts(run, steps)
        structured_events = cls._build_structured_events(run, steps)
        step_types = Counter(str(step.get("step_type") or "unknown") for step in steps)
        loop_phases = Counter(str((step.get("metadata") or {}).get("loop_phase") or "unknown") for step in steps)

        trace_summary = {
            "run_id": run.get("run_id", ""),
            "status": run.get("status", ""),
            "model_id": run.get("model_id", DEFAULT_MODEL_ID),
            "selected_model_id": run.get("model_id", DEFAULT_MODEL_ID),
            "model_call_count": model_summary.get("call_count", 0),
            "model_used": bool(model_summary.get("call_count")),
            "execution_mode": (
                "model_assisted" if model_summary.get("call_count") else "deterministic"
            ),
            "skill_id": run.get("skill_id", ""),
            "total_steps": len(steps),
            "completed_steps": sum(1 for step in steps if step.get("status") == "done"),
            "failed_steps": sum(1 for step in steps if step.get("status") == "failed"),
            "step_types": dict(step_types),
            "loop_phases": dict(loop_phases),
            "tool_call_count": tool_summary["total_calls"],
            "gate_count": gate_summary["total_gates"],
            "artifact_count": artifact_summary["total_output_artifacts"],
            "artifact_count_scope": artifact_summary["scope"],
            "subagent_plan_count": subagent_summary["plan_count"],
            "subagent_call_count": subagent_summary["delegate_count"],
            "subagent_roles": subagent_summary["roles"],
            "memory_read_count": memory_summary["read_steps"],
            "memory_write_count": memory_summary["actual_write_count"],
            "memory_write_attempt_count": memory_summary["write_steps"],
            "has_context_manifest": bool(context_summary.get("manifest_id")),
            "has_prompt_contract": bool(model_summary.get("prompt_contract_ids")),
            "has_source_summary": bool(source_summary.get("route") or source_summary.get("news_source_count") or source_summary.get("policy_source_count")),
            "structured_event_count": len(structured_events),
        }
        return {
            "observability_schema_version": "workbench.trace.v2",
            "trace_summary": trace_summary,
            "context_summary": context_summary,
            "memory_summary": memory_summary,
            "model_summary": model_summary,
            "source_summary": source_summary,
            "subagent_summary": subagent_summary,
            "tool_summary": tool_summary,
            "gate_summary": gate_summary,
            "artifact_summary": artifact_summary,
            "structured_events": structured_events,
        }

    @staticmethod
    def _empty_trace_summary() -> dict[str, Any]:
        return {
            "run_id": "",
            "status": "",
            "model_id": DEFAULT_MODEL_ID,
            "selected_model_id": DEFAULT_MODEL_ID,
            "model_call_count": 0,
            "model_used": False,
            "execution_mode": "deterministic",
            "skill_id": "",
            "total_steps": 0,
            "completed_steps": 0,
            "failed_steps": 0,
            "step_types": {},
            "loop_phases": {},
            "tool_call_count": 0,
            "gate_count": 0,
            "artifact_count": 0,
            "artifact_count_scope": "user_visible_output_artifacts",
            "subagent_plan_count": 0,
            "subagent_call_count": 0,
            "subagent_roles": [],
            "memory_read_count": 0,
            "memory_write_count": 0,
            "memory_write_attempt_count": 0,
            "has_context_manifest": False,
            "has_prompt_contract": False,
            "structured_event_count": 0,
        }

    @staticmethod
    def _summarize_context(steps: list[dict[str, Any]]) -> dict[str, Any]:
        context_steps = [step for step in steps if step.get("step_type") == "context_build"]
        if not context_steps:
            return {}
        manifest = (context_steps[-1].get("output_payload") or {}).get("context_manifest") or {}
        token_budget = manifest.get("token_budget") or {}
        token_estimates = manifest.get("token_estimates") or {}
        hidden_fields = manifest.get("hidden_fields") or {}
        visible_keys = manifest.get("visible_keys") or []
        visible_content = manifest.get("visible_content") or {}
        context_policy = visible_content.get("context_policy") or {}
        token_budget_total = sum(ChatWorkbenchService._numeric_values(token_budget))
        token_estimate_total = sum(ChatWorkbenchService._numeric_values(token_estimates))
        budget_utilization = round(token_estimate_total / token_budget_total, 4) if token_budget_total else 0.0
        hidden_field_keys = sorted(str(key) for key in hidden_fields.keys()) if isinstance(hidden_fields, dict) else []
        return {
            "manifest_id": manifest.get("manifest_id", ""),
            "stage_name": manifest.get("stage_name", ""),
            "agent_role": manifest.get("agent_role", ""),
            "visible_keys": list(visible_keys),
            "visible_key_count": len(visible_keys),
            "hidden_field_count": len(hidden_fields),
            "hidden_field_keys": hidden_field_keys,
            "compression_policy": context_policy.get("policy_id") or "stage_visible_fields",
            "context_policy": context_policy,
            "conversation_summary_present": bool(visible_content.get("conversation_summary")),
            "history_messages_included": len(visible_content.get("recent_history") or []),
            "company_facts_included": len(visible_content.get("company_facts") or []),
            "memory_items_included": len(visible_content.get("relevant_memories") or []),
            "budget_utilization": budget_utilization,
            "token_budget": token_budget,
            "token_budget_total": token_budget_total,
            "token_estimates": token_estimates,
            "token_estimate_total": token_estimate_total,
        }

    @staticmethod
    def _summarize_memory(steps: list[dict[str, Any]]) -> dict[str, Any]:
        reads = [step for step in steps if step.get("step_type") == "memory_read"]
        writes = [step for step in steps if step.get("step_type") == "memory_write"]
        read_items = []
        for step in reads:
            output = step.get("output_payload") or {}
            read_items.append(
                {
                    "query": (step.get("input_payload") or {}).get("query", ""),
                    "memory_count": int(output.get("memory_count") or output.get("count") or 0),
                    "raw_memory_count": int(output.get("raw_memory_count") or 0),
                    "filtered_out": int(output.get("filtered_out") or 0),
                }
            )
        write_items = []
        for step in writes:
            output = step.get("output_payload") or {}
            write_items.append(
                {
                    "memory_id": output.get("memory_id", ""),
                    "namespace": output.get("namespace", ""),
                    "memory_type": (step.get("input_payload") or {}).get("memory_type", ""),
                    "created": bool(output.get("created", True)),
                    "deduplicated": bool(output.get("deduplicated", False)),
                }
            )
        return {
            "read_steps": len(reads),
            "write_steps": len(writes),
            "actual_write_count": sum(1 for item in write_items if item["created"]),
            "deduplicated_write_count": sum(1 for item in write_items if item["deduplicated"]),
            "total_memory_hits": sum(item["memory_count"] for item in read_items),
            "total_raw_candidates": sum(item["raw_memory_count"] for item in read_items),
            "total_filtered_out": sum(item["filtered_out"] for item in read_items),
            "reads": read_items,
            "writes": write_items,
        }

    @staticmethod
    def _summarize_sources(steps: list[dict[str, Any]]) -> dict[str, Any]:
        route_steps = [
            step
            for step in steps
            if step.get("step_type") == "workflow_stage"
            and (
                (step.get("output_payload") or {}).get("internal_route")
                or (step.get("output_payload") or {}).get("selected_source_types")
                or str(step.get("title") or "") == "选择外部变化分析路线"
            )
        ]
        source_steps = [
            step
            for step in steps
            if step.get("step_type") == "tool_call"
            and (
                str((step.get("output_payload") or {}).get("news_source_count") or "")
                or str((step.get("output_payload") or {}).get("policy_source_count") or "")
                or str((step.get("output_payload") or {}).get("fallback_source_count") or "")
            )
        ]
        route_payload = (route_steps[-1].get("output_payload") if route_steps else {}) or {}
        source_payload = (source_steps[-1].get("output_payload") if source_steps else {}) or {}
        snapshots = source_payload.get("snapshots") or route_payload.get("snapshots") or {}
        return {
            "route": route_payload.get("internal_route") or "",
            "route_label": route_payload.get("route_label") or "",
            "selected_source_types": list(route_payload.get("selected_source_types") or []),
            "hidden_legacy_skills": list(route_payload.get("hidden_legacy_skills") or []),
            "news_source_count": int(source_payload.get("news_source_count") or 0),
            "enabled_news_source_count": int(source_payload.get("enabled_news_source_count") or 0),
            "policy_source_count": int(source_payload.get("policy_source_count") or 0),
            "enabled_policy_source_count": int(source_payload.get("enabled_policy_source_count") or 0),
            "fallback_source_count": int(source_payload.get("fallback_source_count") or 0),
            "snapshot_news_count": int((snapshots.get("news") or {}).get("item_count") or 0),
            "snapshot_policy_count": int((snapshots.get("policy") or {}).get("item_count") or 0),
            "snapshot_news_path": str((snapshots.get("news") or {}).get("snapshot_path") or ""),
            "snapshot_policy_path": str((snapshots.get("policy") or {}).get("snapshot_path") or ""),
            "snapshot_news_time": str((snapshots.get("news") or {}).get("captured_at") or ""),
            "snapshot_policy_time": str((snapshots.get("policy") or {}).get("captured_at") or ""),
        }

    @staticmethod
    def _summarize_subagents(run: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, Any]:
        plan_steps = [step for step in steps if step.get("step_type") == "subagent_plan"]
        delegate_steps = [step for step in steps if step.get("step_type") == "subagent_delegate"]
        roles: list[str] = []
        tasks: list[dict[str, Any]] = []
        for step in plan_steps:
            for task in (step.get("output_payload") or {}).get("tasks") or []:
                role = str(task.get("role") or "")
                if role and role not in roles:
                    roles.append(role)
                tasks.append(
                    {
                        "role": role,
                        "reason": task.get("reason", ""),
                        "trigger": task.get("trigger", ""),
                    }
                )
        delegate_results = []
        for step in delegate_steps:
            output = step.get("output_payload") or {}
            input_payload = step.get("input_payload") or {}
            role = str(output.get("role") or "")
            if role and role not in roles:
                roles.append(role)
            delegate_results.append(
                {
                    "role": role,
                    "status": step.get("status") or "",
                    "task_id": output.get("task_id", ""),
                    "reason": input_payload.get("reason", ""),
                    "trigger": input_payload.get("trigger", ""),
                    "stop_reason": output.get("stop_reason", ""),
                    "output_schema": output.get("output_schema", ""),
                    "output": output.get("output") or {},
                    "context_manifest_id": output.get("context_manifest_id", ""),
                }
            )
        metadata = run.get("metadata") or {}
        return {
            "plan_count": len(plan_steps),
            "delegate_count": len(delegate_steps),
            "successful_delegate_count": sum(1 for step in delegate_steps if step.get("status") == "done"),
            "failed_delegate_count": sum(1 for step in delegate_steps if step.get("status") == "failed"),
            "roles": roles,
            "tasks": tasks,
            "results": delegate_results,
            "metadata_delegation_count": int(metadata.get("delegation_count") or 0),
        }

    @staticmethod
    def _summarize_model_calls(steps: list[dict[str, Any]]) -> dict[str, Any]:
        model_steps = [step for step in steps if step.get("step_type") == "model_call"]
        model_ids: list[str] = []
        schemas: list[str] = []
        adapters: list[str] = []
        prompt_contract_ids: list[str] = []
        system_prompt_visible = False
        attempts: list[int] = []
        latencies: list[float] = []
        retry_errors: list[str] = []
        for step in model_steps:
            input_payload = step.get("input_payload") or {}
            metadata = step.get("metadata") or {}
            if input_payload.get("model_id"):
                model_ids.append(str(input_payload["model_id"]))
            if input_payload.get("schema_name"):
                schemas.append(str(input_payload["schema_name"]))
            if metadata.get("adapter"):
                adapters.append(str(metadata["adapter"]))
            attempts.append(int(metadata.get("attempts") or 1))
            if metadata.get("latency_ms") is not None:
                latencies.append(float(metadata["latency_ms"]))
            retry_errors.extend(str(item) for item in (metadata.get("retry_errors") or []))
            contract = input_payload.get("prompt_contract") or {}
            if contract.get("contract_id"):
                prompt_contract_ids.append(str(contract["contract_id"]))
            messages = input_payload.get("messages") or []
            system_prompt_visible = system_prompt_visible or any(
                isinstance(message, dict) and message.get("role") == "system" and bool(message.get("content"))
                for message in messages
            )
        return {
            "call_count": len(model_steps),
            "model_ids": sorted(set(model_ids)),
            "schema_names": sorted(set(schemas)),
            "adapters": sorted(set(adapters)),
            "prompt_contract_ids": sorted(set(prompt_contract_ids)),
            "system_prompt_visible": system_prompt_visible,
            "total_attempts": sum(attempts),
            "retried_call_count": sum(1 for value in attempts if value > 1),
            "total_latency_ms": round(sum(latencies), 2),
            "retry_errors": retry_errors,
        }

    @staticmethod
    def _summarize_tools(tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
        names = [str(call.get("tool_name") or call.get("name") or "unknown") for call in tool_calls]
        decisions = Counter(str(call.get("policy_decision") or call.get("decision") or "unknown") for call in tool_calls)
        failed = [
            call
            for call in tool_calls
            if str(call.get("status") or "success").lower() not in {"ok", "success", "done", "allowed"}
        ]
        denied = [
            call
            for call in tool_calls
            if call.get("allowed") is False or str(call.get("policy_decision") or "").lower() == "deny"
        ]
        return {
            "total_calls": len(tool_calls),
            "unique_tools": sorted(set(names)),
            "policy_decisions": dict(decisions),
            "failed_calls": len(failed),
            "denied_calls": len(denied),
        }

    @staticmethod
    def _summarize_gates(gate_steps: list[dict[str, Any]]) -> dict[str, Any]:
        gates = [step.get("gate_result") or {} for step in gate_steps]
        decisions = Counter(str(gate.get("decision") or "unknown") for gate in gates)
        return {
            "total_gates": len(gates),
            "decisions": dict(decisions),
            "blocked": decisions.get("deny", 0) + decisions.get("ask", 0),
        }

    @staticmethod
    def _summarize_artifacts(run: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, Any]:
        records = list(run.get("artifacts") or [])
        for step in steps:
            if step.get("step_type") != "artifact_write":
                continue
            artifacts = (step.get("output_payload") or {}).get("artifacts") or {}
            if isinstance(artifacts, dict):
                records.extend(
                    {"artifact_type": str(key), "path": value}
                    for key, value in artifacts.items()
                    if value
                )
        unique_records = []
        seen: set[tuple[str, str]] = set()
        for item in records:
            artifact_type = str(item.get("artifact_type") or item.get("type") or "artifact")
            path = str(item.get("path") or "")
            marker = (artifact_type, path)
            if marker in seen:
                continue
            seen.add(marker)
            unique_records.append({"artifact_type": artifact_type, "path": item.get("path")})
        by_type = Counter(item["artifact_type"] for item in unique_records)
        return {
            "scope": "user_visible_output_artifacts",
            "total_output_artifacts": len(unique_records),
            "total_artifacts": len(unique_records),
            "by_type": dict(by_type),
            "artifacts": unique_records,
        }

    @classmethod
    def _build_structured_events(cls, run: dict[str, Any], steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        events = []
        run_id = str(run.get("run_id") or "")
        session_id = str(run.get("session_id") or "")
        model_id = str(run.get("model_id") or DEFAULT_MODEL_ID)
        skill_id = str(run.get("skill_id") or "")
        for step in steps:
            metadata = step.get("metadata") or {}
            step_index = int(step.get("step_index") or 0)
            step_type = str(step.get("step_type") or "unknown")
            loop_phase = str(metadata.get("loop_phase") or "unknown")
            input_payload = step.get("input_payload") or {}
            output_payload = step.get("output_payload") or {}
            tool_calls = step.get("tool_calls") or []
            gate_result = step.get("gate_result") or {}
            event = {
                "event_id": f"{run_id}:{step_index:03d}",
                "run_id": run_id,
                "session_id": session_id,
                "step_index": step_index,
                "name": step.get("title") or step_type,
                "observation_type": cls._observation_type(step_type),
                "step_type": step_type,
                "loop_phase": loop_phase,
                "status": step.get("status") or "",
                "created_at": step.get("created_at") or "",
                "input_keys": cls._payload_keys(input_payload),
                "output_keys": cls._payload_keys(output_payload),
                "attributes": cls._event_attributes(
                    run_id=run_id,
                    session_id=session_id,
                    model_id=model_id,
                    skill_id=skill_id,
                    step_type=step_type,
                    loop_phase=loop_phase,
                    input_payload=input_payload,
                    output_payload=output_payload,
                    tool_calls=tool_calls,
                    gate_result=gate_result,
                    metadata=metadata,
                ),
            }
            events.append(event)
        return events

    @staticmethod
    def _observation_type(step_type: str) -> str:
        if step_type == "model_call":
            return "generation"
        if step_type == "tool_call":
            return "tool"
        if step_type == "memory_read":
            return "retrieval"
        if step_type == "gate_check":
            return "guardrail"
        if step_type == "artifact_write":
            return "artifact"
        if step_type in {"subagent_plan", "subagent_context", "subagent_delegate"}:
            return "subagent"
        return "span"

    @staticmethod
    def _payload_keys(payload: Any) -> list[str]:
        if not isinstance(payload, dict):
            return []
        return sorted(str(key) for key in payload.keys())

    @classmethod
    def _event_attributes(
        cls,
        *,
        run_id: str,
        session_id: str,
        model_id: str,
        skill_id: str,
        step_type: str,
        loop_phase: str,
        input_payload: dict[str, Any],
        output_payload: dict[str, Any],
        tool_calls: list[dict[str, Any]],
        gate_result: dict[str, Any],
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        attributes: dict[str, Any] = {
            "agent.run.id": run_id,
            "agent.session.id": session_id,
            "agent.skill.id": skill_id,
            "agent.loop.phase": loop_phase,
            "agent.step.type": step_type,
            "agent.model.selected": model_id,
        }
        if step_type == "context_build":
            manifest = output_payload.get("context_manifest") or {}
            visible_content = manifest.get("visible_content") or {}
            context_policy = visible_content.get("context_policy") or {}
            token_budget = manifest.get("token_budget") or {}
            token_estimates = manifest.get("token_estimates") or {}
            budget_total = sum(cls._numeric_values(token_budget))
            estimate_total = sum(cls._numeric_values(token_estimates))
            attributes.update(
                {
                    "agent.context.manifest_id": manifest.get("manifest_id", ""),
                    "agent.context.visible_key_count": len(manifest.get("visible_keys") or []),
                    "agent.context.hidden_field_count": len(manifest.get("hidden_fields") or {}),
                    "agent.context.token_budget_total": budget_total,
                    "agent.context.token_estimate_total": estimate_total,
                    "agent.context.budget_utilization": round(estimate_total / budget_total, 4)
                    if budget_total
                    else 0.0,
                    "agent.context.compression_policy": context_policy.get("policy_id")
                    or "stage_visible_fields",
                }
            )
        if step_type in {"memory_read", "memory_write"}:
            attributes.update(
                {
                    "agent.memory.operation": "read" if step_type == "memory_read" else "write",
                    "agent.memory.hit_count": int(output_payload.get("memory_count") or output_payload.get("count") or 0),
                    "agent.memory.raw_candidate_count": int(output_payload.get("raw_memory_count") or 0),
                    "agent.memory.filtered_out": int(output_payload.get("filtered_out") or 0),
                    "agent.memory.namespace": output_payload.get("namespace") or "",
                }
            )
        if step_type == "model_call":
            contract = input_payload.get("prompt_contract") or {}
            attributes.update(
                {
                    "gen_ai.provider.name": "anthropic-compatible",
                    "gen_ai.operation.name": "chat",
                    "gen_ai.request.model": input_payload.get("model_id") or model_id,
                    "gen_ai.request.temperature": metadata.get("temperature"),
                    "gen_ai.request.max_tokens": metadata.get("max_tokens"),
                    "agent.model.timeout_seconds": metadata.get("timeout_seconds"),
                    "agent.model.max_attempts": metadata.get("max_attempts"),
                    "agent.model.attempts": metadata.get("attempts", 1),
                    "agent.model.stop_reason": metadata.get("stop_reason", ""),
                    "agent.prompt.contract_id": contract.get("contract_id", ""),
                    "agent.prompt.schema_name": input_payload.get("schema_name", ""),
                    "agent.model.adapter": metadata.get("adapter", ""),
                }
            )
        if tool_calls:
            tool_names = [str(call.get("tool_name") or call.get("name") or "unknown") for call in tool_calls]
            decisions = Counter(str(call.get("policy_decision") or call.get("decision") or "unknown") for call in tool_calls)
            attributes.update(
                {
                    "gen_ai.tool.call.count": len(tool_calls),
                    "gen_ai.tool.name": sorted(set(tool_names)),
                    "agent.tool.policy_decisions": dict(decisions),
                }
            )
        if gate_result:
            attributes.update(
                {
                    "agent.guardrail.decision": gate_result.get("decision", ""),
                    "agent.guardrail.reason": gate_result.get("reason", ""),
                    "agent.guardrail.policy_id": gate_result.get("policy_id", ""),
                }
            )
        if step_type == "artifact_write":
            artifacts = output_payload.get("artifacts") or {}
            attributes.update(
                {
                    "agent.artifact.count": len(artifacts) if isinstance(artifacts, dict) else 0,
                    "agent.artifact.types": sorted(str(key) for key in artifacts.keys()) if isinstance(artifacts, dict) else [],
                }
            )
        if step_type in {"subagent_plan", "subagent_context", "subagent_delegate"}:
            tasks = output_payload.get("tasks") or []
            attributes.update(
                {
                    "agent.subagent.role": output_payload.get("role")
                    or input_payload.get("role")
                    or metadata.get("subagent_role")
                    or "",
                    "agent.subagent.task_count": len(tasks) if isinstance(tasks, list) else 0,
                    "agent.subagent.stop_reason": output_payload.get("stop_reason", ""),
                    "agent.subagent.dynamic": bool(metadata.get("dynamic_subagent")),
                }
            )
        return attributes

    @staticmethod
    def _numeric_values(value: Any) -> list[int]:
        if not isinstance(value, dict):
            return []
        result = []
        for item in value.values():
            if isinstance(item, (int, float)):
                result.append(int(item))
        return result
