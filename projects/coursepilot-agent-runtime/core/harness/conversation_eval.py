"""Multi-turn conversation evaluation helpers for CoursePilot modes."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional


JudgeFn = Callable[[Dict[str, Any]], Dict[str, Any]]

_INTERNAL_META_RE = re.compile(
    r"<!--\s*(?:QUIZ_META|EXAM_META)\b[\s\S]*?-->",
    flags=re.IGNORECASE,
)
_SCORE_PATTERNS = (
    re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*/\s*100"),
    re.compile(r"(?:总得分|得分|总分)\s*[:：]\s*([0-9]+(?:\.[0-9]+)?)"),
)


def _as_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _strip_hidden_metadata(text: str) -> str:
    return _INTERNAL_META_RE.sub("", str(text or "")).strip()


def _has_hidden_meta(text: str) -> bool:
    return bool(_INTERNAL_META_RE.search(str(text or "")))


def _artifact_to_dict(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "to_dict"):
        return _as_dict(value.to_dict())
    if hasattr(value, "model_dump"):
        return _as_dict(value.model_dump())
    return _as_dict(value)


def _artifact_content(artifact: Mapping[str, Any], fallback: str = "") -> str:
    output = _as_dict(artifact.get("output"))
    return str(output.get("content") if output.get("content") is not None else fallback)


def load_scenarios(path: str | Path) -> List[Dict[str, Any]]:
    """Load and validate a JSONL multi-turn scenario file."""

    src = Path(path)
    scenarios: List[Dict[str, Any]] = []
    with src.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            row = json.loads(s)
            if not isinstance(row, dict):
                raise ValueError(f"{src}:{lineno} scenario must be an object")
            if not str(row.get("case_id", "")).strip():
                raise ValueError(f"{src}:{lineno} missing case_id")
            if row.get("mode") not in {"learn", "practice", "exam"}:
                raise ValueError(f"{src}:{lineno} mode must be learn/practice/exam")
            turns = row.get("turns")
            if not isinstance(turns, list) or not turns:
                raise ValueError(f"{src}:{lineno} missing turns")
            for idx, turn in enumerate(turns, start=1):
                if not isinstance(turn, dict) or not str(turn.get("user", "")).strip():
                    raise ValueError(f"{src}:{lineno} turn {idx} missing user")
                if not isinstance(turn.get("expect", {}), dict):
                    raise ValueError(f"{src}:{lineno} turn {idx} expect must be an object")
            scenarios.append(row)
    return scenarios


def detect_output_type(raw_output: str, visible_output: Optional[str] = None) -> str:
    """Classify output into answer/question/exam/grading for flow checks."""

    raw = str(raw_output or "")
    visible = str(visible_output if visible_output is not None else _strip_hidden_metadata(raw))
    text = f"{visible}\n{raw}"
    if any(marker in text for marker in ("评分结果", "总得分", "得分：", "/100", "各题讲评", "你的答案")):
        return "grading"
    if "EXAM_META" in raw or re.search(r"(模拟考试试卷|考试须知|本试卷|##\s*第一部分|#\s*《[^\n]+》[^\n]*试卷)", visible):
        return "exam"
    if "QUIZ_META" in raw or re.search(r"^\s*#{1,3}\s*练习题(?:\s|$)", visible) or "请回答上述题目" in visible:
        return "question"
    return "answer"


def _extract_scores(text: str) -> List[float]:
    scores: List[float] = []
    for pattern in _SCORE_PATTERNS:
        for match in pattern.finditer(str(text or "")):
            try:
                scores.append(float(match.group(1)))
            except (TypeError, ValueError):
                continue
    return scores


def _add_check(
    checks: List[Dict[str, Any]],
    name: str,
    passed: bool,
    *,
    expected: Any = None,
    actual: Any = None,
    detail: str = "",
) -> None:
    checks.append(
        {
            "name": name,
            "passed": bool(passed),
            "expected": expected,
            "actual": actual,
            "detail": detail,
        }
    )


def _has_tool_meta(artifact: Mapping[str, Any]) -> bool:
    for item in _as_list(artifact.get("tool_calls")):
        name = str(item.get("name") or item.get("tool_name") or "")
        if name in {"quiz_meta", "exam_meta"}:
            return True
    return False


def _diagnostic_errors(artifact: Mapping[str, Any]) -> List[Dict[str, Any]]:
    return [
        item
        for item in _as_list(artifact.get("diagnostics"))
        if str(item.get("status") or "").lower() == "error"
    ]


def evaluate_turn_rules(turn: Mapping[str, Any], artifact: Mapping[str, Any]) -> Dict[str, Any]:
    """Run deterministic checks for one scenario turn."""

    expect = _as_dict(turn.get("expect"))
    artifact = _as_dict(artifact)
    session = _as_dict(artifact.get("session"))
    raw_output = _artifact_content(artifact)
    visible_output = _strip_hidden_metadata(raw_output)
    output_type = detect_output_type(raw_output, visible_output)
    checks: List[Dict[str, Any]] = []

    expected_skill = expect.get("skill")
    if expected_skill:
        actual_skill = session.get("skill_id")
        _add_check(
            checks,
            "skill",
            actual_skill == expected_skill,
            expected=expected_skill,
            actual=actual_skill,
        )

    expected_type = expect.get("output_type")
    if expected_type:
        _add_check(
            checks,
            "output_type",
            output_type == expected_type,
            expected=expected_type,
            actual=output_type,
        )

    if expect.get("must_have_hidden_meta"):
        has_meta = _has_hidden_meta(raw_output) or _has_tool_meta(artifact)
        _add_check(checks, "hidden_meta_present", has_meta, expected=True, actual=has_meta)

    if expect.get("must_not_show_hidden_meta"):
        shown = "QUIZ_META" in visible_output or "EXAM_META" in visible_output
        _add_check(checks, "hidden_meta_not_visible", not shown, expected=False, actual=shown)

    if expect.get("must_not_generate_new_question"):
        generated = output_type in {"question", "exam"}
        _add_check(checks, "not_new_question", not generated, expected=False, actual=generated)

    if expect.get("must_not_grade"):
        graded = output_type == "grading"
        _add_check(checks, "not_grading", not graded, expected=False, actual=graded)

    if expect.get("should_score_low"):
        scores = _extract_scores(raw_output)
        low = bool(scores) and min(scores) <= float(expect.get("low_score_max", 30))
        _add_check(
            checks,
            "low_score",
            low,
            expected=f"<= {expect.get('low_score_max', 30)}",
            actual=scores,
        )

    if expect.get("diagnostics_no_error", True):
        errors = _diagnostic_errors(artifact)
        _add_check(checks, "diagnostics_no_error", not errors, expected=[], actual=errors)

    return {
        "passed": all(item["passed"] for item in checks),
        "checks": checks,
        "output_type": output_type,
        "visible_output": visible_output,
    }


def build_judge_payload(scenario: Mapping[str, Any], turn_result: Mapping[str, Any]) -> Dict[str, Any]:
    """Build a compact payload for an optional LLM Judge."""

    return {
        "case_id": scenario.get("case_id"),
        "mode": scenario.get("mode"),
        "turn_index": turn_result.get("turn_index"),
        "user": turn_result.get("user"),
        "expected": _as_dict(turn_result.get("expect")),
        "rule_passed": _as_dict(turn_result.get("rule_result")).get("passed"),
        "visible_output": str(turn_result.get("visible_output") or "")[:4000],
        "artifact_run_id": turn_result.get("artifact_run_id"),
        "skill_id": turn_result.get("skill_id"),
    }


def make_llm_judge(llm: Any = None) -> JudgeFn:
    """Create a JSON-returning LLM judge. Deterministic rules remain primary."""

    if llm is None:
        from core.llm.openai_compat import LLMClient

        llm = LLMClient()

    def _judge(payload: Dict[str, Any]) -> Dict[str, Any]:
        messages = [
            {
                "role": "system",
                "content": (
                    "你是 CoursePilot 多轮流程评测员。只判断回答是否符合 expected，"
                    "输出 JSON：{\"verdict\":\"pass|warning|fail\",\"score\":0到1,\"reason\":\"...\"}。"
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
        ]
        raw = llm.chat(messages, temperature=0.0, max_tokens=500)
        try:
            data = json.loads(str(raw).strip())
        except Exception:
            data = {"verdict": "warning", "score": 0.0, "reason": str(raw)[:500]}
        if not isinstance(data, dict):
            data = {"verdict": "warning", "score": 0.0, "reason": str(raw)[:500]}
        return data

    return _judge


def _collect_stream(runtime: Any, *, course_name: str, mode: str, user_message: str, history: List[Dict[str, Any]], request_id: str) -> Dict[str, Any]:
    text_parts: List[str] = []
    stream_tool_calls: List[Dict[str, Any]] = []
    for chunk in runtime.run_stream(
        course_name=course_name,
        mode=mode,
        user_message=user_message,
        state={},
        history=history,
        request_id=request_id,
    ):
        if isinstance(chunk, str):
            text_parts.append(chunk)
        elif isinstance(chunk, dict) and isinstance(chunk.get("__tool_calls__"), list):
            stream_tool_calls.extend([dict(item) for item in chunk["__tool_calls__"] if isinstance(item, dict)])

    artifact = _artifact_to_dict(getattr(runtime, "last_artifact", None))
    artifact_path = getattr(runtime, "last_artifact_path", None)
    response_text = _artifact_content(artifact, fallback="".join(text_parts))
    return {
        "response_text": response_text,
        "artifact": artifact,
        "artifact_path": artifact_path,
        "tool_calls": stream_tool_calls,
    }


def run_scenario(runtime: Any, scenario: Mapping[str, Any], judge: Optional[JudgeFn] = None) -> Dict[str, Any]:
    """Run one multi-turn scenario against a HarnessRuntime-like object."""

    scenario = deepcopy(_as_dict(scenario))
    case_id = str(scenario.get("case_id") or "case")
    mode = str(scenario.get("mode") or "learn")
    course_name = str(scenario.get("course_name") or "linear_algebra_eval")
    history = [dict(item) for item in scenario.get("history", []) if isinstance(item, Mapping)]
    turn_results: List[Dict[str, Any]] = []

    for idx, turn in enumerate(scenario.get("turns", []), start=1):
        user_message = str(turn.get("user") or "")
        request_id = f"eval-{case_id}-{idx}"
        collected = _collect_stream(
            runtime,
            course_name=course_name,
            mode=mode,
            user_message=user_message,
            history=history,
            request_id=request_id,
        )
        artifact = collected["artifact"]
        response_text = collected["response_text"]
        rule_result = evaluate_turn_rules(turn, artifact)
        session = _as_dict(artifact.get("session"))
        turn_result: Dict[str, Any] = {
            "turn_index": idx,
            "user": user_message,
            "expect": _as_dict(turn.get("expect")),
            "response_text": response_text,
            "visible_output": rule_result["visible_output"],
            "artifact_run_id": artifact.get("run_id"),
            "artifact_path": collected.get("artifact_path"),
            "skill_id": session.get("skill_id"),
            "rule_result": rule_result,
        }
        if judge is not None:
            turn_result["judge_result"] = judge(build_judge_payload(scenario, turn_result))
        turn_results.append(turn_result)

        assistant_msg: Dict[str, Any] = {"role": "assistant", "content": response_text}
        if collected["tool_calls"]:
            assistant_msg["tool_calls"] = collected["tool_calls"]
        history.extend(
            [
                {"role": "user", "content": user_message},
                assistant_msg,
            ]
        )

    return {
        "case_id": case_id,
        "mode": mode,
        "course_name": course_name,
        "passed": all(_as_dict(item.get("rule_result")).get("passed") for item in turn_results),
        "turns": turn_results,
    }


def summarize_results(results: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    items = [dict(item) for item in results if isinstance(item, Mapping)]
    passed = sum(1 for item in items if item.get("passed"))
    failed = len(items) - passed
    turn_total = sum(len(item.get("turns", [])) for item in items)
    turn_failed = 0
    for item in items:
        for turn in item.get("turns", []):
            if not _as_dict(turn.get("rule_result")).get("passed"):
                turn_failed += 1
    return {
        "total": len(items),
        "passed": passed,
        "failed": failed,
        "turn_total": turn_total,
        "turn_failed": turn_failed,
    }
