"""Select stable selective-collaboration thresholds from development rows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List


LIGHT_THRESHOLDS = (0.20, 0.25, 0.30)
HIGH_THRESHOLDS = (0.50, 0.55, 0.60)


def _float(row: Dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(row.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def _bool(row: Dict[str, Any], key: str) -> bool:
    return bool(row.get(key))


def _selected_tokens(row: Dict[str, Any], light_threshold: float, high_threshold: float) -> float:
    risk = _float(row, "risk")
    if risk < light_threshold:
        return _float(row, "light_tokens")
    if risk < high_threshold:
        return _float(row, "medium_tokens")
    return _float(row, "high_tokens")


def choose_policy(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    clean_rows = [
        {
            "risk": _float(row, "risk"),
            "my_correct": _bool(row, "my_correct"),
            "mact_tokens": _float(row, "mact_tokens"),
            "light_tokens": _float(row, "light_tokens"),
            "medium_tokens": _float(row, "medium_tokens"),
            "high_tokens": _float(row, "high_tokens"),
        }
        for row in rows
    ]
    if not clean_rows:
        return {
            "light_threshold": 0.25,
            "high_threshold": 0.55,
            "avg_token_ratio": 0.0,
            "estimated_accuracy": 0.0,
            "row_count": 0,
        }

    best: Dict[str, Any] | None = None
    best_key: tuple[float, float, float] | None = None
    for light_threshold in LIGHT_THRESHOLDS:
        for high_threshold in HIGH_THRESHOLDS:
            if light_threshold >= high_threshold:
                continue
            selected_total = sum(
                _selected_tokens(row, light_threshold, high_threshold)
                for row in clean_rows
            )
            mact_total = sum(_float(row, "mact_tokens") for row in clean_rows)
            avg_token_ratio = selected_total / mact_total if mact_total else 0.0
            if avg_token_ratio > 0.75:
                continue
            estimated_accuracy = sum(1 for row in clean_rows if row["my_correct"]) / len(clean_rows)
            key = (estimated_accuracy, -avg_token_ratio, -high_threshold)
            if best_key is None or key > best_key:
                best_key = key
                best = {
                    "light_threshold": light_threshold,
                    "high_threshold": high_threshold,
                    "avg_token_ratio": avg_token_ratio,
                    "estimated_accuracy": estimated_accuracy,
                    "row_count": len(clean_rows),
                }

    if best is not None:
        return best

    # No threshold pair satisfies the token budget; return the least expensive pair as a failed calibration.
    fallback_light = max(LIGHT_THRESHOLDS)
    fallback_high = min(HIGH_THRESHOLDS)
    selected_total = sum(
        _selected_tokens(row, fallback_light, fallback_high)
        for row in clean_rows
    )
    mact_total = sum(_float(row, "mact_tokens") for row in clean_rows)
    return {
        "light_threshold": fallback_light,
        "high_threshold": fallback_high,
        "avg_token_ratio": selected_total / mact_total if mact_total else 0.0,
        "estimated_accuracy": sum(1 for row in clean_rows if row["my_correct"]) / len(clean_rows),
        "row_count": len(clean_rows),
        "budget_satisfied": False,
    }


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="JSONL development rows with risk/token fields.")
    parser.add_argument("--output", required=True, help="Path to write the selected policy JSON.")
    args = parser.parse_args()

    policy = choose_policy(_load_jsonl(Path(args.input)))
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(policy, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(policy, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
