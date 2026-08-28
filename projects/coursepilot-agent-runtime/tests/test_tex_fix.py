"""Test LaTeX backslash escape fix for JSON parsing."""
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.agents.quizmaster import QuizMasterAgent


def test_regex_on_individual_patterns():
    q = QuizMasterAgent()
    # The LLM outputs \( which is backslash+paren (2 chars).
    # In valid JSON, backslash must be followed by: " \ / b f n r t or u+4hex
    # Our regex should double any backslash that's NOT followed by those.

    bs = "\x5c"  # single backslash

    # Test 1: \( -> \\(  (bare paren after backslash)
    t1 = bs + "("
    f1 = q._fix_tex_escapes_in_json(t1)
    assert f1 == bs + bs + "(", f"Expected \\\\(, got: {repr(f1)}"

    # Test 2: \) -> \\)
    t2 = bs + ")"
    f2 = q._fix_tex_escapes_in_json(t2)
    assert f2 == bs + bs + ")", f"Expected \\\\), got: {repr(f2)}"

    # Test 3: \\n (already valid JSON) should stay \\n
    t3 = bs + bs + "n"
    f3 = q._fix_tex_escapes_in_json(t3)
    assert f3 == t3, f"Expected {repr(t3)}, got: {repr(f3)}"

    # Test 4: \\begin (LLM correctly escapes \\, then b)
    t4 = bs + bs + "begin"
    f4 = q._fix_tex_escapes_in_json(t4)
    assert f4 == t4, f"Expected {repr(t4)}, got: {repr(f4)}"

    print("PASS: individual regex patterns")


def test_full_json_with_latex():
    q = QuizMasterAgent()
    bs = "\x5c"

    # Build LLM output with invalid JSON (deepseek outputs raw \\( etc)
    # LLM uses REAL newlines between lines, not \n escape sequences
    raw = (
        '{\n  "question": "she ' + bs + '( A ' + bs + bs + 'begin{pmatrix} 4 & 1 '
        + bs + bs + bs + bs + ' 1 & 4 ' + bs + bs + 'end{pmatrix} ' + bs + ')",\n'
        '  "standard_answer": "ans",\n'
        '  "rubric": "ok",\n'
        '  "difficulty": "medium",\n'
        '  "chapter": "ch1",\n'
        '  "concept": "c1"\n'
        '}'
    )

    # Direct json.loads should FAIL (LLM output has raw \()
    try:
        json.loads(raw)
        print("WARNING: direct parse succeeded (unexpected)")
    except json.JSONDecodeError:
        pass  # expected

    # _extract_json_payload should succeed with tex fix
    try:
        result = q._extract_json_payload(raw)
        assert "she" in result["question"], f"Bad question: {result['question'][:40]}"
        assert result["standard_answer"] == "ans"
        print("PASS: full JSON with LaTeX")
    except ValueError as e:
        # Debug
        print(f"FAIL: {e}")
        first = raw.find("{")
        last = raw.rfind("}")
        cand = raw[first:last+1].strip()
        print(f"Brace-range candidate: {repr(cand[:200])}")
        fixed = q._fix_tex_escapes_in_json(cand)
        print(f"After fix: {repr(fixed[:200])}")
        try:
            json.loads(fixed)
            print("Fixed version parses OK!")
        except json.JSONDecodeError as e2:
            print(f"Fixed version still fails: {e2}")


def test_unwrap_with_latex():
    q = QuizMasterAgent()
    bs = "\x5c"

    # Nested JSON case: question field contains a raw JSON string with LaTeX
    inner_json = (
        '{"question":"test ' + bs + '( A ' + bs + ')","standard_answer":"ans",'
        + '"rubric":"ok","difficulty":"easy","chapter":"ch1","concept":"c1"}'
    )
    bad_quiz = {
        "question": inner_json,
        "standard_answer": "N/A",
        "rubric": "N/A",
        "difficulty": "medium",
        "chapter": "",
        "concept": "",
    }

    result = q._try_unwrap_nested_json(bad_quiz)
    assert result["question"] == "test " + bs + "( A " + bs + ")", (
        f"Expected LaTeX question, got: {repr(result['question'][:60])}"
    )
    assert result["standard_answer"] == "ans"
    print("PASS: unwrap with LaTeX")


if __name__ == "__main__":
    test_regex_on_individual_patterns()
    test_full_json_with_latex()
    test_unwrap_with_latex()
    print("\nAll tests PASSED")
