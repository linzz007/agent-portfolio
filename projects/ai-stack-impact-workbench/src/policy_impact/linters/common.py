"""Shared linter result helper."""

from __future__ import annotations

from typing import Any

from policy_impact.harness.state import utc_now_iso


def lint_result(stage_name: str, passed: bool, issues: list[str], metrics: dict[str, Any], retryable: bool) -> dict[str, Any]:
    return {
        "stage": stage_name,
        "passed": passed,
        "issues": issues,
        "metrics": metrics,
        "retryable": retryable,
        "checked_at": utc_now_iso(),
    }
