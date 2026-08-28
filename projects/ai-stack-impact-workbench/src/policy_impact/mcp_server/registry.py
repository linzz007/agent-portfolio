"""Tool registry shared by gateway and MCP-like server."""

from __future__ import annotations

from typing import Any, Callable

from policy_impact.harness.tool_gateway import register_tool
from policy_impact.runtime.plugin_config import import_ref, read_plugin_json


def artifact_read(*, artifact_ref: str) -> Any:
    """Deny unscoped reads; SubagentRunner supplies the task-scoped implementation."""

    del artifact_ref
    raise PermissionError("artifact_read is available only inside an isolated task")


def report_workflow_run(**_: Any) -> Any:
    """Deny unscoped report workflow execution; AgentRuntime injects the scoped tool."""

    raise PermissionError("report_workflow.run is available only inside /report AgentLoop")

def load_tool_registry() -> dict[str, Callable[..., Any]]:
    payload = read_plugin_json("tools", "registry.json")
    if not isinstance(payload, list):
        raise ValueError("plugins/tools/registry.json must contain a list")
    tools: dict[str, Callable[..., Any]] = {}
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("tool registry entries must be objects")
        name = str(item.get("name") or "").strip()
        callable_ref = str(item.get("callable") or "").strip()
        if not name or not callable_ref:
            raise ValueError("tool registry entries require name and callable")
        fn = import_ref(callable_ref)
        if not callable(fn):
            raise TypeError(f"tool callable is not callable: {callable_ref}")
        if name in tools:
            raise ValueError(f"duplicate tool name: {name}")
        tools[name] = fn
    return tools


TOOLS: dict[str, Callable[..., Any]] = load_tool_registry()


def register_all_tools() -> None:
    for name, fn in TOOLS.items():
        register_tool(name, fn)


def call_tool(name: str, **kwargs: Any) -> Any:
    if name not in TOOLS:
        raise KeyError(f"unknown tool: {name}")
    return TOOLS[name](**kwargs)


def tool_schemas() -> list[dict[str, Any]]:
    return [
        {"name": name, "description": fn.__doc__ or name, "inputSchema": {"type": "object"}}
        for name, fn in sorted(TOOLS.items())
    ]
