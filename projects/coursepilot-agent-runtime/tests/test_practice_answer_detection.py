import importlib.util
import sys
import types
import unittest


def _install_optional_dependency_stubs():
    if importlib.util.find_spec("openai") is None:
        openai_mod = types.ModuleType("openai")

        class OpenAI:
            def __init__(self, *args, **kwargs):
                pass

        openai_mod.OpenAI = OpenAI
        sys.modules["openai"] = openai_mod

    if importlib.util.find_spec("faiss") is None:
        faiss_mod = types.ModuleType("faiss")

        class IndexFlatL2:
            def __init__(self, dimension):
                self.dimension = dimension
                self.ntotal = 0

            def add(self, embeddings):
                self.ntotal += len(embeddings)

            def search(self, query_embedding, top_k):
                return [[0.0] * top_k], [[0] * top_k]

        faiss_mod.IndexFlatL2 = IndexFlatL2
        faiss_mod.write_index = lambda *args, **kwargs: None
        faiss_mod.read_index = lambda *args, **kwargs: IndexFlatL2(384)
        sys.modules["faiss"] = faiss_mod

    if importlib.util.find_spec("sentence_transformers") is None:
        st_mod = types.ModuleType("sentence_transformers")

        class SentenceTransformer:
            def __init__(self, *args, **kwargs):
                pass

            def encode(self, texts, *args, **kwargs):
                import numpy as np

                n = len(texts) if isinstance(texts, list) else 1
                return np.zeros((n, 384), dtype="float32")

        st_mod.SentenceTransformer = SentenceTransformer
        sys.modules["sentence_transformers"] = st_mod

    if importlib.util.find_spec("torch") is None:
        torch_mod = types.ModuleType("torch")

        class _Cuda:
            @staticmethod
            def is_available():
                return False

            @staticmethod
            def get_device_name(_idx):
                return "stub"

        torch_mod.cuda = _Cuda()
        sys.modules["torch"] = torch_mod


_install_optional_dependency_stubs()

from backend.schemas import Plan, Quiz
from core.orchestration.runner import OrchestrationRunner


