"""Application service layer for policy impact workflows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from policy_impact.company_wiki.guide import build_wiki_blueprint
from policy_impact.company_wiki.loader import company_dir, load_company_knowledge
from policy_impact.conversation.main_agent import MainAgentService
from policy_impact.conversation.report_chat import ReportChatService
from policy_impact.memory.store import PolicyMemoryStore
from policy_impact.skill_executors.recent_news_report import run_recent_news_report
from policy_impact.skill_executors.policy_weekly_impact import run_policy_weekly_impact

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class PolicyImpactService:
    def _read_optional_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {"error": str(exc), "path": str(path)}
        if not isinstance(data, dict):
            return {"error": "JSON report root must be an object", "path": str(path)}
        return data

    def list_companies(self) -> list[str]:
        base = company_dir("company_001").parents[0]
        if not base.exists():
            return []
        return sorted(path.name for path in base.iterdir() if path.is_dir())

    def list_company_summaries(self) -> list[dict[str, Any]]:
        summaries = []
        for company_id in self.list_companies():
            try:
                overview = self.company_overview(company_id)
            except FileNotFoundError:
                summaries.append(
                    {
                        "company_id": company_id,
                        "company_name": company_id,
                        "short_name": company_id,
                        "stock_code": "",
                        "fact_count": 0,
                        "high_importance_fact_count": 0,
                    }
                )
                continue
            profile = overview.get("profile") or {}
            summaries.append(
                {
                    "company_id": company_id,
                    "company_name": profile.get("company_name") or company_id,
                    "short_name": profile.get("short_name") or profile.get("company_name") or company_id,
                    "stock_code": profile.get("stock_code") or "",
                    "business_keywords": profile.get("business_keywords") or [],
                    "fact_count": overview.get("fact_count", 0),
                    "high_importance_fact_count": overview.get("high_importance_fact_count", 0),
                    "open_question_count": len(overview.get("open_questions") or []),
                }
            )
        return summaries

    def company_overview(self, company_id: str) -> dict[str, Any]:
        knowledge = load_company_knowledge(company_id)
        facts = knowledge.get("facts", [])
        return {
            "profile": knowledge.get("profile", {}),
            "fact_count": len(facts),
            "high_importance_fact_count": sum(1 for fact in facts if int(fact.get("importance", 0)) >= 5),
            "open_questions": [fact for fact in facts if float(fact.get("confidence", 1.0)) < 0.7],
        }

    def run_weekly_analysis(self, company_id: str) -> dict[str, Any]:
        result = run_policy_weekly_impact(company_id)
        state = result.state
        return {
            "status": state.current_stage,
            "run_id": state.run_id,
            "summary": result.summary,
            "report_path": result.report_path,
            "html_report_path": state.report_paths.get("html_report"),
            "run_artifact_path": result.run_artifact_path,
            "metrics": state.quality_metrics,
            "warnings": state.warnings,
            "errors": state.errors,
        }

    def latest_report(self, company_id: str) -> dict[str, Any] | None:
        session = PolicyMemoryStore(company_id).latest_report_session()
        if not session:
            return None
        path = Path(session["report_path"])
        metadata = json.loads(session.get("metadata") or "{}")
        return {
            "report_id": session["report_id"],
            "report_path": session["report_path"],
            "html_report_path": metadata.get("html_report_path"),
            "content": path.read_text(encoding="utf-8") if path.exists() else "",
        }

    def latest_eval_report(self) -> dict[str, Any]:
        return self._read_optional_json(PROJECT_ROOT / "data" / "eval" / "reports" / "latest_eval_report.json")

    def latest_benchmark_report(self) -> dict[str, Any]:
        return self._read_optional_json(PROJECT_ROOT / "data" / "benchmark" / "latest_benchmark_report.json")

    def latest_replay_report(self) -> dict[str, Any]:
        return self._read_optional_json(PROJECT_ROOT / "data" / "replay" / "latest_replay_report.json")

    def ask_report(self, company_id: str, question: str, report_id: str | None = None) -> dict[str, Any]:
        return ReportChatService(company_id).ask(question, report_id=report_id)

    def pending_corrections(self, company_id: str) -> list[dict[str, Any]]:
        return ReportChatService(company_id).pending_corrections()

    def apply_correction(self, company_id: str, correction_id: str) -> dict[str, Any]:
        return ReportChatService(company_id).apply_correction(correction_id)

    def wiki_blueprint(self, company_id: str) -> dict[str, Any]:
        return build_wiki_blueprint(company_id)

    def run_news_analysis(self, company_id: str) -> dict[str, Any]:
        result = run_recent_news_report(company_id)
        return {
            "status": result["status"],
            "run_id": result["run_id"],
            "report_path": result["report_path"],
            "html_report_path": result["html_report_path"],
            "artifact_path": result["artifact_path"],
            "event_count": result["event_count"],
        }

    def chat(self, company_id: str, message: str, session_id: str = "main") -> dict[str, Any]:
        return MainAgentService(company_id).ask(message=message, session_id=session_id)
