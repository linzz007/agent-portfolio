import json
import warnings

import pytest
from pydantic import BaseModel, ValidationError, create_model

from policy_impact.harness.skills import SkillDefinition as CompatibilitySkillDefinition
from policy_impact.harness.skills import SkillRegistry as CompatibilitySkillRegistry
from policy_impact.mcp_server.registry import TOOLS
from policy_impact.runtime.execution_mode import ExecutionMode
from policy_impact.skills.manifest import SkillManifest
from policy_impact.skills.registry import SkillRegistry
from policy_impact.skills.schemas import (
    ExternalImpactOutput,
    GeneralChatOutput,
    NewsReportOutput,
    PolicyImpactOutput,
    ResearchReportOutput,
    SkillInput,
    SkillOutput,
    WikiBlueprintOutput,
)


def test_default_skills_are_listed_in_contract_order():
    registry = SkillRegistry()

    skill_ids = [skill.id for skill in registry.list_manifests()]

    assert skill_ids == [
        "general_chat",
        "external_impact_report",
        "policy_weekly_impact",
        "recent_news_report",
        "research_report",
        "company_wiki_blueprint",
    ]


def test_default_manifests_have_executable_contracts():
    registry = SkillRegistry()
    impact = registry.get_manifest("external_impact_report")
    policy = registry.get_manifest("policy_weekly_impact")

    assert impact.execution_mode is ExecutionMode.SUBAGENT_WORKFLOW
    assert impact.executor_id == "external_impact_report"
    assert impact.max_steps == 32
    assert impact.timeout_seconds == 240
    assert "skeptic" in impact.allowed_subagents
    assert "research_report_subagent" in impact.allowed_subagents
    assert policy.execution_mode is ExecutionMode.SUBAGENT_WORKFLOW
    assert policy.executor_id == "policy_weekly_impact"
    assert policy.max_steps == 24
    assert policy.timeout_seconds == 180
    assert "skeptic" in policy.allowed_subagents


def test_each_default_manifest_has_required_contract_fields():
    registry = SkillRegistry()

    for skill in registry.list_manifests():
        assert skill.id
        assert skill.version
        assert skill.name
        assert skill.description
        assert isinstance(skill.execution_mode, ExecutionMode)
        assert skill.executor_id
        assert issubclass(skill.input_schema, BaseModel)
        assert issubclass(skill.output_schema, BaseModel)
        assert isinstance(skill.allowed_tools, tuple)
        assert isinstance(skill.allowed_subagents, tuple)
        assert skill.context_policy_id
        assert skill.memory_policy_id
        assert skill.permission_policy_id
        assert skill.max_steps > 0
        assert skill.max_model_calls > 0
        assert skill.timeout_seconds > 0


def test_default_manifests_have_exact_execution_modes_and_schema_contracts():
    manifests = SkillRegistry().list_manifests()

    assert [manifest.execution_mode for manifest in manifests] == [
        ExecutionMode.AGENT_LOOP,
        ExecutionMode.SUBAGENT_WORKFLOW,
        ExecutionMode.SUBAGENT_WORKFLOW,
        ExecutionMode.WORKFLOW,
        ExecutionMode.AGENT_LOOP,
        ExecutionMode.DETERMINISTIC,
    ]
    assert [manifest.output_schema for manifest in manifests] == [
        GeneralChatOutput,
        ExternalImpactOutput,
        PolicyImpactOutput,
        NewsReportOutput,
        ResearchReportOutput,
        WikiBlueprintOutput,
    ]
    assert all(manifest.input_schema is SkillInput for manifest in manifests)


def test_default_manifest_tools_and_subagents_preserve_current_contracts():
    registry = SkillRegistry()

    assert registry.get_manifest("general_chat").allowed_tools == (
        "artifact_read",
        "memory_search",
        "company_wiki_search",
        "policy_fetch_recent",
        "policy_retrieve",
        "news.load_items",
    )
    assert registry.get_manifest("general_chat").allowed_subagents == (
        "collector",
        "analyst",
        "skeptic",
        "verifier",
    )
    assert registry.get_manifest("external_impact_report").allowed_tools == (
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
    )
    assert registry.get_manifest("policy_weekly_impact").allowed_tools == (
        "artifact_read",
        "memory_search",
        "company_profile_read",
        "company_context_pack_build",
        "policy_fetch_recent",
        "policy_ingest",
        "policy_retrieve",
        "policy_clause_extract",
        "company_wiki_search",
        "report_write",
    )
    assert registry.get_manifest("recent_news_report").allowed_tools == (
        "news.load_items",
        "news.structure_events",
        "news.analyze_company_impact",
    )
    assert registry.get_manifest("research_report").allowed_tools == ()
    assert registry.get_manifest("company_wiki_blueprint").allowed_tools == (
        "company_wiki_blueprint",
    )
    assert registry.get_manifest("research_report").allowed_subagents == (
        "research_report_subagent",
    )
    assert registry.get_manifest("external_impact_report").allowed_subagents == (
        "skeptic",
        "research_report_subagent",
    )


