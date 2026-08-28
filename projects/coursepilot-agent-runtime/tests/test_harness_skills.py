import unittest

from core.harness.skills import default_skill_registry


class HarnessSkillTests(unittest.TestCase):
    def test_default_skill_resolution(self):
        registry = default_skill_registry()

        self.assertEqual(
            "learn.answer.v1",
            registry.resolve(mode="learn", user_message="讲一下矩阵", history=[]).skill_id,
        )
        self.assertEqual(
            "learn.mindmap.v1",
            registry.resolve(mode="learn", user_message="生成思维导图", history=[]).skill_id,
        )
        self.assertEqual(
            "practice.paper.v1",
            registry.resolve(mode="practice", user_message="出10道选择题", history=[]).skill_id,
        )
        self.assertEqual(
            "exam.paper.v1",
            registry.resolve(mode="exam", user_message="出一套试卷", history=[]).skill_id,
        )

    def test_answer_submission_resolution(self):
        registry = default_skill_registry()
        history = [{"role": "assistant", "content": "题目：1+1=？"}]

        self.assertEqual(
            "practice.grade.v1",
            registry.resolve(mode="practice", user_message="我的答案是2", history=history).skill_id,
        )

    def test_exam_new_paper_request_after_exam_history_routes_to_paper(self):
        registry = default_skill_registry()
        history = [
            {
                "role": "assistant",
                "content": (
                    "# \u6a21\u62df\u8003\u8bd5\u8bd5\u5377\n"
                    "\u7b2c\u4e00\u9898\uff1a...\n"
                    "<!-- EXAM_META [{\"question\":\"q1\",\"standard_answer\":\"a1\"}] -->"
                ),
            }
        ]

        self.assertEqual(
            "exam.paper.v1",
            registry.resolve(
                mode="exam",
                user_message="\u518d\u51fa\u4e00\u5957\u65b0\u7684",
                history=history,
            ).skill_id,
        )

    def test_long_practice_answer_after_quiz_routes_to_grader(self):
        registry = default_skill_registry()
        history = [
            {
                "role": "assistant",
                "content": "## 练习题\n请回答上述题目，回答完毕后我会为你评分。",
            }
        ]
        answer = (
            "第 1 题解答\n"
            "(1) 求矩阵 A 的秩\n"
            "对矩阵 A 做初等行变换，得到非零行有 2 行，所以秩为 2。\n"
            "(2) 列空间的基为主元列对应的列向量。\n"
        ) * 12

        self.assertGreater(len(answer), 500)
        self.assertEqual(
            "practice.grade.v1",
            registry.resolve(mode="practice", user_message=answer, history=history).skill_id,
        )


if __name__ == "__main__":
    unittest.main()
