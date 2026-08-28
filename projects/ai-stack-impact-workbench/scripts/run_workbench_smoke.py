"""End-to-end smoke test for the Agent Workbench using fabricated local data."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from policy_impact.app.chat_workbench_service import ChatWorkbenchService
from policy_impact.company_wiki import loader as wiki_loader
from policy_impact.harness import artifacts as artifact_helpers
from policy_impact.harness.model_config import DEFAULT_MODEL_ID
from policy_impact.memory.store import PolicyMemoryStore
from policy_impact.policy_data import repository as policy_repository
from policy_impact.skill_executors import recent_news_report as news_executor


COMPANY_ID = "company_smoke"


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="policy_impact_workbench_") as raw_tmp:
        project_root = Path(raw_tmp)
        _patch_project_root(project_root)
        _seed_project(project_root)

        service = ChatWorkbenchService()
        store = PolicyMemoryStore(COMPANY_ID)
        store.seed_model_configs()
        store.save_memory(
            namespace=f"company:{COMPANY_ID}:chat",
            memory_type="preference",
            content="The user wants Agent Harness answers with trace, gate, memory, and artifact evidence.",
            importance=0.9,
            metadata={"source_type": "manual_smoke", "path": ".daily/memory/preferences.md"},
        )

        models = service.list_models(COMPANY_ID)
        model_ids = [item["model_id"] for item in models]
        if model_ids != [DEFAULT_MODEL_ID]:
            raise AssertionError(f"unexpected model list: {model_ids}")

        skills = service.list_skills()
        skill_ids = [item["id"] for item in skills]
        _assert_contains_all(
            skill_ids,
            [
                "general_chat",
                "policy_weekly_impact",
                "recent_news_report",
                "research_report",
                "company_wiki_blueprint",
            ],
        )

        chat_result = _run_turn(
            service,
            title="Smoke Chat",
            mode="chat",
            active_skill_id="general_chat",
            message="What Agent Harness and trace capabilities are recorded for this company?",
            required_steps=["context_build", "memory_read", "tool_call", "final_answer"],
        )
        if "根据企业知识库和长期记忆" not in chat_result["answer"]:
            raise AssertionError("general_chat did not use fabricated wiki or memory evidence")

        research_result = _run_turn(
            service,
            title="Smoke Research",
            mode="skill",
            active_skill_id="research_report",
            message="Research what a mature Agent Harness should include for enterprise demos.",
            required_steps=["memory_read", "model_call", "artifact_write", "final_answer"],
        )
        _assert_existing_paths(research_result["artifacts"])

        news_result = _run_turn(
            service,
            title="Smoke News",
            mode="skill",
            active_skill_id="recent_news_report",
            message="Generate a recent news impact report.",
            required_steps=["tool_call", "gate_check", "artifact_write", "final_answer"],
        )
        _assert_existing_paths(news_result["artifacts"])

        policy_result = _run_turn(
            service,
            title="Smoke Policy",
            mode="skill",
            active_skill_id="policy_weekly_impact",
            message="Generate this week's policy impact report.",
            required_steps=["tool_call", "gate_check", "artifact_write", "final_answer"],
        )
        _assert_existing_paths(policy_result["artifacts"])

        summary = {
            "status": "passed",
            "project_root": str(project_root),
            "models": model_ids,
            "skills": skill_ids,
            "runs": {
                "general_chat": chat_result["run_id"],
                "research_report": research_result["run_id"],
                "recent_news_report": news_result["run_id"],
                "policy_weekly_impact": policy_result["run_id"],
            },
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))


def _patch_project_root(project_root: Path) -> None:
    def _root() -> Path:
        return project_root

    artifact_helpers.project_root = _root
    wiki_loader.project_root = _root
    policy_repository.project_root = _root
    news_executor.project_root = _root


def _seed_project(project_root: Path) -> None:
    company_dir = project_root / "data" / "companies" / COMPANY_ID
    wiki_dir = company_dir / "wiki"
    news_dir = project_root / "data" / "news" / "raw"
    policies_dir = project_root / "data" / "policies" / "raw"
    wiki_dir.mkdir(parents=True)
    news_dir.mkdir(parents=True)
    policies_dir.mkdir(parents=True)

    (company_dir / "company.yaml").write_text(
        json.dumps(
            {
                "company_id": COMPANY_ID,
                "company_name": "Smoke Agent Lab",
                "business_keywords": ["agent", "harness", "trace", "memory", "observability"],
                "region": {"city": "Shanghai"},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (wiki_dir / "agent_platform.md").write_text(
        """---
