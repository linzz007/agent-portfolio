"""Run full capability acceptance coverage for Agent Workbench.

The older dialogue matrix answers "can the workbench respond to these prompts?".
This suite answers a harder question: "does each response leave the harness
evidence promised by the design docs?"
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib import error, request
from uuid import uuid4


BASE_URL = os.getenv("WORKBENCH_BASE_URL", "http://127.0.0.1:8501").rstrip("/")
COMPANY_ID = os.getenv("WORKBENCH_COMPANY_ID", "company_001")
MODEL_ID = os.getenv("WORKBENCH_MODEL_ID", "deepseek-v4-flash")
ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "acceptance"


SLASH_COMMANDS = {
    "/report": "external_impact_report",
}

EXPECTED_SKILL_IDS = {
    "general_chat",
    "external_impact_report",
}


CASES = [
    {
        "case_id": "FC01_model_runtime_ready",
        "title": "模型运行时可用",
        "level": "L1",
        "kind": "model_runtime",
        "capabilities": ["model_runtime"],
    },
    {
        "case_id": "FC02_skill_registry_and_slash",
        "title": "SkillManifest 与斜杠命令契约",
        "level": "L1",
        "kind": "skill_registry",
        "capabilities": ["skill_registry", "slash_commands", "tool_gateway"],
    },
    {
        "case_id": "FC03_session_contract",
        "title": "会话创建、消息持久化、run trace 查询",
        "level": "L1",
        "kind": "session_contract",
        "capabilities": ["session_contract", "general_chat", "context_manifest"],
    },
    {
        "case_id": "FC04_invalid_model_rejected",
        "title": "不存在模型不能执行",
        "level": "L1",
        "kind": "invalid_model",
        "capabilities": ["model_runtime", "permission_boundary"],
    },
    {
        "case_id": "FC05_invalid_skill_rejected",
        "title": "不存在技能不能绑定",
        "level": "L1",
        "kind": "invalid_skill",
        "capabilities": ["skill_registry", "permission_boundary"],
    },
    {
        "case_id": "FC06_general_chat_trace",
        "title": "普通对话保留完整 Loop Trace",
        "level": "L2",
        "kind": "turn",
        "capabilities": [
            "general_chat",
            "context_manifest",
            "memory_read",
            "tool_gateway",
            "gate",
            "stop_hook",
        ],
        "session": {"title": "FullCap - General Chat", "mode": "auto", "active_skill_id": ""},
        "message": "请用普通对话说明 Agent Workbench 的可控、可审计、可复盘、可回归分别体现在哪里。",
        "expected_skill": "general_chat",
        "expect_step_types": ["memory_read", "tool_call", "model_call", "run_stopped"],
        "expect_model_calls_min": 1,
        "expect_tools_min": 1,
        "expect_gates_min": 1,
        "expect_memory_reads_min": 1,
        "expect_artifacts_exact": 0,
        "answer_any": ["可控", "可审计", "可复盘", "可回归", "AgentRun", "AgentStep"],
    },
    {
        "case_id": "FC07_slash_chat_command",
        "title": "默认消息保持普通对话",
        "level": "L2",
        "kind": "turn",
        "capabilities": ["default_chat", "general_chat", "context_manifest"],
        "session": {"title": "FullCap - Default Chat", "mode": "auto", "active_skill_id": ""},
        "message": "帮我整理 Agent Workbench 的设计重点，回答要围绕项目本身，不要转成政策报告。",
        "expected_skill": "general_chat",
        "expect_step_types": ["context_build", "model_call", "run_stopped"],
        "expect_model_calls_min": 1,
        "expect_artifacts_exact": 0,
        "answer_forbidden": ["weekly-policy-impact", "政策影响分析已完成"],
    },
    {
        "case_id": "FC08_explicit_memory_write",
        "title": "明确记住时写入长期 Memory",
        "level": "L2",
        "kind": "memory_write_turn",
        "capabilities": ["memory_write", "general_chat", "context_manifest", "gate"],
        "session": {"title": "FullCap - Memory Write", "mode": "auto", "active_skill_id": ""},
        "message_template": "请记住：{token} 的学习偏好是先看 Trace，再看 ContextManifest，最后看代码。",
        "expected_skill": "general_chat",
        "expect_step_types": ["memory_write", "run_stopped"],
        "expect_memory_writes_min": 1,
        "expect_model_calls_min": 0,
        "answer_any": ["长期记忆", "memory_id"],
    },
    {
        "case_id": "FC09_implicit_memory_boundary",
        "title": "普通表达不应隐式写 Memory",
        "level": "L2",
        "kind": "turn",
        "capabilities": ["memory_write", "permission_boundary", "general_chat"],
        "session": {"title": "FullCap - No Implicit Memory", "mode": "auto", "active_skill_id": ""},
        "message": "我今天觉得 Trace 很重要，但这不是长期记忆指令，只是随口一说。",
        "expected_skill": "general_chat",
        "expect_no_step_types": ["memory_write"],
        "expect_memory_writes_exact": 0,
        "expect_artifacts_exact": 0,
    },
    {
        "case_id": "FC09b_report_source_inventory",
        "title": "/report 触发外部变化数据源盘点",
        "level": "L2",
        "kind": "turn",
        "capabilities": ["external_impact_report", "source_inventory", "tool_gateway", "gate", "artifact"],
        "session": {"title": "FullCap - Source Inventory", "mode": "auto", "active_skill_id": ""},
        "message": "/report 当前外部变化分析接入了哪些数据源？请列出来源类型、最近快照和使用边界。",
        "expected_skill": "external_impact_report",
        "expected_internal_route": "source_inventory",
        "expect_step_types": ["workflow_stage", "tool_call", "gate_check", "artifact_write", "run_stopped"],
        "expect_model_calls_exact": 0,
        "expect_tools_min": 1,
        "expect_artifacts_min": 1,
        "answer_any": ["数据源盘点", "新闻", "政策", "使用边界"],
    },
    {
        "case_id": "FC10_research_artifact",
        "title": "Slash /report 生成调研报告 Artifact",
        "level": "L3",
        "kind": "turn",
        "capabilities": ["slash_commands", "research_artifact", "context_manifest", "gate", "stop_hook"],
        "session": {"title": "FullCap - Research", "mode": "auto", "active_skill_id": ""},
        "message": "/report 请用调研报告形式生成一份 Agent Workbench Harness 设计重点报告，必须覆盖 ContextManifest、ToolGateway、Gate、Eval。",
        "expected_skill": "external_impact_report",
        "expected_internal_route": "research",
        "expect_step_types": ["model_call", "artifact_write", "run_stopped"],
        "expect_model_calls_min": 1,
        "expect_artifacts_min": 1,
        "answer_any": ["ContextManifest", "ToolGateway", "Gate", "Eval"],
        "report_any": ["ContextManifest", "ToolGateway", "Gate", "Eval"],
    },
    {
        "case_id": "FC11_wiki_tree_page",
        "title": "Wiki 数据页读取企业知识库",
        "level": "L3",
        "kind": "wiki_tree",
        "capabilities": ["wiki_tree", "company_data", "persistence"],
    },
    {
        "case_id": "FC12_policy_subagent_workflow",
        "title": "Slash /report 运行政策 Subagent Workflow",
        "level": "L3",
        "kind": "turn",
        "capabilities": ["slash_commands", "policy_workflow", "tool_gateway", "gate", "stop_hook"],
        "session": {"title": "FullCap - Policy", "mode": "auto", "active_skill_id": ""},
        "message": "/report 请分析近120天政策监管对 AI Agent 平台工具权限、审计留痕、企业合规的影响。",
        "expected_skill": "external_impact_report",
        "expected_internal_route": "policy",
        "expect_step_types": ["tool_call", "gate_check", "artifact_write", "run_stopped"],
        "expect_tools_min": 3,
        "expect_gates_min": 1,
        "expect_artifacts_min": 1,
        "answer_any": ["工具权限", "审计", "合规", "Agent"],
    },
    {
        "case_id": "FC13_news_workflow",
        "title": "Slash /report 运行新闻影响工作流",
        "level": "L3",
        "kind": "turn",
        "capabilities": ["slash_commands", "news_workflow", "tool_gateway", "gate", "stop_hook"],
        "session": {"title": "FullCap - News", "mode": "auto", "active_skill_id": ""},
        "message": "/report 请生成最近新闻对 AI Agent 平台、模型产品、开发者工具方向的影响报告。",
        "expected_skill": "external_impact_report",
        "expected_internal_route": "news",
        "expect_step_types": ["tool_call", "gate_check", "artifact_write", "run_stopped"],
        "expect_tools_min": 1,
        "expect_gates_min": 1,
        "expect_artifacts_min": 1,
        "answer_any": ["Agent", "模型", "开发者", "新闻"],
    },
    {
        "case_id": "FC14_subject_scope_gate",
        "title": "新闻请求缺少画像时触发对象范围门控",
        "level": "L3",
        "kind": "turn",
        "capabilities": ["subject_scope_gate", "gate", "news_workflow"],
        "session": {"title": "FullCap - Subject Scope Gate", "mode": "auto", "active_skill_id": ""},
        "message": "/report 最新 Agent Harness 新闻对我的项目有什么影响？",
        "expected_skill": "external_impact_report",
        "expected_internal_route": "news",
        "expect_step_types": ["gate_check", "run_stopped"],
        "expect_no_step_types": ["tool_call", "artifact_write"],
        "expect_gate_decision": "ask",
        "expect_artifacts_exact": 0,
        "answer_any": ["项目画像", "对象范围", "没有调用", "补充"],
    },
    {
        "case_id": "FC15_default_policy_stays_chat",
        "title": "无斜杠政策问题保持普通对话",
        "level": "L3",
        "kind": "turn",
        "capabilities": ["default_chat", "skill_registry", "tool_gateway"],
        "session": {"title": "FullCap - Auto Policy", "mode": "auto", "active_skill_id": ""},
        "message": "政策和监管要求对企业使用 Agent 平台的数据留痕、工具权限和合规有什么影响？",
        "expected_skill": "general_chat",
        "expect_step_types": ["memory_read", "tool_call", "model_call", "run_stopped"],
        "expect_artifacts_exact": 0,
        "expect_tools_min": 1,
        "expect_gates_min": 1,
    },
    {
        "case_id": "FC16_run_trace_lookup",
        "title": "指定 run_id 可回放 Trace",
        "level": "L2",
        "kind": "trace_lookup",
        "capabilities": ["session_contract", "context_manifest", "stop_hook"],
    },
    {
        "case_id": "FC17_multi_turn_memory_and_context",
        "title": "多轮上下文与跨会话 Memory 回忆",
        "level": "L4",
        "kind": "multi_turn",
        "capabilities": ["multi_turn", "memory_write", "memory_read", "context_manifest", "general_chat"],
    },
    {
        "case_id": "FC18_tool_manifest_boundary",
        "title": "工具调用不得越过 SkillManifest 允许清单",
        "level": "L3",
        "kind": "tool_manifest_boundary",
        "capabilities": ["tool_gateway", "permission_boundary", "skill_registry"],
    },
    {
        "case_id": "FC19_context_manifest_budget",
        "title": "ContextManifest 暴露字段和预算可审计",
        "level": "L3",
        "kind": "context_budget",
        "capabilities": ["context_manifest", "memory_read", "general_chat"],
    },
    {
        "case_id": "FC20_frontend_trace",
        "title": "前端 Trace 与布局验收",
        "level": "L4",
        "kind": "frontend_trace",
        "capabilities": ["frontend_trace", "slash_commands", "model_runtime"],
    },
]


class HttpStatusError(RuntimeError):
    def __init__(self, method: str, path: str, status: int, detail: str) -> None:
        super().__init__(f"{method} {path} HTTP {status}: {detail}")
        self.method = method
        self.path = path
        self.status = status
        self.detail = detail


def http_json(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: int = 180,
    expected_status: int = 200,
) -> dict[str, Any]:
    body = None
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(BASE_URL + path, data=body, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw) if raw else {}
            if resp.status != expected_status:
                raise HttpStatusError(method, path, resp.status, raw)
            data["_status"] = resp.status
            return data
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        if exc.code == expected_status:
            try:
                data = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                data = {"detail": raw}
            data["_status"] = exc.code
            return data
        raise HttpStatusError(method, path, exc.code, raw) from exc


def add_check(checks: list[dict[str, Any]], name: str, passed: bool, detail: Any = "") -> None:
    if not isinstance(detail, str):
        detail = json.dumps(detail, ensure_ascii=False, default=str)
    checks.append({"name": name, "passed": bool(passed), "detail": detail[:1000]})


def create_session(title: str, *, mode: str = "auto", active_skill_id: str = "") -> dict[str, Any]:
    return http_json(
        "POST",
        f"/companies/{COMPANY_ID}/workbench/sessions",
        {
            "title": title,
            "mode": mode,
            "model_id": MODEL_ID,
            "active_skill_id": active_skill_id,
        },
    )


def send_message(session_id: str, message: str) -> dict[str, Any]:
    return http_json(
        "POST",
        f"/companies/{COMPANY_ID}/workbench/sessions/{session_id}/messages",
        {"message": message},
        timeout=240,
    )


def list_messages(session_id: str) -> list[dict[str, Any]]:
    return http_json(
        "GET",
        f"/companies/{COMPANY_ID}/workbench/sessions/{session_id}/messages",
    ).get("messages") or []


def latest_trace(session_id: str) -> dict[str, Any]:
    return http_json(
        "GET",
        f"/companies/{COMPANY_ID}/workbench/sessions/{session_id}/trace",
    )


def trace_for_run(session_id: str, run_id: str) -> dict[str, Any]:
    return http_json(
        "GET",
        f"/companies/{COMPANY_ID}/workbench/sessions/{session_id}/runs/{run_id}/trace",
    )


def step_types(trace: dict[str, Any]) -> list[str]:
    return [str(step.get("step_type") or "") for step in trace.get("steps") or []]


def tool_names(trace: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for step in trace.get("steps") or []:
        for call in step.get("tool_calls") or []:
            name = str(call.get("tool_name") or call.get("name") or "")
            if name:
                names.add(name)
    return names


def output_artifact_paths(result: dict[str, Any], trace: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    artifacts = result.get("artifacts") or {}
    if isinstance(artifacts, dict):
        paths.extend(str(value) for value in artifacts.values() if value)
    for item in (trace.get("artifact_summary") or {}).get("artifacts") or []:
        path = str(item.get("path") or "")
        if path:
            paths.append(path)
    unique: list[str] = []
    seen: set[str] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


def read_artifact_excerpt(paths: list[str], limit: int = 1600) -> str:
    excerpts: list[str] = []
    for path in paths[:3]:
        candidate = Path(path)
        if candidate.exists() and candidate.is_file():
            excerpts.append(candidate.read_text(encoding="utf-8", errors="replace")[:limit])
    return "\n\n".join(excerpts)


def check_common_turn(
    checks: list[dict[str, Any]],
    *,
    case: dict[str, Any],
    session: dict[str, Any],
    result: dict[str, Any],
    trace: dict[str, Any],
    messages: list[dict[str, Any]],
) -> None:
    types = step_types(trace)
    trace_summary = trace.get("trace_summary") or {}
    context_summary = trace.get("context_summary") or {}
    memory_summary = trace.get("memory_summary") or {}
    model_summary = trace.get("model_summary") or {}
    gate_summary = trace.get("gate_summary") or {}
    artifact_paths = output_artifact_paths(result, trace)
    answer = str(result.get("answer") or "")
    artifact_excerpt = read_artifact_excerpt(artifact_paths)

    add_check(checks, "request_text_not_corrupted", "?" not in str(case.get("message", "")))
    add_check(checks, "session_created", bool(session.get("session_id")), session)
    add_check(
        checks,
        "selected_expected_skill",
        result.get("selected_skill_id") == case.get("expected_skill"),
        {"actual": result.get("selected_skill_id"), "expected": case.get("expected_skill")},
    )
    if case.get("expected_internal_route"):
        source_summary = trace.get("source_summary") or {}
        add_check(
            checks,
            f"internal_route_{case['expected_internal_route']}",
            source_summary.get("route") == case["expected_internal_route"],
            source_summary,
        )
    add_check(
        checks,
        "run_trace_matches_response",
        bool(result.get("run_id")) and trace.get("run_id") == result.get("run_id"),
        {"response": result.get("run_id"), "trace": trace.get("run_id")},
    )
    add_check(
        checks,
        "trace_schema_v2",
        trace.get("observability_schema_version") == "workbench.trace.v2",
        trace.get("observability_schema_version"),
    )
    add_check(
        checks,
        "core_loop_steps_present",
        all(name in types for name in ["intent_classification", "skill_selection", "context_build", "final_answer", "run_stopped"]),
        types,
    )
    add_check(
        checks,
        "loop_phase_recorded",
        all(bool((step.get("metadata") or {}).get("loop_phase")) for step in trace.get("steps") or []),
        [(step.get("step_type"), (step.get("metadata") or {}).get("loop_phase")) for step in trace.get("steps") or []],
    )
    add_check(
        checks,
        "context_manifest_present",
        bool(context_summary.get("manifest_id")) and bool(context_summary.get("visible_keys")),
        context_summary,
    )
    add_check(
        checks,
        "trace_summary_consistent",
        int(trace_summary.get("total_steps") or 0) == len(trace.get("steps") or [])
        and int(trace_summary.get("completed_steps") or 0) + int(trace_summary.get("failed_steps") or 0)
        == len(trace.get("steps") or [])
        and str(trace_summary.get("status") or "") in {"done", "completed"}
        and trace_summary.get("selected_model_id") == MODEL_ID,
        trace_summary,
    )
    add_check(
        checks,
        "structured_events_cover_steps",
        len(trace.get("structured_events") or []) == len(trace.get("steps") or []),
        {"events": len(trace.get("structured_events") or []), "steps": len(trace.get("steps") or [])},
    )
    add_check(
        checks,
        "messages_persisted",
        len(messages) >= 2 and [item.get("role") for item in messages[-2:]] == ["user", "assistant"],
        [item.get("role") for item in messages],
    )
    add_check(checks, "answer_not_empty", len(answer.strip()) >= 8, answer[:300])
    add_check(checks, "answer_has_no_replacement_char", "\ufffd" not in answer, answer[:300])

    for expected in case.get("expect_step_types", []):
        add_check(checks, f"step_has_{expected}", expected in types, types)
    for forbidden in case.get("expect_no_step_types", []):
        add_check(checks, f"step_excludes_{forbidden}", forbidden not in types, types)

    if "expect_model_calls_min" in case:
        add_check(
            checks,
            "model_call_count_min",
            int(model_summary.get("call_count") or 0) >= int(case["expect_model_calls_min"]),
            model_summary,
        )
    if "expect_model_calls_exact" in case:
        add_check(
            checks,
            "model_call_count_exact",
            int(model_summary.get("call_count") or 0) == int(case["expect_model_calls_exact"]),
            model_summary,
        )
    if int(model_summary.get("call_count") or 0) > 0:
        add_check(
            checks,
            "model_runtime_uses_flash",
            MODEL_ID in (model_summary.get("model_ids") or []),
            model_summary,
        )
        add_check(
            checks,
            "prompt_contract_available",
            bool(model_summary.get("prompt_contract_ids")) and model_summary.get("system_prompt_visible") is True,
            model_summary,
        )

    if "expect_tools_min" in case:
        add_check(
            checks,
            "tool_call_count_min",
            int((trace.get("tool_summary") or {}).get("total_calls") or 0) >= int(case["expect_tools_min"]),
            trace.get("tool_summary") or {},
        )
    if "expect_gates_min" in case:
        add_check(
            checks,
            "gate_count_min",
            int(gate_summary.get("total_gates") or 0) >= int(case["expect_gates_min"]),
            gate_summary,
        )
    if "expect_gate_decision" in case:
        decisions = gate_summary.get("decisions") or {}
        add_check(
            checks,
            f"gate_decision_{case['expect_gate_decision']}",
            int(decisions.get(case["expect_gate_decision"], 0)) >= 1,
            gate_summary,
        )
    if "expect_memory_reads_min" in case:
        add_check(
            checks,
            "memory_read_count_min",
            int(memory_summary.get("read_steps") or 0) >= int(case["expect_memory_reads_min"]),
            memory_summary,
        )
    if "expect_memory_writes_min" in case:
        add_check(
            checks,
            "memory_write_count_min",
            int(memory_summary.get("actual_write_count") or 0) >= int(case["expect_memory_writes_min"]),
            memory_summary,
        )
    if "expect_memory_writes_exact" in case:
        add_check(
            checks,
            "memory_write_count_exact",
            int(memory_summary.get("actual_write_count") or 0) == int(case["expect_memory_writes_exact"]),
            memory_summary,
        )
    if "expect_artifacts_min" in case:
        add_check(checks, "artifact_count_min", len(artifact_paths) >= int(case["expect_artifacts_min"]), artifact_paths)
        add_check(
            checks,
            "artifact_files_exist",
            all(Path(path).exists() for path in artifact_paths),
            artifact_paths,
        )
    if "expect_artifacts_exact" in case:
        add_check(checks, "artifact_count_exact", len(artifact_paths) == int(case["expect_artifacts_exact"]), artifact_paths)

    for term in case.get("answer_any", []):
        add_check(
            checks,
            f"answer_mentions_{term}",
            term.lower() in answer.lower() or term.lower() in artifact_excerpt.lower(),
            (answer + "\n" + artifact_excerpt)[:800],
        )
    for term in case.get("answer_forbidden", []):
        add_check(
            checks,
            f"answer_excludes_{term}",
            term.lower() not in answer.lower(),
            answer[:800],
        )
    for term in case.get("report_any", []):
        add_check(checks, f"artifact_mentions_{term}", term.lower() in artifact_excerpt.lower(), artifact_excerpt[:800])


def summarize_turn_result(
    case: dict[str, Any],
    session: dict[str, Any],
    result: dict[str, Any],
    trace: dict[str, Any],
    messages: list[dict[str, Any]],
    checks: list[dict[str, Any]],
    elapsed_ms: float,
) -> dict[str, Any]:
    artifact_paths = output_artifact_paths(result, trace)
    return {
        "case_id": case["case_id"],
        "title": case["title"],
        "level": case["level"],
        "capabilities": case["capabilities"],
        "session_id": session.get("session_id"),
        "run_id": result.get("run_id"),
        "selected_skill": result.get("selected_skill_id"),
        "answer_preview": str(result.get("answer") or "")[:700],
        "artifact_paths": artifact_paths,
        "trace_summary": trace.get("trace_summary") or {},
        "context_summary": trace.get("context_summary") or {},
        "memory_summary": trace.get("memory_summary") or {},
        "model_summary": trace.get("model_summary") or {},
        "tool_summary": trace.get("tool_summary") or {},
        "gate_summary": trace.get("gate_summary") or {},
        "source_summary": trace.get("source_summary") or {},
        "step_types": step_types(trace),
        "checks": checks,
        "passed": all(item["passed"] for item in checks),
        "elapsed_ms": round(elapsed_ms, 2),
        "messages_count": len(messages),
    }


def run_turn_case(case: dict[str, Any]) -> dict[str, Any]:
    started = time.time()
    checks: list[dict[str, Any]] = []
    session_config = case.get("session") or {}
    session = create_session(
        session_config.get("title") or case["title"],
        mode=session_config.get("mode") or "auto",
        active_skill_id=session_config.get("active_skill_id") or "",
    )
    message = str(case.get("message") or "")
    if case.get("message_template"):
        token = f"FULL-CAP-{uuid4().hex[:8].upper()}"
        message = str(case["message_template"]).format(token=token)
        case = dict(case, message=message, generated_token=token)
    result = send_message(session["session_id"], message)
    trace = trace_for_run(session["session_id"], result["run_id"])
    messages = list_messages(session["session_id"])
    check_common_turn(
        checks,
        case=case,
        session=session,
        result=result,
        trace=trace,
        messages=messages,
    )
    return summarize_turn_result(
        case,
        session,
        result,
        trace,
        messages,
        checks,
        (time.time() - started) * 1000,
    )


def run_model_runtime(case: dict[str, Any]) -> dict[str, Any]:
    started = time.time()
    checks: list[dict[str, Any]] = []
    payload = http_json("GET", f"/companies/{COMPANY_ID}/workbench/models")
    models = payload.get("models") or []
    model = models[0] if models else {}
    metadata = model.get("metadata") or {}
    add_check(checks, "only_one_model_exposed", len(models) == 1, models)
    add_check(checks, "model_name_is_real_id", model.get("model_id") == MODEL_ID and model.get("model_name") == MODEL_ID, model)
    add_check(checks, "model_available", metadata.get("available") is True, metadata)
    add_check(checks, "model_auth_configured", metadata.get("auth_configured") is True, metadata)
    add_check(checks, "missing_env_empty", not metadata.get("missing"), metadata)
    return base_case_result(case, checks, (time.time() - started) * 1000, extra={"models": models})


def run_skill_registry(case: dict[str, Any]) -> dict[str, Any]:
    started = time.time()
    checks: list[dict[str, Any]] = []
    payload = http_json("GET", f"/companies/{COMPANY_ID}/workbench/skills")
    skills = payload.get("skills") or []
    ids = {str(skill.get("id") or "") for skill in skills}
    add_check(checks, "skill_ids_complete", ids == EXPECTED_SKILL_IDS, sorted(ids))
    add_check(checks, "slash_commands_complete", set(SLASH_COMMANDS) == {"/report"})
    for skill in skills:
        add_check(
            checks,
            f"skill_{skill.get('id')}_has_contract",
            bool(skill.get("mode")) and isinstance(skill.get("tool_policy"), dict) and isinstance(skill.get("subagents"), list),
            skill,
        )
    impact = next((skill for skill in skills if skill.get("id") == "external_impact_report"), {})
    add_check(checks, "impact_skill_has_skeptic_subagent", "skeptic" in (impact.get("subagents") or []), impact)
    add_check(
        checks,
        "impact_skill_has_research_subagent",
        "research_report_subagent" in (impact.get("subagents") or []),
        impact,
    )
    return base_case_result(case, checks, (time.time() - started) * 1000, extra={"skills": skills})


def run_wiki_tree(case: dict[str, Any]) -> dict[str, Any]:
    started = time.time()
    checks: list[dict[str, Any]] = []
    payload = http_json("GET", f"/companies/{COMPANY_ID}/workbench/wiki")
    pages = payload.get("pages") or []
    facts = payload.get("facts") or []
    stats = payload.get("stats") or {}
    add_check(checks, "wiki_schema_version", payload.get("schema_version") == "workbench.wiki_tree.v1", payload)
    add_check(checks, "wiki_pages_present", len(pages) >= 1, {"page_count": len(pages)})
    add_check(checks, "wiki_facts_present", len(facts) >= 1, {"fact_count": len(facts)})
    add_check(checks, "wiki_stats_match_payload", int(stats.get("page_count") or 0) == len(pages), stats)
    add_check(
        checks,
        "wiki_page_has_hierarchical_parts",
        all(isinstance(page.get("parts"), list) and page.get("parts") for page in pages),
        pages[:3],
    )
    return base_case_result(
        case,
        checks,
        (time.time() - started) * 1000,
        extra={"page_count": len(pages), "fact_count": len(facts), "stats": stats},
    )


def run_session_contract(case: dict[str, Any]) -> dict[str, Any]:
    turn_case = dict(
        case,
        kind="turn",
        session={"title": "FullCap - Session Contract", "mode": "auto", "active_skill_id": ""},
        message="请回答：一次 AgentRun 和 AgentStep 的关系是什么？",
        expected_skill="general_chat",
        expect_step_types=["model_call", "run_stopped"],
        expect_model_calls_min=1,
        expect_artifacts_exact=0,
    )
    result = run_turn_case(turn_case)
    checks = list(result["checks"])
    sessions = http_json("GET", f"/companies/{COMPANY_ID}/workbench/sessions").get("sessions") or []
    add_check(
        checks,
        "session_list_contains_created_session",
        any(item.get("session_id") == result.get("session_id") for item in sessions),
        {"session_id": result.get("session_id"), "count": len(sessions)},
    )
    run_trace = trace_for_run(str(result["session_id"]), str(result["run_id"]))
    add_check(checks, "trace_lookup_by_run_id_works", run_trace.get("run_id") == result.get("run_id"), run_trace.get("run_id"))
    result["checks"] = checks
    result["passed"] = all(item["passed"] for item in checks)
    return result


def run_invalid_model(case: dict[str, Any]) -> dict[str, Any]:
    started = time.time()
    checks: list[dict[str, Any]] = []
    bad_session = http_json(
        "POST",
        f"/companies/{COMPANY_ID}/workbench/sessions",
        {
            "title": "FullCap - Invalid Model",
            "mode": "auto",
            "model_id": "fake-model",
            "active_skill_id": "",
        },
        expected_status=400,
    )
    add_check(checks, "invalid_model_create_rejected", bad_session.get("_status") == 400, bad_session)
    session = create_session("FullCap - Invalid Model Update")
    bad_update = http_json(
        "POST",
        f"/companies/{COMPANY_ID}/workbench/sessions/{session['session_id']}/settings",
        {"model_id": "fake-model"},
        expected_status=400,
    )
    add_check(checks, "invalid_model_update_rejected", bad_update.get("_status") == 400, bad_update)
    return base_case_result(case, checks, (time.time() - started) * 1000)


def run_invalid_skill(case: dict[str, Any]) -> dict[str, Any]:
    started = time.time()
    checks: list[dict[str, Any]] = []
    bad_session = http_json(
        "POST",
        f"/companies/{COMPANY_ID}/workbench/sessions",
        {
            "title": "FullCap - Invalid Skill",
            "mode": "auto",
            "model_id": MODEL_ID,
            "active_skill_id": "missing_skill",
        },
        expected_status=400,
    )
    add_check(checks, "invalid_skill_create_rejected", bad_session.get("_status") == 400, bad_session)
    session = create_session("FullCap - Invalid Skill Update")
    bad_update = http_json(
        "POST",
        f"/companies/{COMPANY_ID}/workbench/sessions/{session['session_id']}/settings",
        {"active_skill_id": "missing_skill"},
        expected_status=404,
    )
    add_check(checks, "invalid_skill_update_rejected", bad_update.get("_status") == 404, bad_update)
    return base_case_result(case, checks, (time.time() - started) * 1000)


def run_trace_lookup(case: dict[str, Any]) -> dict[str, Any]:
    turn_case = dict(
        case,
        kind="turn",
        session={"title": "FullCap - Trace Lookup", "mode": "auto", "active_skill_id": ""},
        message="请解释 trace 为什么能帮助复盘一个坏案例。",
        expected_skill="general_chat",
        expect_step_types=["model_call", "run_stopped"],
        expect_model_calls_min=1,
        expect_artifacts_exact=0,
    )
    result = run_turn_case(turn_case)
    checks = list(result["checks"])
    run_trace = trace_for_run(str(result["session_id"]), str(result["run_id"]))
    latest = latest_trace(str(result["session_id"]))
    add_check(checks, "latest_trace_matches_run_trace", latest.get("run_id") == run_trace.get("run_id"), {"latest": latest.get("run_id"), "run": run_trace.get("run_id")})
    add_check(
        checks,
        "run_trace_has_replay_evidence",
        bool(run_trace.get("steps")) and bool(run_trace.get("context_summary")) and bool(run_trace.get("structured_events")),
        {"steps": len(run_trace.get("steps") or []), "context": run_trace.get("context_summary"), "events": len(run_trace.get("structured_events") or [])},
    )
    result["checks"] = checks
    result["passed"] = all(item["passed"] for item in checks)
    return result


def run_multi_turn(case: dict[str, Any]) -> dict[str, Any]:
    started = time.time()
    checks: list[dict[str, Any]] = []
    token = f"FULL-CAP-MULTI-{uuid4().hex[:8].upper()}"
    writer = create_session("FullCap - Multi Memory Writer", mode="auto", active_skill_id="")
    write_message = f"请记住：{token} 的面试学习顺序是先看 AgentRun，再看 AgentStep，再看 Trace。"
    write_result = send_message(writer["session_id"], write_message)
    write_trace = trace_for_run(writer["session_id"], write_result["run_id"])
    add_check(
        checks,
        "turn1_memory_written",
        int((write_trace.get("memory_summary") or {}).get("actual_write_count") or 0) >= 1,
        write_trace.get("memory_summary") or {},
    )

    reader = create_session("FullCap - Multi Memory Reader", mode="auto", active_skill_id="")
    read_message = f"我刚才让你记住的 {token} 学习顺序是什么？请基于长期记忆回答。"
    read_result = send_message(reader["session_id"], read_message)
    read_trace = trace_for_run(reader["session_id"], read_result["run_id"])
    answer = str(read_result.get("answer") or "")
    add_check(
        checks,
        "turn2_reads_memory",
        int((read_trace.get("memory_summary") or {}).get("read_steps") or 0) >= 1,
        read_trace.get("memory_summary") or {},
    )
    add_check(
        checks,
        "turn2_recalls_token_or_sequence",
        token in answer or all(term in answer for term in ["AgentRun", "AgentStep", "Trace"]),
        answer[:800],
    )

    same_session = create_session("FullCap - Multi Same Session", mode="auto", active_skill_id="")
    first_result = send_message(
        same_session["session_id"],
        "本轮会话里先记一个临时上下文：我要重点学习 ContextManifest、ToolGateway、Gate，但不要写入长期记忆。",
    )
    second_result = send_message(
        same_session["session_id"],
        "刚才这轮会话里的三个临时重点是什么？请只基于本会话上下文回答。",
    )
    second_trace = trace_for_run(same_session["session_id"], second_result["run_id"])
    second_answer = str(second_result.get("answer") or "")
    add_check(
        checks,
        "same_session_context_resolved",
        all(term in second_answer for term in ["ContextManifest", "ToolGateway", "Gate"]),
        second_answer[:800],
    )
    add_check(
        checks,
        "same_session_context_manifest_contains_history",
        int((second_trace.get("context_summary") or {}).get("history_messages_included") or 0) >= 1,
        second_trace.get("context_summary") or {},
    )
    messages = list_messages(same_session["session_id"])
    add_check(checks, "same_session_has_four_messages", len(messages) >= 4, [item.get("role") for item in messages])
    result = {
        "case_id": case["case_id"],
        "title": case["title"],
        "level": case["level"],
        "capabilities": case["capabilities"],
        "session_id": same_session["session_id"],
        "run_id": second_result.get("run_id"),
        "selected_skill": second_result.get("selected_skill_id"),
        "answer_preview": second_answer[:700],
        "trace_summary": second_trace.get("trace_summary") or {},
        "context_summary": second_trace.get("context_summary") or {},
        "memory_summary": {
            "write_turn": write_trace.get("memory_summary") or {},
            "read_turn": read_trace.get("memory_summary") or {},
            "same_session_turn": second_trace.get("memory_summary") or {},
        },
        "model_summary": {
            "write_turn": write_trace.get("model_summary") or {},
            "read_turn": read_trace.get("model_summary") or {},
            "same_session_turn": second_trace.get("model_summary") or {},
        },
        "step_types": step_types(write_trace) + step_types(read_trace) + step_types(second_trace),
        "checks": checks,
        "passed": all(item["passed"] for item in checks),
        "elapsed_ms": round((time.time() - started) * 1000, 2),
    }
    result["model_call_count"] = sum(
        int((trace.get("model_summary") or {}).get("call_count") or 0)
        for trace in [write_trace, read_trace, second_trace]
    )
    return result


def run_tool_manifest_boundary(case: dict[str, Any]) -> dict[str, Any]:
    turn_case = dict(
        case,
        kind="turn",
        session={"title": "FullCap - Tool Boundary", "mode": "auto", "active_skill_id": ""},
        message="/report 请分析企业 Agent 工具权限和审计留痕的合规影响。",
        expected_skill="external_impact_report",
        expect_step_types=["tool_call", "gate_check", "run_stopped"],
        expect_tools_min=3,
        expect_gates_min=1,
        expect_artifacts_min=1,
    )
    result = run_turn_case(turn_case)
    checks = list(result["checks"])
    skills = http_json("GET", f"/companies/{COMPANY_ID}/workbench/skills").get("skills") or []
    selected = next((skill for skill in skills if skill.get("id") == result.get("selected_skill")), {})
    allowed: set[str] = set()
    for tools in (selected.get("tool_policy") or {}).values():
        allowed.update(str(tool) for tool in tools)
    run_trace = trace_for_run(str(result["session_id"]), str(result["run_id"]))
    actual = tool_names(run_trace)
    add_check(
        checks,
        "tool_calls_subset_of_manifest",
        actual <= allowed,
        {"actual": sorted(actual), "allowed": sorted(allowed), "skill": selected.get("id")},
    )
    add_check(
        checks,
        "no_denied_tool_calls_in_success_path",
        int((run_trace.get("tool_summary") or {}).get("denied_calls") or 0) == 0,
        run_trace.get("tool_summary") or {},
    )
    result["checks"] = checks
    result["tool_names"] = sorted(actual)
    result["allowed_tools"] = sorted(allowed)
    result["passed"] = all(item["passed"] for item in checks)
    return result


def run_context_budget(case: dict[str, Any]) -> dict[str, Any]:
    turn_case = dict(
        case,
        kind="turn",
        session={"title": "FullCap - Context Budget", "mode": "auto", "active_skill_id": ""},
        message="请基于当前知识库和记忆，解释这个 Workbench 为什么不是普通工作流，并说明上下文如何被裁剪。",
        expected_skill="general_chat",
        expect_step_types=["memory_read", "tool_call", "model_call", "run_stopped"],
        expect_model_calls_min=1,
        expect_memory_reads_min=1,
        expect_artifacts_exact=0,
    )
    result = run_turn_case(turn_case)
    checks = list(result["checks"])
    context = result.get("context_summary") or {}
    add_check(checks, "context_has_budget_numbers", int(context.get("token_budget_total") or 0) > 0 and int(context.get("token_estimate_total") or 0) > 0, context)
    add_check(checks, "context_budget_not_overflow", float(context.get("budget_utilization") or 0) <= 1.0, context)
    add_check(checks, "context_hidden_fields_recorded", int(context.get("hidden_field_count") or 0) >= 1, context)
    result["checks"] = checks
    result["passed"] = all(item["passed"] for item in checks)
    return result


def run_frontend_trace(case: dict[str, Any]) -> dict[str, Any]:
    started = time.time()
    checks: list[dict[str, Any]] = []
    proc = subprocess.run(
        ["node", "scripts\\run_workbench_ui_cdp_check.mjs"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=90,
    )
    parsed: dict[str, Any] = {}
    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError:
        parsed = {"stdout": proc.stdout, "stderr": proc.stderr}
    add_check(checks, "ui_script_exit_zero", proc.returncode == 0, {"returncode": proc.returncode, "stderr": proc.stderr[-1000:]})
    add_check(checks, "ui_script_passed", parsed.get("passed") is True, parsed)
    latest = OUT_DIR / "latest_workbench_ui_cdp_check.json"
    if latest.exists():
        latest_payload = json.loads(latest.read_text(encoding="utf-8"))
        add_check(checks, "ui_trace_visible", any(item.get("name") == "has_trace" and item.get("passed") for item in latest_payload.get("checks") or []), latest_payload.get("checks") or [])
        add_check(checks, "ui_model_picker_is_flash", (latest_payload.get("inspection") or {}).get("modelPickerValue") == MODEL_ID, latest_payload.get("inspection") or {})
        add_check(checks, "ui_screenshot_exists", bool(latest_payload.get("screenshot_path")) and Path(latest_payload["screenshot_path"]).exists(), latest_payload.get("screenshot_path"))
        extra = {"ui": latest_payload}
    else:
        add_check(checks, "ui_latest_report_written", False, str(latest))
        extra = {"ui": parsed}
    return base_case_result(case, checks, (time.time() - started) * 1000, extra=extra)


def base_case_result(
    case: dict[str, Any],
    checks: list[dict[str, Any]],
    elapsed_ms: float,
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "case_id": case["case_id"],
        "title": case["title"],
        "level": case["level"],
        "capabilities": case["capabilities"],
        "checks": checks,
        "passed": all(item["passed"] for item in checks),
        "elapsed_ms": round(elapsed_ms, 2),
    }
    if extra:
        result.update(extra)
    return result


def run_case(case: dict[str, Any]) -> dict[str, Any]:
    kind = case["kind"]
    if kind == "model_runtime":
        return run_model_runtime(case)
    if kind == "skill_registry":
        return run_skill_registry(case)
    if kind == "wiki_tree":
        return run_wiki_tree(case)
    if kind == "session_contract":
        return run_session_contract(case)
    if kind == "invalid_model":
        return run_invalid_model(case)
    if kind == "invalid_skill":
        return run_invalid_skill(case)
    if kind == "turn" or kind == "memory_write_turn":
        return run_turn_case(case)
    if kind == "trace_lookup":
        return run_trace_lookup(case)
    if kind == "multi_turn":
        return run_multi_turn(case)
    if kind == "tool_manifest_boundary":
        return run_tool_manifest_boundary(case)
    if kind == "context_budget":
        return run_context_budget(case)
    if kind == "frontend_trace":
        return run_frontend_trace(case)
    raise ValueError(f"unknown case kind: {kind}")


def selected_cases(case_ids: str) -> list[dict[str, Any]]:
    wanted = {item.strip() for item in case_ids.split(",") if item.strip()}
    if not wanted:
        return list(CASES)
    known = {case["case_id"] for case in CASES}
    unknown = wanted - known
    if unknown:
        raise ValueError(f"unknown case ids: {sorted(unknown)}")
    return [case for case in CASES if case["case_id"] in wanted]


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    elapsed = [float(item.get("elapsed_ms") or 0) for item in results if item.get("elapsed_ms") is not None]
    capabilities = sorted({capability for case in CASES for capability in case.get("capabilities", [])})
    covered = sorted({capability for item in results for capability in item.get("capabilities", [])})
    model_call_count = 0
    model_latencies: list[float] = []
    for item in results:
        model_summary = item.get("model_summary") or {}
        if "call_count" in model_summary:
            model_call_count += int(model_summary.get("call_count") or 0)
            if model_summary.get("total_latency_ms"):
                model_latencies.append(float(model_summary.get("total_latency_ms") or 0))
        elif item.get("model_call_count"):
            model_call_count += int(item.get("model_call_count") or 0)
        elif isinstance(model_summary, dict):
            for value in model_summary.values():
                if isinstance(value, dict):
                    model_call_count += int(value.get("call_count") or 0)
                    if value.get("total_latency_ms"):
                        model_latencies.append(float(value.get("total_latency_ms") or 0))
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "base_url": BASE_URL,
        "company_id": COMPANY_ID,
        "model_id": MODEL_ID,
        "total": len(results),
        "passed": sum(1 for item in results if item.get("passed")),
        "failed": sum(1 for item in results if not item.get("passed")),
        "capabilities": {
            "declared": capabilities,
            "covered_by_run": covered,
            "missing_from_run": sorted(set(capabilities) - set(covered)),
        },
        "performance": {
            "e2e_mean_ms": round(statistics.mean(elapsed), 2) if elapsed else 0,
            "e2e_max_ms": round(max(elapsed), 2) if elapsed else 0,
            "model_call_count": model_call_count,
            "model_total_latency_mean_ms": round(statistics.mean(model_latencies), 2) if model_latencies else 0,
            "model_total_latency_max_ms": round(max(model_latencies), 2) if model_latencies else 0,
        },
        "results": results,
    }


def write_summary(summary: dict[str, Any]) -> tuple[Path, Path]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamped = OUT_DIR / f"workbench_full_capability_acceptance_{time.strftime('%Y%m%d_%H%M%S')}.json"
    latest = OUT_DIR / "latest_workbench_full_capability_acceptance.json"
    payload = json.dumps(summary, ensure_ascii=False, indent=2)
    timestamped.write_text(payload, encoding="utf-8")
    latest.write_text(payload, encoding="utf-8")
    return timestamped, latest


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default="", help="Comma-separated case ids. Empty means all cases.")
    return parser.parse_args()


def main() -> int:
    args = _args()
    results: list[dict[str, Any]] = []
    for case in selected_cases(args.cases):
        try:
            results.append(run_case(case))
        except Exception as exc:  # noqa: BLE001 - acceptance reports should preserve all failures.
            results.append(
                base_case_result(
                    case,
                    [{"name": "exception", "passed": False, "detail": repr(exc)}],
                    0,
                    extra={"error": repr(exc)},
                )
            )
    summary = summarize(results)
    timestamped, latest = write_summary(summary)
    compact = {
        "report": str(timestamped),
        "latest": str(latest),
        "total": summary["total"],
        "passed": summary["passed"],
        "failed": summary["failed"],
        "performance": summary["performance"],
        "failed_cases": [item["case_id"] for item in results if not item.get("passed")],
    }
    print(json.dumps(compact, ensure_ascii=False, indent=2))
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
