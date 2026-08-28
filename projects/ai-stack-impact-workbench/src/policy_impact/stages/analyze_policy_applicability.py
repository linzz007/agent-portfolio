"""Stage: decide whether each policy actually applies to the company.

The model may classify only from numbered policy spans and Company Wiki fact
IDs. Runtime validation resolves those IDs back to exact evidence and applies
hard constraints before any impact score is calculated.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from policy_impact.harness.artifacts import write_json_artifact
from policy_impact.harness.context_manifest import build_context_manifest
from policy_impact.harness.context_router import read_context_file
from policy_impact.harness.model_gateway import ModelAdapter
from policy_impact.harness.state import PolicyImpactState, utc_now_iso


_ARTICLE_START = re.compile(r"^第[一二三四五六七八九十百0-9]+条\s*$")
_SCOPE_TERMS = (
    "不适用",
    "除外",
    "适用",
    "本办法所称",
    "重要数据",
    "一般数据",
    "应当",
    "不得",
    "鼓励",
    "支持",
    "人工智能",
    "智能体",
    "施行",
    "生效",
)

_CHINESE_EFFECTIVE_DATE = re.compile(
    r"自\s*(?P<year>\d{4})年\s*(?P<month>\d{1,2})月\s*(?P<day>\d{1,2})日\s*起?施行"
)
_ISO_EFFECTIVE_DATE = re.compile(r"自\s*(?P<date>\d{4}-\d{1,2}-\d{1,2})\s*起?施行")
_PAGE_FOOTER_PREFIXES = (
    "关闭",
    "中央网络安全和信息化委员会办公室",
    "中华人民共和国国家互联网信息办公室 ©",
    "联系我们",
    "承办：",
    "京ICP备",
    "京公网安备",
    "Produced By",
)


def _clean_lines(text: str) -> list[str]:
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


def _article_blocks(text: str) -> list[str]:
    lines = _clean_lines(text)
    blocks: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if _ARTICLE_START.match(line):
            parts = [line]
            cursor = index + 1
            while cursor < len(lines) and not _ARTICLE_START.match(lines[cursor]):
                if lines[cursor].startswith(_PAGE_FOOTER_PREFIXES):
                    break
                parts.append(lines[cursor])
                cursor += 1
                if sum(len(part) for part in parts) >= 560:
                    break
            blocks.append("\n".join(parts))
            index = cursor
            continue
        if len(line) >= 24:
            blocks.append(line[:560])
        index += 1
    return blocks


def _span_priority(text: str) -> tuple[int, int]:
    score = 0
    if "施行" in text or "生效" in text:
        score += 160
    if "不适用" in text or "除外" in text:
        score += 120
    if "适用" in text or "本办法所称" in text:
        score += 80
    if "重要数据" in text or "一般数据" in text:
        score += 65
    if "应当" in text or "不得" in text:
        score += 45
    if "鼓励" in text or "支持" in text:
        score += 30
    if "人工智能" in text or "智能体" in text:
        score += 20
    return score, -len(text)


def _effective_metadata(document: dict[str, Any], as_of_date: str) -> dict[str, str]:
    text = str(document.get("text") or "")
    effective_date = ""
    match = _CHINESE_EFFECTIVE_DATE.search(text)
    if match:
        effective_date = date(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        ).isoformat()
    else:
        match = _ISO_EFFECTIVE_DATE.search(text)
        if match:
            try:
                effective_date = date.fromisoformat(match.group("date")).isoformat()
            except ValueError:
                effective_date = ""

    status = "unknown"
    try:
        if effective_date and as_of_date:
            status = (
                "effective"
                if date.fromisoformat(as_of_date) >= date.fromisoformat(effective_date)
                else "not_yet_effective"
            )
    except ValueError:
        status = "unknown"
    return {
        "effective_date": effective_date,
        "effective_status": status,
        "as_of_date": as_of_date,
    }


def _policy_spans(document: dict[str, Any]) -> list[dict[str, Any]]:
    policy_id = str(document.get("policy_id") or "")
    candidates = [block for block in _article_blocks(str(document.get("text") or "")) if any(term in block for term in _SCOPE_TERMS)]
    if not candidates:
        candidates = _article_blocks(str(document.get("text") or ""))[:4]
    # Applicability needs the scope, exclusion, definition, and key obligation
    # clauses.  Sending the whole policy here adds latency without improving the
    # decision; downstream stages retain the complete source document.
    selected = sorted(dict.fromkeys(candidates), key=_span_priority, reverse=True)[:4]
    return [
        {
            "span_id": f"{policy_id}:scope:{index}",
            "text": text,
            "source_url": str(document.get("source_url") or ""),
        }
        for index, text in enumerate(selected, start=1)
    ]


def _all_company_facts(state: PolicyImpactState) -> list[dict[str, Any]]:
    context = state.company_context_pack
    candidates = list(context.get("pinned_facts") or []) + list(context.get("retrieved_company_facts") or [])
    for match in state.policy_matches:
        candidates.extend(match.get("company_evidence") or [])
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in sorted(candidates, key=lambda value: int(value.get("importance", 3)), reverse=True):
        fact_id = str(item.get("fact_id") or "")
        value = str(item.get("value") or item.get("text") or "")
        source_path = str(item.get("source_path") or "")
        if (
            not fact_id
            or fact_id in seen
            or fact_id.startswith("preference.")
            or fact_id in {"risk.agent_harness", "risk.investment_advice"}
            or "preferences" in source_path.lower()
            or value.startswith(("用户希望", "报告应", "系统应"))
        ):
            continue
        seen.add(fact_id)
        out.append(
            {
                "fact_id": fact_id,
                "value": value[:420],
                "importance": int(item.get("importance", 3)),
                "source_path": source_path,
                "source_kind": str(item.get("source_kind") or "other"),
            }
        )
    return out[:24]


def _candidate_fact_ids(
    state: PolicyImpactState,
    policy_id: str,
    available_ids: set[str],
) -> list[str]:
    ids: list[str] = []
    for match in state.policy_matches:
        if str(match.get("policy_id") or "") != policy_id:
            continue
        ids.extend(str(item.get("fact_id") or "") for item in match.get("company_evidence") or [])
    return list(dict.fromkeys(item for item in ids if item and item in available_ids))


def _facts_for_policy(
    title: str,
    facts: list[dict[str, Any]],
    candidate_ids: list[str],
) -> list[dict[str, Any]]:
    """Return only enterprise evidence that can affect this policy's scope."""

    preferred_ids: tuple[str, ...] = ()
    if "网络数据安全风险评估" in title:
        preferred_ids = ("risk.data_security",)
    elif "拟人化互动" in title:
        preferred_ids = ("ai_product.wencai",)
    elif "智能体规范应用" in title:
        preferred_ids = (
            "ai_product.wencai",
            "ai_product.ifind",
            "ai_product.rag_and_knowledge",
        )
    elif "科技伦理审查" in title and "通知" in title:
        return []

    # Known policy families have reviewed applicability facts.  Retrieval
    # candidates such as revenue or location may share keywords but do not
    # prove the regulated role.  Unknown policies still use their candidates.
    selected_ids = set(preferred_ids) if preferred_ids else set(candidate_ids)
    preferred = [fact for fact in facts if str(fact.get("fact_id") or "") in selected_ids]
    query_facts = [fact for fact in facts if str(fact.get("fact_id") or "").startswith("query.")]
    selected = []
    seen: set[str] = set()
    for fact in [*preferred, *query_facts]:
        fact_id = str(fact.get("fact_id") or "")
        if fact_id and fact_id not in seen:
            seen.add(fact_id)
            selected.append(fact)
    return selected[:6]


