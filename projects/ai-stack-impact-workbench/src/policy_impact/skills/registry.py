"""Executable skill manifests and deterministic skill routing."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from policy_impact.runtime.execution_mode import ExecutionMode
from policy_impact.runtime.plugin_config import read_plugin_json
from policy_impact.skills.manifest import SkillManifest
from policy_impact.skills import schemas as _schemas  # noqa: F401 - registers schema refs


DEFAULT_EXECUTION_MODES = {
    "general_chat": ExecutionMode.AGENT_LOOP,
    "external_impact_report": ExecutionMode.SUBAGENT_WORKFLOW,
    "policy_weekly_impact": ExecutionMode.SUBAGENT_WORKFLOW,
    "recent_news_report": ExecutionMode.WORKFLOW,
    "research_report": ExecutionMode.AGENT_LOOP,
    "company_wiki_blueprint": ExecutionMode.DETERMINISTIC,
}


class SkillRegistry:
    def __init__(
        self,
        manifests: Iterable[SkillManifest] | None = None,
        *,
        skills: Iterable[SkillManifest] | None = None,
    ) -> None:
        if manifests is not None and skills is not None:
            raise TypeError("pass either manifests or skills, not both")
        supplied = manifests if manifests is not None else skills
        ordered_manifests = tuple(supplied) if supplied is not None else _DEFAULT_MANIFESTS
        manifests_by_id = {manifest.id: manifest for manifest in ordered_manifests}
        if len(manifests_by_id) != len(ordered_manifests):
            raise ValueError("duplicate skill id")
        self._manifests = ordered_manifests
        self._manifests_by_id = manifests_by_id

    def list_manifests(self) -> list[SkillManifest]:
        return list(self._manifests)

    def get_manifest(self, skill_id: str) -> SkillManifest:
        try:
            return self._manifests_by_id[skill_id]
        except KeyError as exc:
            known_skills = ", ".join(manifest.id for manifest in self._manifests) or "<none>"
            raise KeyError(f"unknown skill {skill_id!r}; known skills: {known_skills}") from exc

    def register_for_test(self, manifest: SkillManifest) -> None:
        if manifest.id in self._manifests_by_id:
            raise ValueError(f"duplicate skill id: {manifest.id!r}")
        self._manifests = (*self._manifests, manifest)
        self._manifests_by_id[manifest.id] = manifest

    def list_skills(self) -> list[SkillManifest]:
        return self.list_manifests()

    def get_skill(self, skill_id: str) -> SkillManifest:
        return self.get_manifest(skill_id)

    def resolve_skill(
        self,
        requested_skill_id: str | None,
        mode: str | None,
        message: str | None,
    ) -> SkillManifest:
        if requested_skill_id:
            return self.get_manifest(requested_skill_id)

        normalized_mode = _normalize_text(mode)
        mode_match = _MODE_TO_SKILL_ID.get(normalized_mode)
        if mode_match and mode_match != "general_chat":
            return self.get_manifest(mode_match)

        # Product rule: an ordinary message is always a chat turn. Heavy external
        # impact analysis must be requested explicitly through /report or a
        # persisted skill binding; keyword-only auto routing made the UI feel
        # surprising and too workflow-driven.
        del message
        return self.get_manifest("general_chat")


def compatibility_tool_policy(manifest: SkillManifest) -> dict[str, list[str]]:
    """Serialize the legacy stage-keyed shape from the manifest allowlist."""
    if manifest.id == "external_impact_report":
        return {"impact": list(manifest.allowed_tools)}
    aliases = (
        alias
        for alias, skill_id in _MODE_TO_SKILL_ID.items()
        if skill_id == manifest.id and alias != manifest.id
    )
    stage_name = min(aliases, key=len, default=manifest.id)
    return {stage_name: list(manifest.allowed_tools)}


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def _resolve_by_keyword_score(normalized_message: str) -> str:
    """Resolve mixed-intent messages without relying on rule order alone."""
    if not normalized_message:
        return ""
    if _requests_general_chat_override(normalized_message):
        return "general_chat"
    scores: dict[str, int] = {}
    for keywords, skill_id in _MESSAGE_KEYWORD_RULES:
        if skill_id == "company_wiki_blueprint" and not _requests_wiki_blueprint(normalized_message):
            continue
        for keyword in keywords:
            if keyword in normalized_message:
                weight = _KEYWORD_WEIGHTS.get(skill_id, {}).get(keyword, 1)
                scores[skill_id] = scores.get(skill_id, 0) + weight
    if not scores:
        return ""
    best_score = max(scores.values())
    if best_score < 2:
        return ""
    winners = [skill_id for skill_id, score in scores.items() if score == best_score]
    if len(winners) == 1:
        return winners[0]
    for skill_id in _KEYWORD_TIE_BREAKER:
        if skill_id in winners:
            return skill_id
    return winners[0]


def _requests_general_chat_override(message: str) -> bool:
    return message.startswith("/chat") or any(
        marker in message
        for marker in (
            "不是知识库规划",
            "不要生成知识库规划",
            "不要生成蓝图",
            "不要列缺失文件",
        )
    )


def _requests_wiki_blueprint(message: str) -> bool:
    knowledge_terms = ("知识库", "wiki", "公司资料", "knowledge base", "company profile")
    planning_terms = (
        "规划",
        "蓝图",
        "目录",
        "结构",
        "搭建",
        "建设",
        "缺失文件",
        "采集问题",
        "模板",
        "schema",
    )
    return any(term in message for term in knowledge_terms) and any(
        term in message for term in planning_terms
    )


def load_default_skill_manifests() -> tuple[SkillManifest, ...]:
    payload = read_plugin_json("skills", "manifest.json")
    if not isinstance(payload, list):
        raise ValueError("plugins/skills/manifest.json must contain a list")
    return tuple(SkillManifest.model_validate(item) for item in payload)


_DEFAULT_MANIFESTS = load_default_skill_manifests()

_MODE_TO_SKILL_ID = {
    "general_chat": "general_chat",
    "chat": "general_chat",
    "report": "external_impact_report",
    "impact": "external_impact_report",
    "external_impact_report": "external_impact_report",
    "policy": "external_impact_report",
    "policy_weekly_impact": "policy_weekly_impact",
    "news": "external_impact_report",
    "recent_news_report": "recent_news_report",
    "research": "external_impact_report",
    "research_report": "research_report",
    "wiki": "company_wiki_blueprint",
    "company_wiki_blueprint": "company_wiki_blueprint",
}

_MESSAGE_KEYWORD_RULES = (
    (
        (
            "\u653f\u7b56",
            "\u76d1\u7ba1",
            "\u6cd5\u89c4",
            "\u6cd5\u6761",
            "\u529e\u6cd5",
            "\u6761\u4f8b",
            "policy",
            "regulation",
        ),
        "external_impact_report",
    ),
    (
        (
            "\u65b0\u95fb",
            "\u65e5\u62a5",
            "\u6700\u65b0",
            "\u884c\u4e1a",
            "\u4e8b\u4ef6",
            "\u53d1\u5e03",
            "\u66f4\u65b0",
            "\u5f00\u6e90",
            "\u6570\u636e\u6e90",
            "news",
            "daily",
            "latest",
            "release",
            "changelog",
            "source",
            "sources",
        ),
        "external_impact_report",
    ),
    (
        (
            "\u8c03\u7814",
            "\u62a5\u544a",
            "\u7814\u7a76",
            "research",
            "report",
            "study",
        ),
        "external_impact_report",
    ),
    (
        (
            "\u77e5\u8bc6\u5e93",
            "wiki",
            "\u516c\u53f8\u8d44\u6599",
            "knowledge base",
            "company profile",
        ),
        "company_wiki_blueprint",
    ),
)

_KEYWORD_WEIGHTS = {
    "external_impact_report": {
        "\u653f\u7b56": 3,
        "\u76d1\u7ba1": 3,
        "\u6cd5\u89c4": 3,
        "\u6cd5\u6761": 3,
        "\u529e\u6cd5": 3,
        "\u6761\u4f8b": 3,
        "policy": 2,
        "regulation": 3,
        "\u65b0\u95fb": 3,
        "\u65e5\u62a5": 2,
        "\u6700\u65b0": 3,
        "\u884c\u4e1a": 2,
        "\u4e8b\u4ef6": 2,
        "\u53d1\u5e03": 2,
        "\u66f4\u65b0": 2,
        "\u5f00\u6e90": 2,
        "\u6570\u636e\u6e90": 3,
        "news": 3,
        "daily": 2,
        "latest": 3,
        "release": 3,
        "changelog": 3,
        "source": 2,
        "sources": 2,
        "\u8c03\u7814": 3,
        "\u62a5\u544a": 2,
        "\u7814\u7a76": 3,
        "research": 3,
        "report": 2,
        "study": 3,
    },
    "policy_weekly_impact": {
        "\u653f\u7b56": 3,
        "\u76d1\u7ba1": 3,
        "\u6cd5\u89c4": 3,
        "\u6cd5\u6761": 3,
        "\u529e\u6cd5": 3,
        "\u6761\u4f8b": 3,
        "policy": 2,
        "regulation": 3,
    },
    "recent_news_report": {
        "\u65b0\u95fb": 3,
        "\u65e5\u62a5": 2,
        "\u6700\u65b0": 3,
        "news": 3,
        "daily": 2,
        "latest": 3,
    },
    "research_report": {
        "\u8c03\u7814": 3,
        "\u62a5\u544a": 2,
        "\u7814\u7a76": 3,
        "research": 3,
        "report": 2,
        "study": 3,
    },
    "company_wiki_blueprint": {
        "\u77e5\u8bc6\u5e93": 3,
        "wiki": 3,
        "\u516c\u53f8\u8d44\u6599": 3,
        "knowledge base": 3,
        "company profile": 3,
    },
}

_KEYWORD_TIE_BREAKER = (
    "external_impact_report",
    "company_wiki_blueprint",
)
