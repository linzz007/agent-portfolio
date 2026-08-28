"""Exercise Workbench as an enterprise user from simple QA to multi-turn workflows."""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "acceptance"
BASE_URL = os.getenv("WORKBENCH_BASE_URL", "http://127.0.0.1:8501")
COMPANY_ID = "company_001"
MODEL_ID = "deepseek-v4-flash"


def http(method: str, path: str, payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        BASE_URL + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with request.urlopen(req, timeout=180) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc


def create_session(title: str, mode: str, skill_id: str) -> dict:
    return http(
        "POST",
        f"/companies/{COMPANY_ID}/workbench/sessions",
        {
            "title": title,
            "mode": mode,
            "model_id": MODEL_ID,
            "active_skill_id": skill_id,
        },
    )


def send(session_id: str, message: str) -> dict:
    started = time.perf_counter()
    result = http(
        "POST",
        f"/companies/{COMPANY_ID}/workbench/sessions/{session_id}/messages",
        {"message": message},
    )
    trace = http(
        "GET",
        f"/companies/{COMPANY_ID}/workbench/sessions/{session_id}/trace",
    )
    return {
        "prompt": message,
        "answer": str(result.get("answer") or ""),
        "selected_skill_id": result.get("selected_skill_id"),
        "run_id": result.get("run_id"),
        "artifacts": result.get("artifacts") or {},
        "citations": result.get("citations") or [],
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "trace": trace,
    }


def check(name: str, passed: bool, detail: str = "") -> dict:
    return {"name": name, "passed": bool(passed), "detail": str(detail)[:800]}


def trace_types(turn: dict) -> list[str]:
    return [str(step.get("step_type") or "") for step in turn["trace"].get("steps") or []]


def no_local_path(text: str) -> bool:
    return re.search(r"[A-Za-z]:\\", text) is None


def _summary_is_substantive(value: str) -> bool:
    text = " ".join(str(value or "").split())
    return len(text) >= 40 and text.lower() not in {
        "what's changed",
        "changes",
        "release notes",
        "来源未提供摘要",
        "官方来源未提供变更摘要",
    }


def _summary_has_complete_tail(value: str) -> bool:
    match = re.search(r"([A-Za-z]+)[^A-Za-z]*$", str(value or "").strip())
    if not match:
        return bool(str(value or "").strip())
    return match.group(1).lower() not in {
        "a", "an", "and", "after", "before", "by", "for", "from", "in", "of",
        "on", "or", "the", "to", "was", "were", "when", "with",
    }


def single_case(
    case_id: str,
    title: str,
    mode: str,
    skill_id: str,
    prompt: str,
    validator,
) -> dict:
    session = create_session(title, mode, skill_id)
    turn = send(session["session_id"], prompt)
    checks = validator(turn)
    checks.extend(
        [
            check("answer_not_empty", len(turn["answer"].strip()) >= 8, turn["answer"][:200]),
            check("answer_hides_local_path", no_local_path(turn["answer"]), turn["answer"][:400]),
            check("trace_completed", turn["trace"].get("status") == "done", turn["trace"].get("status")),
            check("model_is_flash", turn["trace"].get("model_id") == MODEL_ID, turn["trace"].get("model_id")),
        ]
    )
    return {
        "case_id": case_id,
        "session_id": session["session_id"],
        "turns": [turn],
        "checks": checks,
        "passed": all(item["passed"] for item in checks),
    }


def case_model_boundary() -> dict:
    models = http("GET", f"/companies/{COMPANY_ID}/workbench/models").get("models") or []
    rejected = False
    rejection = ""
    try:
        http(
            "POST",
            f"/companies/{COMPANY_ID}/workbench/sessions",
            {"title": "invalid-model", "mode": "auto", "model_id": "nonexistent-model", "active_skill_id": ""},
        )
    except RuntimeError as exc:
        rejection = str(exc)
        rejected = "HTTP 400" in rejection and "模型不存在或不可用" in rejection
    checks = [
        check("only_real_flash_is_listed", [item.get("model_id") for item in models] == [MODEL_ID], models),
        check("flash_auth_is_available", bool(models) and bool((models[0].get("metadata") or {}).get("available")), models),
        check("unknown_model_is_rejected", rejected, rejection),
    ]
    return {"case_id": "B00_model_boundary", "turns": [], "checks": checks, "passed": all(x["passed"] for x in checks)}


def _general_boundary_checks(turn: dict) -> list[dict]:
    answer = turn["answer"]
    return [
        check("routes_to_general_chat", turn["selected_skill_id"] == "general_chat", turn["selected_skill_id"]),
        check("states_evidence_boundary", any(term in answer for term in ("无法确认", "证据不足", "未找到", "不知道")), answer),
        check("does_not_invent_eu_deployment", not any(term in answer for term in ("已经部署", "已在欧盟运营", "确定适用")), answer),
        check("general_trace_has_memory_read", "memory_read" in trace_types(turn), trace_types(turn)),
    ]


def _core_policy_checks(turn: dict) -> list[dict]:
    artifact_path = str(turn["artifacts"].get("run_artifact") or "")
    artifact = json.loads(Path(artifact_path).read_text(encoding="utf-8"))
    actual = {
        str(item.get("policy_title") or ""): str(item.get("applicability") or "")
        for item in artifact.get("policy_applicability") or []
    }
    expected = {
        "智能体规范应用与创新发展实施意见": "not_applicable",
        "网络数据安全风险评估办法": "conditional",
        "人工智能拟人化互动服务管理暂行办法": "not_applicable",
        "工业和信息化部等十部门关于印发《人工智能科技伦理审查与服务办法（试行）》的通知": "insufficient_evidence",
    }
    network = next(
        (item for item in artifact.get("policy_applicability") or [] if item.get("policy_title") == "网络数据安全风险评估办法"),
        {},
    )
    return [
        check("routes_to_policy_skill", turn["selected_skill_id"] == "policy_weekly_impact", turn["selected_skill_id"]),
        check("four_classifications_are_correct", actual == expected, actual),
        check("network_evidence_is_not_keyword_noise", network.get("matched_company_fact_ids") == ["risk.data_security"], network),
        check("forced_relevance_is_zero", (artifact.get("quality_metrics") or {}).get("forced_relevance_count") == 0, artifact.get("quality_metrics")),
        check("official_sources_are_cited", "https://www.cac.gov.cn/" in turn["answer"], turn["answer"]),
        check("guidance_uses_two_axis_semantics", "不产生强制义务" in turn["answer"] and "政策相关性：直接相关（指导性文件，非强制义务）" in turn["answer"] and "直接适用/直接相关" not in turn["answer"], turn["answer"]),
    ]


def _unsupported_policy_checks(turn: dict) -> list[dict]:
    answer = turn["answer"]
    return [
        check("quotes_all_article_55_triggers", all(term in answer for term in ("处理敏感个人信息", "利用个人信息进行自动化决策", "向境外提供个人信息")), answer),
        check("uses_official_npc_source", "https://www.npc.gov.cn/" in answer, answer),
        check("keeps_product_conclusion_conditional", "条件触发，事实不足" in answer and "不能直接认定" in answer, answer),
        check("uses_exact_reference_tool", "tool_call" in trace_types(turn), trace_types(turn)),
    ]


def _unrelated_policy_checks(turn: dict) -> list[dict]:
    answer = turn["answer"]
    return [
        check(
            "states_no_direct_business_impact",
            any(marker in answer for marker in ("没有发现", "未发现")) and "直接影响" in answer,
            answer,
        ),
        check("does_not_force_finance_or_esg_chain", "不构成政策对企业义务或经营流程的直接因果链" in answer, answer),
        check("keeps_source_boundary", "未接入" in answer and "条款级" in answer, answer),
    ]


def case_policy_followup() -> dict:
    session = create_session("验收-政策多轮", "skill", "policy_weekly_impact")
    first = send(
        session["session_id"],
        "网络数据安全风险评估办法对示例公司是否直接适用？只回答适用性、义务主体和缺失事实。",
    )
    second = send(
        session["session_id"],
        "为什么是条件适用？必须补哪两个企业事实才能确定年度评估和报送义务？",
    )
    checks = [
        check("first_turn_is_conditional", "条件适用" in first["answer"], first["answer"]),
        check("first_turn_focuses_named_policy", "拟人化互动" not in first["answer"] and "科技伦理" not in first["answer"], first["answer"]),
        check("followup_inherits_policy_context", "网络数据安全风险评估办法" in second["answer"] and "条件适用" in second["answer"], second["answer"]),
        check("followup_names_important_data_status", "重要数据处理者" in second["answer"] and "重要数据目录" in second["answer"], second["answer"]),
        check("both_turns_use_flash", all(turn["trace"].get("model_id") == MODEL_ID for turn in (first, second))),
        check("both_turns_hide_local_paths", all(no_local_path(turn["answer"]) for turn in (first, second))),
    ]
    return {
        "case_id": "B05_policy_multiturn",
        "session_id": session["session_id"],
        "turns": [first, second],
        "checks": checks,
        "passed": all(item["passed"] for item in checks),
    }


def case_memory_roundtrip() -> dict:
    session = create_session("验收-记忆多轮", "skill", "general_chat")
    first = send(
        session["session_id"],
        "请记住：acceptance_pref_7429 = 政策报告先列明确不适用项，再列条件适用项。",
    )
    second = send(
        session["session_id"],
        "我刚才要求你记住的 acceptance_pref_7429 是什么？只复述该偏好。",
    )
    first_types = trace_types(first)
    second_types = trace_types(second)
    memory_summary = second["trace"].get("memory_summary") or {}
    checks = [
        check("explicit_request_writes_memory", "memory_write" in first_types, first_types),
        check("followup_reads_memory", int(memory_summary.get("read_count") or 0) >= 1 or "memory_read" in second_types, memory_summary),
        check("memory_content_roundtrips", "先列明确不适用项" in second["answer"] and "条件适用项" in second["answer"], second["answer"]),
        check("followup_does_not_write_again", "memory_write" not in second_types, second_types),
    ]
    return {
        "case_id": "B06_memory_roundtrip",
        "session_id": session["session_id"],
        "turns": [first, second],
        "checks": checks,
        "passed": all(item["passed"] for item in checks),
    }


def _news_checks(turn: dict) -> list[dict]:
    answer = turn["answer"]
    has_evidence_list = "本轮可核验的一手新闻证据" in answer and "https://" in answer
    correctly_refused = "拒绝生成推断性结论" in answer and "门控原因" in answer
    return [
        check("routes_to_news_skill", turn["selected_skill_id"] == "recent_news_report", turn["selected_skill_id"]),
        check("news_is_evidence_bound_or_refused", has_evidence_list or correctly_refused, answer),
        check("news_does_not_return_generic_harness_copy", "Harness / Tool Policy 是当前重点" not in answer, answer),
        check("news_excludes_research_noise", "arxiv.org" not in answer and "量子攻击" not in answer, answer),
        check("news_excludes_user_preferences", "用户希望" not in answer, answer),
        check(
            "mainstream_model_query_is_not_replaced_by_cli_news",
            "Claude Code Releases" not in answer and "拒绝生成推断性结论" in answer,
            answer,
        ),
        check("news_trace_has_gate", int((turn["trace"].get("gate_summary") or {}).get("total_gates") or 0) >= 1, turn["trace"].get("gate_summary")),
    ]


def case_company_fact_routing() -> dict:
    session = create_session("验收-企业事实路由", "auto", "")
    first = send(
        session["session_id"],
        "只基于企业知识库，整理示例公司与 iFinD 的产品定位、客户对象和已披露 AI 能力；没有证据的内容不要补。",
    )
    second = send(
        session["session_id"],
        "/chat 这不是知识库规划任务。请只回答上一轮问题，并明确列出使用的 fact_id。",
    )
    citation_ids = {
        str(item.get("fact_id") or "")
        for item in first.get("citations") or []
        if item.get("type") == "company_fact"
    }
    replayed_citation_ids = {
        str(item.get("fact_id") or "")
        for item in second.get("citations") or []
        if item.get("type") == "company_fact"
    }
    checks = [
        check("fact_query_routes_to_general_chat", first["selected_skill_id"] == "general_chat", first["selected_skill_id"]),
        check("ifind_fact_is_retrieved", "ai_product.ifind" in citation_ids, citation_ids),
        check("answer_contains_ifind", "iFinD" in first["answer"] or "ifind" in first["answer"].lower(), first["answer"]),
        check("does_not_generate_wiki_blueprint", "企业知识库规划已生成" not in first["answer"], first["answer"]),
        check("slash_chat_overrides_persisted_routing", second["selected_skill_id"] == "general_chat", second["selected_skill_id"]),
        check("correction_does_not_generate_blueprint", "缺失文件" not in second["answer"], second["answer"]),
        check(
            "fact_ids_are_replayed_from_prior_turn",
            bool(citation_ids)
            and replayed_citation_ids == citation_ids
            and all(fact_id in second["answer"] for fact_id in citation_ids)
            and "不存在" not in second["answer"],
            {
                "first_turn_fact_ids": sorted(citation_ids),
                "replayed_fact_ids": sorted(replayed_citation_ids),
                "answer": second["answer"],
            },
        ),
    ]
    return {
        "case_id": "B08_company_fact_routing",
        "session_id": session["session_id"],
        "turns": [first, second],
        "checks": checks,
        "passed": all(item["passed"] for item in checks),
    }


def case_compliance_word_routing() -> dict:
    return single_case(
        "B09_compliance_word_routing",
        "验收-API评审路由",
        "auto",
        "",
        "请评审 iFinD 异步 API 的超时、重试、幂等和数据合规风险，给出工程改造建议。",
        lambda turn: [
            check("api_review_stays_general_chat", turn["selected_skill_id"] == "general_chat", turn["selected_skill_id"]),
            check("does_not_run_policy_report", "政策影响分析已完成" not in turn["answer"], turn["answer"]),
            check("answers_api_engineering_topic", any(term in turn["answer"] for term in ("超时", "重试", "幂等")), turn["answer"]),
        ],
    )


def case_release_readiness_contract() -> dict:
    return single_case(
        "B15_release_readiness_contract",
        "验收-异步任务上线清单",
        "auto",
        "",
        (
            "请为 iFinD 研报摘要异步任务给出产品、研发、合规三方上线清单。"
            "每项必须包含负责人、验收证据、阻断条件，并分开写已知事实、通用工程建议和待确认边界。"
        ),
        lambda turn: [
            check("release_review_stays_general_chat", turn["selected_skill_id"] == "general_chat", turn["selected_skill_id"]),
            check("does_not_replace_task_with_policy_report", "政策影响分析已完成" not in turn["answer"], turn["answer"]),
            check("covers_three_owner_groups", all(term in turn["answer"] for term in ("产品", "研发", "合规")), turn["answer"]),
            check("covers_acceptance_contract", all(term in turn["answer"] for term in ("负责人", "验收证据", "阻断条件")), turn["answer"]),
            check("separates_known_unknown_and_advice", all(term in turn["answer"] for term in ("已知", "通用", "待确认")), turn["answer"]),
        ],
    )


def case_memory_cross_session() -> dict:
    first_session = create_session("验收-跨会话记忆写入", "auto", "")
    first = send(
        first_session["session_id"],
        "请显式写入长期记忆：blind_pref_9163 = 企业报告先列证据缺口，再列行动建议。只在实际写入后确认。",
    )
    second_session = create_session("验收-跨会话记忆读取", "auto", "")
    second = send(
        second_session["session_id"],
        "我保存的 blind_pref_9163 是什么？只复述该偏好。",
    )
    first_types = trace_types(first)
    checks = [
        check("write_trace_exists", "memory_write" in first_types, first_types),
        check("write_confirmation_is_grounded", any(term in first["answer"] for term in ("已写入长期记忆", "长期记忆中已存在相同内容")), first["answer"]),
        check("new_session_reads_memory", "证据缺口" in second["answer"] and "行动建议" in second["answer"], second["answer"]),
        check("read_does_not_claim_new_write", "已写入长期记忆" not in second["answer"], second["answer"]),
    ]
    return {
        "case_id": "B10_memory_cross_session",
        "session_id": first_session["session_id"],
        "secondary_session_id": second_session["session_id"],
        "turns": [first, second],
        "checks": checks,
        "passed": all(item["passed"] for item in checks),
    }


def case_policy_effective_date() -> dict:
    return single_case(
        "B11_policy_effective_date",
        "验收-政策时间效力",
        "skill",
        "policy_weekly_impact",
        "截至今天 2026-07-16，《网络数据安全风险评估办法》是否已经施行？请区分发布日期与施行日期。",
        lambda turn: [
            check("states_publication_date", "2026-06-18" in turn["answer"], turn["answer"]),
            check("states_not_yet_effective", "尚未施行" in turn["answer"], turn["answer"]),
            check("states_effective_date", "2026-08-20" in turn["answer"], turn["answer"]),
            check("does_not_call_it_current_effective_duty", "已于 2026-08-20 施行" not in turn["answer"], turn["answer"]),
        ],
    )


def case_article_17_exact_reference() -> dict:
    return single_case(
        "B12_article_17_exact_reference",
        "验收-法条原文核验",
        "auto",
        "",
        "有人说《生成式人工智能服务管理暂行办法》第十七条要求所有金融类生成式 AI 产品上线前取得证监会前置许可。请给官方原文、链接和核验结论。",
        lambda turn: [
            check("rejects_false_csrc_claim", "该说法错误" in turn["answer"], turn["answer"]),
            check("quotes_security_assessment", "开展安全评估" in turn["answer"], turn["answer"]),
            check("quotes_full_filing_clause", "算法备案和变更、注销备案手续" in turn["answer"], turn["answer"]),
            check("uses_official_cac_url", "https://www.cac.gov.cn/2023-07/13/" in turn["answer"], turn["answer"]),
            check("trace_uses_legal_reference_tool", "tool_call" in trace_types(turn), trace_types(turn)),
            check("deterministic_legal_path_is_truthfully_labeled", (turn["trace"].get("trace_summary") or {}).get("execution_mode") == "deterministic" and (turn["trace"].get("trace_summary") or {}).get("model_call_count") == 0, turn["trace"].get("trace_summary")),
            check("deterministic_events_do_not_claim_model_request", all("gen_ai.request.model" not in (event.get("attributes") or {}) for event in turn["trace"].get("structured_events") or []), turn["trace"].get("structured_events")),
        ],
    )


def case_strict_news_window() -> dict:
    session = create_session("验收-严格新闻约束", "skill", "recent_news_report")
    turn = send(
        session["session_id"],
        "请汇总过去 30 天（2026-06-16 至 2026-07-16）与金融科技企业研发决策直接相关的 3 条大模型或 Agent 技术新闻。每条必须给事件发生日期、官方一手来源链接、已证实事实、对示例公司产品或研发的具体影响；不要使用未来日期、二手媒体或无法访问的链接。若没有足够的一手证据，请少给并明确缺口。",
    )
    artifact = json.loads(Path(str(turn["artifacts"].get("run_artifact") or "")).read_text(encoding="utf-8"))
    analysis = artifact.get("analysis") or []
    dates = [str((item.get("news_evidence") or {}).get("published_at") or "")[:10] for item in analysis]
    sources = [str((item.get("news_evidence") or {}).get("source_id") or "") for item in analysis]
    model_resume_items = [
        item
        for item in analysis
        if "model override" in str((item.get("news_evidence") or {}).get("summary") or "").lower()
    ]
    session_resume_items = [
        item
        for item in analysis
        if "session resume" in str((item.get("news_evidence") or {}).get("summary") or "").lower()
    ]
    checks = [
        check("news_count_respects_request", 0 < len(analysis) <= 3, len(analysis)),
        check("all_news_within_window", all("2026-06-16" <= value <= "2026-07-16" for value in dates), dates),
        check("research_papers_are_excluded", "arxiv_agent_papers" not in sources, sources),
        check("financial_context_not_false_required_topic", "financial_ai" not in (artifact.get("topic_coverage") or {}), artifact.get("topic_coverage")),
        check("constraint_artifact_is_persisted", (artifact.get("query_constraints") or {}).get("requested_count") == 3, artifact.get("query_constraints")),
        check("news_has_actionable_metrics", all(item.get("verification_metrics") for item in analysis), analysis),
        check("news_summaries_are_substantive", all("未提供变更摘要" not in str((item.get("news_evidence") or {}).get("summary") or "") and len(str((item.get("news_evidence") or {}).get("summary") or "")) >= 40 for item in analysis), analysis),
        check("news_summaries_avoid_known_truncation", all("webhook p" not in str((item.get("news_evidence") or {}).get("summary") or "") for item in analysis), analysis),
        check("news_summaries_have_complete_tails", all(_summary_has_complete_tail(str((item.get("news_evidence") or {}).get("summary") or "")) for item in analysis), analysis),
        check("news_items_name_the_publisher", all(bool((item.get("news_evidence") or {}).get("publisher")) for item in analysis), analysis),
        check(
            "model_resume_impact_is_event_specific_when_present",
            not model_resume_items
            or all("model_id" in " ".join(item.get("impact_chain") or []) for item in model_resume_items),
            model_resume_items,
        ),
        check(
            "session_resume_impact_is_event_specific_when_present",
            not session_resume_items
            or all("后台任务终态一致性" in " ".join(item.get("impact_chain") or []) for item in session_resume_items),
            session_resume_items,
        ),
    ]
    return {
        "case_id": "B13_strict_news_window",
        "session_id": session["session_id"],
        "turns": [turn],
        "checks": checks,
        "passed": all(item["passed"] for item in checks),
    }


def case_named_publisher_whitelist() -> dict:
    session = create_session("验收-新闻发布主体白名单", "skill", "recent_news_report")
    turn = send(
        session["session_id"],
        "请查找 2026-07-01 00:00 至 2026-07-15 23:59（北京时间）发布的 3 条新闻，主题只限 OpenAI、Anthropic、Google DeepMind 的官方产品或研究发布。每条必须来自发布方自己的官网或官方研究博客，给标题、发布主体、精确发布日期、官方原文链接。不要媒体转载；窗口内不足 3 条就明确不足，绝不能用窗口外内容补齐。",
    )
    artifact = json.loads(Path(str(turn["artifacts"].get("run_artifact") or "")).read_text(encoding="utf-8"))
    analysis = artifact.get("analysis") or []
    sources = {
        str((item.get("news_evidence") or {}).get("source_id") or "")
        for item in analysis
    }
    constraints = artifact.get("query_constraints") or {}
    checks = [
        check("routes_to_news_skill", turn["selected_skill_id"] == "recent_news_report", turn["selected_skill_id"]),
        check("publisher_whitelist_is_compiled", constraints.get("publisher_restriction") is True, constraints),
        check("requested_publishers_are_recorded", constraints.get("requested_publishers") == ["OpenAI", "Anthropic", "Google DeepMind"], constraints),
        check("beijing_time_window_is_compiled", constraints.get("query_timezone") == "Asia/Shanghai" and constraints.get("datetime_from") == "2026-07-01T00:00:00+08:00" and constraints.get("datetime_to") == "2026-07-15T23:59:00+08:00", constraints),
        check("owned_web_scope_is_compiled", constraints.get("publisher_owned_web_only") is True and constraints.get("publisher_source_scope") == "publisher_owned_web_or_research_blog", constraints),
        check("incompatible_connected_sources_are_disclosed", constraints.get("unavailable_publishers") == ["OpenAI", "Anthropic", "Google DeepMind"] and "当前未接入满足发布方自有官网或官方研究博客要求的来源：OpenAI、Anthropic、Google DeepMind" in turn["answer"], turn["answer"]),
        check("github_release_channels_are_not_substituted", analysis == [] and "github.com/" not in turn["answer"], {"analysis": analysis, "answer": turn["answer"]}),
        check("does_not_fill_with_huggingface_or_langgraph", not sources.intersection({"huggingface_blog", "langgraph_releases", "mcp_specification_releases"}), sources),
        check("shortage_is_reported_instead_of_fabricated", "系统拒绝生成推断性结论" in turn["answer"] and "没有与用户问题匹配的事件" in turn["answer"], turn["answer"]),
    ]
    return {
        "case_id": "B16_named_publisher_whitelist",
        "session_id": session["session_id"],
        "turns": [turn],
        "checks": checks,
        "passed": all(item["passed"] for item in checks),
    }


def case_policy_product_multiturn() -> dict:
    session = create_session("验收-产品政策多轮", "skill", "policy_weekly_impact")
    first = send(
        session["session_id"],
        "我们计划在示例公司 App 上线面向个人投资者的 AI 投研助手，会根据用户持仓和风险偏好生成个股解读、组合调仓建议，并允许一键跳转到券商交易页面。请判断截至 2026-07-16 最需要关注的中国监管要求；无法确认的事实必须标注。",
    )
    second = send(
        session["session_id"],
        "上一轮哪些只是待核验框架？能否仅凭这些描述直接认定示例公司属于证券投资顾问？",
    )
    third = send(
        session["session_id"],
        "补充事实：产品不能直接下单、不向用户收费、不会输出明确买卖点，只做公开市场信息解读；个性化只根据自选股，不读取持仓和风险偏好。相比第一轮，哪些证据前提变化，哪些结论仍不能下？",
    )
    first_trace_replay = http(
        "GET",
        f"/companies/{COMPANY_ID}/workbench/sessions/{session['session_id']}/runs/{first['run_id']}/trace",
    )
    first_artifact = json.loads(Path(str(first["artifacts"].get("run_artifact") or "")).read_text(encoding="utf-8"))
    third_artifact = json.loads(Path(str(third["artifacts"].get("run_artifact") or "")).read_text(encoding="utf-8"))
    gap_ids = {str(item.get("framework_id") or "") for item in first_artifact.get("regulatory_coverage_gaps") or []}
    third_facts = {str(item.get("fact_id") or ""): str(item.get("value") or "") for item in third_artifact.get("query_company_facts") or []}
    checks = [
        check("baseline_framework_gaps_are_exposed", gap_ids == {"generative_ai", "algorithm_recommendation", "personal_information", "investment_advisory"}, gap_ids),
        check("coverage_is_not_misrepresented_as_applicability", "不是适用性结论" in first["answer"], first["answer"]),
        check("does_not_directly_classify_investment_adviser", "不能仅凭" in second["answer"] and "未评估" in second["answer"], second["answer"]),
        check("later_denials_update_ephemeral_facts", "不读取" in third_facts.get("query.data.holdings", "") and "不读取" in third_facts.get("query.data.risk_preference", ""), third_facts),
        check("facts_are_not_auto_persisted", "不自动写入长期 Memory" in third["answer"], third["answer"]),
        check("no_fabricated_first_round_position", not any(term in third["answer"] for term in ("第一轮已认定", "第一轮高度怀疑", "上一轮已认定")), third["answer"]),
        check("unassessed_frameworks_remain_unassessed", "仍不能下的结论" in third["answer"] and "不能认定或排除" in third["answer"], third["answer"]),
        check("historical_run_trace_is_replayable", first_trace_replay.get("run_id") == first["run_id"] and first_trace_replay.get("status") == "done", first_trace_replay.get("trace_summary")),
    ]
    return {
        "case_id": "B14_policy_product_multiturn",
        "session_id": session["session_id"],
        "turns": [first, second, third],
        "checks": checks,
        "passed": all(item["passed"] for item in checks),
    }


def main() -> int:
    results = [case_model_boundary()]
    results.append(
        single_case(
            "B01_general_evidence_boundary",
            "验收-知识边界",
            "skill",
            "general_chat",
            "只根据当前企业知识库回答：示例公司是否已经在欧盟部署 AI 选股服务？没有证据就明确说无法确认。",
            _general_boundary_checks,
        )
    )
    results.append(case_company_fact_routing())
    results.append(case_compliance_word_routing())
    results.append(case_release_readiness_contract())
    results.append(case_memory_cross_session())
    results.append(case_policy_effective_date())
    results.append(case_article_17_exact_reference())
    results.append(case_strict_news_window())
    results.append(case_named_publisher_whitelist())
    results.append(case_policy_product_multiturn())
    results.append(
        single_case(
            "B02_policy_core",
            "验收-政策四分类",
            "skill",
            "policy_weekly_impact",
            "请分析近4个月官方政策对示例公司的影响。必须区分直接适用、条件适用、明确不适用和证据不足；不要因为出现人工智能关键词就判定相关。",
            _core_policy_checks,
        )
    )
    results.append(
        single_case(
            "B03_uncovered_law",
            "验收-未覆盖法规",
            "skill",
            "policy_weekly_impact",
            "请依据《个人信息保护法》第55条判断示例公司 AI 问答是否必须做个人信息保护影响评估，并逐条引用原文。",
            _unsupported_policy_checks,
        )
    )
    results.append(
        single_case(
            "B04_unrelated_policy",
            "验收-无关政策",
            "skill",
            "policy_weekly_impact",
            "农业农村部的深远海养殖和农机购置补贴政策，对示例公司核心业务有什么直接影响？没有就明确说没有，不要联想概念股或 ESG。",
            _unrelated_policy_checks,
        )
    )
    results.append(case_policy_followup())
    results.append(case_memory_roundtrip())
    results.append(
        single_case(
            "B07_news_evidence",
            "验收-新闻证据",
            "skill",
            "recent_news_report",
            "请分析最近30天主流大模型在推理成本、长上下文和 Agent 工具调用方面的官方更新，对示例产品和 iFinD 有什么可验证影响；找不到可靠新闻就拒绝下结论。",
            _news_checks,
        )
    )
    payload = {
        "schema_version": "business_semantic_acceptance.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": BASE_URL,
        "model_id": MODEL_ID,
        "total_cases": len(results),
        "passed_cases": sum(1 for item in results if item["passed"]),
        "failed_cases": sum(1 for item in results if not item["passed"]),
        "results": results,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = OUT_DIR / f"business_semantic_acceptance_{timestamp}.json"
    latest = OUT_DIR / "latest_business_semantic_acceptance.json"
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    output.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output),
                "total_cases": payload["total_cases"],
                "passed_cases": payload["passed_cases"],
                "failed_cases": payload["failed_cases"],
                "cases": [(item["case_id"], item["passed"]) for item in results],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if payload["failed_cases"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
