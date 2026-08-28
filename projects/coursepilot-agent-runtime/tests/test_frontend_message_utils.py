from pathlib import Path

from frontend.message_utils import (
    HiddenMetadataStreamFilter,
    strip_hidden_metadata,
    strip_source_markers,
)


def test_strip_hidden_metadata_removes_quiz_and_exam_comments_from_visible_text():
    text = (
        "## 练习题\n\n求 A 的特征值。\n"
        '<!-- QUIZ_META {"standard_answer":"不应显示"} -->\n'
        '<!-- EXAM_META [{"standard_answer":"也不应显示"}] -->'
    )

    visible = strip_hidden_metadata(text)

    assert "求 A 的特征值" in visible
    assert "QUIZ_META" not in visible
    assert "EXAM_META" not in visible
    assert "standard_answer" not in visible


def test_hidden_metadata_stream_filter_hides_comments_split_across_chunks():
    stream_filter = HiddenMetadataStreamFilter()
    chunks = [
        "## 练习题\n",
        "求 A 的特征值。\n",
        "<!-- QUI",
        'Z_META {"standard_answer":"不应显示"}',
        " -->",
        "\n请回答上述题目。",
    ]

    visible = "".join(stream_filter.push(chunk) for chunk in chunks)
    visible += stream_filter.flush()

    assert "求 A 的特征值" in visible
    assert "请回答上述题目" in visible
    assert "QUIZ_META" not in visible
    assert "standard_answer" not in visible


def test_strip_source_markers_keeps_hidden_quiz_metadata_for_next_turn():
    text = '题目 [来源1]\n<!-- QUIZ_META {"standard_answer":"A"} -->'

    payload_text = strip_source_markers(text)

    assert "[来源1]" not in payload_text
    assert "QUIZ_META" in payload_text
    assert "standard_answer" in payload_text


def test_streamlit_app_uses_hidden_metadata_filter_for_rendering_and_streaming():
    app_source = (Path(__file__).resolve().parents[1] / "frontend" / "streamlit_app.py").read_text(
        encoding="utf-8"
    )

    assert "HiddenMetadataStreamFilter" in app_source
    assert "strip_hidden_metadata(msg[\"content\"])" in app_source
