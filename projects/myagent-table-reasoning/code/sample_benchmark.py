"""Create deterministic, table-diverse benchmark samples from adapted JSONL."""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List


def _scalar_answer(record: Dict[str, Any]) -> str:
    value = record.get("answer")
    if isinstance(value, (list, tuple)) and value:
        value = value[0]
    return "" if value is None else str(value).strip().lower()


def categorize_record(record: Dict[str, Any], dataset: str) -> str:
    dataset = dataset.lower()
    if dataset == "tabfact":
        label = record.get("label")
        if str(label).strip().lower() in {"1", "true", "yes"}:
            return "true"
        if str(label).strip().lower() in {"0", "false", "no"}:
            return "false"
        return "true" if _scalar_answer(record) in {"1", "true", "yes"} else "false"
    if dataset == "crt":
        question = str(record.get("question") or record.get("statement") or "")
        if re.search(
            r"answer\s+with\s+only\b.*?['\"]yes['\"].*?['\"]no['\"]",
            question,
            flags=re.I,
        ):
            return "yes_no"
        if re.search(r"answer\s+with\s+only\b", question, flags=re.I):
            return "closed_other"
        return "general"
    return "all"


def _table_key(record: Dict[str, Any], index: int) -> str:
    return str(
        record.get("table_id")
        or record.get("source_table_id")
        or record.get("table")
        or record.get("id")
        or index
    )


def select_records(
    records: Iterable[Dict[str, Any]],
    sample_size: int,
    seed: int,
    dataset: str,
) -> List[Dict[str, Any]]:
    indexed = list(enumerate(records))
    if sample_size < 1:
        raise ValueError("sample_size must be at least 1")
    if sample_size > len(indexed):
        raise ValueError(
            f"sample_size {sample_size} exceeds available records {len(indexed)}"
        )

    rng = random.Random(seed)
    by_category: Dict[str, List[tuple[int, Dict[str, Any]]]] = defaultdict(list)
    for index, record in indexed:
        by_category[categorize_record(record, dataset)].append((index, record))
    for rows in by_category.values():
        rng.shuffle(rows)

    categories = sorted(by_category)
    rng.shuffle(categories)
    selected: List[tuple[int, Dict[str, Any]]] = []
    selected_indexes = set()
    used_tables = set()

    while len(selected) < sample_size:
        progress = False
        for category in categories:
            candidate = next(
                (
                    item
                    for item in by_category[category]
                    if item[0] not in selected_indexes
                    and _table_key(item[1], item[0]) not in used_tables
                ),
                None,
            )
            if candidate is None:
                continue
            index, record = candidate
            selected.append(candidate)
            selected_indexes.add(index)
            used_tables.add(_table_key(record, index))
            progress = True
            if len(selected) == sample_size:
                break
        if not progress:
            break

    while len(selected) < sample_size:
        progress = False
        for category in categories:
            candidate = next(
                (
                    item
                    for item in by_category[category]
                    if item[0] not in selected_indexes
                ),
                None,
            )
            if candidate is None:
                continue
            selected.append(candidate)
            selected_indexes.add(candidate[0])
            progress = True
            if len(selected) == sample_size:
                break
        if not progress:
            break

    return [record for _, record in selected]


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write_jsonl(path: Path, records: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--dataset", choices=("wtq", "tabfact", "crt"), required=True)
    parser.add_argument("--sample_size", type=int, default=18)
    parser.add_argument("--seed", type=int, default=20260623)
    parser.add_argument("--summary_output", default="")
    args = parser.parse_args()

    records = load_jsonl(Path(args.input))
    sample = select_records(records, args.sample_size, args.seed, args.dataset)
    output_path = Path(args.output)
    write_jsonl(output_path, sample)

    category_counts = Counter(categorize_record(row, args.dataset) for row in sample)
    table_count = len({_table_key(row, index) for index, row in enumerate(sample)})
    summary = {
        "dataset": args.dataset,
        "seed": args.seed,
        "records": len(sample),
        "unique_tables": table_count,
        "category_counts": dict(category_counts),
        "output": str(output_path),
    }
    if args.summary_output:
        summary_path = Path(args.summary_output)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
