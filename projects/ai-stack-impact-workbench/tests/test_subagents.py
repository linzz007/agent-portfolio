import copy
import json

import pytest
from pydantic import ValidationError

from policy_impact.company_wiki import loader as wiki_loader
from policy_impact.harness.model_gateway import ModelResponse, ScriptedModelAdapter
from policy_impact.harness.permissions import PermissionEngine, ToolPolicy
from policy_impact.harness.state import PolicyImpactState
from policy_impact.harness.subagents import SubagentDefinition, SubagentRunner
from policy_impact.harness.tool_gateway import TOOL_REGISTRY, ToolGateway, register_tool
from policy_impact.memory.store import PolicyMemoryStore
from policy_impact.runtime.subagent_outputs import (
    CollectorOutput,
    EditorOutput,
    SkepticOutput,
    VerifierOutput,
)
from policy_impact.runtime.gate_catalog import (
    AgentStepGateDecisionSink,
    build_default_gate_registry,
)
from policy_impact.runtime.gates import GateRunner
from policy_impact.runtime.subagent_roles import build_default_subagent_role_registry
from policy_impact.runtime.task_brief import SubagentResult, TaskBrief
from policy_impact.runtime.verifier_proof import (
    PolicyMemoryReviewGateResolver,
    PolicyMemoryVerifierGateResolver,
)
from policy_impact.skills.manifest import SkillManifest
from policy_impact.skills.registry import SkillRegistry


class RecordingAdapter:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.message_batches = []
        self.schema_names = []

    def complete(self, messages, schema_name):
        self.message_batches.append(copy.deepcopy(messages))
        self.schema_names.append(schema_name)
        response_index = len(self.message_batches)
        return ModelResponse(
            response_id=f"recording_{response_index}",
            payload=self.payloads[response_index - 1],
        )


def _gateway(stage_name: str, tool_name: str) -> ToolGateway:
    return ToolGateway(
        permission_engine=PermissionEngine(
            [ToolPolicy(stage_name, tool_name, "allow", "test allow")]
        )
    )


def _manifest(*, roles=(), tools=()) -> SkillManifest:
    base = SkillRegistry().get_manifest("policy_weekly_impact")
    return SkillManifest.model_validate(
        {
            **base.model_dump(),
            "allowed_subagents": tuple(roles),
            "allowed_tools": tuple(tools),
        }
    )


def _runner(adapter, *, roles, tools=(), gateway=None, verifier_gate_resolver=None) -> SubagentRunner:
    return SubagentRunner(
        model_adapter=adapter,
        tool_gateway=gateway or ToolGateway(permission_engine=PermissionEngine([])),
        skill_manifest=_manifest(roles=roles, tools=tools),
        verifier_gate_resolver=verifier_gate_resolver,
    )


def _persist_verifier_proof(monkeypatch, tmp_path, state, refs=("claim-1",)):
    monkeypatch.setattr(wiki_loader, "project_root", lambda: tmp_path, raising=False)
    store = PolicyMemoryStore(state.company_id)
    session_id = store.create_chat_session(
        title="Verifier proof",
        mode="skill",
        model_id="test-model",
        active_skill_id="policy_weekly_impact",
    )
    message_id = store.save_chat_message(session_id, "user", "verify claims")
    store.create_agent_run(
        session_id=session_id,
        user_message_id=message_id,
        mode="skill",
        model_id="test-model",
        skill_id="policy_weekly_impact",
        run_id=state.run_id,
    )
    assert store.claim_agent_run(state.run_id) is True
    review_resolver = PolicyMemoryReviewGateResolver(store)

    class TrustedVerifierExecution:
        def resolve_verifier_execution(
            self, *, audit_ref: str, run_id: str, claim_id: str
        ) -> bool:
            return (
                audit_ref == "delegation:verifier:test"
                and run_id == state.run_id
                and claim_id == refs[0]
            )

    runner = GateRunner(
        build_default_gate_registry(
            proof_resolver=review_resolver,
            verifier_execution_resolver=TrustedVerifierExecution(),
        ),
        AgentStepGateDecisionSink(store),
    )
    decision = runner.evaluate(
        run_id=state.run_id,
        span_id="span-verifier",
        gate_id="review_verdict",
        version="1.0.0",
        payload={
            "run_id": state.run_id,
            "claim_id": refs[0],
            "reviewer_role": "verifier",
            "verdict": "pass",
            "unresolved_skeptic_count": 0,
            "review_refs": ["review:verifier:test"],
            "claim_snapshot_hash": "claim-snapshot-test",
            "evidence_refs": ["policy:test:evidence"],
            "verifier_execution_audit_ref": "delegation:verifier:test",
        },
    )
    return PolicyMemoryVerifierGateResolver(store), decision.audit_ref


