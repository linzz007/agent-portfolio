import unittest

from core.harness.evaluation import evaluate_artifact, summarize_evaluations


class HarnessEvaluationTests(unittest.TestCase):
    def test_evaluate_artifact_scores_healthy_run(self):
        artifact = {
            "output": {"content": "This is a grounded answer."},
            "retrieval": [{"doc_id": "lesson.md", "score": 0.91}],
            "context_budget": {"context_pressure_ratio": 0.25},
            "tool_decisions": [
                {"tool_name": "calculator", "allowed": True, "risk_level": "safe"},
            ],
            "tool_calls": [
                {"tool_name": "calculator", "success": True},
            ],
            "error": None,
        }

        result = evaluate_artifact(artifact)

        self.assertEqual("heuristic.v1", result["evaluator"])
        self.assertEqual("passed", result["verdict"])
        self.assertTrue(result["checks"]["has_output"])
        self.assertTrue(result["checks"]["has_retrieval"])
        self.assertFalse(result["checks"]["context_pressure_high"])
        self.assertEqual(1.0, result["scores"]["answer_presence_score"])
        self.assertEqual(1.0, result["scores"]["retrieval_coverage_score"])
        self.assertEqual(1.0, result["scores"]["tool_success_score"])
        self.assertEqual(1.0, result["scores"]["safety_score"])

    def test_evaluate_artifact_flags_blocked_write_and_high_context_pressure(self):
        artifact = {
            "output": {"content": "Saved your note."},
            "retrieval": [],
            "context_budget": {"context_pressure_ratio": 0.92},
            "tool_decisions": [
                {
                    "tool_name": "filewriter",
                    "allowed": False,
                    "risk_level": "write",
                    "reason": "approval_required",
                },
            ],
            "tool_calls": [
                {"tool_name": "filewriter", "failure_class": "fatal_error"},
            ],
            "error": None,
        }

        result = evaluate_artifact(artifact)

        self.assertEqual("warning", result["verdict"])
        self.assertTrue(result["checks"]["blocked_tools"])
        self.assertTrue(result["checks"]["write_risk"])
        self.assertTrue(result["checks"]["context_pressure_high"])
        self.assertEqual(0.0, result["scores"]["retrieval_coverage_score"])
        self.assertEqual(0.0, result["scores"]["tool_success_score"])
        self.assertEqual(0.0, result["scores"]["safety_score"])
        self.assertIn("blocked_tool:filewriter", result["reasons"])

    def test_summarize_evaluations_counts_verdicts_and_averages_scores(self):
        summary = summarize_evaluations(
            [
                {"verdict": "passed", "scores": {"answer_presence_score": 1.0}},
                {"verdict": "warning", "scores": {"answer_presence_score": 0.0}},
            ]
        )

        self.assertEqual(2, summary["total"])
        self.assertEqual(1, summary["verdict_counts"]["passed"])
        self.assertEqual(1, summary["verdict_counts"]["warning"])
        self.assertEqual(0.5, summary["average_scores"]["answer_presence_score"])


if __name__ == "__main__":
    unittest.main()