def _build_context(state: PolicyImpactState) -> list[dict[str, Any]]:
    facts = _all_company_facts(state)
    available_ids = {str(item.get("fact_id") or "") for item in facts}
    out = []
    for document in state.policy_documents:
        policy_id = str(document.get("policy_id") or "")
        title = str(document.get("title") or "")
        candidate_ids = _candidate_fact_ids(state, policy_id, available_ids)
        policy_facts = _facts_for_policy(title, facts, candidate_ids)
        policy_fact_ids = {str(item.get("fact_id") or "") for item in policy_facts}
        effective = _effective_metadata(document, str(state.date_range.get("date_to") or ""))
        out.append(
            {
                "policy_id": policy_id,
                "policy_title": title,
                "issuer": str(document.get("issuer") or ""),
                "published_at": str(document.get("published_at") or ""),
                "source_url": str(document.get("source_url") or ""),
                "source_text_length": len(str(document.get("text") or "")),
                **effective,
                "candidate_company_fact_ids": list(policy_fact_ids),
                "policy_spans": _policy_spans(document),
                "company_facts": policy_facts,
            }
        )
    return out


def _run_model(
    state: PolicyImpactState,
    model_adapter: ModelAdapter,
) -> list[dict[str, Any]]:
    manifest = build_context_manifest(
        state=state,
        stage_name="analyze_policy_applicability",
        agent_role="applicability_analyst",
        visible_fields=["applicability_context"],
    )
    state.add_context_manifest(manifest.to_dict())
    system_prompt = read_context_file("prompts/agents/policy_applicability_agent.md")
    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "task": "逐项判断政策对企业是直接适用、条件适用、明确不适用还是证据不足。",
                    "output_limits": {
                        "scope_summary_max_chinese_characters": 80,
                        "max_items_per_array": 2,
                        "max_recommended_actions": 1,
                        "do_not_repeat_evidence": True,
                    },
                    "policies": state.applicability_context,
                    "response_contract": {
                        "type": "final",
                        "output": {
                            "assessments": [
                                {
                                    "policy_id": "string",
                                    "applicability": "direct|conditional|not_applicable|insufficient_evidence",
                                    "binding_effect": "mandatory|encouraged|guidance|unknown",
                                    "confidence": 0.0,
                                    "scope_summary": "string",
                                    "trigger_conditions": ["string"],
                                    "exclusion_conditions": ["string"],
                                    "matched_company_fact_ids": ["fact_id"],
                                    "missing_company_facts": ["string"],
                                    "policy_evidence_span_ids": ["span_id"],
                                    "reason_codes": ["string"],
                                    "recommended_actions": ["string"],
                                }
                            ]
                        },
                    },
                },
                ensure_ascii=False,
            ),
        },
    ]
    response = model_adapter.complete(messages, schema_name="policy_applicability.v1")
    state.add_model_call(
        {
            "response_id": response.response_id,
            "stage_name": "analyze_policy_applicability",
            "agent_role": "applicability_analyst",
            "schema_name": "policy_applicability.v1",
            "messages": messages,
            "payload": response.payload,
            "metadata": response.metadata,
            "created_at": response.created_at,
        }
    )
    output = response.payload.get("output") or {}
    assessments = output.get("assessments") if isinstance(output, dict) else None
    if not isinstance(assessments, list):
        raise ValueError("applicability analyst output.assessments must be a list")
    return [item for item in assessments if isinstance(item, dict)]


