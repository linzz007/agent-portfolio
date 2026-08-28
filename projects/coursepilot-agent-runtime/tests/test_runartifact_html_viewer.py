import json
from pathlib import Path

from core.harness.html_viewer import render_run_artifact_file, render_run_artifact_html


def _sample_artifact():
    return {
        "run_id": "run_test_123",
        "schema_version": "harness.run_artifact.v3",
        "session": {
            "mode": "practice",
            "skill_id": "practice.quiz.v1",
            "status": "succeeded",
            "user_message": "给我出一个特征值的题目",
            "trace_id": "trace-1",
        },
        "diagnostics": [
            {"check": "run_status", "status": "ok", "message": "Run completed."},
            {"check": "required_calculator_for_grading", "status": "warning", "message": "No calculator."},
        ],
        "timeline": [
            {"type": "session", "name": "practice.quiz.v1", "status": "ok"},
            {"type": "llm_call", "name": "deepseek", "status": "ok", "input": {}, "output": {}},
            {"type": "assistant_output", "name": "assistant", "status": "ok"},
        ],
        "output": {
            "content": "# 练习题\n题目正文\n<!-- QUIZ_META {\"standard_answer\":\"secret\"} -->"
        },
        "metrics": {
            "trace_events": [
                {
                    "type": "llm_call",
                    "model": "deepseek",
                    "input_messages": [{"role": "user", "content": "<script>alert(1)</script>"}],
                    "output_message": {"role": "assistant", "content": "OK"},
                    "prompt_tokens": 12,
                    "completion_tokens": 3,
                }
            ]
        },
    }


def test_render_run_artifact_html_escapes_content_and_groups_key_sections():
    html = render_run_artifact_html(_sample_artifact())

    assert "run_test_123" in html
    assert "Diagnostics" in html
    assert "Timeline" in html
    assert "LLM Calls" in html
    assert "Visible Output" in html
    assert "题目正文" in html
    assert "secret" not in html.split("Visible Output", 1)[1].split("Raw Artifact", 1)[0]
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html


def test_render_run_artifact_file_writes_default_html_next_to_json(tmp_path: Path):
    src = tmp_path / "run.json"
    src.write_text(json.dumps(_sample_artifact(), ensure_ascii=False), encoding="utf-8")

    out = render_run_artifact_file(src)

    assert out == tmp_path / "run.html"
    assert out.exists()
    assert "run_test_123" in out.read_text(encoding="utf-8")