def test_task_brief_and_role_contracts_are_frozen_extra_forbid_and_tuple_safe():
    brief = TaskBrief(
        task_id="task-1",
        parent_run_id="run-1",
        parent_span_id="span-parent",
        role="collector",
        task="Collect official sources",
        input_artifact_refs=["artifact-1"],
        output_schema_name="evidence_collection.v1",
        max_steps=3,
        max_model_calls=2,
        timeout_seconds=30,
    )
    collector = build_default_subagent_role_registry().get("collector", "1.0.0")

    assert brief.input_artifact_refs == ("artifact-1",)
    assert isinstance(brief.input_artifact_refs, tuple)
    assert isinstance(collector.allowed_tools, tuple)
    assert brief.model_config["frozen"] is True
    assert brief.model_config["extra"] == "forbid"
    assert collector.model_config["frozen"] is True
    assert collector.model_config["extra"] == "forbid"
    assert collector.output_schema is CollectorOutput
    with pytest.raises(ValidationError, match="frozen"):
        brief.task = "mutated"
    with pytest.raises(AttributeError):
        brief.input_artifact_refs.append("artifact-2")
    with pytest.raises(ValidationError, match="extra_forbidden"):
        TaskBrief.model_validate({**brief.model_dump(), "hidden_history": []})


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("task_id", " "),
        ("parent_run_id", ""),
        ("parent_span_id", "\t"),
        ("role", " "),
        ("task", ""),
        ("output_schema_name", " "),
        ("max_steps", 0),
        ("max_steps", "3"),
        ("max_model_calls", -1),
        ("max_model_calls", "2"),
        ("timeout_seconds", 0),
        ("timeout_seconds", "30"),
        ("input_artifact_refs", ["artifact-1", " "]),
    ],
)
def test_task_brief_rejects_blank_identity_schema_and_invalid_budgets(field_name, value):
    payload = {
        "task_id": "task-1",
        "parent_run_id": "run-1",
        "parent_span_id": "span-parent",
        "role": "collector",
        "task": "Collect official sources",
        "input_artifact_refs": ("artifact-1",),
        "output_schema_name": "evidence_collection.v1",
        "max_steps": 3,
        "max_model_calls": 2,
        "timeout_seconds": 30,
        field_name: value,
    }

    with pytest.raises(ValidationError):
        TaskBrief.model_validate(payload)


def test_default_role_registry_has_exact_immutable_1_0_0_contracts():
    registry = build_default_subagent_role_registry()
    contracts = {
        role.role: (
            role.version,
            role.output_schema_name,
            role.allowed_tools,
            role.can_publish,
        )
        for role in registry.definitions()
    }

    assert contracts == {
        "collector": (
            "1.0.0",
            "evidence_collection.v1",
            ("artifact_read", "news.load_items", "policy_fetch_recent", "policy_retrieve"),
            False,
        ),
        "analyst": (
            "1.0.0",
            "claim_set.v1",
            ("artifact_read", "company_wiki_search", "memory_search", "policy_retrieve"),
            False,
        ),
        "skeptic": (
            "1.0.0",
            "counterexample_set.v1",
            ("artifact_read", "company_wiki_search", "policy_retrieve"),
            False,
        ),
        "verifier": (
            "1.0.0",
            "claim_verdict_set.v1",
            ("artifact_read", "policy_fetch_recent", "policy_retrieve"),
            False,
        ),
        "editor": (
            "1.0.0",
            "published_answer.v1",
            ("artifact_read", "report_write"),
            True,
        ),
    }
    with pytest.raises(TypeError):
        registry._definitions[("collector", "1.0.0")] = contracts["collector"]


def test_role_contract_budgets_are_strict_integers():
    collector = build_default_subagent_role_registry().get("collector", "1.0.0")

    for field_name in ("max_steps", "max_model_calls", "timeout_seconds"):
        with pytest.raises(ValidationError):
            type(collector).model_validate(
                {**collector.model_dump(), field_name: str(getattr(collector, field_name))}
            )


