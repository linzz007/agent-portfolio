from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from policy_impact.eval.runner import run_eval_suite

GOLD_SUITE_PATH = ROOT / "data" / "eval" / "gold_policy_impact.jsonl"
LATEST_REPORT_PATH = ROOT / "data" / "eval" / "reports" / "latest_eval_report.json"


def _resolve_suite_path(suite: str) -> Path:
    if suite == "gold":
        return GOLD_SUITE_PATH

    suite_path = Path(suite)
    if suite_path.is_absolute():
        return suite_path

    project_relative = ROOT / suite_path
    if project_relative.exists():
        return project_relative

    cwd_relative = Path.cwd() / suite_path
    if cwd_relative.exists():
        return cwd_relative

    raise FileNotFoundError(
        f"Eval suite path not found under project root or current directory: {suite}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic policy impact evals.")
    parser.add_argument("--suite", default="gold", help="Use 'gold' or pass a JSONL suite path.")
    args = parser.parse_args()

    report = run_eval_suite(_resolve_suite_path(args.suite))
    LATEST_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    LATEST_REPORT_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
