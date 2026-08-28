"""Run multi-turn context and long-term memory acceptance against the live API."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from urllib import error, request
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from policy_impact.memory.store import PolicyMemoryStore


BASE_URL = os.getenv("WORKBENCH_BASE_URL", "http://127.0.0.1:8501")
COMPANY_ID = "company_001"
MODEL_ID = "deepseek-v4-flash"
OUT_DIR = ROOT / "data" / "acceptance"


def http(method: str, path: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = request.Request(
        BASE_URL + path,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method=method,
    )
    try:
        with request.urlopen(req, timeout=120) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} HTTP {exc.code}: {detail}") from exc


def create_session(title: str) -> dict:
    return http(
        "POST",
        f"/companies/{COMPANY_ID}/workbench/sessions",
        {
            "title": title,
            "mode": "chat",
            "model_id": MODEL_ID,
            "active_skill_id": "general_chat",
        },
    )


def send(session_id: str, message: str) -> tuple[dict, dict]:
    result = http(
        "POST",
        f"/companies/{COMPANY_ID}/workbench/sessions/{session_id}/messages",
        {"message": message},
    )
    trace = http(
        "GET",
        f"/companies/{COMPANY_ID}/workbench/sessions/{session_id}/runs/{result['run_id']}/trace",
    )
    return result, trace


def turn_record(message: str, result: dict, trace: dict) -> dict:
    """Keep enough evidence for a reviewer to audit one turn without SQLite."""

    return {
        "message": message,
        "result": result,
        "trace_summary": trace.get("trace_summary") or {},
        "context_summary": trace.get("context_summary") or {},
        "memory_summary": trace.get("memory_summary") or {},
        "steps": trace.get("steps") or [],
    }


def check(name: str, passed: bool, detail: str = "") -> dict:
    return {"name": name, "passed": bool(passed), "detail": detail[:800]}


def model_latency(trace: dict) -> float:
    return round(
        sum(
            float((step.get("metadata") or {}).get("latency_ms") or 0)
            for step in trace.get("steps") or []
            if step.get("step_type") == "model_call"
        ),
        2,
    )


def run_same_session_context() -> dict:
    session = create_session("Multi-turn - Session Context")
    first_message = "本次对话先把三个临时重点设为 ContextManifest、Tool Policy 和 Eval。请复述，但不要写入长期记忆。"
    first, first_trace = send(
        session["session_id"],
        first_message,
    )
    second_message = "刚才那三个临时重点是什么？按从输入治理到质量验证的顺序说明。"
    second, second_trace = send(
        session["session_id"],
        second_message,
    )
    answer = second.get("answer") or ""
    first_steps = [step.get("step_type") for step in first_trace.get("steps") or []]
    second_steps = [step.get("step_type") for step in second_trace.get("steps") or []]
    checks = [
        check("turn1_no_memory_write", "memory_write" not in first_steps, str(first_steps)),
        check("turn2_no_memory_write", "memory_write" not in second_steps, str(second_steps)),
        check("turn2_resolves_context", all(term in answer for term in ["ContextManifest", "Tool Policy", "Eval"]), answer),
        check("turn2_has_model_call", "model_call" in second_steps, str(second_steps)),
    ]
    return {
        "case_id": "M01_same_session_context",
        "session_id": session["session_id"],
        "turns": [
            turn_record(first_message, first, first_trace),
            turn_record(second_message, second, second_trace),
        ],
        "answer": answer,
        "checks": checks,
        "model_latency_ms": model_latency(first_trace) + model_latency(second_trace),
        "passed": all(item["passed"] for item in checks),
    }


def run_cross_session_memory() -> dict:
    token = f"ACCEPT-MEM-{uuid4().hex[:8].upper()}"
    memory_text = f"{token}：Harness 学习重点是 ContextManifest、Tool Policy 和 Eval"
    writer_session = create_session("Multi-turn - Memory Writer")
    write_message = f"请记住：{memory_text}。"
    written, write_trace = send(
        writer_session["session_id"],
        write_message,
    )
    write_step = next(
        (step for step in write_trace.get("steps") or [] if step.get("step_type") == "memory_write"),
        {},
    )
    write_output = write_step.get("output_payload") or {}
    memory_id = str(write_output.get("memory_id") or "")
    reader_session = create_session("Multi-turn - Memory Reader")
    read_message = f"我之前让你记住的 {token} 对应的 Harness 学习重点是什么？"
    recalled, read_trace = send(
        reader_session["session_id"],
        read_message,
    )
    answer = recalled.get("answer") or ""
    write_steps = [step.get("step_type") for step in write_trace.get("steps") or []]
    read_steps = [step.get("step_type") for step in read_trace.get("steps") or []]
    memory_summary = read_trace.get("memory_summary") or {}
    read_memory_ids = {
        str(memory_id)
        for step in read_trace.get("steps") or []
        if step.get("step_type") == "memory_read"
        for memory_id in (step.get("output_payload") or {}).get("memory_ids") or []
    }
    citation_memory_ids = {
        str(item.get("memory_id") or "")
        for item in recalled.get("citations") or []
        if item.get("type") == "memory_item"
    }
    cleanup_deleted = PolicyMemoryStore(COMPANY_ID).delete_memory(memory_id) if memory_id else False
    checks = [
        check(
            "explicit_request_creates_new_memory",
            "memory_write" in write_steps and bool(write_output.get("created")) and bool(memory_id),
            json.dumps(write_output, ensure_ascii=False),
        ),
        check("new_session_reads_memory", "memory_read" in read_steps, str(read_steps)),
        check("memory_hit_visible", int(memory_summary.get("total_memory_hits") or 0) >= 1, str(memory_summary)),
        check("reader_hits_exact_memory_id", memory_id in read_memory_ids, str(sorted(read_memory_ids))),
        check("reader_cites_exact_memory_id", memory_id in citation_memory_ids, str(sorted(citation_memory_ids))),
        check(
            "recalled_content_correct",
            token in answer and all(term in answer for term in ["ContextManifest", "Tool Policy", "Eval"]),
            answer,
        ),
        check("acceptance_memory_cleaned_up", cleanup_deleted, memory_id),
    ]
    return {
        "case_id": "M02_cross_session_memory",
        "writer_session_id": writer_session["session_id"],
        "reader_session_id": reader_session["session_id"],
        "memory_id": memory_id,
        "token": token,
        "turns": [
            turn_record(write_message, written, write_trace),
            turn_record(read_message, recalled, read_trace),
        ],
        "cleanup_deleted": cleanup_deleted,
        "answer": answer,
        "checks": checks,
        "model_latency_ms": model_latency(write_trace) + model_latency(read_trace),
        "passed": all(item["passed"] for item in checks),
    }


def run_session_isolation() -> dict:
    source_session = create_session("Multi-turn - Isolation Source")
    source_message = "本次会话的临时代号是 ORANGE-731，只在当前会话使用，不要写入长期记忆。"
    source, source_trace = send(
        source_session["session_id"],
        source_message,
    )
    isolated_session = create_session("Multi-turn - Isolation Target")
    isolated_message = "另一个会话里刚才设置的临时代号是什么？"
    isolated, isolated_trace = send(
        isolated_session["session_id"],
        isolated_message,
    )
    answer = isolated.get("answer") or ""
    checks = [
        check("secret_not_leaked", "ORANGE-731" not in answer, answer),
        check(
            "uncertainty_explicit",
            any(term in answer for term in ["不知道", "无法", "没有", "未提供", "不能访问"]),
            answer,
        ),
        check(
            "source_not_written_to_memory",
            "memory_write" not in [step.get("step_type") for step in source_trace.get("steps") or []],
            json.dumps(source_trace.get("memory_summary") or {}, ensure_ascii=False),
        ),
    ]
    return {
        "case_id": "M03_session_isolation",
        "source_session_id": source_session["session_id"],
        "isolated_session_id": isolated_session["session_id"],
        "turns": [
            turn_record(source_message, source, source_trace),
            turn_record(isolated_message, isolated, isolated_trace),
        ],
        "answer": answer,
        "checks": checks,
        "model_latency_ms": model_latency(source_trace) + model_latency(isolated_trace),
        "passed": all(item["passed"] for item in checks),
    }


def run_long_context_compaction() -> dict:
    session = create_session("Multi-turn - Long Context Compaction")
    setup_messages = [
        "本次会话的临时代号是 POLARIS-17，只在当前会话使用，不要写入长期记忆。",
        "本次会话的输出偏好是先列公开事实，再列分析推断；不要写入长期记忆。",
        "纠正上一条计划：不是每周生成，而是每天 09:00 生成；只在当前会话使用。",
        "未决事项：iFinD 数据源授权状态尚未核实，不能写成已授权。",
        "禁止项：不要把通用工程建议描述成示例公司已经上线的功能。",
    ]
    setup_results = []
    setup_traces = []
    for message in setup_messages:
        result, trace = send(session["session_id"], message)
        setup_results.append(result)
        setup_traces.append(trace)

    setup_contexts = [trace.get("context_summary") or {} for trace in setup_traces]
    setup_answers = [str(result.get("answer") or "") for result in setup_results]

    final_message = "请汇总本次会话的临时代号、生成频率、未决事项和禁止项，并保持事实与推断分开。"
    final, final_trace = send(
        session["session_id"],
        final_message,
    )
    answer = final.get("answer") or ""
    steps = final_trace.get("steps") or []
    step_types = [step.get("step_type") for step in steps]
    context_summary = final_trace.get("context_summary") or {}
    context_step = next((step for step in steps if step.get("step_type") == "context_build"), {})
    manifest = (context_step.get("output_payload") or {}).get("context_manifest") or {}
    visible = manifest.get("visible_content") or {}
    summary = visible.get("conversation_summary") or {}
    summary_text = json.dumps(summary, ensure_ascii=False)
    compaction_write = next(
        (
            step
            for step in steps
            if step.get("step_type") == "memory_write"
            and (step.get("input_payload") or {}).get("write_policy") == "context_pressure_compaction"
        ),
        {},
    )
    model_steps = [step for step in steps if step.get("step_type") == "model_call"]
    checks = [
        check("answer_keeps_session_code", "POLARIS-17" in answer, answer),
        check("answer_keeps_corrected_frequency", "每天" in answer and "09:00" in answer, answer),
        check("answer_keeps_open_loop", "iFinD" in answer and "未核实" in answer, answer),
        check(
            "answer_keeps_prohibition",
            "通用" in answer and ("上线" in answer or "已实现" in answer),
            answer,
        ),
        check(
            "answer_does_not_invent_user_motivation",
            "用户可能" not in answer and "可能正在" not in answer and "推测用户" not in answer,
            answer,
        ),
        check("context_compaction_traced", "context_compaction" in step_types, str(step_types)),
        check("summary_visible_to_answer_model", bool(summary), summary_text),
        check("summary_preserves_oldest_code", "POLARIS-17" in summary_text, summary_text),
        check(
            "history_is_bounded",
            int(context_summary.get("history_messages_included") or 0) <= 8
            and int((context_summary.get("context_policy") or {}).get("history_messages_dropped") or 0) >= 2,
            json.dumps(context_summary, ensure_ascii=False),
        ),
        check("summary_write_is_working_memory", bool(compaction_write), json.dumps(compaction_write, ensure_ascii=False)),
        check("summary_and_answer_use_model", len(model_steps) == 2, str(step_types)),
        check(
            "setup_context_excludes_company_facts",
            all(int(item.get("company_facts_included") or 0) == 0 for item in setup_contexts),
            json.dumps(setup_contexts, ensure_ascii=False),
        ),
        check(
            "setup_context_excludes_global_memory",
            all(int(item.get("memory_items_included") or 0) == 0 for item in setup_contexts),
            json.dumps(setup_contexts, ensure_ascii=False),
        ),
        check(
            "setup_answers_are_concise_acknowledgements",
            all(len(answer) <= 500 and not re.search(r"\[[a-z_]+\.[a-z0-9_.-]+\]", answer, flags=re.I) for answer in setup_answers),
            json.dumps(setup_answers, ensure_ascii=False),
        ),
    ]
    return {
        "case_id": "M04_long_context_compaction",
        "session_id": session["session_id"],
        "turns": [
            *[
                turn_record(message, result, trace)
                for message, result, trace in zip(setup_messages, setup_results, setup_traces)
            ],
            turn_record(final_message, final, final_trace),
        ],
        "answer": answer,
        "conversation_summary": summary,
        "context_summary": context_summary,
        "checks": checks,
        "model_latency_ms": sum(model_latency(trace) for trace in [*setup_traces, final_trace]),
        "final_turn_model_latency_ms": model_latency(final_trace),
        "passed": all(item["passed"] for item in checks),
    }


def main() -> int:
    started = time.time()
    results = []
    for runner in (
        run_same_session_context,
        run_cross_session_memory,
        run_session_isolation,
        run_long_context_compaction,
    ):
        try:
            results.append(runner())
        except Exception as exc:  # noqa: BLE001
            results.append(
                {
                    "case_id": runner.__name__,
                    "passed": False,
                    "error": repr(exc),
                    "checks": [check("exception", False, repr(exc))],
                }
            )
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model_id": MODEL_ID,
        "total": len(results),
        "passed": sum(1 for item in results if item.get("passed")),
        "failed": sum(1 for item in results if not item.get("passed")),
        "elapsed_ms": round((time.time() - started) * 1000, 2),
        "model_latency_total_ms": round(sum(float(item.get("model_latency_ms") or 0) for item in results), 2),
        "results": results,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamped = OUT_DIR / f"workbench_multiturn_acceptance_{time.strftime('%Y%m%d_%H%M%S')}.json"
    latest = OUT_DIR / "latest_workbench_multiturn_acceptance.json"
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    timestamped.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")
    print(timestamped)
    print(json.dumps({key: payload[key] for key in ("total", "passed", "failed", "elapsed_ms")}, ensure_ascii=False, indent=2))
    return 0 if payload["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
