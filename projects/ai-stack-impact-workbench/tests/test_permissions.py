import pytest

from policy_impact.harness.permissions import (
    PermissionEngine,
    ToolPolicy,
    build_default_permission_engine,
)
from policy_impact.harness.tool_gateway import (
    TOOL_REGISTRY,
    ToolGateway,
    get_tool_audit_log,
    register_tool,
    reset_tool_audit_log,
)


def test_permission_engine_applies_deny_ask_allow_precedence():
    assert (
        PermissionEngine(
            [
                ToolPolicy("stage", "tool", "allow", "allowed"),
                ToolPolicy("*", "tool", "ask", "ask first"),
                ToolPolicy("stage", "tool", "deny", "deny first"),
            ]
        ).decide("stage", "tool").decision
        == "deny"
    )
    assert (
        PermissionEngine(
            [
                ToolPolicy("stage", "tool", "allow", "allowed"),
                ToolPolicy("*", "tool", "ask", "ask first"),
            ]
        ).decide("stage", "tool").decision
        == "ask"
    )
    assert (
        PermissionEngine([ToolPolicy("stage", "tool", "allow", "allowed")])
        .decide("stage", "tool")
        .decision
        == "allow"
    )


def test_permission_engine_denies_unknown_stage_tool():
    decision = PermissionEngine([]).decide("unknown_stage", "unknown_tool")

    assert decision.decision == "deny"
    assert decision.reason


def test_tool_gateway_blocks_tool_and_audits_policy_decision():
    reset_tool_audit_log()
    gateway = ToolGateway(
        permission_engine=PermissionEngine([ToolPolicy("stage", "tool", "ask", "requires approval")])
    )

    with pytest.raises(PermissionError):
        gateway.call("stage", "tool")

    calls = get_tool_audit_log()
    assert calls[-1]["status"] == "blocked"
    assert calls[-1]["policy_decision"] == "ask"
    assert calls[-1]["policy_reason"] == "requires approval"


def test_tool_gateway_executes_allowed_registered_tool_and_audits_policy_decision():
    reset_tool_audit_log()
    tool_name = "permission_test_tool"
    register_tool(tool_name, lambda value: {"value": value})
    gateway = ToolGateway(
        permission_engine=PermissionEngine([ToolPolicy("stage", tool_name, "allow", "test allow")])
    )

    try:
        result = gateway.call("stage", tool_name, value=42)
    finally:
        TOOL_REGISTRY.pop(tool_name, None)

    assert result == {"value": 42}
    calls = get_tool_audit_log()
    assert calls[-1]["status"] == "ok"
    assert calls[-1]["policy_decision"] == "allow"
    assert calls[-1]["policy_reason"] == "test allow"


def test_default_permission_engine_wildcard_deny_overrides_exact_allow_for_dangerous_tool():
    engine = build_default_permission_engine({"stage": {"shell_exec"}})

    decision = engine.decide("stage", "shell_exec")

    assert decision.decision == "deny"
