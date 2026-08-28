from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "code"))

from answer_contracts import (  # noqa: E402
    infer_answer_contract,
    normalize_contract_value,
    validate_contract_value,
)


class AnswerContractTests(unittest.TestCase):
    def test_entity_plural_question_requires_a_list(self):
        contract = infer_answer_contract(
            "What teams raced at least 13 races?",
            answer_mode="",
        )

        self.assertEqual(contract.kind, "list")
        self.assertTrue(contract.reasoning_required)
        self.assertIn("list", contract.instructions.lower())

    def test_numeric_fact_check_requires_reasoning(self):
        contract = infer_answer_contract(
            "Grass was the surface in 3 of 14 championships, or 21.43%.",
            answer_mode="true_false",
        )

        self.assertEqual(contract.kind, "label")
        self.assertEqual(contract.allowed_labels, ("true", "false"))
        self.assertTrue(contract.reasoning_required)

    def test_simple_closed_label_lookup_stays_low_risk(self):
        contract = infer_answer_contract(
            "The winning country was Italy.",
            answer_mode="true_false",
        )

        self.assertEqual(contract.kind, "label")
        self.assertFalse(contract.reasoning_required)

    def test_comparison_modes_expose_exact_allowed_labels(self):
        contract = infer_answer_contract(
            "Did the record get better or worse?",
            answer_mode="better_worse",
        )

        self.assertEqual(contract.allowed_labels, ("better", "worse"))
        self.assertTrue(contract.reasoning_required)

    def test_change_mode_exposes_exact_allowed_labels(self):
        contract = infer_answer_contract(
            "Would the average increase, decrease, or stay unchanged?",
            answer_mode="increase_decrease_no_change",
        )

        self.assertEqual(
            contract.allowed_labels,
            ("Increase", "Decrease", "No change"),
        )
        self.assertTrue(contract.reasoning_required)

    def test_unqualified_average_contract_requests_two_decimal_rounding(self):
        contract = infer_answer_contract(
            "What is the average round length?",
            answer_mode="",
        )

        self.assertIn("two decimal places", contract.instructions)

    def test_entity_selection_contract_rejects_intermediate_numeric_answer(self):
        contract = infer_answer_contract(
            "Which combination of writer and director had the highest average viewers?",
            answer_mode="",
        )

        self.assertEqual(contract.kind, "scalar")
        self.assertIn("not the intermediate numeric value", contract.instructions)

    def test_list_normalization_and_validation(self):
        contract = infer_answer_contract("Which teams qualified?", answer_mode="")

        normalized = normalize_contract_value(
            "Champ Motorsports, Krenek Motorsport",
            contract,
        )

        self.assertEqual(normalized, ["Champ Motorsports", "Krenek Motorsport"])
        self.assertEqual(validate_contract_value(normalized, contract), (True, ""))
        valid, reason = validate_contract_value(4, contract)
        self.assertFalse(valid)
        self.assertIn("list", reason.lower())

    def test_label_normalization_maps_boolean_to_allowed_label(self):
        contract = infer_answer_contract("A fact statement.", answer_mode="true_false")

        self.assertEqual(normalize_contract_value(True, contract), "true")
        self.assertEqual(validate_contract_value("true", contract), (True, ""))

    def test_scalar_normalization_removes_format_only_noise(self):
        contract = infer_answer_contract("What was the revenue?", answer_mode="")

        self.assertEqual(
            normalize_contract_value("$12.0 billion", contract),
            "$12 billion",
        )
        self.assertEqual(
            normalize_contract_value('\\O Janewale\\""', contract),
            "O Janewale",
        )

    def test_profile_overrides_create_dynamic_label_contract(self):
        contract = infer_answer_contract(
            "Was the team better at home or away?",
            allowed_labels=("home", "away"),
            kind_override="label",
            reasoning_required=True,
            extra_instructions="Compare home and away win rates.",
        )

        self.assertEqual(contract.kind, "label")
        self.assertEqual(contract.allowed_labels, ("home", "away"))
        self.assertTrue(contract.reasoning_required)
        self.assertIn("Compare home and away win rates", contract.instructions)

    def test_profile_precision_override_replaces_average_default(self):
        contract = infer_answer_contract(
            "What is the average Roll?",
            decimal_places=0,
        )

        self.assertEqual(contract.decimal_places, 0)
        self.assertEqual(normalize_contract_value(289.67, contract), 290.0)

    def test_tuple_contract_normalizes_exact_arity_values(self):
        contract = infer_answer_contract(
            "How many wins by decision and how many by finish?",
            kind_override="tuple",
            arity=2,
        )

        self.assertEqual(contract.kind, "tuple")
        self.assertEqual(normalize_contract_value((3, 12), contract), [3, 12])
        self.assertEqual(normalize_contract_value("3, 12", contract), ["3", "12"])
        self.assertEqual(validate_contract_value([3, 12], contract), (True, ""))
        valid, reason = validate_contract_value(
            "3 by decision and 12 by finish",
            contract,
        )
        self.assertFalse(valid)
        self.assertIn("2 values", reason)


if __name__ == "__main__":
    unittest.main()
