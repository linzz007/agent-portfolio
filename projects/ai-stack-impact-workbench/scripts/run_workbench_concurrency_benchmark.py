"""Benchmark concurrent real-model Workbench turns through the public API."""

from __future__ import annotations

import json
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib import request


BASE_URL = os.getenv("WORKBENCH_BASE_URL", "http://127.0.0.1:8501")
COMPANY_ID = "company_001"
MODEL_ID = "deepseek-v4-flash"
ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "acceptance"
WIDTHS = (1, 2, 4)
CASES = (
    ("示例公司股票代码和公司全称是什么？请标出 fact_id。", ("300033", "profile.identity")),
    ("示例产品主要服务哪类用户？请标出 fact_id。", ("个人投资者", "ai_product.wencai")),
    ("iFinD 主要服务哪类客户？请标出 fact_id。", ("金融机构", "ai_product.ifind")),
    (
        "能否确认示例产品或 iFinD 已使用 RAG？证据不足要明确说明并标出 fact_id。",
        ("无法确认", "ai_product.rag_and_knowledge"),
    ),
)


def http(method: str, path: str, payload: dict | None = None, timeout: float = 120) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = request.Request(
        f"{BASE_URL}{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def create_session(width: int, index: int) -> str:
    session = http(
        "POST",
        f"/companies/{COMPANY_ID}/workbench/sessions",
        {
            "title": f"Benchmark - concurrency {width} - {index + 1}",
            "mode": "chat",
            "model_id": MODEL_ID,
            "active_skill_id": "general_chat",
        },
    )
    return str(session["session_id"])


def run_turn(session_id: str, prompt: str, required_terms: tuple[str, ...]) -> dict:
    started = time.perf_counter()
    try:
        result = http(
            "POST",
            f"/companies/{COMPANY_ID}/workbench/sessions/{session_id}/messages",
            {"message": f"只基于企业知识库回答：{prompt}"},
        )
        trace = http(
            "GET",
            f"/companies/{COMPANY_ID}/workbench/sessions/{session_id}/runs/{result['run_id']}/trace",
        )
        answer = str(result.get("answer") or "")
        model_summary = trace.get("model_summary") or {}
        checks = {
            "answer_terms": all(term in answer for term in required_terms),
            "trace_done": trace.get("status") == "done",
            "model_fixed": trace.get("model_id") == MODEL_ID,
            "one_model_call": int(model_summary.get("call_count") or 0) == 1,
            "context_manifest": bool(trace.get("context_summary")),
        }
        return {
            "session_id": session_id,
            "run_id": result.get("run_id"),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            "answer": answer,
            "checks": checks,
            "model_summary": model_summary,
            "context_summary": trace.get("context_summary") or {},
            "passed": all(checks.values()),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "session_id": session_id,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            "error": repr(exc),
            "passed": False,
        }


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[int((len(ordered) - 1) * fraction)]


def run_wave(width: int) -> dict:
    sessions = [create_session(width, index) for index in range(width)]
    wave_started = time.perf_counter()
    results = []
    with ThreadPoolExecutor(max_workers=width) as executor:
        futures = {
            executor.submit(run_turn, session_id, *CASES[index % len(CASES)]): index
            for index, session_id in enumerate(sessions)
        }
        for future in as_completed(futures):
            result = future.result()
            result["worker_index"] = futures[future]
            results.append(result)
    wave_ms = round((time.perf_counter() - wave_started) * 1000, 2)
    latencies = [float(item.get("elapsed_ms") or 0) for item in results]
    return {
        "concurrency": width,
        "total": len(results),
        "passed": sum(1 for item in results if item.get("passed")),
        "failed": sum(1 for item in results if not item.get("passed")),
        "wave_elapsed_ms": wave_ms,
        "throughput_rps": round(width / max(wave_ms / 1000, 0.001), 3),
        "avg_latency_ms": round(statistics.fmean(latencies), 2),
        "p50_latency_ms": round(percentile(latencies, 0.50), 2),
        "p95_latency_ms": round(percentile(latencies, 0.95), 2),
        "max_latency_ms": round(max(latencies, default=0), 2),
        "results": sorted(results, key=lambda item: int(item.get("worker_index") or 0)),
    }


def main() -> int:
    started = time.perf_counter()
    waves = [run_wave(width) for width in WIDTHS]
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model_id": MODEL_ID,
        "widths": list(WIDTHS),
        "total": sum(item["total"] for item in waves),
        "passed": sum(item["passed"] for item in waves),
        "failed": sum(item["failed"] for item in waves),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "waves": waves,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    path = OUT_DIR / f"workbench_concurrency_benchmark_{timestamp}.json"
    latest = OUT_DIR / "latest_workbench_concurrency_benchmark.json"
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(serialized, encoding="utf-8")
    latest.write_text(serialized, encoding="utf-8")
    print(path)
    print(
        json.dumps(
            {
                "total": payload["total"],
                "passed": payload["passed"],
                "failed": payload["failed"],
                "waves": [
                    {
                        key: wave[key]
                        for key in (
                            "concurrency",
                            "passed",
                            "failed",
                            "wave_elapsed_ms",
                            "throughput_rps",
                            "p95_latency_ms",
                        )
                    }
                    for wave in waves
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if payload["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
