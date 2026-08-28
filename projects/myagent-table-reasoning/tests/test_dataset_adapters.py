from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_ROOT = PROJECT_ROOT.parent / "dataset"
sys.path.insert(0, str(PROJECT_ROOT / "code"))

from dataset_adapters import (  # noqa: E402
    find_crt_missing_tables,
    iter_crt_records,
    iter_tabfact_records,
    iter_wtq_records,
    read_table_csv,
)


def dataset_root(*candidates: str) -> Path:
    for candidate in candidates:
        path = DATASET_ROOT / candidate
        if path.exists():
            return path
    return DATASET_ROOT / candidates[0]


class DatasetAdapterTests(unittest.TestCase):
    def test_wtq_records_include_mact_fields_and_table_text(self):
        wtq_root = dataset_root(
            "WikiTableQuestions-master/WikiTableQuestions-master",
            "WikiTableQuestions",
        )

        records = list(iter_wtq_records(wtq_root, split="training", limit=2))

        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["source_dataset"], "wtq")
        self.assertEqual(
            records[0]["question"],
            "what was the last year where this team was a part of the usl a-league?",
        )
        self.assertEqual(records[0]["statement"], records[0]["question"])
        self.assertEqual(records[0]["answer"], ["2004"])
        self.assertEqual(records[0]["answer_canonical"], ["2004.0"])
        self.assertEqual(records[0]["answer_canonical_type"], ["number"])
        self.assertEqual(
            records[0]["table_text"][0],
            [
                "Year",
                "Division",
                "League",
                "Regular Season",
                "Playoffs",
                "Open Cup",
                "Avg. Attendance",
            ],
        )
        self.assertGreater(len(records[0]["table_text"]), 1)

    def test_tabfact_records_include_statement_label_and_source_table(self):
        tabfact_root = dataset_root(
            "Table-Fact-Checking-master/Table-Fact-Checking-master",
            "Table-Fact-Checking",
        )

        records = list(iter_tabfact_records(tabfact_root, split="dev", limit=1))

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["source_dataset"], "tabfact")
        self.assertEqual(records[0]["task_type"], "fact_checking")
        self.assertEqual(records[0]["answer"], ["true"])
        self.assertTrue(records[0]["statement"].startswith("during the third round"))
        self.assertEqual(records[0]["table_id"], "2-1859269-1.html.csv")
        self.assertEqual(records[0]["table_text"][0][:3], ["round", "clubs remaining", "clubs involved"])
        self.assertIn("süper lig", records[0]["table_text"][2][-1])

    def test_wtq_multi_answer_denotation_is_split_into_items(self):
        wtq_root = dataset_root(
            "WikiTableQuestions-master/WikiTableQuestions-master",
            "WikiTableQuestions",
        )

        records = list(
            iter_wtq_records(wtq_root, split="pristine-unseen-tables", limit=11)
        )

        self.assertEqual(records[10]["id"], "nu-10")
        self.assertEqual(records[10]["answer"], ["2004", "2005", "2006"])
        self.assertEqual(
            records[10]["answer_canonical"],
            ["2004.0", "2005.0", "2006.0"],
        )

    def test_tabfact_restores_entities_hidden_by_processed_unk_tokens(self):
        tabfact_root = dataset_root(
            "Table-Fact-Checking-master/Table-Fact-Checking-master",
            "Table-Fact-Checking",
        )

        record = next(iter_tabfact_records(tabfact_root, split="test", limit=1))

        self.assertNotIn("[UNK]", record["statement"])
        self.assertTrue(record["statement"].startswith("tony lema be in the top 5"))
        self.assertEqual(record["entity"], "tony lema")

    def test_crt_reuses_tables_from_sibling_tabfact_dataset(self):
        crt_root = DATASET_ROOT / "CRT-QA"

        missing = find_crt_missing_tables(crt_root, limit=3)

        self.assertEqual(missing, [])

        record = next(iter_crt_records(crt_root, limit=1))
        self.assertEqual(record["source_dataset"], "crt")
        self.assertEqual(record["table_id"], "2-10311801-2.html.csv")
        self.assertEqual(record["answer"], ["Yes"])
        self.assertGreater(len(record["table_text"]), 1)

    def test_duplicate_table_headers_are_made_unique(self):
        table_path = (
            dataset_root(
                "WikiTableQuestions-master/WikiTableQuestions-master",
                "WikiTableQuestions",
            )
            / "csv"
            / "203-csv"
            / "826.csv"
        )

        table = read_table_csv(table_path)

        self.assertEqual(table[0], ["Date", "Winner", "Yacht", "Loser", "Yacht_2", "Score", "Delta"])


if __name__ == "__main__":
    unittest.main()
