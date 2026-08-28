"""Freeze a reproducible blind holdout with historical ID/table exclusion."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import secrets
from typing import Any, Dict, Iterable, List, Sequence, Set, Tuple

from sample_benchmark import categorize_record, select_records


def _record_id(record: Dict[str, Any]) -> str:
    return str(record.get("id") or "").strip()


def _table_id(record: Dict[str, Any]) -> str:
    return str(
        record.get("table_id")
        or record.get("source_table_id")
        or record.get("table")
        or ""
    ).strip()


def _is_under(path: Path, roots: Sequence[Path]) -> bool:
    resolved = path.resolve()
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


def _jsonl_paths(paths: Iterable[Path]) -> Iterable[Path]:
    for path in paths:
        if path.is_file() and path.suffix.lower() == ".jsonl":
            yield path
        elif path.is_dir():
            yield from path.rglob("*.jsonl")


def collect_exclusions(
    paths: Iterable[Path],
    ignored_roots: Iterable[Path] = (),
) -> Tuple[Set[str], Set[str]]:
    """Collect record and table identities from prior JSONL artifacts."""
    ignored = tuple(Path(path) for path in ignored_roots)
    ids: Set[str] = set()
    tables: Set[str] = set()
    for path in _jsonl_paths(Path(item) for item in paths):
        if _is_under(path, ignored):
            continue
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                record = json.loads(line)
                record_id = _record_id(record)
                table_id = _table_id(record)
                if record_id:
                    ids.add(record_id)
                if table_id:
                    tables.add(table_id)
    return ids, tables


def select_unseen_records(
    records: Iterable[Dict[str, Any]],
    *,
    sample_size: int,
    seed: int,
    dataset: str,
    excluded_ids: Set[str],
    excluded_tables: Set[str],
) -> List[Dict[str, Any]]:
    eligible = [
        record
        for record in records
        if _record_id(record) not in excluded_ids
        and _table_id(record) not in excluded_tables
    ]
    return select_records(eligible, sample_size, seed, dataset)


def _write_jsonl(path: Path, records: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            stream.write("\n")


def freeze_dataset(
    records: List[Dict[str, Any]],
    *,
    output_path: Path,
    dataset: str,
    sample_size: int,
    seed: int,
    excluded_ids: Set[str],
    excluded_tables: Set[str],
) -> Dict[str, Any]:
    eligible_count = sum(
        _record_id(record) not in excluded_ids
        and _table_id(record) not in excluded_tables
        for record in records
    )
    selected = select_unseen_records(
        records,
        sample_size=sample_size,
        seed=seed,
        dataset=dataset,
        excluded_ids=excluded_ids,
        excluded_tables=excluded_tables,
    )
    _write_jsonl(output_path, selected)
    selected_ids = {_record_id(record) for record in selected}
    selected_tables = {_table_id(record) for record in selected}
    return {
        "seed": seed,
        "source_records": len(records),
        "eligible_records": eligible_count,
        "records": len(selected),
        "unique_tables": len(selected_tables),
        "category_counts": dict(
            Counter(categorize_record(record, dataset) for record in selected)
        ),
        "prior_id_overlap": len(selected_ids & excluded_ids),
        "prior_table_overlap": len(selected_tables & excluded_tables),
        "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
        "file": str(output_path),
    }


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wtq_input", required=True)
    parser.add_argument("--tabfact_input", required=True)
    parser.add_argument("--crt_input", required=True)
    parser.add_argument("--output_dir", required=True)
    parser.add_argument("--history_root", action="append", default=[])
    parser.add_argument("--ignore_root", action="append", default=[])
    parser.add_argument("--sample_size", type=int, default=20)
    parser.add_argument("--seed", type=int)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    input_paths = {
        "wtq": Path(args.wtq_input),
        "tabfact": Path(args.tabfact_input),
        "crt": Path(args.crt_input),
    }
    ignored = [Path(path) for path in args.ignore_root]
    ignored.extend(input_paths.values())
    ignored.append(output_dir)
    excluded_ids, excluded_tables = collect_exclusions(
        [Path(path) for path in args.history_root],
        ignored_roots=ignored,
    )
    base_seed = args.seed if args.seed is not None else secrets.randbits(63)
    manifest: Dict[str, Any] = {
        "protocol": "blind_holdout_v3",
        "base_seed": base_seed,
        "sample_size_per_dataset": args.sample_size,
        "excluded_prior_ids": len(excluded_ids),
        "excluded_prior_tables": len(excluded_tables),
        "datasets": {},
    }
    for index, (dataset, input_path) in enumerate(input_paths.items()):
        seed = base_seed + index * 104729
        manifest["datasets"][dataset] = freeze_dataset(
            _load_jsonl(input_path),
            output_path=output_dir / f"{dataset}.jsonl",
            dataset=dataset,
            sample_size=args.sample_size,
            seed=seed,
            excluded_ids=excluded_ids,
            excluded_tables=excluded_tables,
        )

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