class PracticeAnswerDetectionTests(unittest.TestCase):
    def test_visible_quiz_message_hidden_meta_marks_short_answer_as_submission(self):
        runner = OrchestrationRunner()
        quiz = Quiz(
            question="A. keep addition and scalar multiplication\nB. always invertible",
            standard_answer="A",
            rubric="A is correct.",
            difficulty="medium",
        )
        history = [{"role": "assistant", "content": runner._render_quiz_message(quiz)}]

        self.assertTrue(runner._is_answer_submission("A", history))
        self.assertIn("keep addition", runner._extract_quiz_from_history(history))

    def test_stream_practice_grades_when_tool_calls_are_missing_but_hidden_meta_exists(self):
        runner = OrchestrationRunner()
        runner.load_retriever = lambda _course: None
        runner._fetch_history_ctx = lambda **_kwargs: ""
        runner._save_practice_record = lambda *_args, **_kwargs: "practices/mock.md"
        runner._save_grading_to_memory = lambda *_args, **_kwargs: None
        runner.quizmaster.generate_quiz = lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("answer should be graded, not routed to quiz generation")
        )
        runner.grader.grade_practice_stream = lambda **_kwargs: iter(["GRADED"])

        quiz = Quiz(
            question="A. keep addition and scalar multiplication\nB. always invertible",
            standard_answer="A",
            rubric="A is correct.",
            difficulty="medium",
        )
        history = [{"role": "assistant", "content": runner._render_quiz_message(quiz)}]
        plan = Plan(
            need_rag=False,
            allowed_tools=[],
            task_type="practice",
            style="step_by_step",
            output_format="answer",
        )

        chunks = [
            chunk
            for chunk in runner.run_practice_mode_stream(
                "course", "A", plan, history=history
            )
            if isinstance(chunk, str)
        ]

        self.assertTrue("".join(chunks).startswith("GRADED"))

    def test_hidden_quiz_meta_with_latex_braces_supplies_standard_and_student_answer(self):
        runner = OrchestrationRunner()
        runner.load_retriever = lambda _course: None
        runner._fetch_history_ctx = lambda **_kwargs: ""
        runner._save_practice_record = lambda *_args, **_kwargs: "practices/mock.md"
        runner._save_grading_to_memory = lambda *_args, **_kwargs: None
        runner.quizmaster.generate_quiz = lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("answer should be graded, not routed to quiz generation")
        )
        captured = {}

        def fake_grade_practice_stream(**kwargs):
            captured.update(kwargs)
            return iter(["GRADED"])

        runner.grader.grade_practice_stream = fake_grade_practice_stream
        quiz = Quiz(
            question="考虑矩阵 \\( A = \\begin{pmatrix}4 & 2\\\\2 & 1\\end{pmatrix} \\)，求特征值。",
            standard_answer="特征多项式为 \\(\\lambda(\\lambda-5)\\)，标准答案：特征值为 0 和 5。",
            rubric="能写出 \\begin{pmatrix}4 & 2\\\\2 & 1\\end{pmatrix} 的特征值给满分。",
            difficulty="medium",
            chapter="矩阵的特征值与特征向量",
        )
        history = [{"role": "assistant", "content": runner._render_quiz_message(quiz)}]
        plan = Plan(
            need_rag=False,
            allowed_tools=[],
            task_type="practice",
            style="step_by_step",
            output_format="answer",
        )

        chunks = [
            chunk
            for chunk in runner.run_practice_mode_stream(
                "course", "我不太会", plan, history=history
            )
            if isinstance(chunk, str)
        ]

        self.assertTrue("".join(chunks).startswith("GRADED"))
        self.assertEqual(captured["student_answer"], "我不太会")
        self.assertIn("【标准答案】", captured["quiz_content"])
        self.assertIn("特征值为 0 和 5", captured["quiz_content"])
        self.assertNotIn("标准答案缺失", captured["quiz_content"])

    def test_numbered_multistep_answer_after_quiz_routes_to_grader(self):
        runner = OrchestrationRunner()
        runner.load_retriever = lambda _course: None
        runner._fetch_history_ctx = lambda **_kwargs: ""
        runner._save_practice_record = lambda *_args, **_kwargs: "practices/mock.md"
        runner._save_grading_to_memory = lambda *_args, **_kwargs: None
        runner.quizmaster.generate_quiz = lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("answer should be graded, not routed to quiz generation")
        )
        runner.quizmaster.generate_exam_paper = lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("answer should be graded, not routed to exam generation")
        )
        runner.grader.grade_practice_stream = lambda **_kwargs: iter(["GRADED"])

        quiz = Quiz(
            question="设矩阵 A，求特征值、特征向量，并判断能否对角化。",
            standard_answer="标准答案略。",
            rubric="评分标准略。",
            difficulty="medium",
        )
        history = [{"role": "assistant", "content": runner._render_quiz_message(quiz)}]
        answer = (
            "(1) 特征值：2-√2、2、2+√2\n"
            "对应特征向量分别为：k1 (1, √2, 1)^T、k2 (1, 0, -1)^T。\n"
            "(2) A 可对角化，P 的列向量依次取上述特征向量。\n"
            "(3) AᵀA 的特征值等于 A 特征值的平方。\n"
            "(4) 可用 Cholesky 分解，因为 A 是实对称正定矩阵。"
        )
        plan = Plan(
            need_rag=False,
            allowed_tools=[],
            task_type="practice",
            style="step_by_step",
            output_format="answer",
        )

        chunks = [
            chunk
            for chunk in runner.run_practice_mode_stream(
                "course", answer, plan, history=history
            )
            if isinstance(chunk, str)
        ]

        self.assertTrue("".join(chunks).startswith("GRADED"))


if __name__ == "__main__":
    unittest.main()
