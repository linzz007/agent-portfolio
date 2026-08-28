"""Run a dry replay of the latest persisted policy impact checkpoint."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from policy_impact.harness.replay import dry_replay_checkpoint


def latest_checkpoint() -> Path | None:
    checkpoints_dir = ROOT / "data" / "checkpoints"
    candidates = list(checkpoints_dir.glob("*/*.checkpoint.json"))
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def write_latest_replay_report(report: dict, root: Path = ROOT) -> Path:
    output_path = root / "data" / "replay" / "latest_replay_report.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


if __name__ == "__main__":
    checkpoint_path = latest_checkpoint()
    if checkpoint_path is None:
        print("No checkpoint found under data/checkpoints/*/*.checkpoint.json", file=sys.stderr)
        raise SystemExit(1)

    replay = dry_replay_checkpoint(checkpoint_path)
    write_latest_replay_report(replay)
    print(json.dumps(replay, ensure_ascii=False, indent=2))
