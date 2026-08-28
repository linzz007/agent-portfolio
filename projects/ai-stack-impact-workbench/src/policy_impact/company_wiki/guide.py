"""Company Wiki onboarding guidance.

The guide turns a loose enterprise self-description into a maintainable folder
contract. It is intentionally deterministic because the folder shape is a
runtime dependency for RAG, policy matching, and memory updates.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from policy_impact.company_wiki.loader import company_dir, load_company_knowledge


@dataclass(frozen=True)
class WikiFileSpec:
    path: str
    tier: str
    importance: int
    domain: str
    purpose: str
    required_fact_ids: tuple[str, ...]
    guiding_questions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["required_fact_ids"] = list(self.required_fact_ids)
        data["guiding_questions"] = list(self.guiding_questions)
        return data


WIKI_FILE_SPECS: tuple[WikiFileSpec, ...] = (
    WikiFileSpec(
        path="wiki/00_profile.md",
        tier="high",
        importance=5,
        domain="company_profile",
        purpose="沉淀企业身份、经营范围、所在区域和政策适用边界。",
        required_fact_ids=("profile.region", "profile.entity_type", "profile.industry"),
        guiding_questions=(
            "公司注册地、主要经营地和纳税地分别在哪里？",
            "企业属于软件、制造、专精特新、平台经济还是其他类型？",
            "目前最希望政策系统优先关注补贴、合规、资质还是市场机会？",
        ),
    ),
    WikiFileSpec(
        path="wiki/01_business.md",
        tier="high",
        importance=5,
        domain="business",
        purpose="描述核心产品、收入来源、客户行业和未来 6-12 个月经营目标。",
        required_fact_ids=("business.core_product", "business.customer_segment", "business.current_goal"),
        guiding_questions=(
            "核心产品或服务解决什么客户问题？",
            "主要客户来自哪些行业或政府/企业部门？",
            "近期是否有研发投入、市场扩张、招投标、融资或产能建设计划？",
        ),
    ),
    WikiFileSpec(
        path="wiki/02_operations.md",
        tier="medium",
        importance=4,
        domain="operations",
        purpose="记录人员规模、研发费用、项目备案、供应链、厂区和经营约束。",
        required_fact_ids=("ops.employee_count", "ops.rd_ratio", "ops.project_status"),
        guiding_questions=(
            "研发人员、研发费用占比、知识产权和项目备案是否具备？",
            "是否有厂房、数据、设备、供应链、环保、安全生产等约束？",
            "哪些经营数据是政策判断必须优先引用的？",
        ),
    ),
    WikiFileSpec(
        path="wiki/03_qualifications.md",
        tier="high",
        importance=5,
        domain="qualifications",
        purpose="记录高新、专精特新、软著、专利、许可证等政策强相关资质。",
        required_fact_ids=("qualification.high_tech", "qualification.ip", "qualification.license"),
        guiding_questions=(
            "已有资质、证书、知识产权分别是什么，有效期到什么时候？",
            "哪些资质正在申请或准备申请？",
            "哪些资质不具备但政策经常要求？",
        ),
    ),
    WikiFileSpec(
        path="wiki/04_policy_history.md",
        tier="medium",
        importance=4,
        domain="policy_history",
        purpose="记录历史申报、获批、失败、补贴到账和主管部门沟通记录。",
        required_fact_ids=("policy_history.applied_programs", "policy_history.failed_reasons"),
        guiding_questions=(
            "过去 24 个月申报过哪些项目，结果如何？",
            "失败原因是材料缺失、资质不满足、时间窗口错过还是人工判断？",
            "哪些主管部门或园区窗口已经建立沟通？",
        ),
    ),
    WikiFileSpec(
        path="wiki/08_preferences.md",
        tier="low",
        importance=3,
        domain="preferences",
        purpose="记录用户偏好、报告口径、风险承受能力和不想重复解释的信息。",
        required_fact_ids=("preference.report_style", "preference.risk_tolerance"),
        guiding_questions=(
            "报告希望偏执行清单、管理层摘要还是证据审计？",
            "哪些事项必须人工确认后才能推荐行动？",
            "哪些企业表述容易被系统误解，需要固定口径？",
        ),
    ),
)


def build_wiki_blueprint(company_id: str) -> dict[str, Any]:
    """Return the expected Company Wiki folder contract and current gaps."""

    root = company_dir(company_id)
    existing_paths = {path.relative_to(root).as_posix() for path in (root / "wiki").glob("*.md")}
    try:
        knowledge = load_company_knowledge(company_id)
        existing_fact_ids = {str(fact.get("fact_id", "")) for fact in knowledge.get("facts", [])}
    except FileNotFoundError:
        existing_fact_ids = set()

    files = []
    missing_files = []
    missing_fact_ids = []
    for spec in WIKI_FILE_SPECS:
        present = spec.path in existing_paths
        missing = [fact_id for fact_id in spec.required_fact_ids if fact_id not in existing_fact_ids]
        entry = spec.to_dict()
        entry["present"] = present
        entry["missing_fact_ids"] = missing
        files.append(entry)
        if not present:
            missing_files.append(spec.path)
        missing_fact_ids.extend(missing)

    return {
        "company_id": company_id,
        "root": str(root),
        "files": files,
        "missing_files": missing_files,
        "missing_fact_ids": sorted(set(missing_fact_ids)),
        "next_questions": [
            question
            for file_spec in files
            if (not file_spec["present"] or file_spec["missing_fact_ids"])
            for question in file_spec["guiding_questions"][:2]
        ][:8],
    }


def render_wiki_template(spec: WikiFileSpec) -> str:
    """Render a starter Markdown page for one missing wiki file."""

    questions = "\n".join(f"- {question}" for question in spec.guiding_questions)
    fact_blocks = "\n\n".join(
        "\n".join(
            [
                f"## FACT: {fact_id}",
                f"- importance: {spec.importance}",
                "- confidence: 0.5",
                "- source: user_input",
                "- value: ",
                "- policy_relevance: []",
                "",
                "待补充。",
            ]
        )
        for fact_id in spec.required_fact_ids
    )
    return "\n".join(
        [
            "---",
            f"domain: {spec.domain}",
            f"importance: {spec.importance}",
            "confidence: 0.5",
            "---",
            "",
            f"# {Path(spec.path).stem}",
            "",
            spec.purpose,
            "",
            "## 填写提示",
            questions,
            "",
            fact_blocks,
            "",
        ]
    )

