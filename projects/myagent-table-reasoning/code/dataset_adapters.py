"""Dataset adapters for MACT/myAgent table QA experiments.

The project runners expect each MACT-style sample to contain at least:
`statement` or `question`, `table_text` as a list of table rows, and `answer`
as a list of acceptable answers. This module converts the local WTQ and
TabFact-style datasets into that shape and reports the current CRT-QA copy's
missing table CSV files explicitly.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional


Record = Dict[str, Any]


def _clean_cell(value: Any) -> str:
    return "" if value is None else str(value).replace("\n", " ").strip()


def _normalize_row(row: List[str], width: int) -> List[str]:
    cleaned = [_clean_cell(cell) for cell in row]
    if len(cleaned) < width:
        return cleaned + [""] * (width - len(cleaned))
    if len(cleaned) > width:
        return cleaned[: width - 1] + [" ".join(cleaned[width - 1 :])]
    return cleaned


def _deduplicate_header(header: List[str]) -> List[str]:
    result: List[str] = []
    used = set()
    counts: Dict[str, int] = {}
    for index, raw_name in enumerate(header):
        base = _clean_cell(raw_name) or f"column_{index + 1}"
        counts[base] = counts.get(base, 0) + 1
        candidate = base if counts[base] == 1 else f"{base}_{counts[base]}"
        while candidate in used:
            counts[base] += 1
            candidate = f"{base}_{counts[base]}"
        result.append(candidate)
        used.add(candidate)
    return result


def read_table_csv(path: Path, delimiter: Optional[str] = None) -> List[List[str]]:
    """Read a CSV-like table and return MACT list-of-lists table_text."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Missing table file: {path}")

    first_line = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()[0]
    if delimiter is None:
        delimiter = "#" if first_line.count("#") > first_line.count(",") else ","

    rows: List[List[str]] = []
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.reader(f, delimiter=delimiter)
        for row in reader:
            if row and any(_clean_cell(cell) for cell in row):
                rows.append([_clean_cell(cell) for cell in row])

    if not rows:
        raise ValueError(f"Empty table file: {path}")

    width = max(1, len(rows[0]))
    normalized = [_normalize_row(row, width) for row in rows]
    normalized[0] = _deduplicate_header(normalized[0])
    return normalized


def _answer_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [_clean_cell(item) for item in value if _clean_cell(item)]
    text = _clean_cell(value)
    return [text] if text else []


def _wtq_value_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [_clean_cell(item) for item in value if _clean_cell(item)]
    text = "" if value is None else str(value)
    values = []
    for item in text.split("|"):
        unescaped = item.replace(r"\n", "\n").replace(r"\p", "|")
        cleaned = _clean_cell(unescaped)
        if cleaned:
            values.append(cleaned)
    return values


def _load_wtq_canonical(root: Path, split: str) -> Dict[str, Dict[str, List[str]]]:
    tagged_path = root / "tagged" / "data" / f"{split}.tagged"
    if not tagged_path.exists():
        return {}
    result: Dict[str, Dict[str, List[str]]] = {}
    with tagged_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            record_id = _clean_cell(row.get("id"))
            if not record_id:
                continue
            result[record_id] = {
                "answer_canonical": _wtq_value_list(row.get("targetCanon")),
                "answer_canonical_type": _wtq_value_list(row.get("targetCanonType")),
            }
    return result


def iter_wtq_records(root: Path, split: str = "training", limit: Optional[int] = None) -> Iterator[Record]:
    """Yield WikiTableQuestions records in MACT JSONL shape."""
    root = Path(root)
    data_path = root / "data" / f"{split}.tsv"
    if not data_path.exists():
        raise FileNotFoundError(f"Missing WTQ split file: {data_path}")

    canonical_by_id = _load_wtq_canonical(root, split)
    yielded = 0
    with data_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            table_rel = row.get("context") or row.get("table_path") or ""
            table_path = root / table_rel
            table_text = read_table_csv(table_path)
            question = _clean_cell(row.get("utterance") or row.get("question"))
            record_id = _clean_cell(row.get("id")) or f"wtq-{yielded}"
            canonical = canonical_by_id.get(record_id, {})
            yield {
                "id": record_id,
                "source_dataset": "wtq",
                "question": question,
                "statement": question,
                "table_text": table_text,
                "answer": _wtq_value_list(row.get("targetValue") or row.get("answer")),
                "answer_canonical": canonical.get("answer_canonical", []),
                "answer_canonical_type": canonical.get("answer_canonical_type", []),
                "table_id": table_rel,
            }
            yielded += 1
            if limit is not None and yielded >= limit:
                break


