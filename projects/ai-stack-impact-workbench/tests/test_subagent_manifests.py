import pytest

from policy_impact.runtime.subagent_manifests import (
    SubagentManifest,
    SubagentManifestRegistry,
    build_default_subagent_manifest_registry,
)
from policy_impact.runtime.subagent_roles import build_default_subagent_role_registry


def test_default_subagent_manifests_expose_bounded_roster_items():
    registry = build_default_subagent_manifest_registry(
        build_default_subagent_role_registry()
    )

    roster = registry.for_roles(["collector", "analyst", "unknown"])

    assert [item.role for item in roster] == ["collector", "analyst"]
    assert all(item.memory_scope == "read_only" for item in roster)
    assert all(not item.can_publish for item in roster)
    assert all("code" not in " ".join(item.tools).lower() for item in roster)


def test_roster_item_intersects_role_tools_with_caller_allowlist():
    registry = build_default_subagent_manifest_registry(
        build_default_subagent_role_registry()
    )
    manifest = registry.get("collector")

    item = manifest.roster_item(caller_allowed_tools=("artifact_read",))

    assert item["role"] == "collector"
    assert item["tools"] == ["artifact_read"]
    assert "memory_write" in item["disallowed_tools"]


def test_subagent_manifest_registry_rejects_duplicate_roles():
    manifest = build_default_subagent_manifest_registry(
        build_default_subagent_role_registry()
    ).get("skeptic")

    with pytest.raises(ValueError, match="duplicate subagent manifest role"):
        SubagentManifestRegistry([manifest, manifest])


def test_subagent_manifest_requires_non_empty_use_when_and_tools():
    with pytest.raises(ValueError, match="use_when must not be empty"):
        SubagentManifest(
            name="bad",
            role="bad",
            description="bad",
            use_when=(),
            tools=("artifact_read",),
            output_schema_name="bad.v1",
            max_turns=1,
            max_model_calls=1,
            timeout_seconds=1,
            context_policy_id="context.bad.v1",
        )

    with pytest.raises(ValueError, match="tools must not be empty"):
        SubagentManifest(
            name="bad",
            role="bad",
            description="bad",
            use_when=("need isolated work",),
            tools=(),
            output_schema_name="bad.v1",
            max_turns=1,
            max_model_calls=1,
            timeout_seconds=1,
            context_policy_id="context.bad.v1",
        )
