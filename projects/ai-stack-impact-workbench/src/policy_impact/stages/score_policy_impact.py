"""Stage: score policy impact levels."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from policy_impact.harness.artifacts import write_json_artifact
from policy_impact.harness.gate_engine import GateEngine, claim_payload_from_assessment
from policy_impact.harness.state import PolicyImpactState


CLAUSE_TYPE_WEIGHTS = {
    "compliance": 24,
    "deadline": 22,
    "eligibility": 20,
    "benefit": 18,
    "material": 14,
    "support_direction": 10,
}

SOURCE_LEVEL_WEIGHTS = {
    "L1": 12,
    "L2": 9,
    "L3": 6,
    "L4": 3,
}

APPLICABILITY_LABELS = {
    "direct": "直接适用",
    "conditional": "条件适用",
    "not_applicable": "明确不适用",
    "insufficient_evidence": "证据不足",
}

BINDING_LABELS = {
    "mandatory": "强制性规则",
    "encouraged": "鼓励性要求",
    "guidance": "指导性意见",
    "unknown": "法律效力待确认",
}


def _impact_type(clause_type: str) -> str:
    return {
        "benefit": "opportunity",
        "eligibility": "qualification",
        "deadline": "operational_requirement",
        "material": "operational_requirement",
        "compliance": "compliance",
    }.get(clause_type, "market_signal")


def _level(score: int, clause_type: str) -> str:
    if clause_type == "deadline" and score >= 88:
        return "P0"
    if score >= 80:
        return "P1"
    if score >= 62:
        return "P2"
    if score >= 45:
        return "P3"
    return "P4"


def _source_level(state: PolicyImpactState, policy_id: str) -> str:
    for doc in state.policy_documents:
        if str(doc.get("policy_id")) == str(policy_id):
            return str(doc.get("source_level") or "L4")
    return "L4"


def _dedupe_evidence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    seen = set()
    for item in sorted(
        items,
        key=lambda x: (
            float(x.get("semantic_score", 0)),
            int(x.get("importance", 3)),
            float(x.get("confidence", 0.5)),
        ),
        reverse=True,
    ):
        key = item.get("fact_id") or (item.get("source_path"), item.get("value"))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out[:8]


def _missing_fields(clause_type: str, evidence: list[dict[str, Any]]) -> list[str]:
    fact_text = " ".join(str(item.get("value", "")) for item in evidence)
    missing = []
    if clause_type in {"benefit", "eligibility"} and "研发" not in fact_text:
        missing.append("研发费用占比或研发人员占比")
    if clause_type in {"eligibility", "material"} and not any(
        "qualification" in str(item.get("fact_id", "")) or "资质" in str(item.get("value", ""))
        for item in evidence
    ):
        missing.append("资质证书有效期")
    if clause_type == "deadline":
        missing.append("内部申报负责人和材料截止时间")
    if clause_type == "compliance":
        missing.append("现行合规制度或整改责任人")
    return missing


def _score_match(state: PolicyImpactState, match: dict[str, Any]) -> dict[str, Any]:
    evidence = match.get("company_evidence", [])
    clause_type = str(match.get("clause_type", "support_direction"))
    policy_id = str(match.get("policy_id", ""))
    source_level = _source_level(state, policy_id)
    max_importance = max((int(item.get("importance", 3)) for item in evidence), default=3)
    avg_confidence = sum(float(item.get("confidence", 0.5)) for item in evidence) / max(len(evidence), 1)
    semantic_score = float(match.get("match_quality", {}).get("max_semantic_score", 0))
    evidence_diversity = len({str(item.get("source_path", "")) for item in evidence if item.get("source_path")})
    evidence_count = len(evidence)

    raw_score = (
        CLAUSE_TYPE_WEIGHTS.get(clause_type, 10)
        + SOURCE_LEVEL_WEIGHTS.get(source_level, 3)
        + max_importance * 3
        + min(evidence_count * 2, 8)
        + min(semantic_score, 1.0) * 25
        + min(avg_confidence * 6, 6)
        + min(evidence_diversity * 2, 6)
    )
    missing = _missing_fields(clause_type, evidence)
    if missing:
        raw_score -= min(len(missing) * 6, 12)
    if semantic_score < 0.15:
        raw_score -= 10
    type_caps = {
        "support_direction": 68,
        "benefit": 78,
        "eligibility": 82,
        "material": 75,
        "compliance": 88,
        "deadline": 95,
    }
    raw_score = min(raw_score, type_caps.get(clause_type, 72))
    score = max(0, min(100, round(raw_score)))
    level = _level(score, clause_type)
    reasoning = [
        f"政策条款类型为 {clause_type}，基础权重 {CLAUSE_TYPE_WEIGHTS.get(clause_type, 10)}",
        f"政策来源等级为 {source_level}，来源权重 {SOURCE_LEVEL_WEIGHTS.get(source_level, 3)}",
        f"命中 {evidence_count} 条企业证据，最高重要性为 {max_importance}",
        f"归一化匹配质量为 {min(semantic_score, 1.0):.2f}，平均可信度为 {avg_confidence:.2f}",
    ]
    if missing:
        reasoning.append(f"仍需补充：{', '.join(missing)}")
    if evidence_diversity >= 2:
        reasoning.append("证据来自多个企业 Wiki 页面，降低单点误判风险")

    return {
        "assessment_id": "",
        "match_id": match["match_id"],
        "policy_id": match["policy_id"],
        "policy_title": match["policy_title"],
        "impact_level": level,
        "impact_type": _impact_type(clause_type),
        "relevance_score": score,
        "reasoning": reasoning,
        "company_evidence": _dedupe_evidence(evidence),
        "policy_evidence": match["policy_evidence"],
        "missing_fields": missing,
        "recommended_actions": _recommended_actions(level, clause_type, missing),
        "source_level": source_level,
        "clause_type": clause_type,
    }


def _recommended_actions(level: str, clause_type: str, missing: list[str]) -> list[str]:
    actions = []
    if level in {"P0", "P1"}:
        actions.append("24 小时内由业务负责人确认是否进入申报或整改流程")
    if clause_type in {"eligibility", "benefit"}:
        actions.append("核对申报条件、资金口径和主管部门窗口")
    if clause_type in {"deadline", "material"}:
        actions.append("建立材料清单和截止时间提醒")
    if clause_type == "compliance":
        actions.append("由法务/合规负责人确认是否触发制度更新")
    if missing:
        actions.append(f"补齐缺失字段：{', '.join(missing)}")
    return actions or ["持续观察，等待更多官方细则或企业资料补充"]


def _company_fact_map(state: PolicyImpactState) -> dict[str, dict[str, Any]]:
    facts = list(state.company_context_pack.get("pinned_facts") or []) + list(
        state.company_context_pack.get("retrieved_company_facts") or []
    )
    for match in state.policy_matches:
        facts.extend(match.get("company_evidence") or [])
    return {
        str(item.get("fact_id") or ""): item
        for item in facts
        if str(item.get("fact_id") or "")
    }


def _best_match(state: PolicyImpactState, policy_id: str) -> dict[str, Any]:
    matches = [
        item
        for item in state.policy_matches
        if str(item.get("policy_id") or "") == policy_id
    ]
    if not matches:
        return {}
    return max(
        matches,
        key=lambda item: float(item.get("match_quality", {}).get("max_semantic_score", 0)),
    )


def _score_applicability(
    state: PolicyImpactState,
    applicability: dict[str, Any],
    assessment_id: str,
) -> dict[str, Any]:
    policy_id = str(applicability.get("policy_id") or "")
    relationship = str(applicability.get("applicability") or "insufficient_evidence")
    binding = str(applicability.get("binding_effect") or "unknown")
    effective_status = str(applicability.get("effective_status") or "unknown")
    effective_date = str(applicability.get("effective_date") or "")
    as_of_date = str(applicability.get("as_of_date") or "")
    match = _best_match(state, policy_id)
    clause_type = str(match.get("clause_type") or "support_direction")
    fact_map = _company_fact_map(state)
    evidence = [
        fact_map[fact_id]
        for fact_id in applicability.get("matched_company_fact_ids") or []
        if fact_id in fact_map
    ]
    max_importance = max((int(item.get("importance", 3)) for item in evidence), default=0)
    confidence = float(applicability.get("confidence", 0.5))
    base = {
        "direct": 52,
        "conditional": 38,
        "not_applicable": 8,
        "insufficient_evidence": 12,
    }.get(relationship, 12)
    binding_bonus = {
        "mandatory": 16,
        "encouraged": 8,
        "guidance": 6,
        "unknown": 0,
    }.get(binding, 0)
    raw_score = base + binding_bonus + max_importance * 3 + round(confidence * 8)
    caps = {
        ("direct", "mandatory"): 92,
        ("direct", "encouraged"): 78,
        ("direct", "guidance"): 74,
        ("direct", "unknown"): 70,
        ("conditional", "mandatory"): 72,
        ("conditional", "encouraged"): 66,
        ("conditional", "guidance"): 62,
        ("conditional", "unknown"): 58,
        ("not_applicable", "mandatory"): 20,
        ("not_applicable", "encouraged"): 18,
        ("not_applicable", "guidance"): 16,
        ("not_applicable", "unknown"): 14,
        ("insufficient_evidence", "mandatory"): 28,
        ("insufficient_evidence", "encouraged"): 25,
        ("insufficient_evidence", "guidance"): 22,
        ("insufficient_evidence", "unknown"): 20,
    }
    score = max(0, min(raw_score, caps.get((relationship, binding), 60)))
    if relationship == "not_applicable" or relationship == "insufficient_evidence":
        level = "P4"
    elif relationship == "conditional":
        level = "P2" if score >= 62 else "P3"
    else:
        level = "P1" if score >= 80 else "P2" if score >= 62 else "P3"

    spans = list(applicability.get("policy_evidence_spans") or [])
    primary_span = spans[0] if spans else {}
    missing = list(applicability.get("missing_company_facts") or [])
    reasoning = [
        f"适用性结论：{APPLICABILITY_LABELS.get(relationship, relationship)}",
        str(applicability.get("scope_summary") or ""),
        f"规则效力：{BINDING_LABELS.get(binding, binding)}",
        f"绑定企业事实 {len(evidence)} 条，适用性置信度 {confidence:.2f}",
    ]
    if effective_status == "not_yet_effective":
        reasoning.append(f"时间效力：截至 {as_of_date} 尚未施行，将于 {effective_date} 施行")
    elif effective_status == "effective" and effective_date:
        reasoning.append(f"时间效力：已于 {effective_date} 施行")
    reason_codes = [str(item) for item in applicability.get("reason_codes") or []]
    if reason_codes:
        reasoning.append(f"判定代码：{', '.join(reason_codes)}")
    if missing:
        reasoning.append(f"待确认事实：{', '.join(missing)}")

    impact_type = _impact_type(clause_type)
    if binding == "guidance" or relationship in {"not_applicable", "insufficient_evidence"}:
        impact_type = "market_signal"
    return {
        "assessment_id": assessment_id,
        "match_id": str(match.get("match_id") or f"applicability:{policy_id}"),
        "policy_id": policy_id,
        "policy_title": str(applicability.get("policy_title") or ""),
        "impact_level": level,
        "impact_type": impact_type,
        "relevance_score": score,
        "reasoning": [item for item in reasoning if item],
        "company_evidence": _dedupe_evidence(evidence),
        "policy_evidence": {
            "policy_id": policy_id,
            "clause_id": primary_span.get("span_id", ""),
            "text": primary_span.get("text", ""),
            "source_url": primary_span.get("source_url", ""),
            "verified": bool(primary_span.get("verified", False)),
            "additional_spans": spans[1:4],
        },
        "missing_fields": missing,
        "recommended_actions": list(applicability.get("recommended_actions") or []),
        "source_level": _source_level(state, policy_id),
        "clause_type": clause_type,
        "applicability": relationship,
        "binding_effect": binding,
        "effective_date": effective_date,
        "effective_status": effective_status,
        "as_of_date": as_of_date,
        "trigger_conditions": list(applicability.get("trigger_conditions") or []),
        "exclusion_conditions": list(applicability.get("exclusion_conditions") or []),
        "reason_codes": reason_codes,
    }


def _merge_group(items: list[dict[str, Any]], assessment_id: str) -> dict[str, Any]:
    items.sort(key=lambda item: item["relevance_score"], reverse=True)
    primary = dict(items[0])
    primary["assessment_id"] = assessment_id
    if len(items) == 1:
        return primary

    all_evidence = []
    all_missing = []
    merged_match_ids = []
    for item in items:
        all_evidence.extend(item.get("company_evidence", []))
        all_missing.extend(item.get("missing_fields", []))
        merged_match_ids.append(item.get("match_id", ""))
    primary["company_evidence"] = _dedupe_evidence(all_evidence)
    primary["missing_fields"] = sorted(set(all_missing))
    primary["reasoning"] = list(primary["reasoning"]) + [
        f"同一政策下合并 {len(items)} 个相关条款，避免重复生成独立预警",
    ]
    primary["merged_match_ids"] = [item for item in merged_match_ids if item]
    return primary


class ClaimGateBlocked(RuntimeError):
    pass


def _audit_claims(state: PolicyImpactState, gate_engine: GateEngine) -> None:
    attempt = state.stage_retry_counts.get("score_policy_impact", 0) + 1
    for assessment in state.impact_assessments:
        claim_id = str(assessment.get("assessment_id") or "unknown")
        decision = gate_engine.evaluate_claim(
            claim_payload_from_assessment(assessment),
            span_id=f"score_policy_impact:{claim_id}",
            attempt=attempt,
        )
        if not decision.passed:
            reason = "; ".join(decision.reasons) or "claim gate blocked"
            raise ClaimGateBlocked(f"{claim_id}: {reason}")


def run(state: PolicyImpactState, *, gate_engine: GateEngine) -> PolicyImpactState:
    if state.policy_applicability:
        state.impact_assessments = sorted(
            [
                _score_applicability(state, item, f"impact_{index:03d}")
                for index, item in enumerate(state.policy_applicability, start=1)
            ],
            key=lambda item: item.get("relevance_score", 0),
            reverse=True,
        )
        state.quality_metrics["impact_assessment_count"] = len(state.impact_assessments)
        state.quality_metrics["p0_p1_count"] = sum(
            1 for item in state.impact_assessments if item.get("impact_level") in {"P0", "P1"}
        )
        state.quality_metrics["forced_relevance_count"] = sum(
            1
            for item in state.impact_assessments
            if item.get("applicability") == "not_applicable" and item.get("impact_level") != "P4"
        )
        _audit_claims(state, gate_engine)
        write_json_artifact(
            state,
            "impact_assessments",
            state.impact_assessments,
            "data/policies/processed",
            "impact_assessments.json",
        )
        return state

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for match in state.policy_matches:
        assessment = _score_match(state, match)
        key = (str(assessment["policy_id"]), str(assessment["impact_type"]))
        grouped[key].append(assessment)

    assessments = [
        _merge_group(items, f"impact_{idx:03d}")
        for idx, items in enumerate(grouped.values(), start=1)
    ]

    state.impact_assessments = sorted(
        assessments,
        key=lambda item: (item["relevance_score"], item["impact_level"]),
        reverse=True,
    )
    state.quality_metrics["impact_assessment_count"] = len(state.impact_assessments)
    state.quality_metrics["p0_p1_count"] = sum(
        1 for item in state.impact_assessments if item.get("impact_level") in {"P0", "P1"}
    )
    _audit_claims(state, gate_engine)
    write_json_artifact(
        state,
        "impact_assessments",
        state.impact_assessments,
        "data/policies/processed",
        "impact_assessments.json",
    )
    return state
