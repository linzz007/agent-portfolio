"""Shared robust-output helpers for server experiment runners."""

from __future__ import annotations

import re
from typing import Any, Dict


def source_dataset_for_row(row: Dict[str, Any], task: str = "") -> str:
    source = str(row.get("source_dataset") or "").strip().lower()
    if source:
        return {"scitab": "tabfact"}.get(source, source)
    task_name = str(task or "").strip().lower()
    return {"scitab": "tabfact"}.get(task_name, task_name)


def fallback_answer_for_row(row: Dict[str, Any], task: str = "") -> str:
    """Return a non-empty, evaluator-compatible fallback without using gold labels."""

    source = source_dataset_for_row(row, task)
    question = str(row.get("question") or row.get("utterance") or row.get("statement") or "")
    question_l = question.lower()

    if source == "tabfact":
        return "false"
    if source == "crt":
        if re.search(r"\b(yes|no|whether|is|are|was|were|does|do|did|can|could)\b", question_l):
            return "No"
        if re.search(r"\b(percent(?:age)?|proportion|ratio|fraction|average|mean|how many|number of)\b", question_l):
            return "0"
        return "0"
    if source == "wtq":
        if re.search(r"\b(how many|number of|count|total|sum|average|mean)\b", question_l):
            return "0"
        return "unknown"
    return "unknown"


def classify_error(error_message: Any) -> str:
    text = str(error_message or "").lower()
    if not text:
        return ""
    if any(
        marker in text
        for marker in (
            "context length",
            "maximum context",
            "max context",
            "too many tokens",
            "prompt is too long",
            "token limit",
        )
    ):
        return "context_overflow"
    if "badrequesterror" in text or "bad request" in text:
        return "api_bad_request"
    if any(marker in text for marker in ("apiconnectionerror", "connecterror", "timeout", "timed out")):
        return "api_error"
    if any(
        marker in text
        for marker in (
            "traceback",
            "nameerror",
            "keyerror",
            "indexerror",
            "valueerror",
            "typeerror",
            "zerodivisionerror",
            "execution failed",
        )
    ):
        return "execution_error"
    return "unknown_error"


def attach_robust_fields(
    row: Dict[str, Any],
    *,
    fallback_used: bool,
    retry_count: int = 0,
    error_message: Any = "",
    fallback_reason: str = "",
) -> Dict[str, Any]:
    error_type = classify_error(error_message)
    context_overflow = error_type == "context_overflow"
    execution_error = bool(error_type and error_type not in {"context_overflow", "api_bad_request", "api_error"})
    row["fallback_used"] = bool(fallback_used)
    row["retry_count"] = int(max(0, retry_count))
    row["error_type"] = error_type
    row["context_overflow"] = context_overflow
    row["execution_error"] = execution_error
    row["robust_runner"] = {
        "fallback_used": bool(fallback_used),
        "retry_count": int(max(0, retry_count)),
        "error_type": error_type,
        "context_overflow": context_overflow,
        "execution_error": execution_error,
        "fallback_reason": fallback_reason,
        "error_message": str(error_message or "")[:2000],
    }
    return row


def apply_fallback_answer(
    row: Dict[str, Any],
    *,
    task: str = "",
    error_message: Any = "",
    retry_count: int = 0,
    fallback_reason: str = "recovery_failed",
) -> Dict[str, Any]:
    answer = fallback_answer_for_row(row, task)
    row["fallback_answer"] = answer
    row["pred_answer"] = answer
    row["final_answer"] = answer
    row["final_value"] = answer
    return attach_robust_fields(
        row,
        fallback_used=True,
        retry_count=retry_count,
        error_message=error_message,
        fallback_reason=fallback_reason,
    )
