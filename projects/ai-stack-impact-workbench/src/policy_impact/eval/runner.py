"""Deterministic keyword-based eval runner."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "eval_report.v1"
RUNNER_NAME = "policy_impact.keyword_eval"
RUNNER_VERSION = "0.1.0"
DEFAULT_THRESHOLD = 100


class EvalSuiteError(ValueError):
    """Raised when an eval suite cannot be loaded."""


def _load_cases(path: str | Path) -> list[dict[str, Any]]:
    suite_path = Path(path)
    cases: list[dict[str, Any]] = []

    with suite_path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if stripped:
                try:
                    loaded = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    raise EvalSuiteError(
                        f"Malformed eval suite JSONL at {suite_path} line {line_number}: {exc.msg}"
                    ) from exc
                if not isinstance(loaded, dict):
                    raise EvalSuiteError(
                        f"Malformed eval suite JSONL at {suite_path} line {line_number}: "
                        "expected JSON object"
                    )
                cases.append(loaded)

    return cases


def _keyword_hits(report_text: str, keywords: list[str]) -> list[str]:
    normalized_text = report_text.lower()
    return [keyword for keyword in keywords if keyword.lower() in normalized_text]


def score_case(case: dict[str, Any]) -> dict[str, Any]:
    report_text = str(case.get("report_text", ""))
    expected_keywords = list(case.get("expected_keywords", []))
    forbidden_keywords = list(case.get("forbidden_keywords", []))

    present_expected = _keyword_hits(report_text, expected_keywords)
    forbidden_hits = _keyword_hits(report_text, forbidden_keywords)
    missing_keywords = [keyword for keyword in expected_keywords if keyword not in present_expected]

    return {
        "case_id": case.get("case_id"),
        "passed": not missing_keywords and not forbidden_hits,
        "missing_keywords": missing_keywords,
        "forbidden_keywords": forbidden_hits,
    }


def run_eval_suite(path: str | Path) -> dict[str, Any]:
    suite_path = Path(path)
    cases = _load_cases(suite_path)
    results = [score_case(case) for case in cases]

    case_count = len(results)
    passed_count = sum(1 for result in results if result["passed"])
    failed_count = case_count - passed_count
    forbidden_case_count = sum(1 for result in results if result["forbidden_keywords"])

    if case_count:
        forbidden_case_rate = forbidden_case_count / case_count
        score = int((passed_count / case_count) * 100)
    else:
        forbidden_case_rate = 0.0
        score = 0

    return {
        "schema_version": SCHEMA_VERSION,
        "runner": RUNNER_NAME,
        "runner_version": RUNNER_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "suite_id": suite_path.stem,
        "suite_version": "unversioned",
        "suite_path": str(suite_path),
        "suite_type": "keyword_fixture",
        "threshold": DEFAULT_THRESHOLD,
        "passed": case_count > 0 and score >= DEFAULT_THRESHOLD,
        "case_count": case_count,
        "passed_count": passed_count,
        "failed_count": failed_count,
        "forbidden_case_rate": forbidden_case_rate,
        "unsupported_claim_rate": forbidden_case_rate,
        "metric_notes": {
            "forbidden_case_rate": (
                "Cases with at least one forbidden keyword hit divided by case_count."
            ),
            "unsupported_claim_rate": "Compatibility alias for forbidden_case_rate.",
            "suite_type": (
                "Deterministic keyword fixture using hand-written report_text; "
                "not end-to-end agent performance."
            ),
        },
        "score": score,
        "results": results,
    }
