"""Compare paired myAgent and MACT blind-benchmark outputs."""

from __future__ import annotations

import argparse
from math import comb, sqrt
import json
from pathlib import Path
from typing import Any, Dict, List

from evaluate_results import dataset_accuracy, load_jsonl, summarize_rows


def exact_mcnemar_p(myagent_only: int, mact_only: int) -> float:
    discordant = myagent_only + mact_only
    if discordant == 0:
        return 1.0
    tail = sum(
        comb(discordant, index)
        for index in range(min(myagent_only, mact_only) + 1)
    ) / (2**discordant)
    return min(1.0, 2.0 * tail)


def wilson_interval(correct: int, total: int, z: float = 1.96) -> List[float]:
    if total == 0:
        return [0.0, 0.0]
    proportion = correct / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = (
        z
        * sqrt(
            proportion * (1 - proportion) / total
            + z * z / (4 * total * total)
        )
        / denominator
    )
    return [center - margin, center + margin]


def _index_by_id(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    indexed: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        row_id = str(row.get("id") or "")
        if not row_id or row_id in indexed:
            raise ValueError("Every paired result row must have a unique non-empty id.")
        indexed[row_id] = row
    return indexed


def compare_dataset_rows(
    myagent_rows: List[Dict[str, Any]],
    mact_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    my_index = _index_by_id(myagent_rows)
    mact_index = _index_by_id(mact_rows)
    if set(my_index) != set(mact_index):
        raise ValueError("myAgent and MACT result IDs do not match.")

    paired = {
        "both_correct": 0,
        "myagent_only": 0,
        "mact_only": 0,
        "both_wrong": 0,
    }
    for row_id in my_index:
        my_correct = dataset_accuracy(my_index[row_id])
        mact_correct = dataset_accuracy(mact_index[row_id])
        if my_correct and mact_correct:
            paired["both_correct"] += 1
        elif my_correct:
            paired["myagent_only"] += 1
        elif mact_correct:
            paired["mact_only"] += 1
        else:
            paired["both_wrong"] += 1

    my_summary, _ = summarize_rows(myagent_rows)
    mact_summary, _ = summarize_rows(mact_rows)
    total = len(myagent_rows)
    my_correct_count = round(my_summary["primary_accuracy"] * total)
    mact_correct_count = round(mact_summary["primary_accuracy"] * total)
    my_summary["correct"] = my_correct_count
    my_summary["wilson_95"] = wilson_interval(my_correct_count, total)
    mact_summary["correct"] = mact_correct_count
    mact_summary["wilson_95"] = wilson_interval(mact_correct_count, total)
    paired["exact_mcnemar_p"] = exact_mcnemar_p(
        paired["myagent_only"],
        paired["mact_only"],
    )
    token_ratio = (
        my_summary["avg_total_tokens"] / mact_summary["avg_total_tokens"]
        if mact_summary["avg_total_tokens"]
        else None
    )
    risk_accuracy_deltas = {}
    shared_risk_levels = set(my_summary.get("risk_strata", {})) & set(
        mact_summary.get("risk_strata", {})
    )
    for level in sorted(shared_risk_levels):
        risk_accuracy_deltas[level] = (
            my_summary["risk_strata"][level]["accuracy"]
            - mact_summary["risk_strata"][level]["accuracy"]
        )
    return {
        "myagent": my_summary,
        "mact": mact_summary,
        "paired": paired,
        "token_ratio_myagent_to_mact": token_ratio,
        "token_ratio_at_most_0_75": token_ratio is not None and token_ratio <= 0.75,
        "risk_accuracy_deltas": risk_accuracy_deltas,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    for system in ("myagent", "mact"):
        for dataset in ("wtq", "tabfact", "crt"):
            parser.add_argument(f"--{system}_{dataset}", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    datasets: Dict[str, Any] = {}
    all_myagent: List[Dict[str, Any]] = []
    all_mact: List[Dict[str, Any]] = []
    for dataset in ("wtq", "tabfact", "crt"):
        my_rows = load_jsonl(getattr(args, f"myagent_{dataset}"))
        mact_rows = load_jsonl(getattr(args, f"mact_{dataset}"))
        datasets[dataset] = compare_dataset_rows(my_rows, mact_rows)
        all_myagent.extend(my_rows)
        all_mact.extend(mact_rows)

    overall = compare_dataset_rows(all_myagent, all_mact)
    token_ratio = (
        overall["myagent"]["avg_total_tokens"]
        / overall["mact"]["avg_total_tokens"]
        if overall["mact"]["avg_total_tokens"]
        else None
    )
    datasets_at_least_mact = sum(
        result["myagent"]["primary_accuracy"]
        >= result["mact"]["primary_accuracy"]
        for result in datasets.values()
    )
    execution_failure_rate = overall["myagent"]["num_failed_exec"] / max(
        1, overall["myagent"]["num_samples"]
    )
    criteria = {
        "overall_accuracy_at_least_mact": (
            overall["myagent"]["primary_accuracy"]
            >= overall["mact"]["primary_accuracy"]
        ),
        "at_least_two_datasets_at_least_mact": datasets_at_least_mact >= 2,
        "token_ratio_at_most_0_75": token_ratio is not None and token_ratio <= 0.75,
        "execution_failure_rate_at_most_0_02": execution_failure_rate <= 0.02,
    }
    criteria["acceptance_selective_risk_collaboration"] = all(criteria.values())
    report = {
        "datasets": datasets,
        "overall": overall,
        "token_ratio_myagent_to_mact": token_ratio,
        "risk_accuracy_deltas": overall.get("risk_accuracy_deltas", {}),
        "myagent_execution_failure_rate": execution_failure_rate,
        "datasets_myagent_at_least_mact": datasets_at_least_mact,
        "acceptance_criteria": criteria,
        "accepted": all(criteria.values()),
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
