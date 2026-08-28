import importlib.util
from pathlib import Path

from policy_impact.app import service as service_module
from policy_impact.app.service import PolicyImpactService


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_run_replay_module():
    module_path = PROJECT_ROOT / "scripts" / "run_replay.py"
    spec = importlib.util.spec_from_file_location("run_replay", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_service_generates_report_and_answers_question():
    service = PolicyImpactService()
    result = service.run_weekly_analysis("company_001")
    assert result["status"] == "done"
    assert result["report_path"]

    report = service.latest_report("company_001")
    assert report
    assert "政策影响报告" in report["content"]

    answer = service.ask_report("company_001", "为什么这条政策和企业有关？")
    assert answer["answer"]
    assert answer["citations"]


def test_service_lists_company_summaries_with_profile_and_fact_counts():
    summaries = PolicyImpactService().list_company_summaries()
    company = next(item for item in summaries if item["company_id"] == "company_001")

    assert company["short_name"] == "示例公司"
    assert company["stock_code"] == "300033.SZ"
    assert company["fact_count"] >= 6
    assert company["high_importance_fact_count"] >= 4


def test_report_chat_creates_correction_proposal():
    service = PolicyImpactService()
    service.run_weekly_analysis("company_001")
    result = service.ask_report("company_001", "纠正：公司已经有 8 项软件著作权")
    assert result["correction_proposal"]
    assert result["correction_proposal"]["status"] == "pending"


def test_latest_harness_reports_return_dicts_and_missing_reports_are_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "PROJECT_ROOT", tmp_path, raising=False)
    eval_report_path = tmp_path / "data" / "eval" / "reports" / "latest_eval_report.json"
    benchmark_report_path = tmp_path / "data" / "benchmark" / "latest_benchmark_report.json"
    replay_report_path = tmp_path / "data" / "replay" / "latest_replay_report.json"
    eval_report_path.parent.mkdir(parents=True)
    benchmark_report_path.parent.mkdir(parents=True)
    replay_report_path.parent.mkdir(parents=True)
    eval_report_path.write_text('{"suite": "gold"}', encoding="utf-8")
    benchmark_report_path.write_text('{"baseline": "b0"}', encoding="utf-8")
    replay_report_path.write_text('{"status": "replayed", "checkpoint": "demo"}', encoding="utf-8")

    service = PolicyImpactService()

    assert service.latest_eval_report() == {"suite": "gold"}
    assert service.latest_benchmark_report() == {"baseline": "b0"}
    assert service.latest_replay_report() == {"status": "replayed", "checkpoint": "demo"}


def test_missing_harness_reports_are_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "PROJECT_ROOT", tmp_path, raising=False)

    service = PolicyImpactService()

    assert service.latest_replay_report() == {}


def test_malformed_optional_json_report_returns_error(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "PROJECT_ROOT", tmp_path, raising=False)
    eval_report_path = tmp_path / "data" / "eval" / "reports" / "latest_eval_report.json"
    eval_report_path.parent.mkdir(parents=True)
    eval_report_path.write_text("{not json", encoding="utf-8")

    report = PolicyImpactService().latest_eval_report()

    assert report["error"]
    assert report["path"] == str(eval_report_path)


def test_run_replay_writes_latest_report(tmp_path):
    run_replay = _load_run_replay_module()
    report = {"status": "replayed", "checkpoint": "demo", "state": {"large": "payload"}}

    output_path = run_replay.write_latest_replay_report(report, root=tmp_path)

    assert output_path == tmp_path / "data" / "replay" / "latest_replay_report.json"
    assert output_path.read_text(encoding="utf-8")
    assert PolicyImpactService()._read_optional_json(output_path) == report
