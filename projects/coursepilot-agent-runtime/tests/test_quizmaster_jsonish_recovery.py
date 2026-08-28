import unittest

from core.agents.quizmaster import QuizMasterAgent
from core.orchestration.runner import OrchestrationRunner


class _FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)

    def chat(self, *args, **kwargs):
        if not self.responses:
            raise RuntimeError("no_more_fake_responses")
        return self.responses.pop(0)


class QuizmasterJsonishRecoveryTests(unittest.TestCase):
    def test_generate_quiz_recovers_fields_from_truncated_jsonish_response(self):
        raw_response = (
            '{ "question": "设矩阵 A = [[3,1],[0,3]]，求 A 的特征值。", '
            '"standard_answer": "特征值为 3，代数重数为 2。", '
            '"rubric": "写出特征值 50 分，说明重数 50 分'
        )
        agent = QuizMasterAgent()
        agent.llm = _FakeLLM([raw_response])
        agent._structured_chat_json = lambda **_kwargs: {}
        agent._plan_quiz = lambda **_kwargs: {
            "topic": "特征值",
            "num_questions": 1,
            "difficulty": "medium",
            "question_type": "综合题",
            "focus_points": [],
        }
        agent._build_external_ctx = lambda _query: ""
        agent._repair_json_via_llm = lambda *_args, **_kwargs: {}

        quiz = agent.generate_quiz(
            course_name="线性代数",
            topic="给我出一个特征值的题目",
            difficulty="medium",
            context="",
            prefetched_memory_checked=True,
        )

        self.assertEqual(quiz.question, "设矩阵 A = [[3,1],[0,3]]，求 A 的特征值。")
        self.assertEqual(quiz.standard_answer, "特征值为 3，代数重数为 2。")
        self.assertTrue(quiz.rubric.startswith("写出特征值 50 分"))

        rendered = OrchestrationRunner._render_quiz_message(quiz)
        visible = rendered.split("<!-- QUIZ_META", 1)[0]
        self.assertNotIn('"standard_answer"', visible)
        self.assertNotIn('{ "question"', visible)
        self.assertIn("设矩阵 A", visible)


if __name__ == "__main__":
    unittest.main()
