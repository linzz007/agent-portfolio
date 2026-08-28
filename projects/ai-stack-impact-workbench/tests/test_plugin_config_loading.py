import json

from policy_impact.mcp_server.registry import TOOLS
from policy_impact.runtime.plugin_config import plugin_path
from policy_impact.runtime.subagent_manifests import build_default_subagent_manifest_registry
from policy_impact.runtime.subagent_roles import build_default_subagent_role_registry
from policy_impact.skills.registry import SkillRegistry


def test_default_skill_manifests_are_loaded_from_plugin_file():
    payload = json.loads(plugin_path("skills", "manifest.json").read_text(encoding="utf-8"))
    registry = SkillRegistry()

    assert [item["id"] for item in payload] == [skill.id for skill in registry.list_manifests()]
    assert registry.get_manifest("general_chat").context_policy_id == "context.general_chat.v1"
    assert "news.load_items" in registry.get_manifest("external_impact_report").allowed_tools


def test_subagent_roles_and_roster_are_loaded_from_plugin_files():
    roles_payload = json.loads(plugin_path("subagents", "roles.json").read_text(encoding="utf-8"))
    manifests_payload = json.loads(
        plugin_path("subagents", "manifests.json").read_text(encoding="utf-8")
    )
    role_registry = build_default_subagent_role_registry()
    manifest_registry = build_default_subagent_manifest_registry(role_registry)

    assert [item["role"] for item in roles_payload][:4] == [
        "collector",
        "analyst",
        "skeptic",
        "verifier",
    ]
    assert [item["role"] for item in manifests_payload] == [
        "collector",
        "analyst",
        "skeptic",
        "verifier",
    ]
    assert role_registry.get("collector").output_schema_name == "evidence_collection.v1"
    assert manifest_registry.get("skeptic").context_policy_id == "context.subagent.skeptic.v1"


def test_tool_registry_is_loaded_from_plugin_file():
    payload = json.loads(plugin_path("tools", "registry.json").read_text(encoding="utf-8"))

    assert {item["name"] for item in payload} == set(TOOLS)
    assert callable(TOOLS["company_wiki_search"])
    assert callable(TOOLS["report_workflow.run"])
