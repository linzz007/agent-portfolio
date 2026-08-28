"""Benchmark runner utilities for real policy impact pipeline executions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from policy_impact.skill_executors.policy_weekly_impact import run_policy_weekly_impact


SCHEMA_VERSION = "benchmark_report.v1"
SUMMARY_KEYS = (
    "latency_ms",
    "context_tokens",
    "tool_calls",
    "unsupported_claim_rate",
    "unsupported_claims_avg",
)


def summarize_runs(runs: list[dict[str, Any]]) -> dict[str, float | int]:
    run_count = len(runs)
    if run_count == 0:
        return {
            "run_count": 0,
            "success_rate": 0.0,
            "latency_ms": 0.0,
            "context_tokens": 0.0,
            "tool_calls": 0.0,
            "unsupported_claim_rate": 0.0,
            "unsupported_claims_avg": 0.0,
        }

    return {
        "run_count": run_count,
        "success_rate": _average(1.0 if run.get("success") else 0.0 for run in runs),
        "latency_ms": _average(_number(run.get("latency_ms")) for run in runs),
        "context_tokens": _average(_number(run.get("context_tokens")) for run in runs),
        "tool_calls": _average(_number(run.get("tool_calls")) for run in runs),
        "unsupported_claim_rate": _average(_unsupported_claim_rate(run) for run in runs),
        "unsupported_claims_avg": _average(_number(run.get("unsupported_claims")) for run in runs),
    }


def compare_metrics(
    baseline: dict[str, Any],
    target: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    comparison: dict[str, dict[str, Any]] = {}
    for key in sorted(set(baseline) | set(target)):
        baseline_value = baseline.get(key)
        target_value = target.get(key)
        delta = _delta(baseline_value, target_value)
        comparison[key] = {
            "baseline": baseline_value,
            "target": target_value,
            "delta": delta,
            "delta_percent": _delta_percent(baseline_value, delta),
        }
    return comparison


def measure_policy_run(company_id: str = "company_001") -> dict[str, Any]:
    started = perf_counter()
    result = run_policy_weekly_impact(company_id)
    latency_ms = int(round((perf_counter() - started) * 1000))
    state = result.state
    status = state.current_stage

    return {
        "run_id": state.run_id,
        "status": status,
        "success": status == "done" and not state.errors,
        "latency_ms": latency_ms,
        "context_tokens": _context_token_count(state.context_manifests),
        "tool_calls": len(state.tool_calls),
        "unsupported_claims": _unsupported_claim_count(state),
        "checkpoint_count": len(state.checkpoints),
        "warning_count": len(state.warnings),
        "error_count": len(state.errors),
        "artifact_path": result.run_artifact_path,
    }


def measure_policy_runs(company_id: str, runs: int) -> list[dict[str, Any]]:
    if runs < 1:
        raise ValueError("runs must be >= 1")
    return [measure_policy_run(company_id) for _ in range(runs)]


def write_benchmark_report(
    path: str | Path,
    baseline_runs: list[dict[str, Any]],
    target_runs: list[dict[str, Any]],
    baseline_name: str = "baseline",
    target_name: str = "target",
    baseline_capture_mode: str = "provided_runs",
    target_capture_mode: str = "provided_runs",
) -> dict[str, Any]:
    baseline = summarize_runs(baseline_runs)
    target = summarize_runs(target_runs)
    metric_notes = _metric_notes(baseline_runs, target_runs)
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_type": "observational_pipeline_runs",
        "baseline_name": baseline_name,
        "target_name": target_name,
        "baseline_capture_mode": baseline_capture_mode,
        "target_capture_mode": target_capture_mode,
        "provenance": {
            "baseline_name": baseline_name,
            "target_name": target_name,
            "baseline_capture_mode": baseline_capture_mode,
            "target_capture_mode": target_capture_mode,
            "optimization_proof": False,
        },
        "caveats": [
            (
                "This is an observational benchmark over real pipeline runs; "
                "it is not optimization proof by default."
            ),
            (
                "Baseline and target can represent the same current implementation unless "
                "the baseline file was captured from a separately preserved version."
            ),
            "Latency can vary with local machine, cache, filesystem, and data state.",
        ],
        "metric_notes": metric_notes,
        "baseline": baseline,
        "target": target,
        "comparison": compare_metrics(baseline, target),
        "baseline_runs": baseline_runs,
        "target_runs": target_runs,
    }

    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def _average(values: Any) -> float:
    materialized = list(values)
    return sum(materialized) / len(materialized) if materialized else 0.0


def _number(value: Any) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _delta(baseline_value: Any, target_value: Any) -> Any:
    if isinstance(baseline_value, (int, float)) and isinstance(target_value, (int, float)):
        return target_value - baseline_value
    return None


def _delta_percent(baseline_value: Any, delta: Any) -> float | None:
    if not isinstance(baseline_value, (int, float)) or not isinstance(delta, (int, float)):
        return None
    if baseline_value == 0:
        return None
    return (delta / baseline_value) * 100


def _unsupported_claim_rate(run: dict[str, Any]) -> float:
    if "unsupported_claim_rate" in run:
        return _bounded_rate(_number(run.get("unsupported_claim_rate")))

    unsupported_claims = _number(run.get("unsupported_claims"))
    denominator = _number(run.get("claim_count")) or _number(run.get("assessment_count")) or 1.0
    return _bounded_rate(unsupported_claims / max(1.0, denominator))


def _bounded_rate(value: float) -> float:
    return min(1.0, max(0.0, value))


def _metric_notes(
    baseline_runs: list[dict[str, Any]],
    target_runs: list[dict[str, Any]],
) -> dict[str, str]:
    all_runs = baseline_runs + target_runs
    notes = {
        "latency_ms": "Wall-clock runtime measured around run_policy_weekly_impact.",
        "context_tokens": "Derived from context manifest token metadata when available.",
        "tool_calls": "Count of audited tool calls recorded on pipeline state.",
        "unsupported_claim_rate": (
            "Uses explicit run rate when present; otherwise bounded unsupported_claims "
            "over claim_count or assessment_count."
        ),
        "unsupported_claims_avg": "Average raw unsupported claim count per run.",
        "success_rate": "Runs with status done and no state errors divided by run_count.",
    }
    if all_runs and all(_number(run.get("context_tokens")) == 0 for run in all_runs):
        notes["context_tokens_status"] = "not_integrated_or_zero"
    else:
        notes["context_tokens_status"] = "measured_from_manifests"
    return notes


def _context_token_count(context_manifests: list[dict[str, Any]]) -> int:
    total = 0
    for manifest in context_manifests:
        total += _token_count_from_mapping(manifest)
        metadata = manifest.get("metadata")
        if isinstance(metadata, dict):
            total += _token_count_from_mapping(metadata)
            estimates = metadata.get("token_estimates")
            if isinstance(estimates, dict):
                total += sum(int(_number(value)) for value in estimates.values())
    return total


def _token_count_from_mapping(payload: dict[str, Any]) -> int:
    total = 0
    for key in ("token_count", "context_tokens", "total_tokens", "total_token_count"):
        value = payload.get(key)
        if isinstance(value, (int, float)) and value > 0:
            total += int(value)
    return total


def _unsupported_claim_count(state: Any) -> int:
    count = 0
    review_result = getattr(state, "review_result", {}) or {}
    if isinstance(review_result, dict):
        count += _count_explicit_unsupported(review_result.get("unsupported_claims"))
        count += _count_unsupported_evidence_items(review_result.get("findings"))

    for gate in getattr(state, "stage_gate_results", []) or []:
        if not isinstance(gate, dict) or gate.get("passed", True):
            continue
        issues = gate.get("issues", [])
        if not isinstance(issues, list):
            issues = [issues]
        explicit_issues = [
            issue
            for issue in issues
            if any(
                marker in str(issue).lower()
                for marker in ("unsupported", "evidence", "source", "citation")
            )
        ]
        count += len(explicit_issues) or 1
    return count


def _count_explicit_unsupported(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, list):
        return len(value)
    return 0


def _count_unsupported_evidence_items(value: Any) -> int:
    if not isinstance(value, list):
        return 0
    count = 0
    for item in value:
        if not isinstance(item, dict):
            continue
        status = str(
            item.get("verification_status")
            or item.get("evidence_status")
            or item.get("status")
            or ""
        ).lower()
        if status in {"unsupported", "missing_evidence", "unverified"}:
            count += 1
    return count
