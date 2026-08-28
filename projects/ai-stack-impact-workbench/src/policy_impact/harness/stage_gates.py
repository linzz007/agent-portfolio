"""Stage gate checks for policy impact runtime."""

from __future__ import annotations

from typing import Any

from policy_impact.harness.state import PolicyImpactState
from policy_impact.linters.common import lint_result
from policy_impact.schemas import (
    CompanyContextPack,
    ImpactAssessment,
    PolicyClause,
    PolicyApplicability,
    PolicyDocument,
    PolicyMatch,
    ReviewResult,
    validation_errors,
    validate_one,
)


def _not_empty(value: Any) -> bool:
    return bool(value)


def evaluate_stage_gate(state: PolicyImpactState, stage_name: str) -> dict[str, Any]:
    checks = {
        "load_company_context": (state.company_context_pack, "企业上下文为空", True),
        "fetch_recent_policies": (state.policy_documents, "政策文档为空", True),
        "policy_ingest_and_index": (state.policy_chunks, "政策索引 chunk 为空", True),
        "retrieve_relevant_clauses": (state.evidence_hits, "证据召回为空", True),
        "extract_policy_clauses": (state.policy_clauses, "政策条款为空", True),
        "match_company_policy": (state.policy_documents, "缺少可进行适用性判断的政策文档", False),
        "analyze_policy_applicability": (state.policy_applicability, "政策适用性判断为空", False),
        "score_policy_impact": (state.impact_assessments, "影响评估为空", False),
        "review_evidence_and_risk": (state.review_result, "复核结果为空", True),
        "generate_weekly_report": (state.report_paths, "报告路径为空", False),
    }
    if stage_name not in checks:
        return lint_result(stage_name, True, [], {"note": "no linter configured"}, retryable=False)

    value, issue, retryable = checks[stage_name]
    issues = [] if _not_empty(value) else [issue]

    if stage_name == "load_company_context" and state.company_context_pack:
        issues.extend(validate_one(CompanyContextPack, state.company_context_pack))

    if stage_name == "fetch_recent_policies":
        if state.policy_documents:
            issues.extend(validation_errors(PolicyDocument, state.policy_documents))
        elif state.source_manifest.get("no_updates") and state.source_manifest.get("collection_succeeded"):
            issues = [item for item in issues if item != "政策文档为空"]

    if stage_name == "extract_policy_clauses":
        issues.extend(validation_errors(PolicyClause, state.policy_clauses))

    if stage_name == "match_company_policy":
        issues.extend(validation_errors(PolicyMatch, state.policy_matches))

    if stage_name == "analyze_policy_applicability":
        issues.extend(validation_errors(PolicyApplicability, state.policy_applicability))
        document_ids = {str(item.get("policy_id") or "") for item in state.policy_documents}
        applicability_ids = {str(item.get("policy_id") or "") for item in state.policy_applicability}
        missing_policy_ids = sorted(document_ids - applicability_ids)
        if missing_policy_ids:
            issues.append(f"存在未完成适用性判断的政策：{', '.join(missing_policy_ids)}")
        for item in state.policy_applicability:
            if item.get("applicability") == "direct" and not item.get("matched_company_fact_ids"):
                issues.append(f"{item.get('policy_id')}: direct applicability missing company evidence")
            if item.get("applicability") == "conditional" and not item.get("missing_company_facts"):
                issues.append(f"{item.get('policy_id')}: conditional applicability missing condition facts")
            if item.get("applicability") != "insufficient_evidence" and not item.get("policy_evidence_spans"):
                issues.append(f"{item.get('policy_id')}: applicability missing verified policy evidence")
            if any(not span.get("verified") for span in item.get("policy_evidence_spans") or []):
                issues.append(f"{item.get('policy_id')}: applicability contains unverified quote")

    if stage_name == "score_policy_impact":
        issues.extend(validation_errors(ImpactAssessment, state.impact_assessments))
        bad = [x for x in state.impact_assessments if not x.get("impact_level") or not x.get("reasoning")]
        if bad:
            issues.append("存在缺少 impact_level 或 reasoning 的影响评估")
        for assessment in state.impact_assessments:
            if assessment.get("impact_level") in {"P0", "P1"} and not assessment.get("company_evidence"):
                issues.append(
                    f"{assessment.get('assessment_id', 'unknown')}: high impact assessment missing company_evidence"
                )
            if not str((assessment.get("policy_evidence") or {}).get("text") or "").strip():
                issues.append(
                    f"{assessment.get('assessment_id', 'unknown')}: assessment missing policy_evidence"
                )

    if stage_name == "review_evidence_and_risk" and state.review_result:
        issues.extend(validate_one(ReviewResult, state.review_result))

    if stage_name == "generate_weekly_report":
        report = state.report_paths.get("report")
        html_report = state.report_paths.get("html_report")
        artifact = state.report_paths.get("run_artifact")
        if not report:
            issues.append("缺少 report 路径")
        if not html_report:
            issues.append("缺少 html_report 路径")
        if not artifact:
            issues.append("缺少 run_artifact 路径")

    return lint_result(
        stage_name=stage_name,
        passed=not issues,
        issues=issues,
        metrics={
            "policy_documents": len(state.policy_documents),
            "evidence_hits": len(state.evidence_hits),
            "policy_matches": len(state.policy_matches),
            "policy_applicability": len(state.policy_applicability),
            "impact_assessments": len(state.impact_assessments),
            "source_collection_succeeded": bool(state.source_manifest.get("collection_succeeded")),
            "no_updates": bool(state.source_manifest.get("no_updates")),
        },
        retryable=retryable,
    )