def _tabfact_answer(label: str) -> List[str]:
    label = _clean_cell(label)
    if label == "1":
        return ["true"]
    if label == "0":
        return ["false"]
    return [label] if label else []


def _load_tabfact_entities(root: Path, split: str) -> Dict[str, str]:
    source_split = {
        "training": "train",
        "train": "train",
        "dev": "val",
        "val": "val",
        "test": "test",
        "simple_test": "test",
        "complex_test": "test",
        "small_test": "test",
    }.get(split, split)
    source_path = root / "tokenized_data" / f"{source_split}_examples.json"
    if not source_path.exists():
        return {}
    with source_path.open("r", encoding="utf-8-sig") as handle:
        raw_examples = json.load(handle)
    entities = {}
    for table_id, payload in raw_examples.items():
        if isinstance(payload, list) and len(payload) >= 3:
            entity = _clean_cell(payload[2])
            if entity:
                entities[_clean_cell(table_id)] = entity
    return entities


def iter_tabfact_records(root: Path, split: str = "dev", limit: Optional[int] = None) -> Iterator[Record]:
    """Yield Table-Fact-Checking/TabFact records in MACT JSONL shape."""
    root = Path(root)
    split_path = root / "processed_datasets" / "tsv_data_horizontal" / f"{split}.tsv"
    table_dir = root / "data" / "all_csv"
    if not split_path.exists():
        raise FileNotFoundError(f"Missing TabFact split file: {split_path}")

    entities = _load_tabfact_entities(root, split)
    yielded = 0
    with split_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.reader(f, delimiter="\t")
        for parts in reader:
            if len(parts) < 4:
                continue
            table_id = _clean_cell(parts[0])
            if len(parts) >= 6:
                num_columns = _clean_cell(parts[1])
                selected_columns = _clean_cell(parts[2])
                serialized_rows = _clean_cell(parts[3])
                statement = _clean_cell(parts[-2])
                label = _clean_cell(parts[-1])
            else:
                num_columns = _clean_cell(parts[1])
                selected_columns = ""
                serialized_rows = ""
                statement = _clean_cell(parts[-2])
                label = _clean_cell(parts[-1])

            entity = entities.get(table_id, "")
            if entity and "[UNK]" in statement:
                statement = statement.replace("[UNK]", entity)
            table_text = read_table_csv(table_dir / table_id, delimiter="#")
            yield {
                "id": f"tabfact-{split}-{yielded}",
                "source_dataset": "tabfact",
                "task_type": "fact_checking",
                "question": statement,
                "statement": statement,
                "table_text": table_text,
                "answer": _tabfact_answer(label),
                "label": label,
                "table_id": table_id,
                "selected_columns": selected_columns,
                "serialized_rows": serialized_rows,
                "num_columns": num_columns,
                "entity": entity,
            }
            yielded += 1
            if limit is not None and yielded >= limit:
                break


def _load_crt_dataset(crt_root: Path) -> Dict[str, Any]:
    dataset_path = Path(crt_root) / "dataset.json"
    if not dataset_path.exists():
        raise FileNotFoundError(f"Missing CRT-QA dataset file: {dataset_path}")
    with dataset_path.open("r", encoding="utf-8-sig", errors="replace") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"CRT-QA dataset must be a JSON object: {dataset_path}")
    return data


def _candidate_crt_table_paths(
    crt_root: Path,
    table_id: str,
    table_dir: Optional[Path] = None,
) -> List[Path]:
    crt_root = Path(crt_root)
    paths = [
        crt_root / table_id,
        crt_root / "data" / table_id,
        crt_root / "tables" / table_id,
        crt_root / "csv" / table_id,
        crt_root / "all_csv" / table_id,
        crt_root.parent
        / "Table-Fact-Checking-master"
        / "Table-Fact-Checking-master"
        / "data"
        / "all_csv"
        / table_id,
        crt_root.parent / "Table-Fact-Checking-master" / "data" / "all_csv" / table_id,
        crt_root.parent / "Table-Fact-Checking" / "data" / "all_csv" / table_id,
    ]
    if table_dir is not None:
        paths.insert(0, Path(table_dir) / table_id)
    return paths