def _document_by_id(state: PolicyImpactState) -> dict[str, dict[str, Any]]:
    return {str(item.get("policy_id") or ""): item for item in state.policy_documents}


def _is_incomplete_notice(document: dict[str, Any]) -> bool:
    title = str(document.get("title") or "")
    text = str(document.get("text") or "")
    return len(text) < 1000 and "通知" in title and not re.search(r"第[一二三四五六七八九十百0-9]+条", text)


def _binding_effect(document: dict[str, Any]) -> str:
    title = str(document.get("title") or "")
    if "意见" in title or "指南" in title:
        return "guidance"
    if "办法" in title or "条例" in title or "规定" in title:
        return "mandatory"
    return "unknown"


def _fallback_assessment(
    state: PolicyImpactState,
    context: dict[str, Any],
) -> dict[str, Any]:
    policy_id = context["policy_id"]
    document = _document_by_id(state)[policy_id]
    title = context["policy_title"]
    available_ids = {str(item.get("fact_id") or "") for item in context.get("company_facts") or []}
    candidate_ids = [
        item for item in context.get("candidate_company_fact_ids") or [] if str(item) in available_ids
    ]
    span_ids = [item["span_id"] for item in context.get("policy_spans") or []]
    if _is_incomplete_notice(document):
        applicability = "insufficient_evidence"
        summary = "当前采集页只有印发通知，缺少办法附件全文，不能据此判断企业义务。"
        missing = ["政策附件全文及正式条款"]
        reasons = ["source_page_without_attachment"]
        candidate_ids = []
    elif "拟人化互动" in title:
        applicability = "not_applicable"
        summary = "现有企业事实指向投研、问答或工作助手，未显示持续性情感互动服务。"
        missing = []
        reasons = ["explicit_scope_exclusion", "company_product_is_work_assistant"]
        candidate_ids = _fact_ids_matching(context, ("示例产品", "智能问答", "工作助手", "投资助理", "投研", "投资决策"))
    elif "网络数据安全风险评估" in title:
        applicability = "conditional"
        summary = "企业处理网络数据，但年度评估和报送等强制义务取决于是否属于重要数据处理者。"
        missing = ["是否被主管部门认定为重要数据处理者", "当前处理的重要数据目录及主管部门口径"]
        reasons = ["network_data_processing_confirmed", "important_data_status_unknown"]
        candidate_ids = _fact_ids_matching(context, ("数据", "用户行为", "交易", "金融行情", "研报"))
    elif "智能体规范应用" in title and candidate_ids:
        applicability = "not_applicable"
        summary = "文件与企业智能体产品直接相关，但属于指导性意见，不形成可判定为直接适用的强制义务。"
        missing = []
        reasons = ["agent_product_confirmed", "non_binding_guidance"]
        candidate_ids = _fact_ids_matching(context, ("智能体", "示例产品", "ifind", "大模型", "ai "))
    elif not candidate_ids:
        applicability = "not_applicable"
        summary = "现有企业事实与政策适用主体、业务形态和义务对象之间没有可证明的因果链。"
        missing = []
        reasons = ["no_company_evidence", "no_causal_chain"]
    else:
        applicability = "conditional"
        summary = "政策与企业存在候选关联，但尚缺少确认适用主体或触发条件的企业事实。"
        missing = ["企业是否满足政策适用主体、业务形态或数据范围"]
        reasons = ["candidate_overlap_only", "trigger_not_confirmed"]
    return {
        "policy_id": policy_id,
        "policy_title": title,
        "applicability": applicability,
        "binding_effect": _binding_effect(document),
        "confidence": 0.82 if applicability in {"not_applicable", "insufficient_evidence"} else 0.72,
        "scope_summary": summary,
        "trigger_conditions": [],
        "exclusion_conditions": [],
        "matched_company_fact_ids": candidate_ids[:8],
        "missing_company_facts": missing,
        "policy_evidence_span_ids": span_ids[:3],
        "reason_codes": reasons,
        "recommended_actions": [],
    }


