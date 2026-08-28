import json

import pytest

from policy_impact.eval.runner import EvalSuiteError, run_eval_suite
from scripts import run_eval


def _write_jsonl(path, rows):
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def test_run_eval_suite_scores_two_passing_cases(tmp_path):
    suite_path = tmp_path / "suite.jsonl"
    _write_jsonl(
        suite_path,
        [
            {
                "case_id": "case-1",
                "expected_keywords": ["digitalization", "compliance"],
                "report_text": "Digitalization evidence improves compliance review.",
                "forbidden_keywords": ["unsupported", "guaranteed"],
            },
            {
                "case_id": "case-2",
                "expected_keywords": ["evidence"],
                "report_text": "The report cites evidence for the policy impact.",
                "forbidden_keywords": ["unsupported"],
            },
        ],
    )

    report = run_eval_suite(suite_path)

    assert report["suite_path"] == str(suite_path)
    assert report["case_count"] == 2
    assert report["passed_count"] == 2
    assert report["failed_count"] == 0
    assert report["unsupported_claim_rate"] == 0.0
    assert report["forbidden_case_rate"] == 0.0
    assert report["score"] == 100
    assert report["schema_version"] == "eval_report.v1"
    assert report["runner"] == "policy_impact.keyword_eval"
    assert report["runner_version"]
    assert report["generated_at"]
    assert report["suite_id"] == "suite"
    assert report["suite_version"] == "unversioned"
    assert report["suite_type"] == "keyword_fixture"
    assert report["threshold"] == 100
    assert report["passed"] is True
    assert "forbidden_case_rate" in report["metric_notes"]
    assert all(result["passed"] for result in report["results"])


def test_run_eval_suite_fails_case_with_forbidden_keyword(tmp_path):
    suite_path = tmp_path / "suite.jsonl"
    _write_jsonl(
        suite_path,
        [
            {
                "case_id": "case-1",
                "expected_keywords": ["compliance"],
                "report_text": "The compliance outcome is guaranteed.",
                "forbidden_keywords": ["guaranteed", "unsupported"],
            },
        ],
    )

    report = run_eval_suite(suite_path)

    assert report["case_count"] == 1
    assert report["passed_count"] == 0
    assert report["failed_count"] == 1
    assert report["unsupported_claim_rate"] == 1.0
    assert report["forbidden_case_rate"] == 1.0
    assert report["score"] == 0
    assert report["passed"] is False
    assert report["results"] == [
        {
            "case_id": "case-1",
            "passed": False,
            "missing_keywords": [],
            "forbidden_keywords": ["guaranteed"],
        }
    ]


def test_run_eval_suite_handles_empty_suite(tmp_path):
    suite_path = tmp_path / "empty.jsonl"
    suite_path.write_text("", encoding="utf-8")

    report = run_eval_suite(suite_path)

    assert report["suite_path"] == str(suite_path)
    assert report["case_count"] == 0
    assert report["passed_count"] == 0
    assert report["failed_count"] == 0
    assert report["unsupported_claim_rate"] == 0.0
    assert report["score"] == 0
    assert report["passed"] is False
    assert report["results"] == []


def test_run_eval_suite_fails_case_with_missing_expected_keyword(tmp_path):
    suite_path = tmp_path / "suite.jsonl"
    _write_jsonl(
        suite_path,
        [
            {
                "case_id": "case-1",
                "expected_keywords": ["digitalization", "compliance"],
                "report_text": "The report only mentions compliance evidence.",
                "forbidden_keywords": ["unsupported"],
            },
        ],
    )

    report = run_eval_suite(suite_path)

    assert report["passed"] is False
    assert report["score"] == 0
    assert report["results"] == [
        {
            "case_id": "case-1",
            "passed": False,
            "missing_keywords": ["digitalization"],
            "forbidden_keywords": [],
        }
    ]


def test_run_eval_suite_wraps_malformed_jsonl_with_path_and_line(tmp_path):
    suite_path = tmp_path / "broken.jsonl"
    suite_path.write_text(
        json.dumps(
            {
                "case_id": "case-1",
                "expected_keywords": ["evidence"],
                "report_text": "evidence",
                "forbidden_keywords": [],
            }
        )
        + "\n"
        + "{broken json\n",
        encoding="utf-8",
    )

    with pytest.raises(EvalSuiteError) as exc_info:
        run_eval_suite(suite_path)

    message = str(exc_info.value)
    assert str(suite_path) in message
    assert "line 2" in message


def test_run_eval_suite_accepts_utf8_bom_jsonl(tmp_path):
    suite_path = tmp_path / "bom.jsonl"
    suite_path.write_text(
        json.dumps(
            {
                "case_id": "case-1",
                "expected_keywords": ["evidence"],
                "report_text": "The report includes evidence.",
                "forbidden_keywords": ["unsupported"],
            }
        )
        + "\n",
        encoding="utf-8-sig",
    )

    report = run_eval_suite(suite_path)

    assert report["case_count"] == 1
    assert report["score"] == 100
    assert report["passed"] is True


def test_resolve_suite_path_prefers_project_root_for_relative_paths(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    suite_path = run_eval._resolve_suite_path("data/eval/gold_policy_impact.jsonl")

    assert suite_path == run_eval.ROOT / "data" / "eval" / "gold_policy_impact.jsonl"


def test_cli_writes_latest_eval_report(monkeypatch, tmp_path, capsys):
    report_path = tmp_path / "latest_eval_report.json"
    monkeypatch.setattr(run_eval, "LATEST_REPORT_PATH", report_path)
    monkeypatch.setattr(run_eval.sys, "argv", ["run_eval.py", "--suite", "gold"])

    exit_code = run_eval.main()

    printed_report = json.loads(capsys.readouterr().out)
    written_report = json.loads(report_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert written_report == printed_report
    assert written_report["score"] == 100
    assert written_report["suite_type"] == "keyword_fixture"


def test_cli_returns_nonzero_when_eval_report_fails(monkeypatch, tmp_path, capsys):
    report_path = tmp_path / "latest_eval_report.json"
    monkeypatch.setattr(run_eval, "LATEST_REPORT_PATH", report_path)
    monkeypatch.setattr(run_eval.sys, "argv", ["run_eval.py", "--suite", "gold"])

    def failing_suite(_suite_path):
        return {
            "schema_version": "eval_report.v1",
            "suite_type": "keyword_fixture",
            "passed": False,
            "score": 0,
            "results": [],
        }

    monkeypatch.setattr(run_eval, "run_eval_suite", failing_suite)

    exit_code = run_eval.main()

    printed_report = json.loads(capsys.readouterr().out)
    written_report = json.loads(report_path.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert written_report == printed_report
    assert written_report["passed"] is False
