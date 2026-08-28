from __future__ import annotations

from pathlib import Path

import pytest

from policy_impact.app import chat_workbench_service as service_module
from policy_impact.app import api as api_module
from policy_impact.app.chat_workbench_service import ChatWorkbenchService
from policy_impact.company_wiki import loader as wiki_loader
from policy_impact.harness.model_config import DEFAULT_MODEL_ID
from policy_impact.harness.model_gateway import ScriptedModelAdapter
from policy_impact.harness.agent_runtime import AgentRuntime
from policy_impact.memory.store import PolicyMemoryStore


def _seed_company(tmp_path: Path) -> None:
    company_dir = tmp_path / "data" / "companies" / "company_test"
    wiki_dir = company_dir / "wiki"
    wiki_dir.mkdir(parents=True)
    (company_dir / "company.yaml").write_text(
        '{"company_id": "company_test", "company_name": "Demo AI Co"}',
        encoding="utf-8",
    )
    (wiki_dir / "facts.md").write_text(
        """---
domain: product
importance: 5
confidence: 0.9
---
## FACT: fact_runtime
- value: Runs a single-user Agent Workbench with traceable skills and memory.
- importance: 5
- confidence: 0.9
- source: test wiki
- policy_relevance: [agent, runtime, harness]
""",
        encoding="utf-8",
    )


