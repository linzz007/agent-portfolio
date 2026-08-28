import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "code"))

from sample_benchmark import categorize_record, select_records  # noqa: E402


class SampleBenchmarkTests(unittest.TestCase):
    def test_selection_is_deterministic_and_prefers_unique_tables(self):
        records = [
            {
                "id": f"q-{table}-{index}",
                "table_id": f"table-{table}",
                "answer": [str(index)],
            }
            for table in range(8)
            for index in range(2)
        ]

        first = select_records(records, sample_size=8, seed=17, dataset="wtq")
        second = select_records(records, sample_size=8, seed=17, dataset="wtq")

        self.assertEqual([row["id"] for row in first], [row["id"] for row in second])
        self.assertEqual(len({row["table_id"] for row in first}), 8)

    def test_tabfact_selection_balances_labels(self):
        records = [
            {
                "id": f"tf-{index}",
                "table_id": f"table-{index}",
                "label": index % 2,
                "answer": ["true" if index % 2 else "false"],
            }
            for index in range(40)
        ]

        sample = select_records(records, sample_size=18, seed=23, dataset="tabfact")
        categories = [categorize_record(row, "tabfact") for row in sample]

        self.assertEqual(len(sample), 18)
        self.assertLessEqual(abs(categories.count("true") - categories.count("false")), 1)

    def test_crt_selection_covers_yes_no_and_general_questions(self):
        records = []
        for index in range(20):
            records.append(
                {
                    "id": f"yn-{index}",
                    "table_id": f"yn-table-{index}",
                    "question": "Is this supported? Answer with only 'Yes' or 'No' that is most accurate.",
                    "answer": ["Yes"],
                }
            )
            records.append(
                {
                    "id": f"open-{index}",
                    "table_id": f"open-table-{index}",
                    "question": "What is the average value?",
                    "answer": ["12.5"],
                }
            )

        sample = select_records(records, sample_size=18, seed=29, dataset="crt")
        categories = {categorize_record(row, "crt") for row in sample}

        self.assertEqual(len(sample), 18)
        self.assertEqual(categories, {"yes_no", "general"})


if __name__ == "__main__":
    unittest.main()