def test_subagent_receives_task_brief_and_refs_not_full_state_or_chat_history():
    state = PolicyImpactState(run_id="run-isolation")
    state.current_user_message = "CURRENT_SECRET"
    state.recent_history = [{"content": "SECRET_OLD_CHAT"}]
    state.company_context_pack = {"private": "COMPANY_SECRET"}
    adapter = RecordingAdapter(
        [
            {
                "type": "final",
                "output": {
                    "verdicts": [
                        {
                            "claim_ref": "claim-1",
                            "decision": "pass",
                            "evidence_refs": ["claim-1"],
                            "reason": "Official evidence matches.",
                        }
                    ]
                },
            }
        ]
    )
    runner = _runner(adapter, roles=("verifier",))

    result = runner.run(
        state,
        "verifier",
        task="核验 claim-1",
        input_artifact_refs=("claim-1",),
        parent_span_id="span-parent",
    )

    serialized = json.dumps(adapter.message_batches, ensure_ascii=False)
    assert "SECRET_OLD_CHAT" not in serialized
    assert "CURRENT_SECRET" not in serialized
    assert "COMPANY_SECRET" not in serialized
    assert "核验 claim-1" in serialized
    assert "claim-1" in serialized
    assert result.parent_span_id == "span-parent"
    assert result.span_id
    assert result.task_id
    manifest = state.context_manifests[0]
    assert manifest["metadata"]["visible_keys"] == ["task_brief", "input_artifact_refs"]
    assert set(manifest["metadata"]["visible_content"]) == {
        "task_brief",
        "input_artifact_refs",
    }
    assert manifest["metadata"]["role"] == "verifier"
    assert manifest["metadata"]["role_version"] == "1.0.0"
    assert manifest["metadata"]["parent_span_id"] == "span-parent"
    assert manifest["metadata"]["span_id"] == result.span_id
    assert state.delegation_evidence[0]["span_id"] == result.span_id
    assert state.delegation_evidence[0]["parent_run_id"] == state.run_id


def test_unlisted_subagent_role_is_denied_before_model_call():
    adapter = RecordingAdapter([])
    runner = _runner(adapter, roles=("collector",))

    with pytest.raises(PermissionError, match="not allowed"):
        runner.run(PolicyImpactState(run_id="run-role-deny"), "editor", task="publish")

    assert adapter.message_batches == []


def test_allowed_tools_are_exact_skill_and_role_intersection():
    state = PolicyImpactState(run_id="run-tool-intersection")
    allowed_tool = "policy_fetch_recent"
    adapter = RecordingAdapter(
        [
            {"type": "tool_call", "tool_name": allowed_tool, "arguments": {}},
            {
                "type": "final",
                "output": {
                    "evidence": [
                        {
                            "source_ref": "source-1",
                            "summary": "Official policy source.",
                            "source_url": "https://example.test/policy",
                        }
                    ]
                },
            },
        ]
    )
    register_tool(allowed_tool, lambda: {"source": "official"})
    gateway = _gateway("subagent.collector", allowed_tool)
    runner = _runner(
        adapter,
        roles=("collector",),
        tools=(allowed_tool, "report_write"),
        gateway=gateway,
    )

    try:
        result = runner.run(state, "collector", task="collect")
    finally:
        TOOL_REGISTRY.pop(allowed_tool, None)

    assert result.stop_reason == "success"
    assert len(gateway.calls) == 1
    assert state.context_manifests[0]["metadata"]["allowed_tools"] == [allowed_tool]


def test_empty_tool_intersection_means_zero_tools_without_gateway_fallback():
    state = PolicyImpactState(run_id="run-zero-tools")
    executed = []
    adapter = ScriptedModelAdapter(
        [{"type": "tool_call", "tool_name": "report_write", "arguments": {}}]
    )
    gateway = _gateway("subagent.collector", "report_write")
    register_tool("report_write", lambda: executed.append(True) or {"ok": True})
    runner = _runner(
        adapter,
        roles=("collector",),
        tools=("report_write",),
        gateway=gateway,
    )

    try:
        result = runner.run(state, "collector", task="collect")
    finally:
        TOOL_REGISTRY.pop("report_write", None)

    assert result.stop_reason == "tool_denied"
    assert executed == []
    assert gateway.calls == []
    assert state.context_manifests[0]["metadata"]["allowed_tools"] == []


@pytest.mark.parametrize(
    "payload",
    [
        {"conclusion": "publish"},
        {"items": [{"decision": "approve"}]},
        {"nested": {"final_decision": "publish"}},
        {"items": [{"publication_decision": "publish"}]},
    ],
)
def test_collector_rejects_nested_publishing_semantics(payload):
    state = PolicyImpactState(run_id="run-collector-publish")
    runner = _runner(
        ScriptedModelAdapter([{"type": "final", "output": payload}]),
        roles=("collector",),
    )

    result = runner.run(state, "collector", task="collect evidence")

    assert result.stop_reason in {"gate_blocked", "invalid_model_action"}
    assert state.delegation_evidence == []
    assert state.stage_trace[-1]["status"] == "failed"


