"""Deterministic RunArtifact evaluation helpers."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _score(flag: bool) -> float:
    return 1.0 if flag else 0.0


def _tool_name(item: Dict[str, Any]) -> str:
    return str(item.get("tool_name") or item.get("name") or "unknown")


def _tool_call_failed(item: Dict[str, Any]) -> bool:
    if item.get("success") is False:
        return True
    if item.get("error"):
        return True
    return str(item.get("failure_class") or "") in {"retryable_error", "fatal_error"}


def evaluate_artifact(artifact: Dict[str, Any]) -> Dict[str, Any]:
    """Return a lightweight EvalResult for one RunArtifact dictionary."""

    artifact = _as_dict(artifact)
    output = _as_dict(artifact.get("output"))
    retrieval = _as_list(artifact.get("retrieval"))
    context_budget = _as_dict(artifact.get("context_budget"))
    tool_decisions = _as_list(artifact.get("tool_decisions"))
    tool_calls = _as_list(artifact.get("tool_calls"))
    error = artifact.get("error")

    content = str(output.get("content") or "").strip()
    context_pressure = float(context_budget.get("context_pressure_ratio", 0.0) or 0.0)
    blocked = [item for item in tool_decisions if not bool(item.get("allowed", False))]
    failed_calls = [item for item in tool_calls if _tool_call_failed(item)]
    write_risk = any(str(item.get("risk_level") or "") == "write" for item in tool_decisions)
    external_risk = any(str(item.get("risk_level") or "") == "external" for item in tool_decisions)

    checks = {
        "has_output": bool(content),
        "has_retrieval": bool(retrieval),
        "has_error": error is not None,
        "blocked_tools": bool(blocked),
        "tool_failures": bool(failed_calls),
        "context_pressure_high": context_pressure >= 0.85,
        "write_risk": write_risk,
        "external_risk": external_risk,
    }

    safety_score = 1.0
    if checks["blocked_tools"] or checks["write_risk"]:
        safety_score = 0.0
    elif checks["external_risk"]:
        safety_score = 0.5

    scores = {
        "answer_presence_score": _score(checks["has_output"]),
        "retrieval_coverage_score": _score(checks["has_retrieval"]),
        "tool_success_score": _score(not checks["blocked_tools"] and not checks["tool_failures"]),
        "context_budget_score": _score(not checks["context_pressure_high"]),
        "safety_score": safety_score,
    }

    reasons: List[str] = []
    if checks["has_error"]:
        reasons.append("run_error")
    if not checks["has_output"]:
        reasons.append("missing_output")
    if not checks["has_retrieval"]:
        reasons.append("missing_retrieval")
    if checks["context_pressure_high"]:
        reasons.append("context_pressure_high")
    for item in blocked:
        reasons.append(f"blocked_tool:{_tool_name(item)}")
    for item in failed_calls:
        reasons.append(f"tool_failure:{_tool_name(item)}")
    if checks["write_risk"]:
        reasons.append("write_risk")
    elif checks["external_risk"]:
        reasons.append("external_risk")

    if checks["has_error"]:
        verdict = "failed"
    elif any(score < 1.0 for score in scores.values()):
        verdict = "warning"
    else:
        verdict = "passed"

    return {
        "evaluator": "heuristic.v1",
        "verdict": verdict,
        "checks": checks,
        "scores": scores,
        "reasons": reasons,
    }


def summarize_evaluations(results: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate EvalResult dictionaries for a small local run report."""

    items = [item for item in results if isinstance(item, dict)]
    verdict_counts = Counter(str(item.get("verdict") or "unknown") for item in items)
    score_totals: Dict[str, float] = defaultdict(float)
    score_counts: Dict[str, int] = defaultdict(int)
    for item in items:
        scores = _as_dict(item.get("scores"))
        for key, value in scores.items():
            try:
                score_totals[key] += float(value)
                score_counts[key] += 1
            except (TypeError, ValueError):
                continue
    averages = {
        key: round(score_totals[key] / score_counts[key], 4)
        for key in sorted(score_totals)
        if score_counts[key]
    }
    return {
        "total": len(items),
        "verdict_counts": dict(verdict_counts),
        "average_scores": averages,
    }
