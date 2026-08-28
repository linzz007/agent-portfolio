import json
import sys
from types import SimpleNamespace
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "code"))

from tqa import (  # noqa: E402
    _json_default,
    _metric_delta,
    _state_observability,
    _to_serializable,
    answer_mode_for_sample,
    answer_mode_for_task,
    runtime_contract_for_sample,
    table_context_for_row,
)


class TaskModeTests(unittest.TestCase):
    def test_api_metric_delta_is_calculated_per_sample(self):
        before = {
            "request_count": 2,
            "prompt_tokens": 20,
            "completion_tokens": 5,
            "total_tokens": 25,
        }
        after = {
            "request_count": 5,
            "prompt_tokens": 70,
            "completion_tokens": 14,
            "total_tokens": 84,
        }

        self.assertEqual(
            _metric_delta(before, after),
            {
                "request_count": 3,
                "prompt_tokens": 50,
                "completion_tokens": 9,
                "total_tokens": 59,
            },
        )

    def test_state_observability_exposes_contract_and_risk_without_gold(self):
        contract = SimpleNamespace(
            as_dict=lambda: {
                "kind": "label",
                "allowed_labels": ["true", "false"],
                "reasoning_required": True,
                "instructions": "choose one label",
            }
        )
        state = SimpleNamespace(
            answer_contract=contract,
            risk_escalated=True,
            contract_validation={"valid": True, "reason": ""},
            grounding_validation={"valid": True, "reason": ""},
            table_context="Player match history",
        )

        result = _state_observability(state)

        self.assertEqual(result["answer_contract"]["kind"], "label")
        self.assertTrue(result["risk_escalated"])
        self.assertEqual(result["contract_validation"], {"valid": True, "reason": ""})
        self.assertEqual(result["grounding_validation"], {"valid": True, "reason": ""})
        self.assertEqual(result["table_context"], "Player match history")
        self.assertNotIn("gold_answer", result)

    def test_state_observability_includes_selective_fields(self):
        contract = SimpleNamespace(as_dict=lambda: {"kind": "scalar"})
        serializable = SimpleNamespace(to_dict=lambda: {"pre_risk": 0.8})
        state = SimpleNamespace(
            answer_contract=contract,
            risk_escalated=True,
            contract_validation={"valid": True, "reason": ""},
            grounding_validation={"valid": True, "reason": ""},
            table_context="",
            dataset_profile="wtq",
            dataset_instructions="",
            critic_skipped=False,
            risk_level="high",
            risk_assessment=serializable,
            post_risk_assessment={"post_risk": 0.9},
            evidence_pack={"candidate_rows": []},
            candidate_answers=[{"name": "code"}],
            agreement_decision={"requires_fallback": True},
            budget_state={"avg_tokens": 1000},
        )

        payload = _state_observability(state)

        self.assertEqual(payload["risk_level"], "high")
        self.assertEqual(payload["risk_assessment"]["pre_risk"], 0.8)
        self.assertEqual(payload["post_risk_assessment"]["post_risk"], 0.9)
        self.assertIn("budget_state", payload)

    def test_table_context_uses_non_gold_dataset_metadata(self):
        row = {
            "entity": "Joseba Etxeberria",
            "table_title": "International goals",
            "answer": ["true"],
        }

        self.assertEqual(
            table_context_for_row(row),
            "Joseba Etxeberria | International goals",
        )

    def test_numpy_scalar_is_json_serializable_at_output_boundary(self):
        encoded = json.dumps({"answer": np.int64(17)}, default=_json_default)

        self.assertEqual(json.loads(encoded), {"answer": 17})

    def test_numpy_array_is_json_serializable_at_output_boundary(self):
        encoded = json.dumps(
            {"answer": np.array(["jaycen joshua", "rick ross"])},
            default=_json_default,
        )

        self.assertEqual(json.loads(encoded), {"answer": ["jaycen joshua", "rick ross"]})

    def test_numpy_array_is_serialized_as_list_at_output_boundary(self):
        payload = _to_serializable({"answer": np.array(["jaycen joshua", "rick ross"])})

        self.assertEqual(payload, {"answer": ["jaycen joshua", "rick ross"]})

    def test_task_to_answer_mode_mapping(self):
        self.assertEqual(answer_mode_for_task("scitab"), "true_false")
        self.assertEqual(answer_mode_for_task("crt"), "")
        self.assertEqual(answer_mode_for_task("wtq"), "")
        self.assertEqual(answer_mode_for_task("databench"), "")

    def test_crt_answer_mode_is_selected_per_question(self):
        yes_no_question = (
            "Can we identify any outliers? Answer with only 'Yes' or 'No' "
            "that is most accurate and nothing else."
        )
        numeric_question = "What is the average percentage change?"

        self.assertEqual(
            answer_mode_for_sample("crt", yes_no_question),
            "yes_no",
        )
        self.assertEqual(
            answer_mode_for_sample(
                "crt",
                "Can we identify any outlier events based on the number of acts or "
                "number of stages compared to the other events in the table? Answer "
                "with only 'Yes' or 'No' that is most accurate and nothing else.",
            ),
            "yes_no",
        )
        self.assertEqual(answer_mode_for_sample("crt", numeric_question), "")
        self.assertEqual(
            answer_mode_for_sample("scitab", "Any fact-check statement"),
            "true_false",
        )

    def test_crt_implicit_binary_and_qualitative_comparison_modes(self):
        self.assertEqual(
            answer_mode_for_sample(
                "crt",
                "Was the player more successful in the United States or Europe?",
            ),
            "",
        )
        self.assertEqual(
            answer_mode_for_sample(
                "crt",
                "What is the proportion of Intel systems compared to AMD systems?",
            ),
            "more_less_equal",
        )

    def test_runtime_contract_applies_crt_home_away_profile(self):
        question = "Was the team more successful playing at home or away?"
        df = pd.DataFrame({"home / away": ["home", "away"], "wins": [8, 3]})

        answer_mode, hints, contract = runtime_contract_for_sample(
            "crt",
            question,
            df,
            {"source_dataset": "crt"},
        )

        self.assertEqual(answer_mode, "")
        self.assertEqual(hints.dataset, "crt")
        self.assertEqual(contract.kind, "label")
        self.assertEqual(contract.allowed_labels, ("home", "away"))
        self.assertTrue(contract.reasoning_required)

    def test_crt_explicit_closed_labels_are_selected_per_question(self):
        self.assertEqual(
            answer_mode_for_sample(
                "crt",
                "Answer with only 'better', 'worse' or 'equal' and nothing else.",
            ),
            "better_worse_equal",
        )
        self.assertEqual(
            answer_mode_for_sample(
                "crt",
                "Answer with only 'Increase', 'Decrease' or 'No change'.",
            ),
            "increase_decrease_no_change",
        )
        self.assertEqual(
            answer_mode_for_sample(
                "crt",
                "Answer with only 'more', 'less' or 'equal'.",
            ),
            "more_less_equal",
        )
        self.assertEqual(
            answer_mode_for_sample(
                "crt",
                "Answer with only 'mroe', 'less' or 'equal'.",
            ),
            "more_less_equal",
        )
        self.assertEqual(
            answer_mode_for_sample(
                "crt",
                "Answer with only 'better' or 'worse'.",
            ),
            "better_worse",
        )


if __name__ == "__main__":
    unittest.main()
