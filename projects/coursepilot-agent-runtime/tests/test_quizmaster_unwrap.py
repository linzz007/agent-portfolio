"""Test nested JSON unwrap logic in QuizMasterAgent."""
import json
import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.agents.quizmaster import QuizMasterAgent


def test_unwrap_nested_json_question_field():
    """LLM wraps full quiz JSON inside question field - should unwrap."""
    q = QuizMasterAgent()
    nested = {
        "question": json.dumps({
            "question": "What is 2+2?",
            "standard_answer": "4",
            "rubric": "1 point for correct answer",
            "difficulty": "easy",
            "chapter": "Arithmetic",
            "concept": "addition",
        }, ensure_ascii=False),
        "standard_answer": "N/A",
        "rubric": "N/A",
        "difficulty": "medium",
        "chapter": "",
        "concept": "",
    }
    result = q._try_unwrap_nested_json(nested)
    assert result["question"] == "What is 2+2?", f"Expected question text, got: {result['question'][:80]}"
    assert result["standard_answer"] == "4", f"Expected '4', got: {result['standard_answer']}"
    assert result["rubric"] == "1 point for correct answer", f"Expected rubric, got: {result['rubric']}"
    assert result["difficulty"] == "easy"
    assert result["chapter"] == "Arithmetic"
    print("PASS test_unwrap_nested_json_question_field")


def test_unwrap_ignores_normal_question():
    """Normal question text should pass through unchanged."""
    q = QuizMasterAgent()
    normal = {
        "question": "What is the capital of France?",
        "standard_answer": "Paris",
        "rubric": "1 point",
        "difficulty": "easy",
        "chapter": "Geography",
        "concept": "capitals",
    }
    result = q._try_unwrap_nested_json(normal)
    assert result["question"] == "What is the capital of France?"
    assert result["standard_answer"] == "Paris"
    print("PASS test_unwrap_ignores_normal_question")


def test_unwrap_handles_empty_inner():
    """Inner JSON has empty strings, should fall back to outer values."""
    q = QuizMasterAgent()
    nested = {
        "question": json.dumps({
            "question": "",
            "standard_answer": "",
            "rubric": "",
        }),
        "standard_answer": "N/A",
        "rubric": "N/A",
        "difficulty": "medium",
        "chapter": "Ch1",
    }
    result = q._try_unwrap_nested_json(nested)
    # Empty inner values are NOT stripped, so outer values are kept
    assert result["standard_answer"] == "N/A", f"Should keep outer, got: {result['standard_answer']}"
    assert result["rubric"] == "N/A"
    print("PASS test_unwrap_handles_empty_inner")


def test_unwrap_handles_invalid_json_in_question():
    """Question looks like JSON but isn't valid - should pass through unchanged."""
    q = QuizMasterAgent()
    weird = {
        "question": "{this is not valid json",
        "standard_answer": "correct",
        "rubric": "1 point",
        "difficulty": "easy",
        "chapter": "Ch1",
        "concept": "test",
    }
    result = q._try_unwrap_nested_json(weird)
    assert result["question"] == "{this is not valid json"
    assert result["standard_answer"] == "correct"
    print("PASS test_unwrap_handles_invalid_json_in_question")


def test_unwrap_preserves_outer_when_inner_missing_keys():
    """Inner dict has no 'question' key - should pass through unchanged."""
    q = QuizMasterAgent()
    no_q = {
        "question": '{"other_field": "value"}',
        "standard_answer": "correct",
        "rubric": "1 point",
        "difficulty": "easy",
        "chapter": "Ch1",
        "concept": "test",
    }
    result = q._try_unwrap_nested_json(no_q)
    assert result["question"] == '{"other_field": "value"}'
    assert result["standard_answer"] == "correct"
    print("PASS test_unwrap_preserves_outer_when_inner_missing_keys")


if __name__ == "__main__":
    test_unwrap_nested_json_question_field()
    test_unwrap_ignores_normal_question()
    test_unwrap_handles_empty_inner()
    test_unwrap_handles_invalid_json_in_question()
    test_unwrap_preserves_outer_when_inner_missing_keys()
    print("\nAll unwrap tests PASSED")
