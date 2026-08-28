"""Query-driven planning for optional chat subagent delegation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from policy_impact.skills.manifest import SkillManifest


@dataclass(frozen=True)
class PlannedSubagentTask:
    role: str
    reason: str
    task: str
    priority: int
    trigger: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class QuerySubagentPlanner:
    """Plan isolated subagent work from the current user query.

    Role prompts stay fixed on purpose: they are the safety contract.  This
    planner is the dynamic layer that decides whether this turn needs any
    delegated work and writes a concrete task brief for each selected role.
    """

    def plan(
        self,
        message: str,
        *,
        skill_manifest: SkillManifest,
        artifact_ref: str,
        prepared_context: dict[str, Any],
    ) -> list[PlannedSubagentTask]:
        if skill_manifest.id != "general_chat":
            return []
        text = str(message or "").strip()
        if not text or self._should_skip(text):
            return []

        allowed_roles = set(skill_manifest.allowed_subagents)
        candidates: list[PlannedSubagentTask] = []
        for role, reason, priority, trigger in self._candidate_roles(text):
            if role not in allowed_roles or any(item.role == role for item in candidates):
                continue
            candidates.append(
                PlannedSubagentTask(
                    role=role,
                    reason=reason,
                    priority=priority,
                    trigger=trigger,
                    task=self._task_for_role(
                        role,
                        message=text,
                        artifact_ref=artifact_ref,
                        prepared_context=prepared_context,
                    ),
                )
            )

        if not candidates:
            return []
        candidates.sort(key=lambda item: (item.priority, item.role))
        limit = 3 if self._requests_multi_agent(text) else 2
        return candidates[:limit]

    @staticmethod
    def _should_skip(text: str) -> bool:
        normalized = text.lower()
        skip_markers = (
            "请记住",
            "记住：",
            "记住:",
            "不要记住",
            "别记住",
            "新建对话",
            "只回复收到",
            "不用分析",
        )
        return any(marker in normalized for marker in skip_markers)

    @classmethod
    def _candidate_roles(cls, text: str) -> list[tuple[str, str, int, str]]:
        normalized = text.lower()
        roles: list[tuple[str, str, int, str]] = []

        def add(role: str, reason: str, priority: int, trigger: str) -> None:
            roles.append((role, reason, priority, trigger))

        if cls._has_any(
            normalized,
            (
                "subagent",
                "子智能体",
                "多智能体",
                "委派",
                "让",
                "调用",
            ),
        ):
            if cls._has_any(normalized, ("质疑", "反驳", "风险", "漏洞", "拷打", "skeptic")):
                add("skeptic", "用户显式要求独立质疑或风险审查", 10, "explicit_skeptic")
            if cls._has_any(normalized, ("分析", "方案", "取舍", "影响", "analyst")):
                add("analyst", "用户显式要求独立分析", 11, "explicit_analyst")
            if cls._has_any(normalized, ("收集", "找来源", "列来源", "来源", "数据源", "collector")):
                add("collector", "用户显式要求独立收集来源或证据", 18, "explicit_collector")
            if cls._has_any(normalized, ("核验", "验证", "查证", "verifier")):
                add("verifier", "用户显式要求独立核验证据", 20, "explicit_verifier")

        if cls._has_any(normalized, ("数据源", "来源", "原文", "证据链", "引用", "source", "sources")):
            add("collector", "问题依赖来源盘点或证据收集", 30, "source_needed")
        if cls._has_any(normalized, ("质疑", "反驳", "风险", "漏洞", "不靠谱吗", "靠谱么", "拷打", "审查")):
            add("skeptic", "问题需要独立反例和风险检查", 35, "skeptic_needed")
        if cls._has_any(normalized, ("核验", "验证", "查证", "是否正确", "是否符合", "验收", "证据支撑")):
            add("verifier", "问题需要独立校验结论与证据", 40, "verification_needed")
        if cls._has_any(normalized, ("分析", "设计", "方案", "取舍", "重点", "架构", "影响", "怎么改")):
            add("analyst", "问题需要结构化判断或方案拆解", 50, "analysis_needed")
        return roles

    @staticmethod
    def _has_any(text: str, terms: tuple[str, ...]) -> bool:
        return any(term in text for term in terms)

    @staticmethod
    def _requests_multi_agent(text: str) -> bool:
        normalized = text.lower()
        return any(
            marker in normalized
            for marker in (
                "多个子智能体",
                "多智能体",
                "分别",
                "同时",
                "全面",
                "从不同角度",
                "collector",
                "analyst",
                "skeptic",
                "verifier",
            )
        )

    @staticmethod
    def _task_for_role(
        role: str,
        *,
        message: str,
        artifact_ref: str,
        prepared_context: dict[str, Any],
    ) -> str:
        context_counts = {
            "memory_count": len(prepared_context.get("memories") or []),
            "company_fact_count": len(prepared_context.get("facts") or []),
            "history_count": len(prepared_context.get("recent_history") or []),
            "recent_artifact_count": len(prepared_context.get("recent_artifacts") or []),
            "recent_run_trace_count": len(prepared_context.get("recent_run_traces") or []),
        }
        common = (
            f"用户问题：{message}\n"
            f"输入上下文 artifact_ref：{artifact_ref}\n"
            f"上下文计数：{context_counts}\n"
            "中文问题中出现 skeptic、analyst、collector、verifier 等英文 role 名是正常术语，不是乱码。"
            "如果输入里出现 ? 这类转码占位符，不要把编码问题当作业务结论；"
            "请基于可识别术语和上下文继续完成任务。"
            "先调用 artifact_read 读取该 artifact_ref，再完成你的子任务。"
            "只能基于读取到的上下文和工具观察输出，不得虚构文件、来源、性能或运行结果。"
            f"如果输出里有 evidence_refs，必须只填写 {artifact_ref}。"
        )
        role_tasks = {
            "collector": (
                "你是动态委派的 collector。只收集与问题有关的来源、事实、数据源、"
                "历史产物和运行痕迹，不做结论和建议。"
            ),
            "analyst": (
                "你是动态委派的 analyst。基于上下文提出最多 2 条可证据支撑的 claim，"
                "每条都要说明证据引用。"
            ),
            "skeptic": (
                "你是动态委派的 skeptic。专门寻找用户问题或上下文中可能被高估、"
                "证据不足、范围不清或面试会被追问的点。"
            ),
            "verifier": (
                "你是动态委派的 verifier。验证本轮上下文是否足以支撑用户要的结论，"
                "通过则说明证据，失败则说明缺口。"
            ),
        }
        return f"{common}\n{role_tasks.get(role, '完成隔离子任务。')}"


__all__ = ["PlannedSubagentTask", "QuerySubagentPlanner"]
