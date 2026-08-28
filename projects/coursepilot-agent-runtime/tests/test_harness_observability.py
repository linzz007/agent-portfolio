import unittest

from core.harness.observability import build_run_diagnostics, build_run_timeline


class HarnessObservabilityTests(unittest.TestCase):
    def test_timeline_exposes_readable_agent_steps(self):
        timeline = build_run_timeline(
            session={
                "run_id": "run_test",
                "mode": "practice",
                "skill_id": "practice.grade.v1",
                "status": "succeeded",
                "user_message": "回答：我不太会",
            },
            events=[
                {
                    "seq": 1,
                    "type": "llm_call",
                    "model": "demo",
                    "llm_ms": 120.0,
                    "success": True,
                    "input_messages": [{"role": "user", "content": "完整输入"}],
                    "output_message": {"role": "assistant", "content": "完整输出"},
                },
                {"seq": 2, "type": "retrieval", "returned_count": 2, "retrieval_ms": 8.0, "success": True},
                {"seq": 3, "type": "context_budget", "final_tokens_est": 300, "budget_tokens_est": 8000},
                {"seq": 4, "type": "react_phase", "phase": "act", "round": 1},
            ],
            retrieval=[{"doc_id": "lesson.md", "chunk_id": "c1", "score": 0.91, "text": "source text"}],
            context_budget={"final_tokens_est": 300, "budget_tokens_est": 8000, "hard_truncated": False},
            tool_calls=[],
            tool_decisions=[],
            output={"content": "评分结果：0/100"},
            eval_result={"verdict": "passed", "reasons": []},
            error=None,
            elapsed_ms=150.0,
        )

        self.assertEqual("session", timeline[0]["type"])
        self.assertTrue(any(item["type"] == "llm_call" for item in timeline))
        llm_item = next(item for item in timeline if item["type"] == "llm_call")
        self.assertEqual([{"role": "user", "content": "完整输入"}], llm_item["input"]["messages"])
        self.assertEqual({"role": "assistant", "content": "完整输出"}, llm_item["output"]["message"])
        self.assertTrue(any(item["type"] == "retrieval" for item in timeline))
        self.assertTrue(any(item["type"] == "react_phase" for item in timeline))
        self.assertEqual("assistant_output", timeline[-2]["type"])
        self.assertEqual("eval", timeline[-1]["type"])

    def test_diagnostics_flags_missing_calculator_for_grading(self):
        diagnostics = build_run_diagnostics(
            session={
                "run_id": "run_test",
                "mode": "practice",
                "skill_id": "practice.grade.v1",
                "status": "succeeded",
            },
            retrieval=[{"doc_id": "lesson.md"}],
            context_budget={"context_pressure_ratio": 0.1, "hard_truncated": False},
            tool_calls=[{"tool_name": "memory_search", "success": True}],
            tool_decisions=[],
            output={"content": "评分结果：0/100"},
            error=None,
        )

        by_check = {item["check"]: item for item in diagnostics}
        self.assertEqual("ok", by_check["run_status"]["status"])
        self.assertEqual("ok", by_check["answer_output"]["status"])
        self.assertEqual("warning", by_check["required_calculator_for_grading"]["status"])
        self.assertIn("calculator", by_check["required_calculator_for_grading"]["message"])

    def test_diagnostics_flags_grading_route_that_returns_new_question(self):
        diagnostics = build_run_diagnostics(
            session={
                "run_id": "run_test",
                "mode": "practice",
                "skill_id": "practice.grade.v1",
                "status": "succeeded",
            },
            retrieval=[{"doc_id": "lesson.md"}],
            context_budget={"context_pressure_ratio": 0.1, "hard_truncated": False},
            tool_calls=[{"name": "exam_meta", "type": "internal_meta"}],
            tool_decisions=[],
            output={"content": "# 练习题\n<!-- EXAM_META [] -->"},
            error=None,
        )

        by_check = {item["check"]: item for item in diagnostics}
        self.assertEqual("warning", by_check["grading_output_consistency"]["status"])
        self.assertIn("question", by_check["grading_output_consistency"]["message"])


if __name__ == "__main__":
    unittest.main()
