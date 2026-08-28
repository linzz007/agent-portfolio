import json
from pathlib import Path
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "code"))

from freeze_blind_holdout import (  # noqa: E402
    collect_exclusions,
    freeze_dataset,
    select_unseen_records,
)


class BlindHoldoutTests(unittest.TestCase):
    def test_collect_exclusions_reads_ids_and_table_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.jsonl"
            path.write_text(
                json.dumps({"id": "old-1", "table_id": "table-1"}) + "\n",
                encoding="utf-8",
            )

            ids, tables = collect_exclusions([Path(tmp)])

        self.assertEqual(ids, {"old-1"})
        self.assertEqual(tables, {"table-1"})

    def test_unseen_selection_is_balanced_and_table_diverse(self):
        records = [
            {
                "id": f"row-{index}",
                "table_id": f"table-{index}",
                "source_dataset": "tabfact",
                "label": index % 2,
                "answer": ["true" if index % 2 else "false"],
            }
            for index in range(10)
        ]

        selected = select_unseen_records(
            records,
            sample_size=4,
            seed=77,
            dataset="tabfact",
            excluded_ids={"row-0"},
            excluded_tables={"table-1"},
        )

        self.assertEqual(len(selected), 4)
        self.assertEqual(len({row["table_id"] for row in selected}), 4)
        self.assertNotIn("row-0", {row["id"] for row in selected})
        self.assertNotIn("table-1", {row["table_id"] for row in selected})
        self.assertEqual(
            sorted(str(row["label"]) for row in selected),
            ["0", "0", "1", "1"],
        )

    def test_freeze_dataset_records_hash_and_zero_overlap(self):
        records = [
            {"id": f"row-{index}", "table_id": f"table-{index}", "answer": [index]}
            for index in range(5)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "wtq.jsonl"
            summary = freeze_dataset(
                records,
                output_path=output,
                dataset="wtq",
                sample_size=2,
                seed=42,
                excluded_ids={"row-0"},
                excluded_tables={"table-1"},
            )

            first_hash = summary["sha256"]
            second_hash = freeze_dataset(
                records,
                output_path=output,
                dataset="wtq",
                sample_size=2,
                seed=42,
                excluded_ids={"row-0"},
                excluded_tables={"table-1"},
            )["sha256"]

        self.assertEqual(first_hash, second_hash)
        self.assertEqual(summary["prior_id_overlap"], 0)
        self.assertEqual(summary["prior_table_overlap"], 0)


if __name__ == "__main__":
    unittest.main()
