"""
【模块说明】
- 主要作用：定义不同模式下的工具策略与统一工具契约（capability + preflight）。
- 核心类：ToolPolicy、ToolCapability。
- 核心方法：get_allowed_tools、tool_preflight、normalized_tool_signature。
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Literal, Optional, Tuple

# 所有可用工具的完整列表
ALL_TOOLS = ["calculator", "websearch", "filewriter", "memory_search", "mindmap_generator", "get_datetime"]
ACT_PHASES = {"act"}


@dataclass(frozen=True)
class ToolCapability:
    """统一工具能力契约。"""

    intent_types: Tuple[str, ...]
    required_args: Tuple[str, ...]
    phase_allow: Tuple[str, ...]
    retry_policy: str = "once"
    dedup_scope: str = "request"
    fallback_mode: str = "synthesize"
    risk_level: str = "safe"
    approval_mode: str = "off"
    required_approval: Optional[str] = None


@dataclass(frozen=True)
class ToolDecision:
    """一次工具调用的治理决策结果。"""

    tool_name: str
    allowed: bool
    reason: str
    phase: str
    mode: str
    signature: str
    risk_level: str
    approval_mode: str
    required_approval: Optional[str] = None

    def to_event(self) -> Dict[str, Any]:
        return asdict(self)


class ToolPolicy:
    """统一工具访问策略与调用前门控。"""

    MODE_POLICIES = {
        "learn": ALL_TOOLS,
        "practice": ALL_TOOLS,
        "exam": ALL_TOOLS,
    }

    CAPABILITIES: Dict[str, ToolCapability] = {
        "calculator": ToolCapability(
            intent_types=("math_compute", "grading"),
            required_args=("expression",),
            phase_allow=("act",),
            retry_policy="none",
        ),
        "websearch": ToolCapability(
            intent_types=("fresh_info", "outside_knowledge"),
            required_args=("query",),
            phase_allow=("act",),
            retry_policy="once",
            risk_level="external",
            approval_mode="log",
        ),
        "filewriter": ToolCapability(
            intent_types=("persist_note",),
            required_args=("filename", "content"),
            phase_allow=("act",),
            retry_policy="none",
            dedup_scope="round",
            risk_level="write",
            approval_mode="log",
            required_approval="request_human_approval",
        ),
        "memory_search": ToolCapability(
            intent_types=("history_lookup",),
            required_args=("query", "course_name"),
            phase_allow=("act",),
            retry_policy="none",
            risk_level="read",
            approval_mode="log",
        ),
        "mindmap_generator": ToolCapability(
            intent_types=("diagram", "summarize"),
            required_args=("topic", "course_name"),
            phase_allow=("act",),
            retry_policy="none",
            dedup_scope="round",
            risk_level="write",
            approval_mode="log",
        ),
        "get_datetime": ToolCapability(
            intent_types=("time_info",),
            required_args=(),
            phase_allow=("act",),
            retry_policy="none",
        ),
    }

    @staticmethod
    def get_allowed_tools(mode: Literal["learn", "practice", "exam"]) -> List[str]:
        return ToolPolicy.MODE_POLICIES.get(mode, ALL_TOOLS)

    @staticmethod
    def is_tool_allowed(tool: str, mode: Literal["learn", "practice", "exam"]) -> bool:
        return tool in ToolPolicy.get_allowed_tools(mode)

    @staticmethod
    def get_capability(tool_name: str) -> ToolCapability:
        return ToolPolicy.CAPABILITIES.get(
            tool_name,
            ToolCapability(
                intent_types=("general",),
                required_args=(),
                phase_allow=("act",),
                retry_policy="none",
                dedup_scope="request",
                fallback_mode="synthesize",
                risk_level="safe",
                approval_mode="off",
            ),
        )

    @staticmethod
    def _normalize_text(value: Any) -> str:
        return " ".join(str(value or "").strip().split())

    @staticmethod
    def _normalize_memory_query(query: str) -> str:
        src = str(query or "").strip().lower()
        if not src:
            return ""
        toks = re.findall(r"[a-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", src)
        toks = list(dict.fromkeys(toks))[:12]
        return "|".join(sorted(toks)) or src[:80]

    @staticmethod
    def normalized_tool_args(tool_name: str, tool_args: Dict[str, Any]) -> Dict[str, Any]:
        args = dict(tool_args or {})
        out: Dict[str, Any] = {}
        for k in sorted(args.keys()):
            v = args.get(k)
            if v is None:
                continue
            if isinstance(v, str):
                v = ToolPolicy._normalize_text(v)
            elif isinstance(v, list):
                if all(isinstance(x, str) for x in v):
                    v = sorted({ToolPolicy._normalize_text(x) for x in v if ToolPolicy._normalize_text(x)})
            out[k] = v

        if tool_name == "memory_search":
            out["query"] = ToolPolicy._normalize_memory_query(str(args.get("query", "")))
            if "top_k" in out:
                try:
                    out["top_k"] = int(out["top_k"])
                except Exception:
                    out["top_k"] = 5
        return out

    @staticmethod
    def normalized_tool_signature(tool_name: str, tool_args: Dict[str, Any]) -> str:
        normalized = ToolPolicy.normalized_tool_args(tool_name, tool_args)
        return f"{tool_name}:{json.dumps(normalized, ensure_ascii=False, sort_keys=True)}"

    @staticmethod
    def decide_tool_call(
        tool_name: str,
        tool_args: Dict[str, Any],
        *,
        mode: str,
        phase: str,
        memory_search_in_act_default: bool,
    ) -> ToolDecision:
        cap = ToolPolicy.get_capability(tool_name)
        signature = ToolPolicy.normalized_tool_signature(tool_name, tool_args)

        if tool_name not in ToolPolicy.get_allowed_tools(mode):  # mode gate
            return ToolDecision(
                tool_name=tool_name,
                allowed=False,
                reason="mode_not_allowed",
                phase=phase,
                mode=mode,
                signature=signature,
                risk_level=cap.risk_level,
                approval_mode=cap.approval_mode,
                required_approval=cap.required_approval,
            )
        if phase not in set(cap.phase_allow):  # phase gate
            return ToolDecision(
                tool_name=tool_name,
                allowed=False,
                reason="phase_not_allowed",
                phase=phase,
                mode=mode,
                signature=signature,
                risk_level=cap.risk_level,
                approval_mode=cap.approval_mode,
                required_approval=cap.required_approval,
            )
        for required in cap.required_args:  # args gate
            raw = tool_args.get(required)
            if raw is None:
                return ToolDecision(
                    tool_name=tool_name,
                    allowed=False,
                    reason=f"missing_required:{required}",
                    phase=phase,
                    mode=mode,
                    signature=signature,
                    risk_level=cap.risk_level,
                    approval_mode=cap.approval_mode,
                    required_approval=cap.required_approval,
                )
            if isinstance(raw, str) and not raw.strip():
                return ToolDecision(
                    tool_name=tool_name,
                    allowed=False,
                    reason=f"missing_required:{required}",
                    phase=phase,
                    mode=mode,
                    signature=signature,
                    risk_level=cap.risk_level,
                    approval_mode=cap.approval_mode,
                    required_approval=cap.required_approval,
                )
        if tool_name == "memory_search" and not memory_search_in_act_default:
            return ToolDecision(
                tool_name=tool_name,
                allowed=False,
                reason="memory_search_disabled_in_act",
                phase=phase,
                mode=mode,
                signature=signature,
                risk_level=cap.risk_level,
                approval_mode=cap.approval_mode,
                required_approval=cap.required_approval,
            )
        return ToolDecision(
            tool_name=tool_name,
            allowed=True,
            reason="allowed",
            phase=phase,
            mode=mode,
            signature=signature,
            risk_level=cap.risk_level,
            approval_mode=cap.approval_mode,
            required_approval=cap.required_approval,
        )

    @staticmethod
    def tool_preflight(
        tool_name: str,
        tool_args: Dict[str, Any],
        *,
        mode: str,
        phase: str,
        memory_search_in_act_default: bool,
    ) -> Tuple[bool, str, ToolCapability, str]:
        decision = ToolPolicy.decide_tool_call(
            tool_name,
            tool_args,
            mode=mode,
            phase=phase,
            memory_search_in_act_default=memory_search_in_act_default,
        )
        cap = ToolPolicy.get_capability(tool_name)
        return decision.allowed, decision.reason, cap, decision.signature
