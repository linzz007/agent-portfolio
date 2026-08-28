"""Run multi-turn CoursePilot conversation scenarios."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.harness.conversation_eval import (  # noqa: E402
    load_scenarios,
    make_llm_judge,
    run_scenario,
    summarize_results,
)
from core.harness.runtime import HarnessRuntime  # noqa: E402
from core.orchestration.runner import OrchestrationRunner  # noqa: E402


def _write_markdown(path: Path, report: Dict[str, Any]) -> None:
    summary = report.get("summary", {})
    lines: List[str] = [
        "# CoursePilot Conversation Eval",
        "",
        f"- total: {summary.get('total', 0)}",
        f"- passed: {summary.get('passed', 0)}",
        f"- failed: {summary.get('failed', 0)}",
        f"- turn_total: {summary.get('turn_total', 0)}",
        f"- turn_failed: {summary.get('turn_failed', 0)}",
        "",
        "| case_id | mode | passed | failed checks |",
        "|---|---|---:|---|",
    ]
    for case in report.get("results", []):
        failed_checks: List[str] = []
        for turn in case.get("turns", []):
            rule_result = turn.get("rule_result", {})
            for check in rule_result.get("checks", []):
                if not check.get("passed"):
                    failed_checks.append(f"turn{turn.get('turn_index')}:{check.get('name')}")
        lines.append(
            f"| `{case.get('case_id')}` | `{case.get('mode')}` | {int(bool(case.get('passed')))} | "
            f"{', '.join(failed_checks) if failed_checks else 'none'} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate CoursePilot multi-turn conversation flows.")
    parser.add_argument(
        "--scenario-file",
        default=str(ROOT / "benchmarks" / "eval_scenarios_v1.jsonl"),
        help="JSONL scenario file.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "data" / "eval" / "conversations" / datetime.now().strftime("%Y%m%d_%H%M%S")),
        help="Directory for JSON/Markdown reports.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Limit number of scenarios.")
    parser.add_argument("--json", action="store_true", help="Print full report JSON to stdout.")
    parser.add_argument("--llm-judge", action="store_true", help="Attach optional LLM Judge results.")
    parser.add_argument("--no-fail", action="store_true", help="Always exit 0 even if scenarios fail.")
    args = parser.parse_args()

    scenarios = load_scenarios(args.scenario_file)
    if args.limit and args.limit > 0:
        scenarios = scenarios[: args.limit]

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    runtime = HarnessRuntime(OrchestrationRunner())
    judge = make_llm_judge() if args.llm_judge else None
    results = [run_scenario(runtime, scenario, judge=judge) for scenario in scenarios]
    report = {
        "scenario_file": str(Path(args.scenario_file)),
        "llm_judge": bool(args.llm_judge),
        "summary": summarize_results(results),
        "results": results,
    }

    json_path = out_dir / "conversation_eval_report.json"
    md_path = out_dir / "conversation_eval_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(md_path, report)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        summary = report["summary"]
        print(
            "Evaluated {total} scenario(s): {passed} passed, {failed} failed, "
            "{turn_failed}/{turn_total} failed turn(s).".format(**summary)
        )
        print(f"Report JSON: {json_path}")
        print(f"Report MD: {md_path}")

    if report["summary"].get("failed", 0) and not args.no_fail:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

