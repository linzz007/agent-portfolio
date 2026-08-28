import json
from pathlib import Path

from core.harness.conversation_eval import (
    build_judge_payload,
    detect_output_type,
    evaluate_turn_rules,
    load_scenarios,
    run_scenario,
)


def _artifact(skill_id: str, content: str, *, status: str = "succeeded"):
    return {
        "run_id": f"run_{skill_id}",
        "session": {
            "mode": skill_id.split(".", 1)[0],
            "skill_id": skill_id,
            "status": status,
            "user_message": "user",
        },
        "output": {"role": "assistant", "content": content},
        "diagnostics": [{"check": "run_status", "status": "ok", "message": "ok"}],
    }


def test_load_scenarios_validates_jsonl_shape(tmp_path: Path):
    path = tmp_path / "scenarios.jsonl"
    path.write_text(
        json.dumps(
            {
                "case_id": "practice_generate_then_giveup",
                "mode": "practice",
                "course_name": "linear_algebra_eval",
                "turns": [{"user": "给我出一个题目", "expect": {"skill": "practice.quiz.v1"}}],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    scenarios = load_scenarios(path)

    assert scenarios[0]["case_id"] == "practice_generate_then_giveup"
    assert scenarios[0]["turns"][0]["user"] == "给我出一个题目"


def test_evaluate_turn_rules_detects_practice_question_metadata_visibility():
    content = "# 练习题\n请回答。\n<!-- QUIZ_META {\"standard_answer\":\"A\"} -->"
    turn = {
        "expect": {
            "skill": "practice.quiz.v1",
            "output_type": "question",
            "must_have_hidden_meta": True,
            "must_not_show_hidden_meta": True,
        }
    }

    result = evaluate_turn_rules(turn, _artifact("practice.quiz.v1", content))

    assert result["passed"]
    assert result["visible_output"] == "# 练习题\n请回答。"
    assert all(check["passed"] for check in result["checks"])


def test_evaluate_turn_rules_distinguishes_giveup_grading_from_new_question():
    turn = {
        "expect": {
            "skill": "practice.grade.v1",
            "output_type": "grading",
            "should_score_low": True,
            "must_not_generate_new_question": True,
        }
    }
    artifact = _artifact("practice.grade.v1", "## 评分结果\n总得分：0 / 100\n你的答案：我不会")

    result = evaluate_turn_rules(turn, artifact)

    assert result["passed"]
    assert result["output_type"] == "grading"


def test_detect_output_type_keeps_learn_explanation_with_question_word_as_answer():
    content = (
        "\u597d\u7684\uff0c\u8fd9\u4e2a\u9898\u76ee\u91cc\u7684\u5173\u952e\u662f"
        "\u7406\u89e3\u7279\u5f81\u503c\u548c\u7279\u5f81\u5411\u91cf\u7684\u5173\u7cfb\u3002\n\n"
        "### \u7b2c\u4e00\u6b65\uff1a\u7279\u5f81\u503c\u5230\u5e95\u5728\u95ee\u4ec0\u4e48\uff1f\n"
        "\u5b83\u4e0d\u662f\u65b0\u7684\u7ec3\u4e60\u9898\uff0c\u800c\u662f\u5bf9\u4e0a\u4e00\u4e2a\u95ee\u9898\u7684\u8bb2\u89e3\u3002"
    )

    assert detect_output_type(content) == "answer"


def test_run_scenario_preserves_raw_hidden_metadata_in_next_turn_history():
    class FakeRuntime:
        def __init__(self):
            self.histories = []
            self.last_artifact = None
            self.last_artifact_path = None

        def run_stream(self, *, course_name, mode, user_message, history, request_id=None, state=None):
            self.histories.append([dict(item) for item in history])
            if "不会" in user_message:
                assert any("QUIZ_META" in msg.get("content", "") for msg in history if msg.get("role") == "assistant")
                content = "## 评分结果\n总得分：0 / 100\n你的答案：我不会"
                self.last_artifact = _artifact("practice.grade.v1", content)
                yield content
                return
            content = "# 练习题\n请回答。\n<!-- QUIZ_META {\"standard_answer\":\"A\"} -->"
            self.last_artifact = _artifact("practice.quiz.v1", content)
            yield content

    scenario = {
        "case_id": "practice_generate_then_giveup",
        "mode": "practice",
        "course_name": "linear_algebra_eval",
        "turns": [
            {
                "user": "给我出一个特征值的题目",
                "expect": {
                    "skill": "practice.quiz.v1",
                    "output_type": "question",
                    "must_have_hidden_meta": True,
                },
            },
            {
                "user": "我不会",
                "expect": {
                    "skill": "practice.grade.v1",
                    "output_type": "grading",
                    "should_score_low": True,
                },
            },
        ],
    }

    result = run_scenario(FakeRuntime(), scenario)

    assert result["passed"]
    assert len(result["turns"]) == 2
    assert result["turns"][1]["rule_result"]["output_type"] == "grading"


def test_run_scenario_attaches_optional_llm_judge_result():
    class FakeRuntime:
        last_artifact = None
        last_artifact_path = None

        def run_stream(self, **_kwargs):
            content = "特征值是矩阵作用后方向不变向量对应的伸缩倍数。"
            self.last_artifact = _artifact("learn.answer.v1", content)
            yield content

    seen_payloads = []

    def fake_judge(payload):
        seen_payloads.append(payload)
        return {"verdict": "pass", "score": 1.0, "reason": "answers the question"}

    scenario = {
        "case_id": "learn_followup",
        "mode": "learn",
        "course_name": "linear_algebra_eval",
        "turns": [{"user": "特征值是什么", "expect": {"skill": "learn.answer.v1", "output_type": "answer"}}],
    }

    result = run_scenario(FakeRuntime(), scenario, judge=fake_judge)

    assert result["passed"]
    assert result["turns"][0]["judge_result"]["score"] == 1.0
    assert seen_payloads[0] == build_judge_payload(scenario, result["turns"][0])
