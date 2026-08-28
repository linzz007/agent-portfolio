"""Render a CoursePilot RunArtifact JSON file as a static HTML page."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.harness.html_viewer import render_run_artifact_file  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Render RunArtifact JSON to HTML.")
    parser.add_argument("input", help="Path to one run_*.json artifact")
    parser.add_argument("-o", "--output", help="Output HTML path. Defaults to input with .html suffix.")
    args = parser.parse_args()

    out = render_run_artifact_file(args.input, args.output)
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