def test_every_default_manifest_tool_is_registered():
    missing = {
        (manifest.id, tool_id)
        for manifest in SkillRegistry().list_manifests()
        for tool_id in manifest.allowed_tools
        if tool_id not in TOOLS
    }

    assert missing == set()


def test_artifact_types_have_output_schema_as_their_source_of_truth():
    registry = SkillRegistry()
    expected = {
        "general_chat": ["message"],
        "external_impact_report": ["markdown_report", "html_report", "run_artifact"],
        "policy_weekly_impact": ["markdown_report", "html_report", "run_artifact"],
        "recent_news_report": ["markdown_report", "html_report", "run_artifact"],
        "research_report": ["markdown_report"],
        "company_wiki_blueprint": ["json_blueprint"],
    }

    for manifest in registry.list_manifests():
        assert manifest.output_schema.model_json_schema().get("artifact_types") == expected[manifest.id]
        assert "artifact_types" not in type(manifest).model_fields


def test_artifact_type_schema_metadata_is_immutable_and_isolated():
    first = PolicyImpactOutput.model_json_schema()
    second = PolicyImpactOutput.model_json_schema()

    assert PolicyImpactOutput.artifact_types == (
        "markdown_report",
        "html_report",
        "run_artifact",
    )
    assert first["artifact_types"] is not second["artifact_types"]

    first["artifact_types"].append("mutated")

    assert second["artifact_types"] == ["markdown_report", "html_report", "run_artifact"]
    assert PolicyImpactOutput.model_json_schema()["artifact_types"] == [
        "markdown_report",
        "html_report",
        "run_artifact",
    ]


def test_manifest_strips_descriptor_fields_and_identifier_entries():
    base = SkillRegistry().get_manifest("general_chat")
    payload = {
        **base.model_dump(),
        "id": "  test_skill  ",
        "version": " 1.2.3-rc.1+build.5 ",
        "name": "  Test Skill  ",
        "description": "  Test description.  ",
        "executor_id": "  executor.test_skill  ",
        "allowed_tools": [" news.load_items ", "news.structure_events"],
        "allowed_subagents": [" skeptic "],
        "context_policy_id": " context.test_skill.v1 ",
        "memory_policy_id": " memory.test_skill.v1 ",
        "permission_policy_id": " tools.test_skill.v1 ",
    }

    manifest = SkillManifest.model_validate(payload)

    assert manifest.id == "test_skill"
    assert manifest.version == "1.2.3-rc.1+build.5"
    assert manifest.name == "Test Skill"
    assert manifest.description == "Test description."
    assert manifest.executor_id == "executor.test_skill"
    assert manifest.allowed_tools == ("news.load_items", "news.structure_events")
    assert manifest.allowed_subagents == ("skeptic",)
    assert manifest.context_policy_id == "context.test_skill.v1"
    assert manifest.memory_policy_id == "memory.test_skill.v1"
    assert manifest.permission_policy_id == "tools.test_skill.v1"


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("id", ""),
        ("id", "Bad Skill"),
        ("executor_id", "executor-name"),
        ("context_policy_id", "context bad"),
        ("memory_policy_id", ".memory.bad"),
        ("permission_policy_id", "tools.bad."),
        ("name", "   "),
        ("description", "\t"),
        ("version", "1.0"),
        ("version", "v1.0.0"),
    ],
)
def test_manifest_rejects_invalid_descriptor_fields(field_name, invalid_value):
    manifest = SkillRegistry().get_manifest("general_chat")
    payload = {**manifest.model_dump(), field_name: invalid_value}

    with pytest.raises(ValidationError):
        SkillManifest.model_validate(payload)


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("allowed_tools", ["news.load_items", " news.load_items "]),
        ("allowed_tools", ["news.load-items"]),
        ("allowed_tools", ["  "]),
        ("allowed_subagents", ["skeptic", "skeptic"]),
        ("allowed_subagents", ["bad subagent"]),
        ("allowed_subagents", [""]),
    ],
)
def test_manifest_rejects_invalid_or_duplicate_allowlist_entries(field_name, invalid_value):
    manifest = SkillRegistry().get_manifest("general_chat")
    payload = {**manifest.model_dump(), field_name: invalid_value}

    with pytest.raises(ValidationError):
        SkillManifest.model_validate(payload)


