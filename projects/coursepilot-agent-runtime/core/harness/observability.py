"""Human-readable observability views for RunArtifact data."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


INTERNAL_TOOL_NAMES = {"memory_search", "quiz_meta", "exam_meta", "react_phase"}


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _trim(value: Any, max_chars: int = 180) -> str:
    text = str(value or "").strip().replace("\r", " ").replace("\n", " ")
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def _status_from_success(success: Any, *, default: str = "unknown") -> str:
    if success is True:
        return "ok"
    if success is False:
        return "error"
    return default


def _session_status(status: Any, error: Optional[Dict[str, Any]]) -> str:
    if error:
        return "error"
    text = str(status or "").lower()
    if text == "succeeded":
        return "ok"
    if text == "failed":
        return "error"
    return "unknown"


def _tool_name(item: Dict[str, Any]) -> str:
    return str(
        item.get("tool_name")
        or item.get("name")
        or item.get("function", {}).get("name")
        or item.get("type")
        or "unknown"
    )


def _tool_success(item: Dict[str, Any]) -> Any:
    if "success" in item:
        return item.get("success")
    if "tool_success" in item:
        return item.get("tool_success")
    if item.get("error"):
        return False
    return None


def _duration_ms(item: Dict[str, Any]) -> Optional[float]:
    for key in ("elapsed_ms", "llm_ms", "retrieval_ms", "tool_ms", "duration_ms"):
        value = item.get(key)
        if value is None:
            continue
        try:
            return round(float(value), 3)
        except (TypeError, ValueError):
            return None
    return None


def _context_pressure(context_budget: Dict[str, Any]) -> Optional[float]:
    value = context_budget.get("context_pressure_ratio")
    if value is not None:
        try:
            return round(float(value), 4)
        except (TypeError, ValueError):
            return None
    final_tokens = context_budget.get("final_tokens_est")
    budget_tokens = context_budget.get("budget_tokens_est")
    try:
        final = float(final_tokens)
        budget = float(budget_tokens)
    except (TypeError, ValueError):
        return None
    if budget <= 0:
        return None
    return round(final / budget, 4)


def _append(out: List[Dict[str, Any]], item: Dict[str, Any]) -> None:
    payload = {key: value for key, value in item.items() if value not in (None, {}, [])}
    payload["step"] = len(out) + 1
    out.append(payload)


def _top_retrieval(retrieval: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not retrieval:
        return {}
    item = retrieval[0]
    return {
        "doc_id": item.get("doc_id"),
        "chunk_id": item.get("chunk_id"),
        "score": item.get("score"),
        "text_preview": _trim(item.get("text"), 120),
    }


def _tool_call_item(call: Dict[str, Any], *, source_event_seq: Any = None) -> Dict[str, Any]:
    name = _tool_name(call)
    success = _tool_success(call)
    duration = _duration_ms(call)
    status = _status_from_success(success, default="unknown")
    args = call.get("arguments") or call.get("args") or call.get("input") or call.get("payload")
    return {
        "type": "tool_call",
        "name": name,
        "status": status,
        "title": f"Tool call: {name}",
        "summary": f"success={success} elapsed_ms={duration}",
        "input": {"arguments_preview": _trim(args, 220)} if args else {},
        "output": {
            "success": success,
            "error": _trim(call.get("error"), 220) if call.get("error") else None,
            "elapsed_ms": duration,
        },
        "source_event_seq": source_event_seq or call.get("seq"),
    }


def build_run_timeline(
    *,
    session: Dict[str, Any],
    events: Iterable[Dict[str, Any]],
    retrieval: List[Dict[str, Any]],
    context_budget: Optional[Dict[str, Any]],
    tool_calls: List[Dict[str, Any]],
    tool_decisions: List[Dict[str, Any]],
    output: Optional[Dict[str, Any]],
    eval_result: Optional[Dict[str, Any]],
    error: Optional[Dict[str, Any]],
    elapsed_ms: Optional[float],
) -> List[Dict[str, Any]]:
    """Build a compact, ordered view of one run for humans."""

    session = _as_dict(session)
    event_items = _as_list(list(events))
    retrieval = _as_list(retrieval)
    context_budget = _as_dict(context_budget)
    tool_calls = _as_list(tool_calls)
    tool_decisions = _as_list(tool_decisions)
    output = _as_dict(output)
    eval_result = _as_dict(eval_result)
    error = _as_dict(error) if error else None

    timeline: List[Dict[str, Any]] = []
    _append(
        timeline,
        {
            "type": "session",
            "name": session.get("skill_id"),
            "status": _session_status(session.get("status"), error),
            "title": "Session",
            "summary": f"{session.get('mode')} / {session.get('skill_id')}",
            "input": {
                "user_message": _trim(session.get("user_message"), 240),
                "course_name": session.get("course_name"),
            },
            "output": {
                "run_id": session.get("run_id"),
                "request_id": session.get("request_id"),
                "trace_id": session.get("trace_id"),
            },
            "duration_ms": round(float(elapsed_ms), 3) if elapsed_ms is not None else None,
        },
    )

    seen_tool_event_seqs = set()
    retrieval_seen = False
    context_seen = False
    llm_index = 0
    for event in event_items:
        event_type = str(event.get("type") or "")
        seq = event.get("seq")
        if event_type == "llm_call":
            llm_index += 1
            duration = _duration_ms(event)
            _append(
                timeline,
                {
                    "type": "llm_call",
                    "name": event.get("model"),
                    "status": _status_from_success(event.get("success"), default="unknown"),
                    "title": f"LLM call #{llm_index}",
                    "summary": (
                        f"model={event.get('model')} provider={event.get('provider')} "
                        f"stream={event.get('stream')} elapsed_ms={duration}"
                    ),
                    "input": {
                        "messages": event.get("input_messages"),
                        "prompt_tokens": event.get("prompt_tokens"),
                        "prompt_tokens_est": event.get("prompt_tokens_est"),
                        "with_tools": event.get("with_tools"),
                        "react_phase": event.get("react_phase"),
                    },
                    "output": {
                        "message": event.get("output_message"),
                        "completion_tokens": event.get("completion_tokens"),
                        "first_token_latency_ms": event.get("first_token_latency_ms"),
                        "final_output_source": event.get("final_output_source"),
                    },
                    "duration_ms": duration,
                    "source_event_seq": seq,
                },
            )
        elif event_type == "retrieval":
            retrieval_seen = True
            duration = _duration_ms(event)
            _append(
                timeline,
                {
                    "type": "retrieval",
                    "name": event.get("mode"),
                    "status": _status_from_success(event.get("success"), default="unknown"),
                    "title": "RAG retrieval",
                    "summary": f"returned={event.get('returned_count')} candidates={event.get('candidate_count')} elapsed_ms={duration}",
                    "input": {"mode": event.get("mode"), "top_k": event.get("top_k")},
                    "output": {"returned_count": event.get("returned_count"), "top_chunk": _top_retrieval(retrieval)},
                    "duration_ms": duration,
                    "source_event_seq": seq,
                },
            )
        elif event_type == "context_budget":
            context_seen = True
            pressure = _context_pressure(event)
            _append(
                timeline,
                {
                    "type": "context_budget",
                    "name": "context",
                    "status": "warning" if event.get("hard_truncated") else "ok",
                    "title": "Context budget",
                    "summary": f"final_tokens={event.get('final_tokens_est')} budget={event.get('budget_tokens_est')} pressure={pressure}",
                    "input": {
                        "history_tokens_est": event.get("history_tokens_est"),
                        "rag_tokens_est": event.get("rag_tokens_est"),
                        "memory_tokens_est": event.get("memory_tokens_est"),
                    },
                    "output": {
                        "final_tokens_est": event.get("final_tokens_est"),
                        "budget_tokens_est": event.get("budget_tokens_est"),
                        "context_pressure_ratio": pressure,
                        "hard_truncated": event.get("hard_truncated"),
                    },
                    "source_event_seq": seq,
                },
            )
        elif event_type == "react_phase":
            _append(
                timeline,
                {
                    "type": "react_phase",
                    "name": event.get("phase"),
                    "status": "ok",
                    "title": "ReAct phase",
                    "summary": f"phase={event.get('phase')} round={event.get('round')}",
                    "input": {"round": event.get("round"), "stream_tools": event.get("stream_tools")},
                    "source_event_seq": seq,
                },
            )
        elif event_type == "tool_gate_decision":
            seen_tool_event_seqs.add(seq)
            allowed = bool(event.get("tool_gate_decision", False))
            name = _tool_name(event)
            _append(
                timeline,
                {
                    "type": "tool_decision",
                    "name": name,
                    "status": "ok" if allowed else "warning",
                    "title": f"Tool decision: {name}",
                    "summary": "allowed" if allowed else str(event.get("tool_skip_reason") or "blocked"),
                    "input": {
                        "risk_level": event.get("risk_level"),
                        "approval_mode": event.get("approval_mode"),
                        "tool_round": event.get("tool_round"),
                    },
                    "source_event_seq": seq,
                },
            )
        elif event_type == "tool_call":
            seen_tool_event_seqs.add(seq)
            _append(timeline, _tool_call_item(event, source_event_seq=seq))

    if retrieval and not retrieval_seen:
        _append(
            timeline,
            {
                "type": "retrieval",
                "name": "artifact",
                "status": "ok",
                "title": "RAG retrieval",
                "summary": f"stored_chunks={len(retrieval)}",
                "output": {"returned_count": len(retrieval), "top_chunk": _top_retrieval(retrieval)},
            },
        )

    if context_budget and not context_seen:
        pressure = _context_pressure(context_budget)
        _append(
            timeline,
            {
                "type": "context_budget",
                "name": "context",
                "status": "warning" if context_budget.get("hard_truncated") else "ok",
                "title": "Context budget",
                "summary": f"final_tokens={context_budget.get('final_tokens_est')} budget={context_budget.get('budget_tokens_est')} pressure={pressure}",
                "output": {
                    "final_tokens_est": context_budget.get("final_tokens_est"),
                    "budget_tokens_est": context_budget.get("budget_tokens_est"),
                    "context_pressure_ratio": pressure,
                    "hard_truncated": context_budget.get("hard_truncated"),
                },
            },
        )

    for decision in tool_decisions:
        seq = decision.get("source_event_seq")
        if seq in seen_tool_event_seqs:
            continue
        name = _tool_name(decision)
        allowed = bool(decision.get("allowed", False))
        _append(
            timeline,
            {
                "type": "tool_decision",
                "name": name,
                "status": "ok" if allowed else "warning",
                "title": f"Tool decision: {name}",
                "summary": str(decision.get("reason") or ("allowed" if allowed else "blocked")),
                "input": {
                    "risk_level": decision.get("risk_level"),
                    "approval_mode": decision.get("approval_mode"),
                    "tool_round": decision.get("tool_round"),
                },
                "source_event_seq": seq,
            },
        )

    for call in tool_calls:
        if call.get("source") == "trace" and call.get("seq") in seen_tool_event_seqs:
            continue
        if str(call.get("type") or "") == "tool_progress":
            continue
        _append(timeline, _tool_call_item(call))

    if error:
        _append(
            timeline,
            {
                "type": "error",
                "name": error.get("type"),
                "status": "error",
                "title": "Run error",
                "summary": _trim(error.get("message"), 220),
                "output": error,
            },
        )

    content = str(output.get("content") or "")
    _append(
        timeline,
        {
            "type": "assistant_output",
            "name": "assistant",
            "status": "ok" if content.strip() else "error",
            "title": "Assistant output",
            "summary": _trim(content, 220),
            "output": {
                "content_chars": len(content),
                "stream": output.get("stream"),
                "chunk_count": output.get("chunk_count"),
            },
        },
    )

    if eval_result:
        verdict = str(eval_result.get("verdict") or "unknown")
        _append(
            timeline,
            {
                "type": "eval",
                "name": eval_result.get("evaluator"),
                "status": "ok" if verdict == "passed" else ("error" if verdict == "failed" else "warning"),
                "title": "Artifact evaluation",
                "summary": f"verdict={verdict} reasons={','.join(eval_result.get('reasons') or [])}",
                "output": {"verdict": verdict, "scores": eval_result.get("scores")},
            },
        )

    return timeline


def _diagnostic(check: str, status: str, message: str, **details: Any) -> Dict[str, Any]:
    item: Dict[str, Any] = {"check": check, "status": status, "message": message}
    compact = {key: value for key, value in details.items() if value not in (None, {}, [])}
    if compact:
        item["details"] = compact
    return item


def _has_actual_tool_call(tool_calls: List[Dict[str, Any]], tool_name: str) -> bool:
    expected = tool_name.lower()
    for call in tool_calls:
        name = _tool_name(call).lower()
        if name != expected:
            continue
        call_type = str(call.get("type") or "")
        if call_type in {"tool_progress", "tool_gate_decision"}:
            continue
        return True
    return False


def _has_failed_tool_call(tool_calls: List[Dict[str, Any]]) -> bool:
    return any(_tool_success(call) is False for call in tool_calls)


def _governed_tool_names(tool_calls: List[Dict[str, Any]]) -> List[str]:
    names: List[str] = []
    for call in tool_calls:
        name = _tool_name(call)
        call_type = str(call.get("type") or "")
        if name in INTERNAL_TOOL_NAMES or call_type in {"tool_progress", "react_phase"}:
            continue
        if name not in names:
            names.append(name)
    return names


def build_run_diagnostics(
    *,
    session: Dict[str, Any],
    retrieval: List[Dict[str, Any]],
    context_budget: Optional[Dict[str, Any]],
    tool_calls: List[Dict[str, Any]],
    tool_decisions: List[Dict[str, Any]],
    output: Optional[Dict[str, Any]],
    error: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Build concise checks that point humans to suspicious run behavior."""

    session = _as_dict(session)
    retrieval = _as_list(retrieval)
    context_budget = _as_dict(context_budget)
    tool_calls = _as_list(tool_calls)
    tool_decisions = _as_list(tool_decisions)
    output = _as_dict(output)
    error = _as_dict(error) if error else None

    diagnostics: List[Dict[str, Any]] = []
    run_status = _session_status(session.get("status"), error)
    diagnostics.append(
        _diagnostic(
            "run_status",
            run_status,
            "Run completed successfully." if run_status == "ok" else "Run did not complete cleanly.",
            run_id=session.get("run_id"),
            error=error,
        )
    )

    content = str(output.get("content") or "").strip()
    diagnostics.append(
        _diagnostic(
            "answer_output",
            "ok" if content else "error",
            "Assistant output is present." if content else "Assistant output is empty.",
            content_chars=len(content),
        )
    )

    diagnostics.append(
        _diagnostic(
            "retrieval",
            "ok" if retrieval else "warning",
            f"Retrieved {len(retrieval)} chunk(s)." if retrieval else "No retrieval chunks were stored.",
            retrieved_count=len(retrieval),
            top_chunk=_top_retrieval(retrieval),
        )
    )

    if context_budget:
        pressure = _context_pressure(context_budget)
        hard_truncated = bool(context_budget.get("hard_truncated", False))
        if hard_truncated:
            context_status = "error"
            message = "Context was hard-truncated before the final model call."
        elif pressure is not None and pressure >= 0.85:
            context_status = "warning"
            message = "Context pressure is high."
        else:
            context_status = "ok"
            message = "Context budget is within limit."
        diagnostics.append(
            _diagnostic(
                "context_budget",
                context_status,
                message,
                context_pressure_ratio=pressure,
                final_tokens_est=context_budget.get("final_tokens_est"),
                budget_tokens_est=context_budget.get("budget_tokens_est"),
                hard_truncated=hard_truncated,
            )
        )
    else:
        diagnostics.append(
            _diagnostic(
                "context_budget",
                "warning",
                "No context budget record was stored.",
            )
        )

    failed_tool = _has_failed_tool_call(tool_calls)
    diagnostics.append(
        _diagnostic(
            "tool_call_success",
            "error" if failed_tool else "ok",
            "At least one tool call failed." if failed_tool else "No failed tool calls were observed.",
            tool_call_count=len(tool_calls),
        )
    )

    governed_tools = _governed_tool_names(tool_calls)
    if tool_decisions:
        diagnostics.append(
            _diagnostic(
                "tool_governance",
                "ok",
                f"Stored {len(tool_decisions)} tool gate decision(s).",
                governed_tools=governed_tools,
                decision_count=len(tool_decisions),
            )
        )
    elif governed_tools:
        diagnostics.append(
            _diagnostic(
                "tool_governance",
                "warning",
                "Tool calls were observed without matching tool gate decisions.",
                governed_tools=governed_tools,
            )
        )
    else:
        diagnostics.append(
            _diagnostic(
                "tool_governance",
                "ok",
                "No governed external tool calls were observed.",
            )
        )

    skill_id = str(session.get("skill_id") or "")
    if skill_id in {"practice.grade.v1", "exam.grade.v1"}:
        has_calculator = _has_actual_tool_call(tool_calls, "calculator")
        generated_question_output = any(
            marker in content
            for marker in (
                "<!-- QUIZ_META",
                "<!-- EXAM_META",
                "# 练习题",
                "## 练习题",
                "模拟考试试卷",
            )
        )
        generated_question_tool = any(_tool_name(call) in {"quiz_meta", "exam_meta"} for call in tool_calls)
        diagnostics.append(
            _diagnostic(
                "grading_output_consistency",
                "warning" if generated_question_output or generated_question_tool else "ok",
                (
                    "Grading route returned question-generation output."
                    if generated_question_output or generated_question_tool
                    else "Grading route output does not contain question-generation markers."
                ),
                skill_id=skill_id,
                generated_question_output=generated_question_output,
                generated_question_tool=generated_question_tool,
            )
        )
        diagnostics.append(
            _diagnostic(
                "required_calculator_for_grading",
                "ok" if has_calculator else "warning",
                (
                    "A calculator tool call was observed during grading."
                    if has_calculator
                    else "No calculator tool call was observed during grading."
                ),
                skill_id=skill_id,
            )
        )

    return diagnostics
