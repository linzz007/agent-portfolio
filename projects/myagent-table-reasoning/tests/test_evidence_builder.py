from pathlib import Path
import sys
import unittest

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "code"))

from answer_contracts import infer_answer_contract  # noqa: E402
from evidence_builder import EvidenceBuilder  # noqa: E402


class EvidenceBuilderTests(unittest.TestCase):
    def test_wtq_entity_index_finds_rows_beyond_preview(self):
        df = pd.DataFrame(
            {
                "Horse": ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Falcon"],
                "Result": ["lost", "lost", "lost", "lost", "lost", "won"],
            }
        )
        contract = infer_answer_contract("What was the result for Falcon?")
        pack = EvidenceBuilder(max_candidates=10).build(
            question="What was the result for Falcon?",
            df=df,
            schema={"column_profiles": []},
            dataset_name="wtq",
            answer_contract=contract,
        )
        self.assertIn(5, [row.row_index for row in pack.candidate_rows])
        self.assertEqual(pack.gap_signals["entity"], 0.0)

    def test_unmatched_entity_and_ambiguous_column_raise_risk_signals(self):
        df = pd.DataFrame({"City": ["Paris"], "City Rank": [1], "Score": [9]})
        contract = infer_answer_contract("What is the city ranking for Berlin?")
        pack = EvidenceBuilder().build(
            question="What is the city ranking for Berlin?",
            df=df,
            schema={"column_profiles": []},
            dataset_name="wtq",
            answer_contract=contract,
        )
        self.assertGreater(pack.gap_signals["entity"], 0.0)
        self.assertGreaterEqual(pack.ambiguity_signals["column"], 0.25)

    def test_tabfact_statement_sets_logic_and_count_signals(self):
        df = pd.DataFrame({"Nation": ["A", "B", "C"], "Medals": [3, 1, 3]})
        contract = infer_answer_contract(
            "Two nations have 3 medals and no nation has 4.",
            answer_mode="true_false",
        )
        pack = EvidenceBuilder().build(
            question="Two nations have 3 medals and no nation has 4.",
            df=df,
            schema={"column_profiles": []},
            dataset_name="tabfact",
            answer_contract=contract,
        )
        self.assertGreaterEqual(pack.operation_signals["logic"], 0.5)
        self.assertGreaterEqual(pack.operation_signals["steps"], 0.5)

    def test_crt_tuple_and_unit_contract_raise_contract_signals(self):
        df = pd.DataFrame({"Method": ["decision", "finish"], "Wins": [3, 12]})
        contract = infer_answer_contract(
            "How many wins by decision and by finish?",
            kind_override="tuple",
            arity=2,
        )
        pack = EvidenceBuilder().build(
            question="How many wins by decision and by finish?",
            df=df,
            schema={"column_profiles": []},
            dataset_name="crt",
            answer_contract=contract,
        )
        self.assertGreaterEqual(pack.operation_signals["contract"], 0.5)
        self.assertIn("Method", pack.candidate_columns)

    def test_semantic_stressors_raise_logic_and_dependency_signals(self):
        df = pd.DataFrame(
            {
                "Location": ["Park A", "Park B"],
                "rank 2008": [3, 11],
                "rank 2012": [8, 4],
            }
        )
        contract = infer_answer_contract(
            "Are there any locations that consistently ranked in the top 10 "
            "from 2008 to 2012?",
            answer_mode="yes_no",
        )
        pack = EvidenceBuilder().build(
            question=(
                "Are there any locations that consistently ranked in the top "
                "10 from 2008 to 2012?"
            ),
            df=df,
            schema={"column_profiles": []},
            dataset_name="crt",
            answer_contract=contract,
        )

        self.assertIn("temporal_consistency", pack.operation_hints)
        self.assertGreaterEqual(pack.operation_signals["logic"], 0.8)
        self.assertGreaterEqual(pack.operation_signals["dependency"], 0.8)

    def test_pack_serialization_excludes_gold_answer(self):
        df = pd.DataFrame({"Name": ["A"], "Value": [1]})
        contract = infer_answer_contract("What is the value for A?")
        pack = EvidenceBuilder().build(
            question="What is the value for A?",
            df=df,
            schema={"gold_answer": "1", "column_profiles": []},
            dataset_name="wtq",
            answer_contract=contract,
        )
        payload = pack.to_dict()
        self.assertNotIn("gold_answer", str(payload).lower())


if __name__ == "__main__":
    unittest.main()