def test_manifest_uses_default_schema_types_when_omitted():
    payload = SkillRegistry().get_manifest("general_chat").model_dump()
    payload.pop("input_schema")
    payload.pop("output_schema")

    manifest = SkillManifest.model_validate(payload)

    assert manifest.input_schema is SkillInput
    assert manifest.output_schema is SkillOutput


def test_manifest_json_descriptor_uses_stable_schema_references_without_warnings():
    manifest = SkillRegistry().get_manifest("general_chat")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        descriptor = json.loads(manifest.model_dump_json())
        manifest_schema = SkillManifest.model_json_schema()

    assert caught == []
    assert descriptor["input_schema"] == "policy_impact.skills.schemas:SkillInput"
    assert descriptor["output_schema"] == "policy_impact.skills.schemas:GeneralChatOutput"
    assert manifest.model_dump()["input_schema"] is SkillInput
    assert manifest.model_dump()["output_schema"] is GeneralChatOutput
    assert manifest_schema["properties"]["input_schema"]["type"] == "string"
    assert manifest_schema["properties"]["input_schema"]["format"] == "pydantic-model-ref"
    assert manifest_schema["properties"]["output_schema"]["type"] == "string"
    assert manifest_schema["properties"]["output_schema"]["format"] == "pydantic-model-ref"
    assert "input_schema" not in manifest_schema["required"]
    assert "output_schema" not in manifest_schema["required"]


def test_default_manifest_json_round_trip_preserves_schema_classes_and_contract():
    manifest = SkillRegistry().get_manifest("policy_weekly_impact")

    rebuilt = SkillManifest.model_validate_json(manifest.model_dump_json())

    assert rebuilt.input_schema is SkillInput
    assert rebuilt.output_schema is PolicyImpactOutput
    assert rebuilt == manifest


def test_runtime_loaded_schema_class_is_registered_for_safe_json_round_trip():
    runtime_input = create_model(
        "RuntimeSkillInput",
        task=(str, ...),
        __module__="tests.runtime_skill_schemas",
    )
    payload = {
        **SkillRegistry().get_manifest("general_chat").model_dump(),
        "id": "runtime_schema_skill",
        "input_schema": runtime_input,
    }

    manifest = SkillManifest.model_validate(payload)
    rebuilt = SkillManifest.model_validate_json(manifest.model_dump_json())

    assert rebuilt.input_schema is runtime_input


def test_manifest_json_rejects_unknown_schema_reference():
    descriptor = json.loads(
        SkillRegistry().get_manifest("general_chat").model_dump_json()
    )
    descriptor["input_schema"] = "unknown.module:UnknownInput"

    with pytest.raises(ValidationError, match="unknown schema reference"):
        SkillManifest.model_validate_json(json.dumps(descriptor, ensure_ascii=False))


def test_manifest_rejects_conflicting_duplicate_schema_references():
    first = create_model(
        "ConflictingSchema",
        first=(str, ...),
        __module__="tests.conflicting_skill_schemas",
    )
    second = create_model(
        "ConflictingSchema",
        second=(str, ...),
        __module__="tests.conflicting_skill_schemas",
    )
    payload = SkillRegistry().get_manifest("general_chat").model_dump()

    SkillManifest.model_validate({**payload, "input_schema": first})

    with pytest.raises(ValidationError, match="conflicting schema reference"):
        SkillManifest.model_validate({**payload, "input_schema": second})


@pytest.mark.parametrize("budget_field", ["max_steps", "max_model_calls", "timeout_seconds"])
def test_manifest_rejects_non_positive_budgets(budget_field):
    manifest = SkillRegistry().get_manifest("general_chat")
    payload = {**manifest.model_dump(), budget_field: 0}

    with pytest.raises(ValidationError):
        type(manifest).model_validate(payload)


