from pathlib import Path
import importlib.util
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_ROOT = PROJECT_ROOT.parent / "dataset"
sys.path.insert(0, str(PROJECT_ROOT / "code"))

from dataset_adapters import iter_crt_records, iter_tabfact_records, iter_wtq_records  # noqa: E402


def first_existing_root(*candidates: Path) -> Path:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


MACT_CODE = first_existing_root(
    PROJECT_ROOT.parent / "MACT-main" / "MACT-main" / "code",
    PROJECT_ROOT.parent / "MACT" / "code",
)


def load_mact_table2df():
    spec = importlib.util.spec_from_file_location("mact_utils_for_smoke", MACT_CODE / "utils.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.table2df


class MactSmokeTests(unittest.TestCase):
    def test_mact_utils_builds_dataframes_for_all_three_datasets(self):
        table2df = load_mact_table2df()
        wtq_root = first_existing_root(
            DATASET_ROOT / "WikiTableQuestions-master" / "WikiTableQuestions-master",
            DATASET_ROOT / "WikiTableQuestions",
        )
        tabfact_root = first_existing_root(
            DATASET_ROOT / "Table-Fact-Checking-master" / "Table-Fact-Checking-master",
            DATASET_ROOT / "Table-Fact-Checking",
        )
        crt_root = DATASET_ROOT / "CRT-QA"
        records = {
            "wtq": next(iter_wtq_records(wtq_root, split="training", limit=1)),
            "tabfact": next(iter_tabfact_records(tabfact_root, split="dev", limit=1)),
            "crt": next(iter_crt_records(crt_root, limit=1)),
        }

        for dataset_name, row in records.items():
            with self.subTest(dataset=dataset_name):
                df_code = table2df(row["table_text"])
                local_env = {}
                exec(df_code, {}, local_env)
                df = local_env["df"]

                self.assertGreater(int(df.shape[0]), 0)
                self.assertGreater(int(df.shape[1]), 0)
                self.assertTrue(row["answer"])
                self.assertEqual(len(df.columns), len(set(df.columns)))


if __name__ == "__main__":
    unittest.main()
