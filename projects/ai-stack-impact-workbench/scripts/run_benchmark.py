from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from policy_impact.benchmark.runner import measure_policy_runs, write_benchmark_report

BENCHMARK_DIR = ROOT / "data" / "benchmark"
LATEST_REPORT_PATH = BENCHMARK_DIR / "latest_benchmark_report.json"


def _baseline_runs_path(name: str) -> Path:
    return BENCHMARK_DIR / f"{name}_baseline_runs.json"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_baseline_runs(path: Path, baseline_name: str) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(
            "Baseline runs file is missing. Run with --capture-baseline first: "
            f"baseline={baseline_name}, path={path}"
        )
    loaded = _read_json(path)
    if not isinstance(loaded, list):
        raise ValueError(f"Baseline runs file must contain a JSON list: {path}")
    return loaded


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark real policy impact harness runs.")
    parser.add_argument("--baseline", default="baseline", help="Baseline run set name.")
    parser.add_argument("--target", default="target", help="Target run set name.")
    parser.add_argument("--runs", type=int, default=1, help="Number of real runs to measure.")
    parser.add_argument("--company-id", default="company_001", help="Company id to run.")
    parser.add_argument(
        "--capture-baseline",
        action="store_true",
        help="Capture only baseline runs and write them for later comparisons.",
    )
    args = parser.parse_args()

    if args.runs < 1:
        parser.error("--runs must be >= 1")

    baseline_path = _baseline_runs_path(args.baseline)

    if args.capture_baseline:
        runs = measure_policy_runs(args.company_id, args.runs)
        _write_json(baseline_path, runs)
        print(json.dumps({"captured_baseline": args.baseline, "runs": runs}, ensure_ascii=False, indent=2))
        return 0

    try:
        baseline_runs = _load_baseline_runs(baseline_path, args.baseline)
    except (FileNotFoundError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "error": "baseline_not_available",
                    "message": str(exc),
                    "baseline": args.baseline,
                    "baseline_path": str(baseline_path),
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2

    target_runs = measure_policy_runs(args.company_id, args.runs)
    report = write_benchmark_report(
        LATEST_REPORT_PATH,
        list(baseline_runs),
        target_runs,
        baseline_name=args.baseline,
        target_name=args.target,
        baseline_capture_mode="loaded_baseline_file",
        target_capture_mode="measured_current_pipeline",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