def _use_temp_project_root(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(wiki_loader, "project_root", lambda: tmp_path, raising=False)


def _service() -> ChatWorkbenchService:
    return ChatWorkbenchService(
        model_adapter_factory=lambda: ScriptedModelAdapter(
            [
                {
                    "type": "general_chat",
                    "output": {"answer": "Agent Runtime facts are available through the Workbench trace."},
                }
            ]
        )
    )


def test_company_fact_marker_extraction_accepts_prompt_contract_and_model_styles():
    assert AgentRuntime._extract_company_fact_ids(
        "产品事实 [ai_product.ifind]，能力事实 [fact_id: qualification.ai_benchmark]，风险 [P0]。"
    ) == {"ai_product.ifind", "qualification.ai_benchmark"}


def test_general_citations_only_keep_evidence_used_by_the_final_answer():
    citations = AgentRuntime._ground_general_chat_citations(
        "只根据企业知识库回答",
        "已披露 iFinD 能力 [fact_id: ai_product.ifind]。",
        [
            {"type": "company_fact", "fact_id": "ai_product.ifind"},
            {"type": "company_fact", "fact_id": "profile.location"},
            {"type": "memory_item", "memory_id": "unrelated"},
        ],
        memory_requested=False,
        strict_knowledge_only=True,
        memory_items=[],
    )

    assert citations == [{"type": "company_fact", "fact_id": "ai_product.ifind"}]


def test_memory_citations_only_keep_content_repeated_in_the_answer():
    citations = AgentRuntime._ground_general_chat_citations(
        "我刚才要求你记住的 policy_pref 是什么？",
        "policy_pref = 先列证据缺口。",
        [
            {"type": "memory_item", "memory_id": "used"},
            {"type": "memory_item", "memory_id": "unused"},
        ],
        memory_requested=False,
        strict_knowledge_only=False,
        memory_items=[
            {"id": "used", "content": "policy_pref = 先列证据缺口"},
            {"id": "unused", "content": "location = Hangzhou"},
        ],
    )

    assert citations == [{"type": "memory_item", "memory_id": "used"}]


def test_memory_citation_survives_grounded_paraphrase_with_explicit_id():
    citations = AgentRuntime._ground_general_chat_citations(
        "我之前让你记住的 ACCEPT-MEM-1234 是什么？",
        "ACCEPT-MEM-1234 的重点是 ContextManifest 和 Eval [memory_id: mem-1234]。",
        [
            {"type": "memory_item", "memory_id": "mem-1234"},
            {"type": "memory_item", "memory_id": "unrelated"},
        ],
        memory_requested=False,
        strict_knowledge_only=False,
        memory_items=[
            {"id": "mem-1234", "content": "ACCEPT-MEM-1234：Harness 学习重点是 ContextManifest、Tool Policy 和 Eval"},
            {"id": "unrelated", "content": "location = Hangzhou"},
        ],
    )

    assert citations == [{"type": "memory_item", "memory_id": "mem-1234"}]


def test_policy_citations_only_keep_sources_named_in_the_answer():
    citations = AgentRuntime._ground_policy_citations(
        "仅核验《网络数据安全风险评估办法》，原文：https://example.test/network-data",
        [
            {"type": "policy_source", "title": "网络数据安全风险评估办法", "url": "https://example.test/network-data"},
            {"type": "policy_source", "title": "无关政策", "url": "https://example.test/unrelated"},
        ],
    )

    assert [item["title"] for item in citations] == ["网络数据安全风险评估办法"]


def test_chat_workbench_service_creates_session_and_sends_message(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("ANTHROPIC_MODEL", DEFAULT_MODEL_ID)
    _seed_company(tmp_path)
    service = _service()

    session = service.create_session(
        company_id="company_test",
        title="Agent Workbench",
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )

    def fail_if_reseeded(*_args, **_kwargs):
        raise AssertionError("send_message must not rewrite fixed model configuration")

    monkeypatch.setattr(PolicyMemoryStore, "seed_model_configs", fail_if_reseeded)
    result = service.send_message(
        company_id="company_test",
        session_id=session["session_id"],
        message="What agent runtime facts are available?",
    )
    messages = service.list_messages("company_test", session["session_id"])
    trace = service.latest_trace("company_test", session["session_id"])

    assert result["answer"]
    assert result["selected_skill_id"] == "general_chat"
    assert [item["role"] for item in messages][-2:] == ["user", "assistant"]
    assert trace["run_id"] == result["run_id"]
    assert trace["observability_schema_version"] == "workbench.trace.v2"
    assert trace["trace_summary"]["model_id"] == DEFAULT_MODEL_ID
    assert trace["trace_summary"]["model_used"] is True
    assert trace["trace_summary"]["execution_mode"] == "model_assisted"
    assert trace["trace_summary"]["tool_call_count"] >= 1
    assert trace["context_summary"]["visible_keys"] == [
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
    assert trace["context_summary"]["compression_policy"] == "chat_context.budgeted.v1"
    assert trace["context_summary"]["company_facts_included"] >= 1
    assert trace["context_summary"]["budget_utilization"] > 0
    assert trace["memory_summary"]["read_steps"] == 1
    assert trace["tool_summary"]["unique_tools"]
    assert trace["trace_summary"]["structured_event_count"] == len(trace["steps"])
    assert len(trace["structured_events"]) == len(trace["steps"])
    assert trace["structured_events"][0]["attributes"]["agent.model.selected"] == DEFAULT_MODEL_ID
    model_events = [event for event in trace["structured_events"] if event["step_type"] == "model_call"]
    assert model_events[0]["attributes"]["gen_ai.request.model"] == DEFAULT_MODEL_ID
    assert any(event["observation_type"] == "tool" for event in trace["structured_events"])
    assert any(
        event["attributes"].get("agent.context.compression_policy") == "chat_context.budgeted.v1"
        for event in trace["structured_events"]
    )
    assert [step["step_type"] for step in trace["steps"]][-1] == "run_stopped"
    historical = service.trace_for_run("company_test", session["session_id"], result["run_id"])
    assert historical["run_id"] == result["run_id"]


def test_trace_distinguishes_selected_model_from_deterministic_execution(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("ANTHROPIC_MODEL", DEFAULT_MODEL_ID)
    _seed_company(tmp_path)
    service = _service()
    session = service.create_session(
        company_id="company_test",
        title="Legal reference",
        mode="auto",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="",
    )

    result = service.send_message(
        "company_test",
        session["session_id"],
        "《个人信息保护法》第55条有哪些情形？",
    )
    trace = service.trace_for_run("company_test", session["session_id"], result["run_id"])

    assert trace["trace_summary"]["selected_model_id"] == DEFAULT_MODEL_ID
    assert trace["trace_summary"]["model_call_count"] == 0
    assert trace["trace_summary"]["model_used"] is False
    assert trace["trace_summary"]["execution_mode"] == "deterministic"


def test_service_shares_one_run_lock_registry_across_runtime_instances(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    _seed_company(tmp_path)
    captured_locks = []

    class RecordingRuntime:
        def __init__(self, **kwargs):
            captured_locks.append(kwargs["run_locks"])

        def run_turn(self, **kwargs):
            return {"run_id": f"run-{len(captured_locks)}"}

    monkeypatch.setattr(service_module, "AgentRuntime", RecordingRuntime)
    service = _service()
    session = service.create_session(
        company_id="company_test",
        title="Shared locks",
        mode="auto",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="",
    )

    service.send_message("company_test", session["session_id"], "first")
    service.send_message("company_test", session["session_id"], "second")

    assert len(captured_locks) == 2
    assert captured_locks[0] is captured_locks[1]
    assert captured_locks[0] is service.run_locks


def test_chat_workbench_service_lists_models_skills_and_sessions(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    _seed_company(tmp_path)
    service = _service()

    session = service.create_session(
        company_id="company_test",
        title="Research",
        mode="skill",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="research_report",
    )
    service.update_session(
        "company_test",
        session["session_id"],
        title="Updated Research",
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )

    models = service.list_models("company_test")
    skills = service.list_skills()
    sessions = service.list_sessions("company_test")

    assert [item["model_id"] for item in models] == [DEFAULT_MODEL_ID]
    assert [item["id"] for item in skills] == [
        "general_chat",
        "external_impact_report",
    ]
    impact_skill = next(item for item in skills if item["id"] == "external_impact_report")
    policy_skill = impact_skill
    assert set(policy_skill) == {
        "id",
        "name",
        "description",
        "mode",
        "tool_policy",
        "artifact_types",
        "subagents",
    }
    assert policy_skill["mode"] == "subagent_workflow"
    assert policy_skill["artifact_types"] == ["markdown_report", "html_report", "run_artifact"]
    assert policy_skill["subagents"] == ["skeptic", "research_report_subagent"]
    assert policy_skill["tool_policy"] == {
        "impact": [
            "artifact_read",
            "memory_search",
            "company_profile_read",
            "company_context_pack_build",
            "policy_fetch_recent",
            "policy_ingest",
            "policy_retrieve",
            "policy_clause_extract",
            "company_wiki_search",
            "news.load_items",
            "news.structure_events",
            "news.analyze_company_impact",
            "report_workflow.run",
            "report_write",
        ]
    }
    wiki = service.wiki_tree("company_test")
    assert wiki["schema_version"] == "workbench.wiki_tree.v1"
    assert wiki["stats"]["page_count"] == 1
    assert wiki["stats"]["fact_count"] == 1
    assert all(isinstance(tools, list) for tools in policy_skill["tool_policy"].values())
    assert sessions[0]["title"] == "Updated Research"
    assert sessions[0]["active_skill_id"] == "general_chat"


def test_workbench_wiki_update_writes_markdown_and_reindexes_sqlite(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    _seed_company(tmp_path)
    service = _service()

    updated = service.update_wiki_page(
        "company_test",
        "wiki/facts.md",
        """## FACT: fact_runtime
- value: Updated Workbench Wiki data is written back to markdown and indexed.
- importance: 5
- confidence: 0.95
- source: user_confirmed
- policy_relevance: [agent, runtime, wiki]

## FACT: fact_new_metric
- value: Wiki 保存动作会刷新 SQLite company_facts。
- importance: 4
- confidence: 0.9
- source: user_confirmed
- policy_relevance: [wiki, sqlite]
""",
    )

    saved_text = (
        tmp_path / "data" / "companies" / "company_test" / "wiki" / "facts.md"
    ).read_text(encoding="utf-8")
    assert "Updated Workbench Wiki data" in saved_text
    assert updated["schema_version"] == "workbench.wiki_update.v1"
    assert updated["page"]["fact_count"] == 2
    assert updated["wiki"]["stats"]["fact_count"] == 2

    store = PolicyMemoryStore("company_test")
    with store._conn() as conn:
        rows = conn.execute(
            "SELECT fact_id, value FROM company_facts ORDER BY fact_id"
        ).fetchall()
    assert [(row["fact_id"], row["value"]) for row in rows] == [
        ("fact_new_metric", "Wiki 保存动作会刷新 SQLite company_facts。"),
        ("fact_runtime", "Updated Workbench Wiki data is written back to markdown and indexed."),
    ]


def test_workbench_wiki_update_rejects_paths_outside_wiki(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    _seed_company(tmp_path)
    service = _service()

    with pytest.raises(ValueError, match="wiki page path"):
        service.update_wiki_page("company_test", "../company.yaml", "bad")


def test_workbench_api_create_session_maps_unknown_skill_to_bad_request(monkeypatch):
    class UnknownSkillService:
        def create_session(self, **_kwargs):
            raise KeyError("unknown skill 'missing_skill'")

    monkeypatch.setattr(api_module, "workbench_service", UnknownSkillService())

    with pytest.raises(api_module.HTTPException) as exc_info:
        api_module.create_workbench_session(
            "company_test",
            api_module.WorkbenchSessionRequest(
                title="Bad Skill",
                mode="auto",
                model_id=DEFAULT_MODEL_ID,
                active_skill_id="missing_skill",
            ),
        )

    assert exc_info.value.status_code == 400
    assert "missing_skill" in str(exc_info.value.detail)


def test_chat_workbench_service_rejects_user_model_config(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    _seed_company(tmp_path)
    service = _service()

    with pytest.raises(ValueError, match="模型管理已禁用"):
        service.save_model_config(
            "company_test",
            {
                "model_id": "deepseek-compatible",
                "display_name": "DeepSeek 兼容接口",
                "provider": "openai-compatible",
                "model_name": "deepseek-chat",
            },
        )


def test_chat_workbench_service_rejects_send_without_model_credentials(tmp_path, monkeypatch):
    _use_temp_project_root(monkeypatch, tmp_path)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_MODEL", DEFAULT_MODEL_ID)
    _seed_company(tmp_path)
    service = ChatWorkbenchService()

    session = service.create_session(
        company_id="company_test",
        title="No Model",
        mode="chat",
        model_id="",
        active_skill_id="general_chat",
    )

    models = service.list_models("company_test")
    assert models[0]["model_id"] == DEFAULT_MODEL_ID
    assert models[0]["metadata"]["available"] is False
    with pytest.raises(ValueError, match="当前没有可用模型"):
        service.send_message(
            company_id="company_test",
            session_id=session["session_id"],
            message="Can you answer without a model?",
        )
