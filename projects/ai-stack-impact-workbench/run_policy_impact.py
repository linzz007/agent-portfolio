"""Run the local policy impact MVP pipeline."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from policy_impact.skill_executors.policy_weekly_impact import run_policy_weekly_impact


if __name__ == "__main__":
    result = run_policy_weekly_impact(company_id="company_001")
    print(result.summary)
    print(f"report: {result.report_path}")
    print(f"run_artifact: {result.run_artifact_path}")
