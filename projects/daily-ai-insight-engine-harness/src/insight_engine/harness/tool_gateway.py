"""Tool Gateway —— 运行时工具白名单控制。

Tool Gateway 控制每个 stage 能调用哪些工具函数。
Agent 的工具调用必须经过这个网关 —— 直接调用会被阻断。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

ToolFn = Callable[..., Any]


@dataclass(frozen=True)
class ToolCallRecord:
    """一次工具调用的记录。"""

    stage_name: str
    tool_name: str
    allowed: bool


# 工具注册表 —— 在此添加新工具。
TOOL_REGISTRY: dict[str, ToolFn] = {}

# 每个 stage 的工具白名单。
STAGE_ALLOWED_TOOLS: dict[str, set[str]] = {}


class ToolGateway:
    """受控工具调用入口。"""

    def __init__(self) -> None:
        self.calls: list[ToolCallRecord] = []

    def call(self, stage_name: str, tool_name: str, *args: Any, **kwargs: Any) -> Any:
        """调用工具。不在白名单中时抛出 PermissionError。"""
        allowed = tool_name in STAGE_ALLOWED_TOOLS.get(stage_name, set())
        self.calls.append(
            ToolCallRecord(stage_name=stage_name, tool_name=tool_name, allowed=allowed)
        )

        if not allowed:
            raise PermissionError(
                f"stage `{stage_name}` 不允许调用工具 `{tool_name}`"
            )

        tool = TOOL_REGISTRY.get(tool_name)
        if tool is None:
            raise KeyError(f"工具不存在：{tool_name}")
        return tool(*args, **kwargs)
