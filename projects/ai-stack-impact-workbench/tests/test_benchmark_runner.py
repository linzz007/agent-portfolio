import json
import importlib.util
from pathlib import Path

from policy_impact.benchmark.runner import (
    compare_metrics,
    summarize_runs,
    write_benchmark_report,
)


ROOT = Path(__file__).resolve().parents[1]


def _load_run_benchmark_module():
    spec = importlib.util.spec_from_file_location(
        "run_benchmark",
        ROOT / "scripts" / "run_benchmark.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def test_summarize_runs_computes_averages_and_success_rate():
    runs = [
        {
            "success": True,
            "latency_ms": 100,
            "context_tokens": 10,
            "tool_calls": 2,
            "unsupported_claims": 0,
        },
        {
            "success": False,
            "latency_ms": 300,
            "context_tokens": 30,
            "tool_calls": 4,
            "unsupported_claims": 1,
        },
    ]

    summary = summarize_runs(runs)

    assert summary == {
        "run_count": 2,
        "success_rate": 0.5,
        "latency_ms": 200.0,
        "context_tokens": 20.0,
        "tool_calls": 3.0,
        "unsupported_claim_rate": 0.5,
        "unsupported_claims_avg": 0.5,
    }


def test_summarize_runs_bounds_unsupported_claim_rate_and_keeps_count_metric():
    runs = [
        {
            "success": True,
            "latency_ms": 100,
            "context_tokens": 0,
            "tool_calls": 1,
            "unsupported_claims": 3,
            "claim_count": 2,
        },
        {
            "success": True,
            "latency_ms": 100,
            "context_tokens": 0,
            "tool_calls": 1,
            "unsupported_claims": 1,
            "assessment_count": 4,
        },
        {
            "success": True,
            "latency_ms": 100,
            "context_tokens": 0,
            "tool_calls": 1,
            "unsupported_claims": 10,
            "unsupported_claim_rate": 0.2,
            "claim_count": 1,
        },
    ]

    summary = summarize_runs(runs)

    assert summary["unsupported_claim_rate"] == (1.0 + 0.25 + 0.2) / 3
    assert summary["unsupported_claim_rate"] <= 1.0
    assert summary["unsupported_claims_avg"] == (3 + 1 + 10) / 3


def test_summarize_runs_empty_returns_zero_metrics():
    assert summarize_runs([]) == {
        "run_count": 0,
        "success_rate": 0.0,
        "latency_ms": 0.0,
        "context_tokens": 0.0,
        "tool_calls": 0.0,
        "unsupported_claim_rate": 0.0,
        "unsupported_claims_avg": 0.0,
    }


def test_compare_metrics_reports_delta_and_safe_percent_delta():
    comparison = compare_metrics(
        {"latency_ms": 100.0, "context_tokens": 0.0, "run_count": 2},
        {"latency_ms": 125.0, "context_tokens": 10.0, "run_count": 2},
    )

    assert comparison["latency_ms"] == {
        "baseline": 100.0,
        "target": 125.0,
        "delta": 25.0,
        "delta_percent": 25.0,
    }
    assert comparison["context_tokens"] == {
        "baseline": 0.0,
        "target": 10.0,
        "delta": 10.0,
        "delta_percent": None,
    }
    assert comparison["run_count"] == {
        "baseline": 2,
        "target": 2,
        "delta": 0,
        "delta_percent": 0.0,
    }


def test_write_benchmark_report_writes_stable_schema_fields(tmp_path):
    baseline_runs = [
        {
            "success": True,
            "latency_ms": 100,
            "context_tokens": 10,
            "tool_calls": 2,
            "unsupported_claims": 0,
        }
    ]
    target_runs = [
        {
            "success": True,
            "latency_ms": 90,
            "context_tokens": 8,
            "tool_calls": 1,
            "unsupported_claims": 0,
        }
    ]

    report = write_benchmark_report(
        tmp_path / "report.json",
        baseline_runs,
        target_runs,
        baseline_name="b0",
        target_name="harness",
    )

    payload = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert payload == report
    assert payload["schema_version"] == "benchmark_report.v1"
    assert payload["generated_at"]
    assert payload["baseline_name"] == "b0"
    assert payload["target_name"] == "harness"
    assert payload["baseline"]["run_count"] == 1
    assert payload["target"]["run_count"] == 1
    assert payload["comparison"]["latency_ms"]["baseline"] == 100.0
    assert payload["baseline_runs"] == baseline_runs
    assert payload["target_runs"] == target_runs


def test_write_benchmark_report_includes_observational_provenance_and_notes(tmp_path):
    baseline_runs = [
        {
            "success": True,
            "latency_ms": 100,
            "context_tokens": 0,
            "tool_calls": 2,
            "unsupported_claims": 0,
        }
    ]
    target_runs = [
        {
            "success": True,
            "latency_ms": 90,
            "context_tokens": 0,
            "tool_calls": 2,
            "unsupported_claims": 0,
        }
    ]

    report = write_benchmark_report(
        tmp_path / "report.json",
        baseline_runs,
        target_runs,
        baseline_name="b0",
        target_name="harness",
        baseline_capture_mode="loaded_baseline_file",
        target_capture_mode="measured_current_pipeline",
    )

    assert report["benchmark_type"] == "observational_pipeline_runs"
    assert report["baseline_capture_mode"] == "loaded_baseline_file"
    assert report["target_capture_mode"] == "measured_current_pipeline"
    assert report["provenance"]["baseline_name"] == "b0"
    assert report["provenance"]["target_name"] == "harness"
    assert report["provenance"]["optimization_proof"] is False
    assert any("observational" in caveat for caveat in report["caveats"])
    assert any("not optimization proof" in caveat for caveat in report["caveats"])
    assert report["metric_notes"]["context_tokens_status"] == "not_integrated_or_zero"
    assert "unsupported_claim_rate" in report["metric_notes"]


def test_missing_baseline_loader_requires_explicit_capture(tmp_path):
    run_benchmark = _load_run_benchmark_module()

    missing_path = tmp_path / "missing_baseline_runs.json"
    try:
        run_benchmark._load_baseline_runs(missing_path, "missing")
    except FileNotFoundError as exc:
        assert "--capture-baseline" in str(exc)
        assert str(missing_path) in str(exc)
    else:
        raise AssertionError("missing baseline should require explicit capture")