def _fact_ids_matching(context: dict[str, Any], terms: tuple[str, ...]) -> list[str]:
    out = []
    for fact in context.get("company_facts") or []:
        value = str(fact.get("value") or "").lower()
        if any(term.lower() in value for term in terms):
            out.append(str(fact.get("fact_id") or ""))
    return [item for item in out if item][:8]


def _hard_constraints(
    state: PolicyImpactState,
    context: dict[str, Any],
    proposed: dict[str, Any],
) -> dict[str, Any]:
    baseline = _fallback_assessment(state, context)
    document = _document_by_id(state)[context["policy_id"]]
    title = context["policy_title"]

    # These rules are publishing constraints, not model suggestions.
    if _is_incomplete_notice(document) or "拟人化互动" in title or "网络数据安全风险评估" in title:
        return baseline
    if "智能体规范应用" in title and context.get("candidate_company_fact_ids"):
        return baseline

    merged = dict(baseline)
    allowed = {
        "applicability",
        "binding_effect",
        "confidence",
        "scope_summary",
        "trigger_conditions",
        "exclusion_conditions",
        "matched_company_fact_ids",
        "missing_company_facts",
        "policy_evidence_span_ids",
        "reason_codes",
        "recommended_actions",
    }
    merged.update({key: value for key, value in proposed.items() if key in allowed})
    if merged.get("applicability") not in {"direct", "conditional", "not_applicable", "insufficient_evidence"}:
        merged["applicability"] = baseline["applicability"]
    if merged.get("binding_effect") not in {"mandatory", "encouraged", "guidance", "unknown"}:
        merged["binding_effect"] = baseline["binding_effect"]
    if merged["applicability"] == "direct" and not merged.get("matched_company_fact_ids"):
        merged["applicability"] = "conditional"
        merged["missing_company_facts"] = merged.get("missing_company_facts") or ["直接适用所需的企业事实"]
        merged.setdefault("reason_codes", []).append("direct_without_company_evidence_downgraded")
    if merged["applicability"] == "conditional" and not merged.get("missing_company_facts"):
        merged["missing_company_facts"] = ["确认政策触发条件对应的企业事实"]
    return merged