domain: agent_platform
importance: 5
confidence: 0.92
---
## FACT: fact_agent_harness_trace
- value: Builds Agent Harness systems with ToolGateway, ContextManifest, Gate checks, Replay, Memory, and artifact trace.
- importance: 5
- confidence: 0.92
- source: smoke wiki
- policy_relevance: [agent, harness, trace, memory, observability]

## FACT: fact_enterprise_demo
- value: Enterprise demos require reproducible traces, model selection, skill routing, and auditable artifacts.
- importance: 5
- confidence: 0.9
- source: smoke wiki
- policy_relevance: [enterprise, demo, artifact, replay]
""",
        encoding="utf-8",
    )
    (news_dir / "smoke_news.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "title": "Agent observability vendors add trace replay features",
                        "source": "smoke_news",
                        "published_at": "2026-07-01",
                        "url": "https://example.com/agent-trace",
                        "summary": "New products emphasize harness trace, replay, memory governance, and model routing.",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "title": "Enterprise teams demand auditable AI agent tool policies",
                        "source": "smoke_news",
                        "published_at": "2026-07-01",
                        "url": "https://example.com/tool-policy",
                        "summary": "Procurement teams ask vendors to show tool permission logs and artifact evidence.",
                    },
                    ensure_ascii=False,
                ),
            ]
        ),
        encoding="utf-8",
    )
    (policies_dir / "smoke_agent_governance.md").write_text(
        """---
policy_id: smoke_agent_governance
title: Agent System Trace And Governance Pilot
issuer: Smoke Technology Bureau
region_scope: [Shanghai]
published_at: 2026-07-01
source_url: https://example.com/policy/agent-governance
source_level: L3
policy_type: [technology, compliance]
---
AI agent service providers should retain tool call records, context manifests,
memory update records, gate review results, replay artifacts, and final reports.

Enterprises building agent platforms are encouraged to provide observable
runtime traces and controlled model/tool routing for internal audits.
""",
        encoding="utf-8",
    )


def _run_turn(
    service: ChatWorkbenchService,
    title: str,
    mode: str,
    active_skill_id: str,
    message: str,
    required_steps: list[str],
) -> dict[str, Any]:
    session = service.create_session(
        company_id=COMPANY_ID,
        title=title,
        mode=mode,
        model_id=DEFAULT_MODEL_ID,
        active_skill_id=active_skill_id,
    )
    result = service.send_message(COMPANY_ID, session["session_id"], message)
    trace = service.latest_trace(COMPANY_ID, session["session_id"])
    messages = service.list_messages(COMPANY_ID, session["session_id"])
    steps = [step["step_type"] for step in trace["steps"]]
    _assert_contains_all(steps, required_steps)
    if result["selected_skill_id"] != active_skill_id:
        raise AssertionError(f"expected {active_skill_id}, got {result['selected_skill_id']}")
    if trace["run_id"] != result["run_id"]:
        raise AssertionError("latest_trace returned a different run_id")
    if [item["role"] for item in messages][-2:] != ["user", "assistant"]:
        raise AssertionError("chat messages did not persist user/assistant turn")
    return result


def _assert_contains_all(values: list[str], expected: list[str]) -> None:
    missing = [item for item in expected if item not in values]
    if missing:
        raise AssertionError(f"missing expected values {missing}; actual={values}")


def _assert_existing_paths(artifacts: dict[str, str]) -> None:
    if not artifacts:
        raise AssertionError("expected at least one artifact")
    for artifact_type, raw_path in artifacts.items():
        if raw_path and not Path(raw_path).exists():
            raise AssertionError(f"{artifact_type} artifact does not exist: {raw_path}")


if __name__ == "__main__":
    main()
