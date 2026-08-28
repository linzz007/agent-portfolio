from core.llm.openai_compat import (
    _compact_messages_for_tool_round,
    _rehydrate_messages_for_final,
)


def _practice_grading_prompt() -> str:
    long_question = "\n".join(f"第{i}步：矩阵特征值计算过程。" for i in range(80))
    return f"""请根据以下信息评判学生答案：

【题目（来自本次练习）】
{long_question}

【标准答案】
特征值为 0 和 5；学生若回答不会，应判为未作答。

【学生提交的答案】
我不太会

请按评分标准逐题核对。"""


def test_final_rehydrate_preserves_practice_grading_standard_and_student_answer(monkeypatch):
    monkeypatch.delenv("TOOL_FINAL_REHYDRATE_MODE", raising=False)
    original = _practice_grading_prompt()
    messages = [
        {"role": "system", "content": "grader"},
        {"role": "user", "content": "compact placeholder"},
        {"role": "assistant", "content": "calculator returned 0"},
    ]

    rehydrated = _rehydrate_messages_for_final(messages, original)
    user_content = rehydrated[1]["content"]

    assert "【标准答案】" in user_content
    assert "特征值为 0 和 5" in user_content
    assert "【学生提交的答案】" in user_content
    assert "我不太会" in user_content


def test_tool_round_compaction_keeps_practice_grading_prompt_full(monkeypatch):
    monkeypatch.setenv("TOOL_ROUND_FULL_CONTEXT_ROUNDS", "1")
    original = _practice_grading_prompt()
    messages = [
        {"role": "system", "content": "grader"},
        {"role": "user", "content": original},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "calculator", "arguments": '{"expression":"0"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": '{"result":0}'},
    ]

    compacted = _compact_messages_for_tool_round(messages, original, round_no=2)
    user_content = compacted[1]["content"]

    assert "【标准答案】" in user_content
    assert "特征值为 0 和 5" in user_content
    assert "【学生提交的答案】" in user_content
    assert "我不太会" in user_content