def test_editor_rejects_mutable_state_gate_without_persisted_resolver():
    state = PolicyImpactState(run_id="run-editor-deny")
    state.add_stage_gate_result(
        {
            "gate_id": "review_verdict",
            "gate_version": "1.0.0",
            "passed": True,
            "metadata": {"reviewer_role": "verifier", "verified_claim_refs": ["claim-1"]},
        }
    )
    adapter = RecordingAdapter([])
    runner = _runner(adapter, roles=("editor",))

    with pytest.raises(PermissionError, match="verified claim"):
        runner.run(
            state,
            "editor",
            task="publish",
            input_artifact_refs=("claim-1",),
        )

    assert adapter.message_batches == []


def test_editor_accepts_only_real_persisted_verifier_pass_step(monkeypatch, tmp_path):
    state = PolicyImpactState(company_id="company-proof-pass", run_id="run-editor-pass")
    resolver, step_id = _persist_verifier_proof(monkeypatch, tmp_path, state)
    runner = _runner(
        ScriptedModelAdapter(
            [
                {
                    "type": "final",
                    "output": {
                        "answer": "Published verified claim.",
                        "verified_claim_refs": ["claim-1"],
                    },
                }
            ]
        ),
        roles=("editor",),
        verifier_gate_resolver=resolver,
    )

    result = runner.run(
        state,
        "editor",
        task="publish",
        input_artifact_refs=("claim-1",),
    )

    assert result.stop_reason == "success"
    assert result.output == {
        "answer": "Published verified claim.",
        "verified_claim_refs": ("claim-1",),
    }
    assert result.verifier_gate_audit_refs == (step_id,)


def test_legacy_definition_requires_manifest_and_registered_role():
    state = PolicyImpactState(run_id="run-legacy-isolation")
    state.recent_history = [{"content": "LEGACY_HISTORY_SECRET"}]
    adapter = RecordingAdapter([{"type": "final", "output": {"summary": "done"}}])
    runner = SubagentRunner(
        definitions=[
            SubagentDefinition(
                role="collector",
                stage_name="legacy_stage",
                visible_fields=["recent_history"],
                allowed_tools=[],
                output_key="evidence_collection.v1",
                task_prompt="Read explicit inputs only",
            )
        ],
        model_adapter=adapter,
        tool_gateway=ToolGateway(permission_engine=PermissionEngine([])),
        skill_manifest=_manifest(roles=("collector",)),
    )

    result = runner.run(state, "collector")

    assert result.stop_reason == "invalid_model_action"
    assert "LEGACY_HISTORY_SECRET" not in json.dumps(adapter.message_batches)
    assert state.context_manifests[0]["metadata"]["visible_keys"] == [
        "task_brief",
        "input_artifact_refs",
    ]


def test_legacy_definitions_cannot_authorize_or_create_unknown_role():
    with pytest.raises((KeyError, ValueError), match="missing_role"):
        SubagentRunner(
            [
                SubagentDefinition(
                    role="missing_role",
                    stage_name="legacy",
                    visible_fields=(),
                    allowed_tools=(),
                    output_key="anything.v1",
                )
            ],
            ScriptedModelAdapter([]),
            ToolGateway(permission_engine=PermissionEngine([])),
            skill_manifest=_manifest(roles=("collector",)),
        )


def test_subagent_runner_requires_explicit_skill_manifest():
    with pytest.raises(TypeError, match="skill_manifest"):
        SubagentRunner(
            [],
            ScriptedModelAdapter([]),
            ToolGateway(permission_engine=PermissionEngine([])),
        )


def test_skeptic_reads_only_explicit_task_artifact_payload():
    state = PolicyImpactState(run_id="run-artifact-read")
    adapter = RecordingAdapter(
        [
            {
                "type": "tool_call",
                "tool_name": "artifact_read",
                "arguments": {"artifact_ref": "review-context-1"},
            },
            {
                "type": "final",
                "output": {
                    "counterexamples": [
                        {
                            "claim_ref": "claim-1",
                            "issue": "Missing company evidence.",
                            "evidence_refs": ["review-context-1"],
                        }
                    ],
                    "notes": ["Deterministic gate remains authoritative."],
                },
            },
        ]
    )
    runner = _runner(
        adapter,
        roles=("skeptic",),
        tools=("artifact_read",),
    )

    result = runner.run(
        state,
        "skeptic",
        task="Read the artifact, then challenge claims.",
        input_artifact_refs=("review-context-1",),
        artifact_payloads={"review-context-1": {"claims": [{"claim_ref": "claim-1"}]}},
    )

    assert result.stop_reason == "success"
    assert result.output == SkepticOutput.model_validate(
        {
            "counterexamples": [
                {
                    "claim_ref": "claim-1",
                    "issue": "Missing company evidence.",
                    "evidence_refs": ["review-context-1"],
                }
            ],
            "notes": ["Deterministic gate remains authoritative."],
        }
    ).model_dump(mode="python")
    assert result.to_dict()["output"]["counterexamples"][0]["claim_ref"] == "claim-1"
    assert adapter.message_batches[1][-1]["content"]["tool_result"] == {
        "claims": [{"claim_ref": "claim-1"}]
    }


