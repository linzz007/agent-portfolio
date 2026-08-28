"""Permission policy evaluation for harness tool calls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PolicyDecisionValue = Literal["deny", "ask", "allow"]


@dataclass(frozen=True)
class ToolPolicy:
    stage_name: str
    tool_name: str
    decision: PolicyDecisionValue
    reason: str = ""

    def __post_init__(self) -> None:
        if self.decision not in {"deny", "ask", "allow"}:
            raise ValueError(f"invalid tool policy decision: {self.decision}")


@dataclass(frozen=True)
class PermissionDecision:
    decision: PolicyDecisionValue
    reason: str

    def __post_init__(self) -> None:
        if self.decision not in {"deny", "ask", "allow"}:
            raise ValueError(f"invalid permission decision: {self.decision}")


class PermissionEngine:
    def __init__(self, policies: list[ToolPolicy]) -> None:
        self.policies = tuple(policies)

    def decide(self, stage_name: str, tool_name: str) -> PermissionDecision:
        matches = [
            policy
            for policy in self.policies
            if policy.tool_name == tool_name and policy.stage_name in {stage_name, "*"}
        ]
        for decision in ("deny", "ask", "allow"):
            for policy in matches:
                if policy.decision == decision:
                    return PermissionDecision(decision, policy.reason)
        return PermissionDecision("deny", "no matching allow policy")


def build_default_permission_engine(stage_allowed_tools: dict[str, set[str]]) -> PermissionEngine:
    policies: list[ToolPolicy] = [
        ToolPolicy("*", "shell_exec", "deny", "dangerous tool denied by default"),
        ToolPolicy("*", "git_write", "deny", "dangerous tool denied by default"),
        ToolPolicy("*", "file_write", "deny", "dangerous tool denied by default"),
        ToolPolicy("*", "profile_write", "deny", "dangerous tool denied by default"),
    ]
    for stage_name, tool_names in stage_allowed_tools.items():
        for tool_name in sorted(tool_names):
            policies.append(ToolPolicy(stage_name, tool_name, "allow", "stage tool allowlist"))
    return PermissionEngine(policies)
