"""Benchmark the live policy workflow with semantic acceptance checks."""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "acceptance"
BASE_URL = os.getenv("WORKBENCH_BASE_URL", "http://127.0.0.1:8501")
COMPANY_ID = "company_001"
MODEL_ID = "deepseek-v4-flash"
PROMPT = (
    "请分析近4个月官方政策对示例公司的影响。必须区分直接适用、条件适用、明确不适用和证据不足；"
    "不要因为出现人工智能关键词就判定相关。对每条政策给出企业证据、政策原文、缺失事实和建议动作。"
)
EXPECTED = {
    "智能体规范应用与创新发展实施意见": ("not_applicable", "guidance"),
    "网络数据安全风险评估办法": ("conditional", "mandatory"),
    "人工智能拟人化互动服务管理暂行办法": ("not_applicable", "mandatory"),
    "工业和信息化部等十部门关于印发《人工智能科技伦理审查与服务办法（试行）》的通知": (
        "insufficient_evidence",
        "mandatory",
    ),
}
BASELINE = {
    "label": "before_context_and_persistence_optimization",
    "session_id": "3b40136b626d4a70a45b6c997a15c5fc",
    "workflow_run_id": "c41ca35b4159452dbad675412754529f",
    "e2e_ms": 17934.92,
    "applicability_input_tokens": 8647,
    "applicability_latency_ms": 13021.08,
}


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
        raise RuntimeError(f"{method} {path} HTTP {exc.code}: {detail}") from exc


def _run_once(iteration: int) -> dict:
    session = http(
        "POST",
        f"/companies/{COMPANY_ID}/workbench/sessions",
        {
            "title": f"验收-政策性能-{iteration}",
            "mode": "skill",
            "model_id": MODEL_ID,
            "active_skill_id": "policy_weekly_impact",
        },
    )
    session_id = session["session_id"]
    started = time.perf_counter()
    result = http(
        "POST",
        f"/companies/{COMPANY_ID}/workbench/sessions/{session_id}/messages",
        {"message": PROMPT},
    )
    e2e_ms = (time.perf_counter() - started) * 1000
    trace = http(
        "GET",
        f"/companies/{COMPANY_ID}/workbench/sessions/{session_id}/trace",
    )
    artifact_path = str((result.get("artifacts") or {}).get("run_artifact") or "")
    artifact = json.loads(Path(artifact_path).read_text(encoding="utf-8"))
    answer = str(result.get("answer") or "")
    model_steps = [
        step for step in trace.get("steps", []) if step.get("step_type") == "model_call"
    ]
    applicability_step = next(
        (step for step in model_steps if "适用性" in str(step.get("title") or "")),
        {},
    )
    applicability_meta = applicability_step.get("metadata") or {}
    usage = applicability_meta.get("usage") or {}
    uncached_input_tokens = int(usage.get("input_tokens") or 0)
    cache_read_input_tokens = int(usage.get("cache_read_input_tokens") or 0)
    cache_creation_input_tokens = int(usage.get("cache_creation_input_tokens") or 0)
    effective_input_tokens = (
        uncached_input_tokens + cache_read_input_tokens + cache_creation_input_tokens
    )
    assessments = artifact.get("policy_applicability") or []
    actual = {
        str(item.get("policy_title") or ""): (
            str(item.get("applicability") or ""),
            str(item.get("binding_effect") or ""),
        )
        for item in assessments
    }
    network = next(
        (item for item in assessments if item.get("policy_title") == "网络数据安全风险评估办法"),
        {},
    )
    context = artifact.get("applicability_context") or []
    checks = {
        "model_is_flash": trace.get("model_id") == MODEL_ID,
        "all_expected_classifications": actual == EXPECTED,
        "forced_relevance_is_zero": int((artifact.get("quality_metrics") or {}).get("forced_relevance_count", -1)) == 0,
        "unsupported_claim_rate_is_zero": float((artifact.get("quality_metrics") or {}).get("unsupported_claim_rate", -1)) == 0,
        "no_runtime_warning": not artifact.get("warnings"),
        "no_runtime_error": not artifact.get("errors"),
        "network_evidence_is_scope_relevant": network.get("matched_company_fact_ids") == ["risk.data_security"],
        "answer_has_four_labels": all(label in answer for label in ("直接适用", "条件适用", "明确不适用", "证据不足")),
        "guidance_does_not_conflate_relevance_and_applicability": "直接适用/直接相关" not in answer and "不产生强制义务" in answer,
        "answer_hides_local_paths": not re.search(r"[A-Za-z]:\\", answer),
        "context_is_bounded": all(
            len(item.get("policy_spans") or []) <= 4 and len(item.get("company_facts") or []) <= 6
            for item in context
        ),
        "trace_has_two_subagent_calls": len(model_steps) == 2,
    }
    return {
        "iteration": iteration,
        "session_id": session_id,
        "top_run_id": result.get("run_id"),
        "workflow_run_id": artifact.get("run_id"),
        "e2e_ms": round(e2e_ms, 2),
        "applicability_input_tokens": effective_input_tokens,
        "applicability_uncached_input_tokens": uncached_input_tokens,
        "applicability_cache_read_input_tokens": cache_read_input_tokens,
        "applicability_cache_creation_input_tokens": cache_creation_input_tokens,
        "applicability_output_tokens": int(usage.get("output_tokens") or 0),
        "applicability_latency_ms": round(float(applicability_meta.get("latency_ms") or 0), 2),
        "model_latency_total_ms": round(
            sum(float((step.get("metadata") or {}).get("latency_ms") or 0) for step in model_steps),
            2,
        ),
        "stage_durations_ms": {
            str(stage.get("stage") or ""): int(stage.get("duration_ms") or 0)
            for stage in artifact.get("stage_trace") or []
        },
        "classifications": actual,
        "network_company_fact_ids": network.get("matched_company_fact_ids") or [],
        "quality_metrics": artifact.get("quality_metrics") or {},
        "checks": checks,
        "passed": all(checks.values()),
    }


def _median(rows: list[dict], key: str) -> float:
    return round(statistics.median(float(row[key]) for row in rows), 2)


def _improvement(before: float, after: float) -> float:
    return round((before - after) / before * 100, 2) if before else 0.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    runs = [_run_once(index) for index in range(1, max(1, args.runs) + 1)]
    current = {
        "sample_count": len(runs),
        "e2e_median_ms": _median(runs, "e2e_ms"),
        "applicability_input_tokens_median": _median(runs, "applicability_input_tokens"),
        "applicability_latency_median_ms": _median(runs, "applicability_latency_ms"),
        "all_passed": all(run["passed"] for run in runs),
    }
    comparison = {
        "e2e_improvement_percent": _improvement(BASELINE["e2e_ms"], current["e2e_median_ms"]),
        "input_token_reduction_percent": _improvement(
            BASELINE["applicability_input_tokens"],
            current["applicability_input_tokens_median"],
        ),
        "applicability_latency_improvement_percent": _improvement(
            BASELINE["applicability_latency_ms"],
            current["applicability_latency_median_ms"],
        ),
    }
    payload = {
        "schema_version": "live_policy_benchmark.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": BASE_URL,
        "model_id": MODEL_ID,
        "prompt": PROMPT,
        "baseline": BASELINE,
        "current": current,
        "comparison": comparison,
        "runs": runs,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = OUT_DIR / f"live_policy_benchmark_{timestamp}.json"
    latest = OUT_DIR / "latest_live_policy_benchmark.json"
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    output.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")
    print(json.dumps({"output": str(output), **current, **comparison}, ensure_ascii=False, indent=2))
    return 0 if current["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
