from __future__ import annotations

from pathlib import Path

import pytest

from policy_impact.company_wiki import loader as wiki_loader
from policy_impact.harness.model_config import DEFAULT_MODEL_ID
from policy_impact.memory.store import PolicyMemoryStore
from policy_impact.runtime.run_status import RunStatus


def _use_temp_project_root(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(wiki_loader, "project_root", lambda: tmp_path, raising=False)


def test_chat_sessions_messages_and_model_configs_roundtrip(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    store = PolicyMemoryStore("company_test")

    store.seed_model_configs()
    models = store.list_model_configs()
    assert [model["model_id"] for model in models] == [DEFAULT_MODEL_ID]
    assert models[0]["is_default"] is True

    session_id = store.create_chat_session(
        title="Research chat",
        mode="research",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="research_report",
        metadata={"source": "test"},
    )
    assert store.get_chat_session(session_id)["message_count"] == 0

    store.update_chat_session_settings(
        session_id,
        title="Updated chat",
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )
    user_message_id = store.save_chat_message(
        session_id,
        role="user",
        content="Track policy updates for my company.",
        metadata={"turn": 1},
    )
    assistant_message_id = store.save_chat_message(
        session_id,
        role="assistant",
        content="I will check the policy impact.",
        metadata={"turn": 1, "run_id": "run_1"},
    )

    messages = store.list_chat_messages(session_id)
    assert [message["message_id"] for message in messages] == [user_message_id, assistant_message_id]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[0]["metadata"] == {"turn": 1}

    session = store.get_chat_session(session_id)
    assert session["title"] == "Updated chat"
    assert session["mode"] == "chat"
    assert session["model_id"] == DEFAULT_MODEL_ID
    assert session["active_skill_id"] == "general_chat"
    assert session["metadata"] == {"source": "test"}
    assert session["message_count"] == 2

    sessions = store.list_chat_sessions()
    assert [item["session_id"] for item in sessions] == [session_id]
    assert sessions[0]["last_message_at"] == messages[-1]["created_at"]


def test_seed_model_configs_prunes_stale_defaults(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    store = PolicyMemoryStore("company_test")

    store.upsert_model_config({"model_id": "stale-local", "display_name": "Stale Local"})
    store.seed_model_configs()

    models = store.list_model_configs()
    assert [item["model_id"] for item in models] == [DEFAULT_MODEL_ID]
    assert models[0]["display_name"] == DEFAULT_MODEL_ID


def test_upsert_model_config_adds_user_model_and_updates_existing(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    store = PolicyMemoryStore("company_test")
    store.seed_model_configs()

    store.upsert_model_config(
        {
            "model_id": "qwen-local",
            "display_name": "Qwen Local",
            "provider": "openai-compatible",
            "model_name": "qwen2.5-72b",
            "endpoint": "http://localhost:8000/v1",
            "temperature": 0.3,
            "max_tokens": 12000,
            "metadata": {"api_key": "<redacted>", "source": "ui"},
        }
    )
    store.upsert_model_config(
        {
            "model_id": "qwen-local",
            "display_name": "Qwen Local Updated",
            "provider": "openai-compatible",
            "model_name": "qwen3",
            "endpoint": "http://localhost:9000/v1",
            "temperature": 0.1,
            "max_tokens": 16000,
            "metadata": {"api_key": "<redacted>"},
        }
    )

    model = next(item for item in store.list_model_configs() if item["model_id"] == "qwen-local")

    assert model["display_name"] == "Qwen Local Updated"
    assert model["model_name"] == "qwen3"
    assert model["endpoint"] == "http://localhost:9000/v1"
    assert model["temperature"] == 0.1
    assert model["max_tokens"] == 16000
    assert model["metadata"] == {"api_key": "<redacted>"}


def test_search_memory_decodes_metadata_for_callers(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    store = PolicyMemoryStore("company_test")

    memory_id = store.save_memory(
        namespace="company:company_test:chat",
        memory_type="preference",
        content="User cares about observable Agent Harness traces.",
        importance=0.9,
        metadata={"source_type": "manual", "path": "memory/preference.md"},
    )

    results = store.search_memory("Agent Harness traces", top_k=5)

    assert results[0]["id"] == memory_id
    assert results[0]["metadata"] == {"source_type": "manual", "path": "memory/preference.md"}


def test_save_memory_once_deduplicates_exact_explicit_memory(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    store = PolicyMemoryStore("company_test")

    first_id, first_created = store.save_memory_once(
        namespace="company:company_test:user_profile",
        memory_type="explicit_user_memory",
        content="先列证据缺口，再列行动建议",
    )
    second_id, second_created = store.save_memory_once(
        namespace="company:company_test:user_profile",
        memory_type="explicit_user_memory",
        content="先列证据缺口，再列行动建议",
    )

    assert first_created is True
    assert second_created is False
    assert second_id == first_id


def test_delete_memory_removes_only_requested_item(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    store = PolicyMemoryStore("company_test")
    first_id = store.save_memory("test", "explicit_user_memory", "DELETE-ME")
    second_id = store.save_memory("test", "explicit_user_memory", "KEEP-ME")

    assert store.delete_memory(first_id) is True
    assert store.delete_memory(first_id) is False
    assert [item["id"] for item in store.search_memory("KEEP-ME", top_k=5)] == [second_id]


def test_conversation_summary_roundtrip_updates_same_session(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    store = PolicyMemoryStore("company_test")
    session_id = store.create_chat_session(
        title="Long context",
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )

    created = store.upsert_conversation_summary(
        session_id=session_id,
        summary={
            "summary": "用户要求区分公开事实与推断。",
            "user_facts": [],
            "decisions": ["引用 fact_id"],
            "open_loops": ["核验 iFinD 服务对象"],
        },
        source_message_count=4,
        source_hash="hash-v1",
    )
    updated = store.upsert_conversation_summary(
        session_id=session_id,
        summary={
            "summary": "用户要求区分公开事实与推断，并关注 iFinD。",
            "user_facts": [],
            "decisions": ["引用 fact_id"],
            "open_loops": [],
        },
        source_message_count=6,
        source_hash="hash-v2",
    )

    assert created["source_message_count"] == 4
    assert updated["source_message_count"] == 6
    assert updated["source_hash"] == "hash-v2"
    assert updated["summary"]["open_loops"] == []
    assert store.get_conversation_summary(session_id) == updated


def test_company_knowledge_reindex_removes_stale_pages_and_facts(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    store = PolicyMemoryStore("company_test")
    profile = {"company_id": "company_test", "company_name": "Demo"}
    store.index_company_knowledge(
        profile,
        [
            {
                "path": "wiki/old.md",
                "meta": {"domain": "business", "importance": 4},
                "body": "old",
                "facts": [
                    {
                        "fact_id": "fact.old",
                        "source_path": "wiki/old.md",
                        "value": "stale",
                        "source": "old-source",
                    }
                ],
            }
        ],
    )
    store.index_company_knowledge(
        profile,
        [
            {
                "path": "wiki/current.md",
                "meta": {"domain": "business", "importance": 5},
                "body": "current",
                "facts": [
                    {
                        "fact_id": "fact.current",
                        "source_path": "wiki/current.md",
                        "value": "current",
                        "source": "current-source",
                    }
                ],
            }
        ],
    )

    with store._conn() as conn:
        page_ids = [row[0] for row in conn.execute("SELECT page_id FROM wiki_pages ORDER BY page_id")]
        fact_ids = [row[0] for row in conn.execute("SELECT fact_id FROM company_facts ORDER BY fact_id")]

    assert page_ids == ["wiki/current.md"]
    assert fact_ids == ["fact.current"]


def test_agent_runs_and_steps_roundtrip(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    store = PolicyMemoryStore("company_test")
    session_id = store.create_chat_session(
        title="General chat",
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )
    user_message_id = store.save_chat_message(
        session_id,
        role="user",
        content="What changed today?",
    )

    run_id = store.create_agent_run(
        session_id=session_id,
        user_message_id=user_message_id,
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        skill_id="general_chat",
        metadata={"loop": "react"},
    )
    first_step_id = store.add_agent_step(
        run_id,
        step_type="context",
        title="Build context manifest",
        status="done",
        input_payload={"message_id": user_message_id},
        output_payload={"sections": ["memory", "session"]},
        metadata={"token_budget": 4096},
    )
    second_step_id = store.add_agent_step(
        run_id,
        step_type="tool",
        title="Search memory",
        status="blocked",
        tool_calls=[{"tool_name": "memory_search", "decision": "ask"}],
        gate_result={"decision": "ask", "reason": "low trust input"},
    )
    assistant_message_id = store.save_chat_message(
        session_id,
        role="assistant",
        content="No high confidence update yet.",
    )

    assert store.claim_agent_run(run_id) is True
    store.finish_agent_run(
        run_id,
        status="done",
        assistant_message_id=assistant_message_id,
        metrics={"steps": 2},
        artifacts=[{"artifact_type": "trace", "path": "runs/run_1/trace.json"}],
    )

    steps = store.list_agent_steps(run_id)
    assert [step["step_id"] for step in steps] == [first_step_id, second_step_id]
    assert [step["step_index"] for step in steps] == [1, 2]
    assert steps[0]["output_payload"] == {"sections": ["memory", "session"]}
    assert steps[1]["tool_calls"] == [{"tool_name": "memory_search", "decision": "ask"}]
    assert steps[1]["gate_result"]["decision"] == "ask"

    run = store.get_agent_run(run_id)
    assert run["status"] == "done"
    assert run["assistant_message_id"] == assistant_message_id
    assert run["metadata"] == {"loop": "react"}
    assert run["metrics"] == {"steps": 2}
    assert run["artifacts"] == [{"artifact_type": "trace", "path": "runs/run_1/trace.json"}]
    assert run["finished_at"]

    latest = store.latest_agent_run(session_id)
    assert latest["run_id"] == run_id


def test_agent_run_metadata_merge_and_status_transitions_are_atomic(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    store = PolicyMemoryStore("company_test")
    session_id = store.create_chat_session(
        title="Lifecycle",
        mode="auto",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="",
    )
    user_message_id = store.save_chat_message(session_id, role="user", content="Run")
    run_id = store.create_agent_run(
        session_id=session_id,
        user_message_id=user_message_id,
        mode="auto",
        model_id=DEFAULT_MODEL_ID,
        skill_id="general_chat",
        metadata={"runtime": "agent_workbench", "keep": True},
        status=RunStatus.CREATED,
    )

    store.update_agent_run_metadata(run_id, {"execution_mode": "model_once"})
    store.update_agent_run_metadata(run_id, {"stop_reason": "success"})
    store.transition_agent_run_status(run_id, RunStatus.RUNNING)
    store.finish_agent_run(run_id, RunStatus.DONE)

    run = store.get_agent_run(run_id)
    assert run["metadata"] == {
        "runtime": "agent_workbench",
        "keep": True,
        "execution_mode": "model_once",
        "stop_reason": "success",
    }
    assert run["status"] == RunStatus.DONE.value
    with pytest.raises(ValueError, match="illegal Run transition"):
        store.finish_agent_run(run_id, RunStatus.FAILED)
