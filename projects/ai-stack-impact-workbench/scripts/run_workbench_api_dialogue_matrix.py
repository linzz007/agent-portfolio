"""Run API-level dialogue coverage for Agent Workbench.

This script intentionally keeps Chinese prompts in a UTF-8 Python file instead
of piping them through PowerShell stdin, because stdin code page conversion can
turn Chinese text into question marks and invalidate routing tests.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from pathlib import Path
from urllib import error, request


BASE_URL = os.getenv("WORKBENCH_BASE_URL", "http://127.0.0.1:8501")
COMPANY_ID = "company_001"
ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "acceptance"


def http(method: str, path: str, payload: dict | None = None) -> dict:
    body = None
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(BASE_URL + path, data=body, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=90) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} HTTP {exc.code}: {raw}") from exc


def create_session(title: str, mode: str, skill_id: str) -> dict:
    return http(
        "POST",
        f"/companies/{COMPANY_ID}/workbench/sessions",
        {
            "title": title,
            "mode": mode,
            "model_id": "deepseek-v4-flash",
            "active_skill_id": skill_id,
        },
    )


def add_check(checks: list[dict], name: str, passed: bool, detail: str = "") -> None:
    checks.append({"name": name, "passed": bool(passed), "detail": detail})


def run_case(case: dict) -> dict:
    started = time.time()
    session = create_session(case["title"], case["mode"], case["active_skill_id"])
    result = http(
        "POST",
        f"/companies/{COMPANY_ID}/workbench/sessions/{session['session_id']}/messages",
        {"message": case["message"]},
    )
    messages = http(
        "GET",
        f"/companies/{COMPANY_ID}/workbench/sessions/{session['session_id']}/messages",
    )["messages"]
    trace = http(
        "GET",
        f"/companies/{COMPANY_ID}/workbench/sessions/{session['session_id']}/trace",
    )

    report_text = ""
    report_path = (result.get("artifacts") or {}).get("report")
    if isinstance(report_path, str) and report_path and Path(report_path).exists():
        report_text = Path(report_path).read_text(encoding="utf-8")

    steps = trace.get("steps") or []
    model_steps = [step for step in steps if step.get("step_type") == "model_call"]
    model_latencies = [
        float((step.get("metadata") or {}).get("latency_ms") or 0)
        for step in model_steps
        if float((step.get("metadata") or {}).get("latency_ms") or 0) > 0
    ]
    model_usage = [
        (step.get("metadata") or {}).get("usage") or {}
        for step in model_steps
    ]
    step_types = [step.get("step_type") for step in steps]
    loop_phases = [(step.get("metadata") or {}).get("loop_phase") for step in steps]
    backend_summary = trace.get("trace_summary") or {}
    answer = result.get("answer") or ""
    checks: list[dict] = []

    add_check(checks, "prompt_not_mojibake", "???" not in case["message"], case["message"])
    add_check(
        checks,
        "selected_skill",
        result.get("selected_skill_id") == case["expected_skill"],
        f"actual={result.get('selected_skill_id')}",
    )
    add_check(
        checks,
        "has_trace_run_id",
        bool(trace.get("run_id")) and trace.get("run_id") == result.get("run_id"),
        f"trace={trace.get('run_id')} result={result.get('run_id')}",
    )
    add_check(
        checks,
        "has_core_steps",
        all(item in step_types for item in ["intent_classification", "skill_selection", "context_build", "final_answer"]),
        ",".join(str(item) for item in step_types),
    )
    add_check(checks, "all_steps_have_loop_phase", all(loop_phases), str(loop_phases))
    add_check(
        checks,
        "backend_trace_summary_v2",
        trace.get("observability_schema_version") == "workbench.trace.v2"
        and backend_summary.get("total_steps") == len(steps)
        and backend_summary.get("model_id") == "deepseek-v4-flash",
        json.dumps(backend_summary, ensure_ascii=False)[:500],
    )
    add_check(
        checks,
        "backend_context_summary",
        bool((trace.get("context_summary") or {}).get("manifest_id")),
        json.dumps(trace.get("context_summary") or {}, ensure_ascii=False)[:300],
    )
    context_step = next((step for step in steps if step.get("step_type") == "context_build"), {})
    context_manifest = (context_step.get("output_payload") or {}).get("context_manifest") or {}
    visible_memories = (context_manifest.get("visible_content") or {}).get("relevant_memories") or []
    memory_step = next((step for step in steps if step.get("step_type") == "memory_read"), {})
    filtered_memory_count = int((memory_step.get("output_payload") or {}).get("memory_count") or 0)
    if case["expected_skill"] in {"general_chat", "research_report", "external_impact_report"}:
        add_check(
            checks,
            "context_contains_only_filtered_memories",
            len(visible_memories) == filtered_memory_count,
            f"manifest={len(visible_memories)} filtered={filtered_memory_count}",
        )
    else:
        add_check(
            checks,
            "context_does_not_expose_unused_memory",
            not visible_memories,
            f"manifest={len(visible_memories)}",
        )
    add_check(checks, "answer_not_empty", len(answer.strip()) >= 8, answer[:120])
    for term in case.get("answer_terms_all", []):
        add_check(checks, f"answer_mentions_{term}", term in answer, answer[:500])
    for term in case.get("forbidden_answer_terms", []):
        add_check(checks, f"answer_excludes_{term}", term not in answer, answer[:500])
    for index, alternatives in enumerate(case.get("answer_term_groups", []), start=1):
        add_check(
            checks,
            f"answer_semantic_group_{index}",
            any(term in answer for term in alternatives),
            f"alternatives={alternatives}; answer={answer[:500]}",
        )
    for term in case.get("report_terms_all", []):
        add_check(checks, f"report_mentions_{term}", term in report_text, report_text[:500])
    add_check(
        checks,
        "messages_saved",
        [item.get("role") for item in messages[-2:]] == ["user", "assistant"],
        str([item.get("role") for item in messages]),
    )
    citations = result.get("citations") or []
    cited_fact_ids = {str(item.get("fact_id") or "") for item in citations if item.get("fact_id")}
    cited_source_kinds = {
        str(item.get("source_kind") or "") for item in citations if item.get("source_kind")
    }
    for fact_id in case.get("required_fact_ids", []):
        add_check(
            checks,
            f"citation_has_{fact_id}",
            fact_id in cited_fact_ids,
            json.dumps(citations, ensure_ascii=False)[:1000],
        )
    for source_kind in case.get("required_source_kinds", []):
        add_check(
            checks,
            f"citation_source_kind_{source_kind}",
            source_kind in cited_source_kinds,
            json.dumps(citations, ensure_ascii=False)[:1000],
        )
    if not case.get("expects_memory_write"):
        add_check(
            checks,
            "no_implicit_memory_write",
            "memory_write" not in step_types,
            ",".join(step_types),
        )

    expected_skill = case["expected_skill"]
    expected_internal_route = case.get("expected_internal_route")
    if expected_internal_route:
        route = str((trace.get("source_summary") or {}).get("route") or "")
        add_check(
            checks,
            f"impact_internal_route_{expected_internal_route}",
            route == expected_internal_route,
            json.dumps(trace.get("source_summary") or {}, ensure_ascii=False),
        )
        if expected_internal_route == "source_inventory":
            source_summary = trace.get("source_summary") or {}
            add_check(checks, "source_inventory_has_tool_call", "tool_call" in step_types, ",".join(step_types))
            add_check(checks, "source_inventory_has_gate", "gate_check" in step_types, ",".join(step_types))
            add_check(checks, "source_inventory_has_artifact", "artifact_write" in step_types, ",".join(step_types))
            add_check(
                checks,
                "source_inventory_counts_visible",
                int(source_summary.get("news_source_count") or 0) > 0
                or int(source_summary.get("policy_source_count") or 0) > 0,
                json.dumps(source_summary, ensure_ascii=False),
            )
    if expected_skill == "general_chat":
        add_check(checks, "general_has_memory_read", "memory_read" in step_types, ",".join(step_types))
        add_check(checks, "general_has_tool_call", "tool_call" in step_types, ",".join(step_types))
        add_check(checks, "general_no_artifact", not (result.get("artifacts") or {}), str(result.get("artifacts")))
        add_check(
            checks,
            "general_no_stale_report_spam",
            "weekly-policy-impact" not in answer and "生成政策影响报告" not in answer,
            answer[:400],
        )
    elif expected_skill == "research_report" or (
        expected_skill == "external_impact_report" and expected_internal_route == "research"
    ):
        add_check(checks, "research_has_model_call", "model_call" in step_types, ",".join(step_types))
        add_check(checks, "research_has_artifact", bool(report_path) and Path(report_path).exists(), str(report_path))
        add_check(checks, "research_no_implicit_memory_write", "memory_write" not in step_types, ",".join(step_types))
        if case.get("expects_workbench_design"):
            add_check(checks, "research_answer_mentions_runtime", "AgentRuntime" in answer or "AgentRuntime" in report_text)
            add_check(checks, "research_report_mentions_skill_registry", "SkillRegistry" in report_text)
            add_check(
                checks,
                "research_no_stale_policy_memory",
                "weekly-policy-impact" not in report_text and "生成政策影响报告" not in report_text,
            )
    elif expected_skill == "company_wiki_blueprint":
        add_check(checks, "wiki_has_tool_call", "tool_call" in step_types, ",".join(step_types))
        add_check(checks, "wiki_has_artifact_write", "artifact_write" in step_types, ",".join(step_types))
        add_check(
            checks,
            "wiki_artifact_persisted",
            bool((result.get("artifacts") or {}).get("wiki_blueprint"))
            and Path((result.get("artifacts") or {}).get("wiki_blueprint")).exists(),
            str(result.get("artifacts"))[:300],
        )
        add_check(checks, "wiki_answers_collection_questions", "下一步采集问题" in answer, answer[:500])
        add_check(checks, "wiki_does_not_truncate_questions", "共 8 个" in answer, answer[:900])
    elif (
        expected_skill in {"recent_news_report", "external_impact_report"}
        and case.get("expects_subject_clarification")
    ):
        gate_decisions = trace.get("gate_summary", {}).get("decisions", {})
        add_check(
            checks,
            "subject_scope_gate_asks",
            int(gate_decisions.get("ask", 0)) >= 1,
            json.dumps(trace.get("gate_summary") or {}, ensure_ascii=False),
        )
        add_check(checks, "subject_scope_has_no_tool_call", "tool_call" not in step_types, ",".join(step_types))
        add_check(checks, "subject_scope_has_no_artifact", not (result.get("artifacts") or {}), str(result.get("artifacts")))
        add_check(
            checks,
            "subject_scope_is_explicit",
            "没有调用新闻工作流" in answer and "项目画像" in answer,
            answer[:700],
        )
    elif expected_skill in {"policy_weekly_impact", "recent_news_report"} or (
        expected_skill == "external_impact_report" and expected_internal_route in {"policy", "news"}
    ):
        add_check(checks, "workflow_has_tool_call", "tool_call" in step_types, ",".join(step_types))
        add_check(checks, "workflow_has_gate_check", "gate_check" in step_types, ",".join(step_types))
        add_check(checks, "workflow_has_artifact_write", "artifact_write" in step_types, ",".join(step_types))
        if case.get("min_tool_call_count") is not None:
            tool_call_count = sum(len(step.get("tool_calls") or []) for step in steps)
            add_check(
                checks,
                "workflow_tool_call_count_min",
                tool_call_count >= int(case["min_tool_call_count"]),
                str(tool_call_count),
            )
        artifacts = result.get("artifacts") or {}
        add_check(
            checks,
            "workflow_has_report_artifact",
            bool(artifacts.get("report") or artifacts.get("html_report") or artifacts.get("run_artifact")),
            str(artifacts),
        )
        expected_artifact_count = len({str(value) for value in artifacts.values() if value})
        add_check(
            checks,
            "workflow_artifact_count_is_unique",
            (trace.get("artifact_summary") or {}).get("total_output_artifacts") == expected_artifact_count
            and (trace.get("artifact_summary") or {}).get("scope") == "user_visible_output_artifacts",
            json.dumps(trace.get("artifact_summary") or {}, ensure_ascii=False)[:500],
        )
        tool_names = {
            str(call.get("tool_name") or "")
            for step in steps
            for call in (step.get("tool_calls") or [])
        }
        add_check(
            checks,
            "workflow_has_no_implicit_memory_save_tool",
            "memory_save" not in tool_names,
            str(sorted(tool_names)),
        )
        if case.get("expects_fixture_disclosure"):
            add_check(
                checks,
                "policy_fixture_disclosed",
                "本地验收样例" in answer and "不可作为现实合规结论" in answer,
                answer[:500],
            )
            add_check(
                checks,
                "policy_report_has_no_fake_official_url",
                "example.gov.cn" not in report_text and "本地样例政策" in report_text,
                report_text[:500],
            )
        if case.get("expects_relevance_refusal"):
            gate_decisions = trace.get("gate_summary", {}).get("decisions", {})
            add_check(
                checks,
                "news_relevance_gate_asks_for_evidence",
                int(gate_decisions.get("ask", 0)) >= 1,
                json.dumps(trace.get("gate_summary") or {}, ensure_ascii=False),
            )
            add_check(
                checks,
                "news_refuses_unsupported_conclusion",
                "拒绝生成推断性结论" in answer and "门控原因" in answer,
                answer[:500],
            )
        if case.get("expects_live_sources"):
            gate_decisions = trace.get("gate_summary", {}).get("decisions", {})
            add_check(
                checks,
                "live_source_gate_allows",
                int(gate_decisions.get("allow", 0)) >= 1,
                json.dumps(trace.get("gate_summary") or {}, ensure_ascii=False),
            )
            add_check(
                checks,
                "live_source_disclosed",
                "一手来源" in answer,
                answer[:600],
            )
            add_check(
                checks,
                "report_contains_primary_urls",
                "https://" in report_text and "本地验收样例" not in report_text,
                report_text[:800],
            )
        if case.get("expects_no_policy_updates"):
            add_check(
                checks,
                "no_update_is_explicit",
                "官方来源抓取成功" in answer and "没有发现新增政策文件" in answer,
                answer[:600],
            )
            add_check(
                checks,
                "no_update_does_not_use_fixture",
                "本地测试样例伪造" in answer and "形成评估项：" not in answer,
                answer[:600],
            )
        if case.get("expects_live_policy"):
            add_check(
                checks,
                "policy_uses_official_sources",
                "https://www.cac.gov.cn/" in report_text
                or "https://www.miit.gov.cn/" in report_text,
                report_text[:1000],
            )
            add_check(
                checks,
                "policy_does_not_use_fixture",
                "本地验收样例" not in answer and "本地样例政策" not in report_text,
                answer[:600],
            )

    return {
        "case_id": case["case_id"],
        "title": case["title"],
        "simulated_command": case["command"],
        "message": case["message"],
        "session_id": session["session_id"],
        "run_id": result.get("run_id"),
        "expected_skill": expected_skill,
        "selected_skill": result.get("selected_skill_id"),
        "answer_preview": answer[:600],
        "answer": answer,
        "artifacts": result.get("artifacts") or {},
        "citations": citations,
        "trace_summary": {
            "step_types": step_types,
            "loop_phases": loop_phases,
            "memory_reads": [step.get("output_payload") for step in steps if step.get("step_type") == "memory_read"],
            "tool_call_count": sum(len(step.get("tool_calls") or []) for step in steps),
            "gate_count": sum(1 for step in steps if step.get("gate_result")),
        },
        "backend_trace_summary": backend_summary,
        "backend_context_summary": trace.get("context_summary") or {},
        "backend_memory_summary": trace.get("memory_summary") or {},
        "checks": checks,
        "passed": all(item["passed"] for item in checks),
        "elapsed_ms": round((time.time() - started) * 1000, 2),
        "performance": {
            "model_call_count": len(model_steps),
            "model_latency_ms": model_latencies,
            "model_latency_total_ms": round(sum(model_latencies), 2),
            "model_usage": model_usage,
        },
        "report_excerpt": report_text[:1500] if report_text else "",
    }


CASES = [
    {
        "case_id": "T01_general_basic",
        "title": "API Test - General Basic",
        "command": "(no slash)",
        "mode": "auto",
        "active_skill_id": "",
        "message": "你好，先用普通对话介绍一下你这个 Agent Workbench 能做什么。",
        "expected_skill": "general_chat",
        "answer_term_groups": [
            ["Agent Workbench"],
            ["Skill", "技能", "/report"],
            ["AgentRun", "执行追踪", "审计"],
            ["AgentStep", "执行追踪", "审计"],
        ],
    },
    {
        "case_id": "T02_chat_command_project_context",
        "title": "API Test - Default Chat Project Context",
        "command": "(no slash)",
        "mode": "auto",
        "active_skill_id": "",
        "message": "请只基于当前知识库和记忆，解释这个项目和 CoursePilot 的区别。",
        "expected_skill": "general_chat",
        "answer_term_groups": [
            ["CoursePilot"],
            ["证据不足", "没有足够", "未找到", "无法"],
        ],
    },
    {
        "case_id": "T03_report_source_inventory",
        "title": "API Test - Report Source Inventory",
        "command": "/report",
        "mode": "auto",
        "active_skill_id": "",
        "message": "/report 当前外部变化分析接入了哪些数据源？请列出来源类型、最近快照和使用边界。",
        "expected_skill": "external_impact_report",
        "expected_internal_route": "source_inventory",
        "answer_terms_all": ["外部变化数据源盘点", "新闻", "政策", "使用边界"],
    },
    {
        "case_id": "T03_impact_workbench_design",
        "title": "API Test - Slash Report Workbench",
        "command": "/report",
        "mode": "auto",
        "active_skill_id": "",
        "message": "/report 请用调研报告形式整理 Agent Workbench 的设计重点",
        "expected_skill": "external_impact_report",
        "expected_internal_route": "research",
        "expects_workbench_design": True,
    },
    {
        "case_id": "T04_impact_professional_harness",
        "title": "API Test - Slash Report Harness",
        "command": "/report",
        "mode": "auto",
        "active_skill_id": "",
        "message": "/report 请用调研报告形式从 Agent Harness 面试角度整理 ContextManifest、ToolGateway、Trace、Eval 的讲法。",
        "expected_skill": "external_impact_report",
        "expected_internal_route": "research",
        "answer_terms_all": ["ContextManifest", "ToolGateway", "Trace", "Eval"],
        "report_terms_all": ["ContextManifest", "ToolGateway", "Trace", "Eval"],
    },
    {
        "case_id": "T05_wiki_default_chat",
        "title": "API Test - Wiki Data Through Default Chat",
        "command": "(no slash)",
        "mode": "auto",
        "active_skill_id": "",
        "message": "请基于企业 Wiki 说明当前有哪些核心事实来源。",
        "expected_skill": "general_chat",
        "answer_term_groups": [["企业事实", "事实"], ["来源", "证据"]],
    },
    {
        "case_id": "T06_impact_policy_workflow",
        "title": "API Test - Slash Report Policy",
        "command": "/report",
        "mode": "auto",
        "active_skill_id": "",
        "message": "/report 请分析近120天政策监管对 AI Agent 平台工具权限、审计留痕和企业合规的影响。",
        "expected_skill": "external_impact_report",
        "expected_internal_route": "policy",
        "answer_terms_all": ["工具权限", "审计留痕", "企业合规", "Agent 平台"],
        "min_tool_call_count": 5,
        "expects_live_policy": True,
    },
    {
        "case_id": "T07_impact_news_workflow",
        "title": "API Test - Slash Report News",
        "command": "/report",
        "mode": "auto",
        "active_skill_id": "",
        "message": "/report 请生成最近新闻对 AI Agent 平台、模型产品和开发者工具方向的影响报告。",
        "expected_skill": "external_impact_report",
        "expected_internal_route": "news",
        "answer_terms_all": ["Agent 平台", "模型产品", "开发者工具"],
        "answer_term_groups": [["实时抓取的一手来源", "一手来源快照"]],
        "min_tool_call_count": 3,
        "expects_live_sources": True,
    },
    {
        "case_id": "T08_auto_news_mixed_tool_policy",
        "title": "API Test - Report News Mixed",
        "command": "/report",
        "mode": "auto",
        "active_skill_id": "",
        "message": "/report 最新新闻里关于 agent harness、tool policy、开发者工具的变化，对我的项目有什么影响？",
        "expected_skill": "external_impact_report",
        "expected_internal_route": "news",
        "answer_terms_all": ["对象范围门控", "项目画像", "示例公司"],
        "expects_subject_clarification": True,
    },
    {
        "case_id": "T09_default_policy_stays_chat",
        "title": "API Test - Default Policy Stays Chat",
        "command": "(no slash)",
        "mode": "auto",
        "active_skill_id": "",
        "message": "政策和监管要求对企业使用 Agent 平台的数据留痕、工具权限和合规有什么影响？",
        "expected_skill": "general_chat",
        "answer_term_groups": [["企业事实", "长期记忆", "证据"]],
    },
    {
        "case_id": "T10_tonghuashun_core_facts",
        "title": "API Test - Tonghuashun Core Facts",
        "command": "(no slash)",
        "mode": "auto",
        "active_skill_id": "",
        "message": (
            "只基于企业知识库回答：示例公司的核心业务是什么，示例产品和 iFinD 分别服务谁？"
            "请区分公开披露事实、分析推断与项目设计观点，并标出 fact_id；证据不足就明确说不足。"
        ),
        "expected_skill": "general_chat",
        "answer_terms_all": ["business.core_product", "ai_product.wencai", "ai_product.ifind"],
        "forbidden_answer_terms": ["iFinD 的具体服务对象未在知识库中明确说明"],
        "required_fact_ids": ["business.core_product", "ai_product.wencai", "ai_product.ifind"],
        "required_source_kinds": ["public_disclosure"],
    },
    {
        "case_id": "T11_tonghuashun_rag_boundary",
        "title": "API Test - Tonghuashun RAG Boundary",
        "command": "(no slash)",
        "mode": "auto",
        "active_skill_id": "",
        "message": (
            "只基于企业知识库回答：能否确认示例产品或 iFinD 已经使用 RAG？"
            "请标出 fact_id，并把已披露事实和仍需核实的内容分开。"
        ),
        "expected_skill": "general_chat",
        "answer_terms_all": ["ai_product.rag_and_knowledge", "仍需", "核实"],
        "answer_term_groups": [["无法确认", "不能确认", "尚无证据", "证据不足"]],
        "forbidden_answer_terms": ["已确认使用 RAG"],
        "required_fact_ids": ["ai_product.rag_and_knowledge"],
        "required_source_kinds": ["public_disclosure"],
    },
    {
        "case_id": "T12_tonghuashun_source_kinds",
        "title": "API Test - Tonghuashun Source Kinds",
        "command": "(no slash)",
        "mode": "auto",
        "active_skill_id": "",
        "message": (
            "只基于企业知识库回答：risk.data_security 与 risk.agent_harness 各自属于什么来源类型？"
            "请分别标注 fact_id、analysis_inference 或 project_design，不能把它们写成公司公开披露。"
        ),
        "expected_skill": "general_chat",
        "answer_terms_all": ["risk.data_security", "risk.agent_harness", "analysis_inference", "project_design"],
        "forbidden_answer_terms": ["均为公司公开披露", "都是公开披露"],
        "required_fact_ids": ["risk.data_security", "risk.agent_harness"],
        "required_source_kinds": ["analysis_inference", "project_design"],
    },
]


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cases",
        default="",
        help="Comma-separated case ids. Empty means all cases.",
    )
    parser.add_argument("--repeat", type=int, default=1, help="Repeat the selected case set.")
    return parser.parse_args()


def main() -> int:
    args = _args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    selected_ids = {item.strip() for item in args.cases.split(",") if item.strip()}
    selected_cases = [case for case in CASES if not selected_ids or case["case_id"] in selected_ids]
    for iteration in range(1, max(1, args.repeat) + 1):
        for case in selected_cases:
            try:
                result = run_case(case)
                result["iteration"] = iteration
                results.append(result)
            except Exception as exc:  # noqa: BLE001
                results.append(
                    {
                        "case_id": case["case_id"],
                        "iteration": iteration,
                        "title": case["title"],
                        "simulated_command": case["command"],
                        "message": case["message"],
                        "expected_skill": case["expected_skill"],
                        "passed": False,
                        "error": repr(exc),
                        "checks": [{"name": "exception", "passed": False, "detail": repr(exc)}],
                    }
                )

    elapsed_values = [float(item.get("elapsed_ms") or 0) for item in results if item.get("elapsed_ms")]
    model_values = [
        value
        for item in results
        for value in ((item.get("performance") or {}).get("model_latency_ms") or [])
    ]
    summary = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "base_url": BASE_URL,
        "company_id": COMPANY_ID,
        "total": len(results),
        "passed": sum(1 for item in results if item.get("passed")),
        "failed": sum(1 for item in results if not item.get("passed")),
        "performance": {
            "e2e_mean_ms": round(statistics.mean(elapsed_values), 2) if elapsed_values else 0,
            "e2e_max_ms": round(max(elapsed_values), 2) if elapsed_values else 0,
            "model_mean_ms": round(statistics.mean(model_values), 2) if model_values else 0,
            "model_max_ms": round(max(model_values), 2) if model_values else 0,
            "model_call_count": len(model_values),
        },
        "results": results,
    }
    out = OUT_DIR / f"workbench_api_dialogue_matrix_{time.strftime('%Y%m%d_%H%M%S')}.json"
    latest = OUT_DIR / "latest_workbench_api_dialogue_matrix.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    latest.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out)
    print(
        json.dumps(
            {
                "total": summary["total"],
                "passed": summary["passed"],
                "failed": summary["failed"],
                "cases": [
                    (item["case_id"], item.get("selected_skill"), item.get("passed"))
                    for item in results
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