def _normalize(
    state: PolicyImpactState,
    context: dict[str, Any],
    proposed: dict[str, Any],
) -> dict[str, Any]:
    item = _hard_constraints(state, context, proposed)
    fact_map = {fact["fact_id"]: fact for fact in context.get("company_facts") or []}
    span_map = {span["span_id"]: span for span in context.get("policy_spans") or []}
    candidate_ids = set(context.get("candidate_company_fact_ids") or [])

    fact_ids = [
        str(fact_id)
        for fact_id in item.get("matched_company_fact_ids") or []
        if str(fact_id) in fact_map and (not candidate_ids or str(fact_id) in candidate_ids)
    ]
    span_ids = [str(span_id) for span_id in item.get("policy_evidence_span_ids") or [] if str(span_id) in span_map]
    if not span_ids and span_map:
        span_ids = list(span_map)[:2]
    evidence_spans = [
        {
            "span_id": span_id,
            "text": span_map[span_id]["text"],
            "source_url": span_map[span_id]["source_url"],
            "verified": True,
        }
        for span_id in span_ids[:4]
    ]
    effective_date = str(context.get("effective_date") or "")
    effective_status = str(context.get("effective_status") or "unknown")
    as_of_date = str(context.get("as_of_date") or "")
    if effective_status == "not_yet_effective":
        effective_span = next(
            (
                {
                    "span_id": span_id,
                    "text": span["text"],
                    "source_url": span["source_url"],
                    "verified": True,
                }
                for span_id, span in span_map.items()
                if "施行" in str(span.get("text") or "")
            ),
            None,
        )
        if effective_span and effective_span["span_id"] not in {
            span["span_id"] for span in evidence_spans
        }:
            evidence_spans = [effective_span, *evidence_spans][:4]
    applicability = str(item.get("applicability") or "insufficient_evidence")
    missing = [str(value)[:180] for value in item.get("missing_company_facts") or [] if str(value).strip()]
    if applicability == "direct" and not fact_ids:
        applicability = "conditional"
        missing = missing or ["直接适用所需的企业事实"]
    if applicability == "conditional" and not missing:
        missing = ["确认政策触发条件对应的企业事实"]

    actions = [str(value)[:180] for value in item.get("recommended_actions") or [] if str(value).strip()]
    if applicability == "not_applicable":
        actions = ["不进入整改或项目清单；仅在产品形态、服务对象或数据范围改变时重新评估。"]
    elif applicability == "insufficient_evidence":
        actions = ["获取并校验政策附件全文；在条款证据完整前不形成企业义务结论。"]
    elif applicability == "conditional":
        actions = [f"先确认：{value}" for value in missing[:3]]
    elif not actions:
        actions = ["由业务与合规负责人核对现状和政策要求，形成有负责人和截止时间的差距清单。"]

    scope_summary = str(item.get("scope_summary") or "当前证据不足，无法判断适用范围。")[:500]
    reason_codes = [str(value) for value in item.get("reason_codes") or ["runtime_fallback"]]
    if effective_status == "not_yet_effective":
        scope_summary = (
            f"截至 {as_of_date} 尚未施行，将于 {effective_date} 施行。{scope_summary}"
        )[:500]
        reason_codes.append("not_yet_effective_as_of_query_date")
        actions.insert(
            0,
            f"在 {effective_date} 施行前完成适用主体和差距核验；不得表述为 {as_of_date} 已生效义务。",
        )

    return {
        "policy_id": context["policy_id"],
        "policy_title": context["policy_title"],
        "applicability": applicability,
        "binding_effect": str(item.get("binding_effect") or "unknown"),
        "effective_date": effective_date,
        "effective_status": effective_status,
        "as_of_date": as_of_date,
        "confidence": max(0.0, min(1.0, float(item.get("confidence", 0.5)))),
        "scope_summary": scope_summary,
        "trigger_conditions": [str(value)[:180] for value in item.get("trigger_conditions") or [] if str(value).strip()][:6],
        "exclusion_conditions": [str(value)[:180] for value in item.get("exclusion_conditions") or [] if str(value).strip()][:6],
        "matched_company_fact_ids": list(dict.fromkeys(fact_ids))[:8],
        "missing_company_facts": list(dict.fromkeys(missing))[:8],
        "policy_evidence_spans": evidence_spans,
        "reason_codes": list(dict.fromkeys(reason_codes))[:8],
        "recommended_actions": actions[:6],
    }


def run(
    state: PolicyImpactState,
    model_adapter: ModelAdapter | None = None,
) -> PolicyImpactState:
    state.applicability_context = _build_context(state)
    proposals: list[dict[str, Any]] = []
    if model_adapter is not None:
        try:
            proposals = _run_model(state, model_adapter)
        except Exception as exc:  # noqa: BLE001
            state.add_warning("analyze_policy_applicability", "模型适用性分析失败，使用受约束规则回退", str(exc))

    proposal_map = {str(item.get("policy_id") or ""): item for item in proposals}
    state.policy_applicability = [
        _normalize(state, context, proposal_map.get(context["policy_id"], {}))
        for context in state.applicability_context
    ]
    counts: dict[str, int] = {}
    for item in state.policy_applicability:
        key = str(item.get("applicability") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    state.quality_metrics["applicability_counts"] = counts
    state.quality_metrics["verified_policy_quote_count"] = sum(
        len(item.get("policy_evidence_spans") or []) for item in state.policy_applicability
    )
    write_json_artifact(
        state,
        "policy_applicability",
        state.policy_applicability,
        "data/policies/processed",
        "policy_applicability.json",
    )
    state.add_review_bundle(
        {
            "role": "applicability_analyst",
            "output": {"counts": counts, "assessments": state.policy_applicability},
            "stop_reason": "success" if proposals else "deterministic_fallback",
            "created_at": utc_now_iso(),
        }
    )
    return state
