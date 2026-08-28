from __future__ import annotations

import inspect
from pathlib import Path
import threading

import pytest

from policy_impact.company_wiki import loader as wiki_loader
from policy_impact.harness import agent_runtime as agent_runtime_module
from policy_impact.harness.agent_runtime import AgentRuntime
from policy_impact.harness.model_config import DEFAULT_MODEL_ID
from policy_impact.harness.model_gateway import ScriptedModelAdapter
from policy_impact.memory.store import PolicyMemoryStore
from policy_impact.runtime.execution_context import SkillExecutionContext
from policy_impact.runtime.execution_mode import ExecutionMode
from policy_impact.runtime.executor_registry import ExecutorRegistry
from policy_impact.runtime.hooks import HookDecision
from policy_impact.runtime.result import CitationRef, SkillResult
from policy_impact.skills.schemas import SkillOutput


def _seed_company(tmp_path: Path) -> None:
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
## FACT: fact_agent_harness
- value: Builds Agent Harness systems with ToolGateway, ContextManifest, Replay, and Memory.
- importance: 5
- confidence: 0.9
- source: internal wiki
- policy_relevance: [agent, harness, runtime]
""",
        encoding="utf-8",
    )


def _use_temp_project_root(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(wiki_loader, "project_root", lambda: tmp_path, raising=False)


@pytest.fixture(autouse=True)
def _model_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("ANTHROPIC_MODEL", DEFAULT_MODEL_ID)


def _seed_test_model(company_id: str = "company_test") -> None:
    PolicyMemoryStore(company_id).seed_model_configs()


def _runtime(company_id: str) -> AgentRuntime:
    payload = {
        "type": "final",
        "output": {
            "answer": (
                "Agent Workbench 通过 AgentRuntime、Skill、ContextManifest、Memory、"
                "ToolGateway、AgentRun 和 AgentStep 提供可观测 Harness；"
                "CoursePilot 更侧重课程学习 Runtime。"
            ),
            "thesis": "成熟 Harness 需要把模型调用放在可控、可观测、可评估的运行时中。",
            "evidence": ["ContextManifest 和 ToolGateway 已有可审计记录。"],
            "risks": ["需要防止旧报告记忆污染当前问题。"],
            "recommendations": ["持续运行 API dialogue matrix。"],
            "next_checks": ["检查真实模型延迟与 JSON 契约成功率。"],
        },
    }
    return AgentRuntime(
        company_id=company_id,
        model_adapter=ScriptedModelAdapter([payload] * 8),
    )


def test_parse_slash_command_accepts_inline_report_command():
    skill_id, task = AgentRuntime._parse_slash_command("请帮我 /report 分析 Agent Harness 对示例公司的影响")

    assert skill_id == "external_impact_report"
    assert task == "请帮我 分析 Agent Harness 对示例公司的影响"


def test_agent_runtime_general_chat_records_harness_trace(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    _seed_company(tmp_path)
    _seed_test_model()
    runtime = _runtime("company_test")
    store = PolicyMemoryStore("company_test")
    session_id = store.create_chat_session(
        title="Chat",
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )
    store.save_memory(
        namespace="company:company_test:chat",
        memory_type="preference",
        content="User cares about Agent Harness traceability.",
        importance=0.9,
    )

    result = runtime.run_turn(
        session_id=session_id,
        message="What Agent Harness capabilities do we have?",
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="",
    )

    run = store.get_agent_run(result["run_id"])
    steps = store.list_agent_steps(result["run_id"])
    messages = store.list_chat_messages(session_id)

    assert result["selected_skill_id"] == "general_chat"
    assert run["status"] == "done"
    step_types = [step["step_type"] for step in steps]
    assert step_types[:5] == [
        "intent_classification",
        "skill_selection",
        "memory_read",
        "tool_call",
        "context_build",
    ]
    assert "model_call" in step_types
    assert step_types[-2:] == ["final_answer", "run_stopped"]
    assert steps[4]["output_payload"]["context_manifest"]["stage_name"] == "chat_turn"
    assert steps[3]["tool_calls"]
    assert [step["metadata"].get("loop_phase") for step in steps[:5]] == [
        "think",
        "think",
        "observe",
        "act",
        "think",
    ]
    assert any(
        step["step_type"] == "model_call"
        and step["input_payload"].get("prompt_contract", {}).get("contract_id")
        == "general_chat_agent_loop.v1"
        for step in steps
    )
    assert messages[-2]["role"] == "user"
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["metadata"]["run_id"] == result["run_id"]


def test_general_chat_can_plan_and_run_dynamic_subagents(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    _seed_company(tmp_path)
    _seed_test_model()
    runtime = AgentRuntime(
        company_id="company_test",
        model_adapter=ScriptedModelAdapter(
            [
                {
                    "type": "delegate",
                    "role": "skeptic",
                    "task": "读取委派上下文，指出 Agent Workbench 设计成熟度中的风险、证据缺口和可被面试追问的点。",
                },
                {
                    "type": "final",
                    "output": {
                        "counterexamples": [
                            {
                                "claim_ref": "claim-1",
                                "issue": "设计成熟度需要真实运行数据支撑。",
                                "evidence_refs": [],
                            }
                        ],
                        "notes": ["需要看 Trace、Eval 和报告产物是否真实存在。"],
                    },
                },
                {
                    "type": "delegate",
                    "role": "analyst",
                    "task": "读取委派上下文，分析 Agent Workbench 的 Harness 控制面价值和可讲述的成熟设计点。",
                },
                {
                    "type": "final",
                    "output": {
                        "claims": [
                            {
                                "claim_ref": "claim-2",
                                "statement": "Workbench 的重点是 Harness 控制面，而不是新闻业务本身。",
                                "evidence_refs": [],
                                "confidence": 0.82,
                            }
                        ]
                    },
                },
                {
                    "type": "final",
                    "output": {
                        "answer": "已调用 skeptic 和 analyst 子智能体；结论应聚焦可控、可审计、可复盘、可回归。"
                    },
                },
            ]
        ),
    )
    store = PolicyMemoryStore("company_test")
    session_id = store.create_chat_session(
        title="Dynamic subagents",
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )

    result = runtime.run_turn(
        session_id=session_id,
        message="请用子智能体质疑并分析 Agent Workbench 的设计是否成熟。",
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )
    steps = store.list_agent_steps(result["run_id"])
    step_types = [step["step_type"] for step in steps]
    run = store.get_agent_run(result["run_id"])

    assert "subagent_plan" in step_types
    assert step_types.count("subagent_delegate") == 2
    assert step_types.count("subagent_context") == 2
    assert sum(
        1
        for step in steps
        if step["step_type"] == "model_call"
        and step["output_payload"].get("payload", {}).get("type") == "delegate"
    ) == 2
    assert any(
        step["step_type"] == "subagent_plan"
        and step["output_payload"].get("planner") == "main_agent_loop.delegate_action.v1"
        and step["output_payload"].get("agent_manifest", {}).get("role") == "skeptic"
        for step in steps
    )
    assert run["metadata"]["execution_mode"] == "subagent_workflow"
    assert run["metadata"]["delegation_count"] == 2
    assert "skeptic" in run["metadata"]["delegation_evidence"][0]["role"]


def test_agent_runtime_general_chat_filters_stale_report_memories(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    _seed_company(tmp_path)
    _seed_test_model()
    runtime = _runtime("company_test")
    store = PolicyMemoryStore("company_test")
    session_id = store.create_chat_session(
        title="Chat",
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )
    store.save_memory(
        namespace="company:company_test:skill_results",
        memory_type="skill_result",
        content=(
            "生成政策影响报告："
            r"D:\AAAcode\code-code\agent+\harness\policy_impact\data\companies\company_test\reports\weekly-policy-impact.md"
        ),
        importance=0.99,
    )
    store.save_memory(
        namespace="company:company_test:conversation",
        memory_type="conversation_summary",
        content="Agent Workbench capabilities discussed in a different session.",
        importance=1.0,
    )

    result = runtime.run_turn(
        session_id=session_id,
        message="What Agent Workbench capabilities do we have?",
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )
    steps = store.list_agent_steps(result["run_id"])
    memory_step = next(step for step in steps if step["step_type"] == "memory_read")
    context_step = next(step for step in steps if step["step_type"] == "context_build")
    visible_memories = context_step["output_payload"]["context_manifest"]["visible_content"]["relevant_memories"]

    assert "weekly-policy-impact" not in result["answer"]
    assert "生成政策影响报告" not in result["answer"]
    assert memory_step["output_payload"]["filtered_out"] >= 2
    assert visible_memories == []


def test_unbound_project_comparison_does_not_expose_company_facts(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    _seed_company(tmp_path)
    _seed_test_model()
    runtime = _runtime("company_test")
    store = PolicyMemoryStore("company_test")
    session_id = store.create_chat_session(
        title="Project comparison",
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )

    result = runtime.run_turn(
        session_id=session_id,
        message="请只基于当前知识库和记忆，解释这个项目和 CoursePilot 的区别。",
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )
    steps = store.list_agent_steps(result["run_id"])
    context_step = next(step for step in steps if step["step_type"] == "context_build")
    visible_facts = context_step["output_payload"]["context_manifest"]["visible_content"]["company_facts"]
    scope_gate = next(step for step in steps if step["step_type"] == "gate_check")

    assert visible_facts == []
    assert "不是 Agent Workbench 或 CoursePilot 的项目资料" in result["answer"]
    assert "阻止把企业画像事实冒充项目实现事实" in result["answer"]
    assert scope_gate["gate_result"]["policy_id"] == "general_chat.subject_scope.v1"
    assert not any(step["step_type"] == "model_call" for step in steps)


def test_agent_runtime_only_writes_long_term_memory_on_explicit_request(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    _seed_company(tmp_path)
    _seed_test_model()
    runtime = _runtime("company_test")
    store = PolicyMemoryStore("company_test")
    session_id = store.create_chat_session(
        title="Memory",
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )

    explicit = runtime.run_turn(
        session_id=session_id,
        message="请记住：我的 Harness 学习重点是 Context、Tool Policy 和 Eval。",
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )
    implicit = runtime.run_turn(
        session_id=session_id,
        message="我今天觉得 Replay 也挺重要。",
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )

    explicit_steps = store.list_agent_steps(explicit["run_id"])
    implicit_steps = store.list_agent_steps(implicit["run_id"])
    saved = store.search_memory("Harness Context Tool Policy Eval", top_k=5)

    assert any(step["step_type"] == "memory_write" for step in explicit_steps)
    assert not any(step["step_type"] == "memory_write" for step in implicit_steps)
    assert any("Context、Tool Policy 和 Eval" in item["content"] for item in saved)


def test_agent_runtime_persists_durable_feedback_memory(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    _seed_company(tmp_path)
    _seed_test_model()
    runtime = _runtime("company_test")
    store = PolicyMemoryStore("company_test")
    session_id = store.create_chat_session(
        title="Feedback memory",
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )

    result = runtime.run_turn(
        session_id=session_id,
        message="我不认可刚才报告中的第二条建议，因为我们短期不做 C 端情感陪伴。请根据这个反馈重新调整后续关注重点。",
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )

    steps = store.list_agent_steps(result["run_id"])
    saved = store.search_memory("短期不做 C 端情感陪伴 关注重点", top_k=5)
    memory_step = next(step for step in steps if step["step_type"] == "memory_write")

    assert memory_step["input_payload"]["memory_type"] == "feedback_memory"
    assert any("短期不做 C 端情感陪伴" in item["content"] for item in saved)
    assert "已记录这条反馈" in result["answer"]


def test_agent_runtime_does_not_persist_feedback_question_as_new_memory(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    _seed_company(tmp_path)
    _seed_test_model()
    runtime = _runtime("company_test")
    store = PolicyMemoryStore("company_test")
    session_id = store.create_chat_session(
        title="Feedback question",
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )

    result = runtime.run_turn(
        session_id=session_id,
        message="刚才我给了什么反馈？后续报告和建议排序应该怎么受影响？",
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )

    steps = store.list_agent_steps(result["run_id"])

    assert not any(step["step_type"] == "memory_write" for step in steps)


def test_stop_denial_cannot_leak_explicit_memory(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    _seed_company(tmp_path)
    _seed_test_model()
    runtime = _runtime("company_test")
    runtime.hooks.register(
        "Stop",
        lambda payload: HookDecision(action="deny", reason="memory release blocked"),
    )
    store = PolicyMemoryStore("company_test")
    session_id = store.create_chat_session(
        title="Blocked memory",
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )

    with pytest.raises(PermissionError, match="Stop deny"):
        runtime.run_turn(
            session_id=session_id,
            message="remember that: BLOCKED-MEMORY-4107",
            mode="chat",
            model_id=DEFAULT_MODEL_ID,
            active_skill_id="general_chat",
        )

    run = store.latest_agent_run(session_id)
    assert store.search_memory("BLOCKED-MEMORY-4107", top_k=5) == []
    assert not any(
        step["step_type"] == "memory_write"
        for step in store.list_agent_steps(run["run_id"])
    )
    assert run["artifacts"] == []
    assert [message["role"] for message in store.list_chat_messages(session_id)] == [
        "user"
    ]


def test_terminal_failure_rolls_back_explicit_memory_and_publication(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    _seed_company(tmp_path)
    _seed_test_model()
    runtime = _runtime("company_test")
    store = PolicyMemoryStore("company_test")
    session_id = store.create_chat_session(
        title="Atomic memory",
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )
    original_insert = PolicyMemoryStore._insert_agent_step_conn

    def fail_final_answer(conn, run_id, step):
        if step.get("step_type") == "final_answer":
            raise RuntimeError("planned atomic final-answer failure")
        return original_insert(conn, run_id, step)

    monkeypatch.setattr(runtime.store, "_insert_agent_step_conn", fail_final_answer)

    with pytest.raises(RuntimeError, match="planned atomic final-answer failure"):
        runtime.run_turn(
            session_id=session_id,
            message="remember that: ATOMIC-MEMORY-4108",
            mode="chat",
            model_id=DEFAULT_MODEL_ID,
            active_skill_id="general_chat",
        )

    run = store.latest_agent_run(session_id)
    assert run["status"] == "running"
    assert store.search_memory("ATOMIC-MEMORY-4108", top_k=5) == []
    assert [message["role"] for message in store.list_chat_messages(session_id)] == [
        "user"
    ]


def test_concurrent_explicit_memory_answers_share_one_persisted_reference(
    tmp_path,
    monkeypatch,
):
    _use_temp_project_root(monkeypatch, tmp_path)
    _seed_company(tmp_path)
    _seed_test_model()
    first_runtime = _runtime("company_test")
    second_runtime = _runtime("company_test")
    stop_barrier = threading.Barrier(2)

    def wait_at_stop(payload):
        stop_barrier.wait(timeout=5)
        return HookDecision(action="allow")

    first_runtime.hooks.register("Stop", wait_at_stop)
    second_runtime.hooks.register("Stop", wait_at_stop)
    store = PolicyMemoryStore("company_test")
    first_session_id = store.create_chat_session(
        title="Concurrent memory one",
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )
    second_session_id = store.create_chat_session(
        title="Concurrent memory two",
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )
    message = "remember that: CONCURRENT-MEMORY-4109"
    first_prepared = first_runtime.coordinator.prepare_turn(
        session_id=first_session_id,
        message=message,
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )
    second_prepared = second_runtime.coordinator.prepare_turn(
        session_id=second_session_id,
        message=message,
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )
    results = []
    errors = []

    def execute(runtime, prepared):
        try:
            results.append(runtime.coordinator.execute_prepared_turn(prepared))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=execute, args=(first_runtime, first_prepared)),
        threading.Thread(target=execute, args=(second_runtime, second_prepared)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert len(results) == 2
    matching_memories = [
        item
        for item in store.search_memory("CONCURRENT-MEMORY-4109", top_k=20)
        if item["memory_type"] == "explicit_user_memory"
    ]
    assert len(matching_memories) == 1
    persisted_id = matching_memories[0]["id"]
    for result in results:
        assert f"[memory_id: {persisted_id}]" in result["answer"]
        assert result["citations"] == [
            {"type": "memory_item", "memory_id": persisted_id}
        ]
    assistant_messages = [
        item
        for session_id in (first_session_id, second_session_id)
        for item in store.list_chat_messages(session_id)
        if item["role"] == "assistant"
    ]
    assert len(assistant_messages) == 2
    assert all(
        item["metadata"]["citations"]
        == [{"type": "memory_item", "memory_id": persisted_id}]
        for item in assistant_messages
    )


def test_canonical_memory_id_uses_normalized_stable_identity():
    from policy_impact.memory.store import canonical_memory_id

    canonical = canonical_memory_id(
        " company:company_test:user_profile ",
        " explicit_user_memory ",
        " CONCURRENT-MEMORY-4109 ",
    )

    assert canonical == canonical_memory_id(
        "company:company_test:user_profile",
        "explicit_user_memory",
        "CONCURRENT-MEMORY-4109",
    )
    assert canonical.startswith("mem_")
    assert len(canonical) == 68
    assert canonical != canonical_memory_id(
        "company:company_test:user_profile",
        "explicit_user_memory",
        "CONCURRENT-MEMORY-OTHER",
    )


def test_negative_memory_instruction_is_not_treated_as_write_request(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    _seed_company(tmp_path)
    _seed_test_model()
    runtime = _runtime("company_test")
    store = PolicyMemoryStore("company_test")
    session_id = store.create_chat_session(
        title="Negative memory instruction",
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )

    result = runtime.run_turn(
        session_id=session_id,
        message="本次会话的临时代号是 POLARIS-17，不要写入长期记忆。",
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )
    steps = store.list_agent_steps(result["run_id"])

    assert not AgentRuntime._requests_memory_write("不要写入长期记忆。")
    assert not AgentRuntime._requests_memory_write("本轮内容不写入长期记忆。")
    assert not any(step["step_type"] == "memory_write" for step in steps)
    assert "请使用“请记住" not in result["answer"]


def test_immediate_history_question_excludes_cross_session_memory_and_new_facts(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    _seed_company(tmp_path)
    _seed_test_model()
    runtime = _runtime("company_test")
    store = PolicyMemoryStore("company_test")
    session_id = store.create_chat_session(
        title="History first",
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )
    store.save_memory(
        namespace="company:company_test:user_profile",
        memory_type="explicit_user_memory",
        content="另一个旧会话的临时代号是 OLD-001。",
        importance=0.95,
    )
    store.save_chat_message(
        session_id,
        role="user",
        content="本次会话的三个重点是 ContextManifest、Tool Policy 和 Eval。",
    )
    store.save_chat_message(
        session_id,
        role="assistant",
        content="已在当前会话中记录这三个临时重点，但没有写入长期记忆。",
    )

    result = runtime.run_turn(
        session_id=session_id,
        message="刚才那三个临时重点是什么？",
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )
    context_step = next(
        step for step in store.list_agent_steps(result["run_id"]) if step["step_type"] == "context_build"
    )
    visible = context_step["output_payload"]["context_manifest"]["visible_content"]

    assert visible["relevant_memories"] == []
    assert visible["company_facts"] == []
    assert len(visible["recent_history"]) == 2


def test_unpersisted_cross_session_query_is_blocked_without_model_call(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    _seed_company(tmp_path)
    _seed_test_model()
    runtime = _runtime("company_test")
    store = PolicyMemoryStore("company_test")
    session_id = store.create_chat_session(
        title="Cross-session isolation",
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )

    result = runtime.run_turn(
        session_id=session_id,
        message="另一个会话里刚才设置的临时代号是什么？",
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )
    steps = store.list_agent_steps(result["run_id"])
    gate = next(step for step in steps if step["step_type"] == "gate_check")

    assert "无法读取另一个会话" in result["answer"]
    assert gate["gate_result"]["policy_id"] == "memory.cross_session_isolation.v1"
    assert not any(step["step_type"] == "model_call" for step in steps)


def test_long_history_creates_traceable_conversation_summary(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    _seed_company(tmp_path)
    _seed_test_model()
    adapter = ScriptedModelAdapter(
        [
            {
                "type": "conversation_summary",
                "output": {
                    "summary": "用户将会话代号设为 POLARIS-17，并明确禁止写入长期记忆。",
                    "user_facts": ["会话代号 POLARIS-17"],
                    "decisions": ["仅在当前会话使用"],
                    "open_loops": [],
                },
            },
            {
                "type": "general_chat",
                "output": {"answer": "本次会话代号是 POLARIS-17，仅限当前会话。"},
            },
        ]
    )
    runtime = AgentRuntime(company_id="company_test", model_adapter=adapter)
    store = PolicyMemoryStore("company_test")
    session_id = store.create_chat_session(
        title="Compaction",
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )
    for index in range(5):
        store.save_chat_message(
            session_id,
            role="user",
            content=(
                "本次会话代号是 POLARIS-17，不写入长期记忆。"
                if index == 0
                else f"第 {index + 1} 轮会话设置。"
            ),
        )
        store.save_chat_message(
            session_id,
            role="assistant",
            content=f"已在当前会话处理第 {index + 1} 轮设置。",
        )

    result = runtime.run_turn(
        session_id=session_id,
        message="请汇总本次会话代号。",
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )
    steps = store.list_agent_steps(result["run_id"])
    context_step = next(step for step in steps if step["step_type"] == "context_build")
    summary = context_step["output_payload"]["context_manifest"]["visible_content"]["conversation_summary"]
    compaction = next(step for step in steps if step["step_type"] == "context_compaction")
    working_memory_write = next(
        step
        for step in steps
        if step["step_type"] == "memory_write"
        and step["input_payload"].get("write_policy") == "context_pressure_compaction"
    )

    assert "POLARIS-17" in summary["summary"]
    assert compaction["gate_result"]["policy_id"] == "context.compaction.v1"
    assert working_memory_write["gate_result"]["decision"] == "allow"
    assert store.get_conversation_summary(session_id)["source_message_count"] == 2


def test_session_fact_provenance_gate_repairs_public_disclosure_label():
    answer, contract = AgentRuntime._govern_general_chat_answer(
        "请汇总本次会话的临时代号，并把事实和推断分开。",
        "## 公开事实（public_disclosure）\n- POLARIS-17\n\n以上“公开事实”来自用户消息。",
        [],
    )

    assert "用户确认的会话事实（user_confirmed）" in answer
    assert "公开事实（public_disclosure）" not in answer
    assert contract["contract_id"] == "general_chat.session_provenance.v1"


def test_workbench_self_description_uses_conditional_harness_semantics():
    answer, contract = AgentRuntime._govern_general_chat_answer(
        "请介绍 Agent Workbench 能做什么。",
        "Harness 会全面记录和控制所有模块。",
        [],
    )

    assert "按路由实际触发" in answer
    assert "模型回答和技能内容不是" in answer
    assert contract["contract_id"] == "general_chat.workbench_capabilities.v1"


def test_workbench_model_boundary_uses_runtime_self_knowledge_only():
    answer, contract = AgentRuntime._govern_general_chat_answer(
        "请用一句话说明 Agent Workbench 的模型调用边界。",
        "模型会自行决定是否调用工具 [fact_id: risk.agent_harness]。",
        [],
    )

    assert "deepseek-v4-flash" in answer
    assert "缺少可用模型时整轮拒绝执行" in answer
    assert "隐藏模型旁路" in answer
    assert "fact_id" not in answer
    assert contract["contract_id"] == "general_chat.workbench_capabilities.v1"


def test_agent_runtime_confirms_memory_only_after_traceable_write(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    _seed_company(tmp_path)
    _seed_test_model()
    runtime = _runtime("company_test")
    store = PolicyMemoryStore("company_test")
    session_id = store.create_chat_session(
        title="Memory Contract",
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )

    result = runtime.run_turn(
        session_id=session_id,
        message=(
            "请把以下内容显式写入长期记忆：项目代号是“银杏-0716”；"
            "默认上线闸门是“无官方证据不得上线”。请只在实际写入成功后说已写入。"
        ),
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )
    steps = store.list_agent_steps(result["run_id"])
    saved = store.search_memory("银杏-0716 无官方证据不得上线", top_k=5)

    assert result["answer"].startswith("已写入长期记忆")
    assert any(step["step_type"] == "memory_write" for step in steps)
    assert any("银杏-0716" in item["content"] for item in saved)
    assert all("请只在实际写入" not in item["content"] for item in saved)
    memory_step = next(step for step in steps if step["step_type"] == "memory_write")
    assert f"[memory_id: {memory_step['output_payload']['memory_id']}]" in result["answer"]


def test_runtime_replaces_model_memory_placeholder_with_grounded_id():
    answer = AgentRuntime._attach_grounded_memory_citations(
        "已找到对应内容。[memory_id: 无具体ID，来自 explicit_user_memory]",
        [{"type": "memory_item", "memory_id": "mem-verified-01"}],
    )

    assert "无具体ID" not in answer
    assert answer.endswith("记忆引用：[memory_id: mem-verified-01]")


def test_explicit_memory_write_overrides_incidental_research_keywords(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    _seed_company(tmp_path)
    _seed_test_model()
    runtime = _runtime("company_test")
    store = PolicyMemoryStore("company_test")
    session_id = store.create_chat_session(
        title="Memory Priority",
        mode="auto",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="",
    )

    result = runtime.run_turn(
        session_id=session_id,
        message="请显式写入长期记忆：report_pref = 企业报告先列证据缺口，再列行动建议。",
        mode="auto",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="",
    )
    steps = store.list_agent_steps(result["run_id"])

    assert result["selected_skill_id"] == "general_chat"
    assert result["answer"].startswith("已写入长期记忆")
    assert any(step["step_type"] == "memory_write" for step in steps)


def test_inline_chat_command_overrides_a_persisted_workflow_skill(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    _seed_company(tmp_path)
    _seed_test_model()
    runtime = _runtime("company_test")
    store = PolicyMemoryStore("company_test")
    session_id = store.create_chat_session(
        title="Slash Override",
        mode="skill",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="company_wiki_blueprint",
    )

    result = runtime.run_turn(
        session_id=session_id,
        message="/chat 请直接回答企业知识库中的公司事实，不要生成蓝图。",
        mode="skill",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="company_wiki_blueprint",
    )

    assert result["selected_skill_id"] == "general_chat"
    assert not result["artifacts"]


def test_agent_runtime_research_report_creates_markdown_artifact(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    _seed_company(tmp_path)
    _seed_test_model()
    runtime = _runtime("company_test")
    store = PolicyMemoryStore("company_test")
    session_id = store.create_chat_session(
        title="Research",
        mode="skill",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="research_report",
    )

    result = runtime.run_turn(
        session_id=session_id,
        message="Research what a mature Agent Harness should include.",
        mode="skill",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="research_report",
    )

    report_path = Path(result["artifacts"]["report"])
    steps = store.list_agent_steps(result["run_id"])

    assert result["selected_skill_id"] == "research_report"
    assert report_path.exists()
    assert "Agent Harness" in report_path.read_text(encoding="utf-8")
    assert any(step["step_type"] == "model_call" for step in steps)
    assert any(step["step_type"] == "artifact_write" for step in steps)
    assert not any(step["step_type"] == "memory_write" for step in steps)
    assert steps[-1]["step_type"] == "run_stopped"
    model_step = next(step for step in steps if step["step_type"] == "model_call")
    prompt_contract = model_step["input_payload"]["prompt_contract"]
    messages = model_step["input_payload"]["messages"]
    assert prompt_contract["contract_id"] == "research_report.v1"
    assert prompt_contract["role"] == "research_report_subagent"
    assert prompt_contract["context_controls"]["visible_keys"] == [
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
    ]
    assert messages[0]["role"] == "system"
    assert "harness contract" in messages[0]["content"]
    assert model_step["metadata"]["loop_phase"] == "think"


def test_agent_runtime_research_writes_memory_only_on_explicit_request(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    _seed_company(tmp_path)
    _seed_test_model()
    runtime = _runtime("company_test")
    store = PolicyMemoryStore("company_test")
    session_id = store.create_chat_session(
        title="Research Memory",
        mode="skill",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="research_report",
    )

    result = runtime.run_turn(
        session_id=session_id,
        message="请记住：这份 Agent Harness 调研报告的产物和结论。",
        mode="skill",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="research_report",
    )
    steps = store.list_agent_steps(result["run_id"])
    memory_step = next(step for step in steps if step["step_type"] == "memory_write")

    assert memory_step["input_payload"]["write_policy"] == "explicit_user_request_only"
    assert memory_step["gate_result"]["decision"] == "allow"
    assert memory_step["output_payload"]["memory_id"]


def test_agent_runtime_workbench_design_research_filters_stale_policy_memory(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    _seed_company(tmp_path)
    _seed_test_model()
    runtime = _runtime("company_test")
    store = PolicyMemoryStore("company_test")
    session_id = store.create_chat_session(
        title="Workbench Design",
        mode="skill",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="research_report",
    )
    store.save_memory(
        namespace="company:company_test:skill_results",
        memory_type="skill_result",
        content=(
            "生成政策影响报告："
            r"D:\AAAcode\code-code\agent+\harness\policy_impact\data\companies\company_test\reports\weekly-policy-impact.md"
        ),
        importance=0.8,
        metadata={"path": r"D:\AAAcode\code-code\agent+\harness\policy_impact\data\companies\company_test\reports"},
    )
    for _ in range(2):
        store.save_memory(
            namespace="company:company_test:user_profile",
            memory_type="explicit_user_memory",
            content="Agent Workbench 重点是 AgentRuntime、Context 和 Trace",
            importance=0.9,
        )
    store.save_memory(
        namespace="company:company_test:conversation",
        memory_type="conversation_summary",
        content="另一个会话讨论过 Agent Workbench 的 Context 和 Trace",
        importance=1.0,
    )

    result = runtime.run_turn(
        session_id=session_id,
        message="帮我整理 Agent Workbench 的设计重点",
        mode="skill",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="research_report",
    )

    report_text = Path(result["artifacts"]["report"]).read_text(encoding="utf-8")
    steps = store.list_agent_steps(result["run_id"])
    memory_step = next(step for step in steps if step["step_type"] == "memory_read")
    context_step = next(step for step in steps if step["step_type"] == "context_build")
    visible_memories = context_step["output_payload"]["context_manifest"]["visible_content"]["relevant_memories"]

    assert "AgentRuntime" in report_text
    assert "SkillRegistry" in report_text
    assert "系统设计链（按任务条件触发，并非每轮全走）" in report_text
    assert "本次 Run 已发生的事实" in report_text
    assert "tool_call=0" in report_text
    assert "gate_check=0" in report_text
    assert "memory_write=0" in report_text
    assert "weekly-policy-impact" not in report_text
    assert "生成政策影响报告" not in report_text
    assert memory_step["output_payload"]["filtered_out"] >= 1
    assert len(visible_memories) == 1
    assert "AgentRuntime" in visible_memories[0]["content"]


def test_news_skill_asks_for_project_scope_before_running_workflow(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    _seed_company(tmp_path)
    _seed_test_model()
    runtime = _runtime("company_test")
    store = PolicyMemoryStore("company_test")
    session_id = store.create_chat_session(
        title="Ambiguous project",
        mode="auto",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="",
    )

    result = runtime.run_turn(
        session_id=session_id,
        message="/report 最新 Agent Harness 新闻对我的项目有什么影响？",
        mode="auto",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="",
    )
    steps = store.list_agent_steps(result["run_id"])
    scope_gate = next(step for step in steps if step["step_type"] == "gate_check")

    route_step = next(
        step
        for step in steps
        if step["step_type"] == "workflow_stage"
        and step["output_payload"].get("public_skill_id") == "external_impact_report"
    )

    assert result["selected_skill_id"] == "external_impact_report"
    assert route_step["output_payload"]["internal_route"] == "news"
    assert result["artifacts"] == {}
    assert "无法可靠分析“我的项目”" in result["answer"]
    assert "没有调用新闻工作流" in result["answer"]
    assert scope_gate["gate_result"]["decision"] == "ask"
    assert scope_gate["gate_result"]["policy_id"] == "news.subject_scope.v1"
    assert not any(step["step_type"] in {"tool_call", "artifact_write"} for step in steps)


def test_impact_router_treats_tool_policy_as_news_when_latest_news_is_explicit(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    _seed_company(tmp_path)
    _seed_test_model()
    runtime = _runtime("company_test")
    store = PolicyMemoryStore("company_test")
    session_id = store.create_chat_session(
        title="Tool Policy News",
        mode="auto",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="",
    )

    result = runtime.run_turn(
        session_id=session_id,
        message="/report 最新新闻里和 agent observability、tool policy 相关的内容有什么影响？",
        mode="auto",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="",
    )
    steps = store.list_agent_steps(result["run_id"])
    route_step = next(
        step
        for step in steps
        if step["step_type"] == "workflow_stage"
        and step["output_payload"].get("public_skill_id") == "external_impact_report"
    )

    assert result["selected_skill_id"] == "external_impact_report"
    assert route_step["output_payload"]["internal_route"] == "news"


def test_agent_runtime_rejects_unknown_skill_without_dangling_message(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    _seed_company(tmp_path)
    _seed_test_model()
    runtime = _runtime("company_test")
    store = PolicyMemoryStore("company_test")
    session_id = store.create_chat_session(
        title="Invalid Skill",
        mode="skill",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="missing_skill",
    )

    with pytest.raises(KeyError, match="unknown skill"):
        runtime.run_turn(
            session_id=session_id,
            message="This should not be persisted without a trace.",
            mode="skill",
            model_id=DEFAULT_MODEL_ID,
            active_skill_id="missing_skill",
        )

    assert store.list_chat_messages(session_id) == []
    assert store.latest_agent_run(session_id) is None


def test_agent_runtime_policy_skill_records_existing_workflow_artifacts():
    _seed_test_model("company_001")
    runtime = _runtime("company_001")
    store = PolicyMemoryStore("company_001")
    session_id = store.create_chat_session(
        title="Policy",
        mode="auto",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="",
    )

    result = runtime.run_turn(
        session_id=session_id,
        message="/report Please analyze recent policy impact.",
        mode="auto",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="",
    )

    steps = store.list_agent_steps(result["run_id"])

    route_step = next(
        step
        for step in steps
        if step["step_type"] == "workflow_stage"
        and step["output_payload"].get("public_skill_id") == "external_impact_report"
    )

    assert result["selected_skill_id"] == "external_impact_report"
    assert route_step["output_payload"]["internal_route"] == "policy"
    assert result["artifacts"]["run_artifact"]
    embedded_loop_calls = [
        step
        for step in steps
        if step["step_type"] == "model_call"
        and step["metadata"].get("embedded_loop") == "external_impact_report"
    ]
    embedded_observations = [
        step
        for step in steps
        if step["step_type"] == "workflow_stage"
        and step["metadata"].get("embedded_loop") == "external_impact_report"
    ]
    assert any(
        step["output_payload"]["payload"].get("type") == "tool_call"
        and step["output_payload"]["payload"].get("tool_name") == "report_workflow.run"
        for step in embedded_loop_calls
    )
    first_workflow_call = next(
        step
        for step in steps
        if step["title"] == "报告主 Agent 决定调用 Workflow"
    )
    route_step_index = route_step["step_index"]
    artifact_step = next(
        step
        for step in steps
        if step["step_type"] == "artifact_write"
    )
    workflow_observation = next(
        step
        for step in steps
        if step["title"] == "报告 Workflow 结果回填 AgentLoop"
    )
    final_summary_call = next(
        step
        for step in steps
        if step["title"] == "报告主 Agent 汇总 Workflow 观察结果"
    )
    assert first_workflow_call["step_index"] < route_step_index
    assert artifact_step["step_index"] < workflow_observation["step_index"]
    assert workflow_observation["step_index"] < final_summary_call["step_index"]
    assert any(
        step["output_payload"]["observation"].get("schema_version")
        == "report_workflow.observation.v1"
        for step in embedded_observations
    )
    assert any(step["step_type"] == "gate_check" for step in steps)
    assert any(step["step_type"] == "tool_call" for step in steps)
    assert any(step["step_type"] == "artifact_write" for step in steps)


def test_extract_company_fact_ids_accepts_brackets_markdown_and_explicit_prose():
    content = (
        "公开事实 [fact_id: ai_product.wencai]；"
        "分析推断 `risk.data_security`；"
        "项目观点 `risk.agent_harness`；"
        "对应 fact_id 为 business.core_product。"
    )

    assert AgentRuntime._extract_company_fact_ids(content) == {
        "ai_product.wencai",
        "risk.data_security",
        "risk.agent_harness",
        "business.core_product",
    }


def test_execute_skill_does_not_branch_on_skill_id_or_legal_reference():
    source = inspect.getsource(AgentRuntime._execute_skill)

    assert "if skill.id" not in source
    assert "elif skill.id" not in source
    assert "skill.id ==" not in source
    assert "resolve_official_legal_reference" not in source


class RecordingCoordinator:
    def __init__(self) -> None:
        self.calls = []

    def run_turn(self, **kwargs):
        self.calls.append(kwargs)
        return {"delegated": True}


def test_agent_runtime_run_turn_delegates_directly_to_coordinator():
    runtime = object.__new__(AgentRuntime)
    coordinator = RecordingCoordinator()
    runtime.coordinator = coordinator
    request = {
        "session_id": "session-1",
        "message": "delegate this turn",
        "mode": "auto",
        "model_id": DEFAULT_MODEL_ID,
        "active_skill_id": "",
    }

    result = runtime.run_turn(**request)

    assert result == {"delegated": True}
    assert coordinator.calls == [request]


def test_agent_runtime_accepts_injected_turn_operations(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    _seed_company(tmp_path)
    _seed_test_model()
    baseline = _runtime("company_test")
    operations = baseline.coordinator.operations

    runtime = AgentRuntime(
        company_id="company_test",
        model_adapter=ScriptedModelAdapter([]),
        operations=operations,
    )

    assert runtime.coordinator.operations is operations


class CapturingExecutor:
    def execute(self, context: SkillExecutionContext) -> SkillResult:
        raise AssertionError("schema wiring probe must not execute")


def test_agent_runtime_passes_manifest_output_schemas_to_executors(monkeypatch):
    captured = {}
    adapter_ids = {
        "GeneralChatExecutor": "general_chat",
        "PolicyWeeklyImpactExecutor": "policy_weekly_impact",
        "RecentNewsReportExecutor": "recent_news_report",
        "ResearchReportExecutor": "research_report",
        "CompanyWikiBlueprintExecutor": "company_wiki_blueprint",
        "OfficialLegalReferenceExecutor": "official_legal_reference",
    }

    def factory(executor_id):
        def build(fn, output_schema):
            captured[executor_id] = (fn, output_schema)
            return CapturingExecutor()

        return build

    for adapter_name, executor_id in adapter_ids.items():
        monkeypatch.setattr(agent_runtime_module, adapter_name, factory(executor_id))

    runtime = AgentRuntime(
        company_id="company_test",
        model_adapter=ScriptedModelAdapter([]),
    )

    for skill_id in set(adapter_ids.values()) - {"official_legal_reference"}:
        fn, output_schema = captured[skill_id]
        assert callable(fn)
        assert output_schema is runtime.registry.get_manifest(skill_id).output_schema
    assert captured["official_legal_reference"][1] is SkillOutput


class RecordingLegalExecutor:
    def __init__(self):
        self.contexts = []
        self.results = []

    def execute(self, context: SkillExecutionContext) -> SkillResult:
        self.contexts.append(context)
        result = SkillResult(
            answer="registered legal executor",
            actual_execution_mode=ExecutionMode.DETERMINISTIC,
            stop_reason="success",
            citations=[
                CitationRef(
                    citation_type="official_legal_reference",
                    title="Recorded official source",
                    url="https://example.test/official",
                    source_ref="article-55",
                    path="evidence/article-55.json",
                )
            ],
        )
        self.results.append(result)
        return result


def test_official_shortcut_uses_registered_deterministic_executor(
    tmp_path,
    monkeypatch,
):
    _use_temp_project_root(monkeypatch, tmp_path)
    _seed_company(tmp_path)
    _seed_test_model()
    executor = RecordingLegalExecutor()
    executor_registry = ExecutorRegistry()
    executor_registry.register("official_legal_reference", executor)
    runtime = AgentRuntime(
        company_id="company_test",
        executor_registry=executor_registry,
        model_adapter=ScriptedModelAdapter([]),
    )
    store = PolicyMemoryStore("company_test")
    session_id = store.create_chat_session(
        title="Official reference",
        mode="skill",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="research_report",
    )
    message = "\u8bf7\u6838\u9a8c\u4e2a\u4eba\u4fe1\u606f\u4fdd\u62a4\u6cd5 article 55"

    result = runtime.run_turn(
        session_id=session_id,
        message=message,
        mode="skill",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="research_report",
    )

    assert result["selected_skill_id"] == "research_report"
    assert result["answer"] == "registered legal executor"
    assert executor.results[0].actual_execution_mode is ExecutionMode.DETERMINISTIC
    assert len(executor.contexts) == 1
    context = executor.contexts[0]
    assert context.message == message
    assert context.services["legal_reference"]["reference_id"] == "pipl_article_55"
    with pytest.raises(TypeError):
        context.services["legal_reference"]["answer"] = "changed"

    expected_citations = [
        {
            "type": "official_legal_reference",
            "title": "Recorded official source",
            "url": "https://example.test/official",
            "source_ref": "article-55",
            "path": "evidence/article-55.json",
        }
    ]
    assert result["citations"] == expected_citations
    messages = store.list_chat_messages(session_id)
    assert messages[-1]["metadata"]["citations"] == expected_citations


def test_session_context_declarations_do_not_request_global_evidence():
    assert AgentRuntime._is_immediate_history_query(
        "本次会话的输出偏好是先列事实；不要写入长期记忆。"
    )
    assert AgentRuntime._is_session_context_declaration(
        "未决事项：iFinD 数据源授权状态尚未核实。"
    )
    assert AgentRuntime._is_session_context_declaration(
        "禁止项：不要把工程建议描述成已上线功能。"
    )
    assert AgentRuntime._is_session_context_declaration(
        "纠正上一条计划：改为每天 09:00 生成。"
    )
    assert not AgentRuntime._is_immediate_history_query(
        "本次会话请读取长期记忆中保存的输出偏好。"
    )
