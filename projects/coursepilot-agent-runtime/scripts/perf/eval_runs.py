"""Evaluate RunArtifact JSON files with deterministic harness checks."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.harness.evaluation import evaluate_artifact, summarize_evaluations  # noqa: E402


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def _artifact_paths(target: Path) -> List[Path]:
    if target.is_file():
        return [target]
    return sorted(path for path in target.rglob("*.json") if path.is_file())


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate CoursePilot RunArtifact files.")
    parser.add_argument("target", help="RunArtifact JSON file or directory")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args()

    target = Path(args.target)
    paths = _artifact_paths(target)
    if not paths:
        print(f"No RunArtifact JSON files found under {target}", file=sys.stderr)
        return 1

    rows = []
    results = []
    for path in paths:
        artifact = _load_json(path)
        result = evaluate_artifact(artifact)
        results.append(result)
        rows.append(
            {
                "path": str(path),
                "run_id": artifact.get("run_id"),
                "verdict": result.get("verdict"),
                "scores": result.get("scores", {}),
                "reasons": result.get("reasons", []),
            }
        )

    report = {
        "summary": summarize_evaluations(results),
        "runs": rows,
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        summary = report["summary"]
        print(f"Evaluated {summary['total']} RunArtifact file(s)")
        print(f"Verdicts: {summary['verdict_counts']}")
        print(f"Average scores: {summary['average_scores']}")
        for row in rows:
            reasons = ", ".join(row["reasons"]) if row["reasons"] else "none"
            print(f"- {row['verdict']} {row['run_id'] or Path(row['path']).name}: {reasons}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