def test_manifest_is_frozen():
    manifest = SkillRegistry().get_manifest("general_chat")

    with pytest.raises(ValidationError):
        manifest.name = "changed"


def test_register_for_test_appends_a_manifest():
    registry = SkillRegistry()
    payload = {
        **registry.get_manifest("general_chat").model_dump(),
        "id": "test_skill",
        "executor_id": "test_skill",
    }
    manifest = SkillManifest.model_validate(payload)

    registry.register_for_test(manifest)

    assert registry.get_manifest("test_skill") is manifest
    assert registry.list_manifests()[-1] is manifest


def test_harness_skill_exports_remain_compatible():
    assert CompatibilitySkillDefinition is SkillManifest
    assert CompatibilitySkillRegistry is SkillRegistry


def test_get_skill_returns_known_skill_and_raises_clear_error_for_unknown():
    registry = SkillRegistry()

    assert registry.get_manifest("recent_news_report").id == "recent_news_report"
    assert registry.get_skill("recent_news_report").id == "recent_news_report"
    with pytest.raises(KeyError, match="unknown skill 'missing_skill'"):
        registry.get_manifest("missing_skill")
    with pytest.raises(KeyError, match="unknown skill 'missing_skill'"):
        registry.get_skill("missing_skill")

    assert registry.list_skills() == registry.list_manifests()


def test_resolve_skill_prefers_explicit_requested_skill():
    registry = SkillRegistry()

    skill = registry.resolve_skill(
        requested_skill_id="research_report",
        mode="chat",
        message="\u6700\u65b0\u653f\u7b56\u65e5\u62a5",
    )

    assert skill.id == "research_report"


def test_resolve_skill_keeps_keyword_messages_in_default_chat():
    registry = SkillRegistry()

    assert (
        registry.resolve_skill(None, "chat", "\u653f\u7b56\u5f71\u54cd\u5206\u6790").id
        == "general_chat"
    )
    assert (
        registry.resolve_skill(None, "chat", "\u6700\u65b0\u65b0\u95fb\u65e5\u62a5").id
        == "general_chat"
    )
    assert (
        registry.resolve_skill(None, "chat", "\u884c\u4e1a\u8c03\u7814\u62a5\u544a").id
        == "general_chat"
    )
    assert (
        registry.resolve_skill(None, "chat", "\u89c4\u5212\u516c\u53f8wiki\u77e5\u8bc6\u5e93\u76ee\u5f55").id
        == "general_chat"
    )


def test_resolve_skill_uses_explicit_report_mode_for_external_impact():
    registry = SkillRegistry()

    skill = registry.resolve_skill(
        None,
        "report",
        "\u6700\u65b0\u65b0\u95fb\u91cc\u548c agent observability\u3001tool policy \u76f8\u5173\u7684\u5185\u5bb9\u6709\u4ec0\u4e48\u5f71\u54cd\uff1f",
    )

    assert skill.id == "external_impact_report"


def test_resolve_skill_defaults_to_general_chat_for_empty_or_ambiguous_input():
    registry = SkillRegistry()

    assert registry.resolve_skill(None, None, "").id == "general_chat"
    assert registry.resolve_skill(None, "chat", "hello there").id == "general_chat"


def test_enterprise_fact_query_does_not_route_to_wiki_blueprint():
    registry = SkillRegistry()

    skill = registry.resolve_skill(
        None,
        "auto",
        "请只依据企业知识库说明核心业务、示例产品与 iFinD 的服务对象，并逐项给来源。",
    )

    assert skill.id == "general_chat"


def test_compliance_word_alone_does_not_route_product_review_to_policy_workflow():
    registry = SkillRegistry()

    skill = registry.resolve_skill(
        None,
        "auto",
        "请给产品、研发、合规一份异步接口上线评审清单。",
    )

    assert skill.id == "general_chat"


def test_wiki_blueprint_requires_a_planning_intent_and_supports_correction():
    registry = SkillRegistry()

    assert registry.resolve_skill(None, "auto", "请规划企业知识库目录和采集问题").id == "general_chat"
    assert registry.resolve_skill(None, "wiki", "请规划企业知识库目录和采集问题").id == "company_wiki_blueprint"
    assert registry.resolve_skill(None, "auto", "这不是知识库规划，请直接回答企业事实").id == "general_chat"
