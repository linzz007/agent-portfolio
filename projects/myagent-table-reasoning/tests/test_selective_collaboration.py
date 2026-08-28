from pathlib import Path
import json
import sys
import unittest

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "code"))

from answer_contracts import infer_answer_contract  # noqa: E402
from selective_collaboration import (  # noqa: E402
    AgreementJudge,
    CandidateAnswer,
    ThinkingSolver,
    answer_similarity,
    verification_gap,
)


class FakeLLM:
    def __init__(self, text):
        self.text = text
        self.calls = []
        self.call_kwargs = []

    def complete(self, prompt, temperature=0.0, max_tokens=None):
        self.calls.append(prompt)
        self.call_kwargs.append({"temperature": temperature, "max_tokens": max_tokens})
        return self.text


class SelectiveCollaborationTests(unittest.TestCase):
    def test_list_similarity_is_order_insensitive_for_wtq(self):
        contract = infer_answer_contract("Which teams qualified?")
        self.assertEqual(answer_similarity(["A", "B"], ["b", "a"], contract), 1.0)

    def test_tuple_similarity_preserves_position_for_crt(self):
        contract = infer_answer_contract(
            "How many wins by decision and finish?",
            kind_override="tuple",
            arity=2,
        )
        self.assertEqual(answer_similarity(["3", "12"], ["3", "12"], contract), 1.0)
        self.assertEqual(answer_similarity(["3", "12"], ["12", "3"], contract), 0.0)

    def test_numeric_similarity_accepts_small_format_difference(self):
        contract = infer_answer_contract("What is the average score?")
        self.assertEqual(answer_similarity("3.0", "3", contract), 1.0)

    def test_invalid_candidate_loses_to_valid_candidate(self):
        contract = infer_answer_contract("A has 3 wins.", answer_mode="true_false")
        judge = AgreementJudge(contract)
        valid = CandidateAnswer(name="code", raw_answer="true", normalized_answer="true", is_valid=True)
        invalid = CandidateAnswer(name="react", raw_answer="", normalized_answer="", is_valid=False, failure="exec")
        decision = judge.decide([invalid, valid])
        self.assertEqual(decision.selected.name, "code")
        self.assertFalse(decision.requires_fallback)

    def test_conflicting_valid_candidates_require_fallback(self):
        contract = infer_answer_contract("A has 3 wins.", answer_mode="true_false")
        judge = AgreementJudge(contract)
        left = CandidateAnswer(name="code", raw_answer="true", normalized_answer="true", is_valid=True)
        right = CandidateAnswer(name="react", raw_answer="false", normalized_answer="false", is_valid=True)
        decision = judge.decide([left, right])
        self.assertTrue(decision.requires_fallback)
        self.assertGreater(decision.disagreement, 0.0)

    def test_verification_gap_accepts_numpy_execution_result(self):
        candidate = CandidateAnswer(
            name="code",
            raw_answer="jaycen joshua",
            normalized_answer="jaycen joshua",
            is_valid=True,
            execution_result=np.array(["jaycen joshua", "rick ross"]),
        )

        self.assertEqual(verification_gap(candidate), 0.0)

    def test_thinking_solver_parses_json_answer(self):
        llm = FakeLLM(json.dumps({"answer": "true", "confidence": 0.7, "reasoning_summary": "checked rows"}))
        solver = ThinkingSolver(llm)
        candidate = solver.solve(
            question="A has 3 wins.",
            evidence={"candidate_rows": []},
            candidates=[],
            answer_contract=infer_answer_contract("A has 3 wins.", answer_mode="true_false"),
        )
        self.assertEqual(candidate.normalized_answer, "true")
        self.assertTrue(candidate.is_valid)
        self.assertIn("Return JSON", llm.calls[0])
        self.assertEqual(llm.call_kwargs[0]["max_tokens"], 512)

    def test_thinking_solver_extracts_json_from_fenced_response(self):
        llm = FakeLLM(
            "```json\n"
            + json.dumps({"answer": "false", "confidence": 0.8, "reasoning_summary": "checked rows"})
            + "\n```"
        )
        solver = ThinkingSolver(llm)
        candidate = solver.solve(
            question="A has 3 wins.",
            evidence={"candidate_rows": []},
            candidates=[],
            answer_contract=infer_answer_contract("A has 3 wins.", answer_mode="true_false"),
        )

        self.assertEqual(candidate.normalized_answer, "false")
        self.assertTrue(candidate.is_valid)

    def test_thinking_solver_compacts_large_prompt_payloads(self):
        llm = FakeLLM(json.dumps({"answer": "true", "confidence": 0.7, "reasoning_summary": "checked rows"}))
        solver = ThinkingSolver(llm)
        long_program = "final_answer_value = 'x'\n" + ("#" * 5000)
        solver.solve(
            question="A has 3 wins.",
            evidence={"original_table": "row\n" + ("cell " * 3000)},
            candidates=[
                CandidateAnswer(
                    name="code",
                    raw_answer="true",
                    normalized_answer="true",
                    is_valid=True,
                    reasoning_summary="reason " * 400,
                    executable_program=long_program,
                    confidence=0.7,
                )
            ],
            answer_contract=infer_answer_contract("A has 3 wins.", answer_mode="true_false"),
        )

        prompt = llm.calls[0]
        self.assertNotIn(long_program, prompt)
        self.assertLess(len(prompt), 6500)
        self.assertIn("truncated", prompt)


if __name__ == "__main__":
    unittest.main()