def find_crt_missing_tables(
    crt_root: Path,
    limit: Optional[int] = None,
    table_dir: Optional[Path] = None,
) -> List[str]:
    """Return CRT-QA table ids that are referenced but not present locally."""
    data = _load_crt_dataset(Path(crt_root))
    missing: List[str] = []
    for table_id in data.keys():
        if not any(
            path.exists()
            for path in _candidate_crt_table_paths(Path(crt_root), table_id, table_dir=table_dir)
        ):
            missing.append(table_id)
            if limit is not None and len(missing) >= limit:
                break
    return missing


def iter_crt_records(
    crt_root: Path,
    limit: Optional[int] = None,
    table_dir: Optional[Path] = None,
) -> Iterator[Record]:
    """Yield CRT-QA records if the referenced table CSV files are available."""
    crt_root = Path(crt_root)
    data = _load_crt_dataset(crt_root)
    yielded = 0
    for table_id, questions in data.items():
        table_path = next(
            (
                path
                for path in _candidate_crt_table_paths(crt_root, table_id, table_dir=table_dir)
                if path.exists()
            ),
            None,
        )
        if table_path is None:
            raise FileNotFoundError(
                f"CRT-QA references table {table_id}, but no matching CSV exists under {crt_root}."
            )
        table_text = read_table_csv(table_path)
        for item in questions:
            question = _clean_cell(item.get("Question name"))
            yield {
                "id": f"crt-{yielded}",
                "source_dataset": "crt",
                "question": question,
                "statement": question,
                "table_text": table_text,
                "answer": _answer_list(item.get("Answer")),
                "table_id": table_id,
                "title": _clean_cell(item.get("Title") or item.get("Tittle")),
                "directness": _clean_cell(item.get("Directness")),
                "composition_type": _clean_cell(item.get("Composition Type")),
            }
            yielded += 1
            if limit is not None and yielded >= limit:
                return


def write_jsonl(records: Iterable[Record], output_path: Path) -> int:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output_path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
    return count


def build_records(
    dataset: str,
    root: Path,
    split: str,
    limit: Optional[int],
    table_dir: Optional[Path] = None,
) -> Iterator[Record]:
    dataset = dataset.lower()
    if dataset == "wtq":
        return iter_wtq_records(root, split=split, limit=limit)
    if dataset in {"tabfact", "tablefact", "table-fact-checking"}:
        return iter_tabfact_records(root, split=split, limit=limit)
    if dataset == "crt":
        return iter_crt_records(root, limit=limit, table_dir=table_dir)
    raise ValueError(f"Unsupported dataset: {dataset}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert local table datasets to MACT JSONL format.")
    parser.add_argument("--dataset", choices=["wtq", "tabfact", "crt"], required=True)
    parser.add_argument("--root", required=True, help="Dataset root directory.")
    parser.add_argument("--output", required=True, help="Output JSONL path.")
    parser.add_argument("--split", default="training", help="Split name for WTQ/TabFact.")
    parser.add_argument("--limit", type=int, default=0, help="Optional maximum number of records.")
    parser.add_argument(
        "--table_dir",
        default="",
        help="Optional CRT-QA CSV table directory; sibling TabFact data is auto-detected.",
    )
    parser.add_argument(
        "--inspect_missing_crt",
        action="store_true",
        help="For CRT-QA, print missing referenced CSV table ids instead of converting.",
    )
    args = parser.parse_args()

    root = Path(args.root)
    limit = args.limit if args.limit > 0 else None
    table_dir = Path(args.table_dir) if args.table_dir else None
    if args.dataset == "crt" and args.inspect_missing_crt:
        missing = find_crt_missing_tables(root, limit=limit, table_dir=table_dir)
        print(json.dumps({"missing_tables": missing, "count": len(missing)}, ensure_ascii=False, indent=2))
        return

    records = build_records(
        args.dataset,
        root,
        split=args.split,
        limit=limit,
        table_dir=table_dir,
    )
    count = write_jsonl(records, Path(args.output))
    print(json.dumps({"output": args.output, "records": count}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