def test_artifact_read_denies_ref_not_listed_in_task_brief():
    state = PolicyImpactState(run_id="run-artifact-deny")
    adapter = ScriptedModelAdapter(
        [
            {
                "type": "tool_call",
                "tool_name": "artifact_read",
                "arguments": {"artifact_ref": "secret-ref"},
            }
        ]
    )
    runner = _runner(adapter, roles=("skeptic",), tools=("artifact_read",))

    result = runner.run(
        state,
        "skeptic",
        task="Read only allowed evidence.",
        input_artifact_refs=("allowed-ref",),
        artifact_payloads={
            "allowed-ref": {"visible": True},
            "secret-ref": {"secret": True},
        },
    )

    assert result.stop_reason == "tool_denied"
    assert "secret" not in json.dumps(result.to_dict())


def test_subagent_result_output_is_deeply_immutable_json_round_trip_and_no_reasoning():
    result = SubagentResult(
        task_id="task-1",
        parent_run_id="run-1",
        role="skeptic",
        role_version="1.0.0",
        output={"counterexamples": [{"claim_ref": "claim-1"}], "notes": []},
        stop_reason="success",
        context_manifest_id="ctx-1",
        parent_span_id="span-parent",
        span_id="span-child",
    )

    with pytest.raises(TypeError):
        result.output["new"] = True
    with pytest.raises(TypeError):
        result.output["counterexamples"][0]["claim_ref"] = "mutated"
    rebuilt = SubagentResult.model_validate_json(result.model_dump_json())
    assert rebuilt == result
    with pytest.raises(ValidationError, match="reasoning"):
        SubagentResult.model_validate(
            {
                **result.model_dump(mode="json"),
                "output": {"nested": {"reasoning": "SECRET"}},
            }
        )


def test_subagent_hidden_reasoning_fails_closed_without_persisting_secret():
    state = PolicyImpactState(run_id="run-hidden-reasoning")
    runner = _runner(
        ScriptedModelAdapter(
            [
                {
                    "type": "final",
                    "output": {
                        "verdicts": [],
                        "analysis": "PRIVATE_CHAIN_OF_THOUGHT",
                    },
                }
            ]
        ),
        roles=("verifier",),
    )

    result = runner.run(state, "verifier", task="verify")

    assert result.stop_reason == "invalid_model_action"
    assert result.output == {}
    assert state.delegation_evidence == []
    serialized = json.dumps({"calls": state.model_calls, "result": result.to_dict()})
    assert "PRIVATE_CHAIN_OF_THOUGHT" not in serialized


def test_skeptic_rejects_evidence_ref_outside_task_brief():
    state = PolicyImpactState(run_id="run-forged-output-ref")
    runner = _runner(
        ScriptedModelAdapter(
            [
                {
                    "type": "final",
                    "output": {
                        "counterexamples": [
                            {
                                "claim_ref": "claim-1",
                                "issue": "unsupported",
                                "evidence_refs": ["not-delegated"],
                            }
                        ],
                        "notes": [],
                    },
                }
            ]
        ),
        roles=("skeptic",),
    )

    result = runner.run(
        state,
        "skeptic",
        task="challenge",
        input_artifact_refs=("delegated-evidence",),
    )

    assert result.stop_reason == "gate_blocked"
    assert state.delegation_evidence == []


def test_role_output_schemas_are_strict_frozen_and_extra_forbid():
    verifier = VerifierOutput.model_validate(
        {
            "verdicts": [
                {
                    "claim_ref": "claim-1",
                    "decision": "pass",
                    "evidence_refs": ["evidence-1"],
                    "reason": "Matched official evidence.",
                }
            ]
        }
    )

    assert verifier.model_config["frozen"] is True
    assert verifier.model_config["extra"] == "forbid"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        EditorOutput.model_validate(
            {
                "answer": "ok",
                "verified_claim_refs": ["claim-1"],
                "artifact_refs": ["model-invented-ref"],
            }
        )
