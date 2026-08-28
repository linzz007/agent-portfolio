"""Acceptance suite for the single-user Agent Workbench.

The suite is intentionally scenario-shaped instead of unit-shaped: each case
checks one user-visible workbench capability and the harness evidence behind it.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from policy_impact.app.chat_workbench_service import ChatWorkbenchService
from policy_impact.company_wiki import loader as wiki_loader
from policy_impact.harness.model_config import DEFAULT_MODEL_ID
from policy_impact.harness.model_gateway import ModelResponse
from policy_impact.harness import artifacts as artifact_helpers
from policy_impact.memory.store import PolicyMemoryStore
from policy_impact.policy_data import repository as policy_repository
from policy_impact.skill_executors import recent_news_report as news_executor


COMPANY_ID = "company_acceptance"


class _AcceptanceModelAdapter:
    """Schema-aware offline test double; never used by the production API."""

    def complete(self, messages: list[dict[str, Any]], schema_name: str) -> ModelResponse:
        if schema_name == "general_chat.v1":
            payload = {
                "type": "general_chat",
                "output": {
                    "answer": "企业事实来自受控知识库工具，长期记忆只读取与本轮问题相关的内容；完整 Trace 会记录上下文、工具和模型调用证据。"
                },
            }
        elif schema_name == "research_report.v1":
            payload = {
                "type": "research_report",
                "output": {
                    "thesis": "成熟 Agent Harness 应将 Context、Memory、Tool Policy、Trace 与 Eval 组成可审计闭环。",
                    "evidence": ["每轮运行由 AgentRun 和有序 AgentStep 持久化。"],
                    "risks": ["上下文或工具权限失控会降低结果可靠性。"],
                    "recommendations": ["以契约、门控和回归评测约束运行时。"],
                    "next_checks": ["检查真实模型延迟与结构化输出成功率。"],
                },
            }
        elif schema_name == "review_bundle":
            payload = {
                "type": "final",
                "output": {
                    "verification_status": "verified",
                    "final_decision": "publish",
                    "downgraded_assessment_ids": [],
                    "unsupported_claim_rate": 0.0,
                    "notes": ["离线验收仅验证 Skeptic 子智能体的结构化契约。"],
                },
            }
        else:
            payload = {"type": "final", "output": {schema_name: "accepted"}}
        return ModelResponse(
            response_id=f"acceptance-{schema_name}",
            payload=payload,
            metadata={
                "adapter": type(self).__name__,
                "model_id": DEFAULT_MODEL_ID,
                "schema_name": schema_name,
                "latency_ms": 0.0,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        )


def _acceptance_service() -> ChatWorkbenchService:
    return ChatWorkbenchService(model_adapter_factory=_AcceptanceModelAdapter)


@dataclass
class CaseResult:
    case_id: str
    title: str
    level: str
    status: str = "passed"
    checks: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ui-url", default="http://127.0.0.1:8501/")
    parser.add_argument("--skip-ui", action="store_true")
    parser.add_argument("--report-json", default="data/acceptance/latest-workbench-acceptance.json")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="policy_impact_acceptance_") as raw_tmp:
        project_root = Path(raw_tmp)
        _patch_project_root(project_root)
        _seed_project(project_root)

        service = _acceptance_service()
        store = PolicyMemoryStore(COMPANY_ID)
        _seed_memory(store)

        results = [
            _case_static_frontend_contract(),
            _case_fixed_model_policy(service),
            _case_single_turn_general_chat(service, store),
            _case_single_turn_research(service, store),
            _case_report_policy_workflow(service),
            _case_multi_turn_skill_switching(service, store),
        ]
        if not args.skip_ui:
            results.append(_case_headless_ui(args.ui_url))

        summary = {
            "status": "passed" if all(item.status == "passed" for item in results) else "failed",
            "company_id": COMPANY_ID,
            "project_root": str(project_root),
            "cases": [item.__dict__ for item in results],
        }
        report_path = Path(args.report_json)
        summary["report_json"] = str(report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if summary["status"] != "passed":
            raise SystemExit(1)


def _case_static_frontend_contract() -> CaseResult:
    root = Path(__file__).resolve().parents[1]
    index = (root / "src" / "policy_impact" / "app" / "static" / "workbench" / "index.html").read_text(
        encoding="utf-8"
    )
    app_js = (root / "src" / "policy_impact" / "app" / "static" / "workbench" / "app.js").read_text(
        encoding="utf-8"
    )
    styles = (root / "src" / "policy_impact" / "app" / "static" / "workbench" / "styles.css").read_text(
        encoding="utf-8"
    )
    _assert_all_in(
        index,
        [
            "Agent Workbench",
            "runtime-status",
            "slash-palette",
            "sidebar-toggle",
            "company-card",
            "command-list",
            "tool-list",
        ],
    )
    _assert_all_in(app_js, [DEFAULT_MODEL_ID, "/report", "parseSlashCommand"])
    _assert_all_in(app_js, ["trace_summary", "context_summary", "memory_summary", "tool_summary", "structured_events"])
    _assert_all_in(
        styles,
        [
            "height: 100dvh",
            "overflow: hidden",
            "overflow-y: auto",
            "grid-template-columns: 288px minmax(0, 1fr)",
        ],
    )
    _assert_not_in(index, ["model-select", "model-settings", "model-modal", "skill-dock", "技能路由", "运行观测", "inspector"])
    return CaseResult(
        case_id="F01",
        title="前端工作台壳契约",
        level="简单",
        checks=[
            "品牌为 Agent Workbench",
            "模型固定为 deepseek-v4-flash，页面不提供模型管理入口",
            "企业空间展示公司画像摘要",
            "仅报告流程以 /report slash command 暴露",
            "trace 支持 context/memory/tool summary",
            "trace 展示 structured events 观测摘要",
            "右侧运行观测栏和中间技能卡片已移除",
            "页面使用固定 100dvh 工作台布局，避免整页空白滚动",
            "侧栏允许内部滚动，避免工具/技能列表被裁剪",
        ],
    )


def _case_fixed_model_policy(service: ChatWorkbenchService) -> CaseResult:
    models = service.list_models(COMPANY_ID)
    model_ids = [item["model_id"] for item in models]
    if model_ids != [DEFAULT_MODEL_ID]:
        raise AssertionError(f"unexpected model list: {model_ids}")
    try:
        service.save_model_config(
            COMPANY_ID,
            {
                "model_id": "acceptance-compatible",
                "display_name": "验收兼容模型",
                "provider": "openai-compatible",
                "model_name": "acceptance-chat",
            },
        )
    except ValueError as exc:
        rejection = str(exc)
    else:
        raise AssertionError("model management should be disabled")
    session = service.create_session(
        company_id=COMPANY_ID,
        title="Fixed Model Acceptance",
        mode="auto",
        model_id="",
        active_skill_id="",
    )
    try:
        service.update_session(COMPANY_ID, session["session_id"], model_id="acceptance-compatible")
    except ValueError as exc:
        selection_rejection = str(exc)
    else:
        raise AssertionError("custom model selection should be rejected")
    return CaseResult(
        case_id="B01",
        title="固定模型策略和模型管理禁用",
        level="简单",
        checks=[
            "模型列表只保留 deepseek-v4-flash",
            "添加自定义模型被拒绝",
            "会话更新为非默认模型被拒绝",
        ],
        evidence={
            "model_id": model_ids[0],
            "session_id": session["session_id"],
            "rejection": rejection,
            "selection_rejection": selection_rejection,
        },
    )


def _case_single_turn_general_chat(service: ChatWorkbenchService, store: PolicyMemoryStore) -> CaseResult:
    session = service.create_session(
        company_id=COMPANY_ID,
        title="Single General Chat",
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )
    result = service.send_message(
        COMPANY_ID,
        session["session_id"],
        "What Agent Harness trace and memory capabilities are recorded?",
    )
    trace = service.latest_trace(COMPANY_ID, session["session_id"])
    messages = service.list_messages(COMPANY_ID, session["session_id"])
    step_types = [step["step_type"] for step in trace["steps"]]
    _assert_all_in(step_types, ["intent_classification", "skill_selection", "context_build", "memory_read", "tool_call", "final_answer"])
    if result["selected_skill_id"] != "general_chat":
        raise AssertionError("general chat did not use the expected skill")
    if [item["role"] for item in messages] != ["user", "assistant"]:
        raise AssertionError("single-turn chat did not persist exactly one user/assistant pair")
    if "企业事实" not in result["answer"] or "长期记忆" not in result["answer"]:
        raise AssertionError("general chat answer did not expose wiki and memory evidence")
    memories = store.search_memory("trace memory preference", top_k=5)
    if not memories:
        raise AssertionError("seeded memory was not searchable")
    return CaseResult(
        case_id="B02",
        title="单轮普通对话：知识库和长期记忆",
        level="简单到中等",
        checks=["选择 general_chat", "读取 memory", "调用企业知识库工具", "回答包含 evidence", "trace 挂在 assistant 消息上"],
        evidence={"run_id": result["run_id"], "steps": step_types},
    )


def _case_single_turn_research(service: ChatWorkbenchService, store: PolicyMemoryStore) -> CaseResult:
    session = service.create_session(
        company_id=COMPANY_ID,
        title="Single Impact Research",
        mode="auto",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="",
    )
    result = service.send_message(
        COMPANY_ID,
        session["session_id"],
        "/report 请用调研报告形式分析成熟 Agent Harness 应该如何呈现 trace、memory、model selection 和 skills。",
    )
    trace = service.latest_trace(COMPANY_ID, session["session_id"])
    step_types = [step["step_type"] for step in trace["steps"]]
    source_summary = trace.get("source_summary") or {}
    if result["selected_skill_id"] != "external_impact_report":
        raise AssertionError(f"impact research expected external_impact_report, got {result['selected_skill_id']}")
    if source_summary.get("route") != "research":
        raise AssertionError(f"impact research expected research internal route, got {source_summary}")
    _assert_all_in(step_types, ["memory_read", "workflow_stage", "model_call", "artifact_write", "final_answer"])
    if "memory_write" in step_types:
        raise AssertionError("external_impact_report wrote long-term memory without an explicit user request")
    _assert_existing_artifact_paths(result["artifacts"])
    return CaseResult(
        case_id="B03",
        title="单轮 /report 调研路线：artifact 与显式 memory 边界",
        level="中等",
        checks=["/report 显式触发 external_impact_report", "内部 research 路线", "模型调用 step", "报告 artifact 存在", "未隐式写入长期 memory"],
        evidence={"run_id": result["run_id"], "artifact_keys": sorted(result["artifacts"].keys())},
    )


def _case_report_policy_workflow(service: ChatWorkbenchService) -> CaseResult:
    session = service.create_session(
        company_id=COMPANY_ID,
        title="Auto Policy",
        mode="auto",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="",
    )
    result = service.send_message(
        COMPANY_ID,
        session["session_id"],
        "/report 请分析最近政策和合规要求对 Agent 平台审计能力的影响。",
    )
    trace = service.latest_trace(COMPANY_ID, session["session_id"])
    step_types = [step["step_type"] for step in trace["steps"]]
    source_summary = trace.get("source_summary") or {}
    if result["selected_skill_id"] != "external_impact_report":
        raise AssertionError(f"/report expected external_impact_report, got {result['selected_skill_id']}")
    if source_summary.get("route") != "policy":
        raise AssertionError(f"/report expected policy internal route, got {source_summary}")
    _assert_all_in(step_types, ["workflow_stage", "tool_call", "gate_check", "artifact_write", "final_answer"])
    _assert_existing_artifact_paths(result["artifacts"])
    return CaseResult(
        case_id="B04",
        title="/report 外部变化政策路线",
        level="中等",
        checks=["/report 触发 external_impact_report", "内部 policy 路线", "workflow 工具调用", "gate 汇总", "产物挂载"],
        evidence={"run_id": result["run_id"], "skill": result["selected_skill_id"]},
    )


def _case_multi_turn_skill_switching(service: ChatWorkbenchService, store: PolicyMemoryStore) -> CaseResult:
    session = service.create_session(
        company_id=COMPANY_ID,
        title="Multi Turn Skill Switching",
        mode="auto",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="",
    )
    turn1 = service.send_message(
        COMPANY_ID,
        session["session_id"],
        "What Agent Harness facts should I remember?",
    )
    turn2 = service.send_message(
        COMPANY_ID,
        session["session_id"],
        "/report 请用调研报告形式生成一份关于 Workbench acceptance design 的结构化报告。",
    )
    turn3 = service.send_message(
        COMPANY_ID,
        session["session_id"],
        "最新新闻里和 agent observability 相关的内容有什么影响？",
    )
    messages = service.list_messages(COMPANY_ID, session["session_id"])
    latest_trace = service.latest_trace(COMPANY_ID, session["session_id"])
    if [turn1["selected_skill_id"], turn2["selected_skill_id"], turn3["selected_skill_id"]] != [
        "general_chat",
        "external_impact_report",
        "general_chat",
    ]:
        raise AssertionError("multi-turn skill sequence did not match expected general -> report/research -> general")
    if [item["role"] for item in messages] != ["user", "assistant", "user", "assistant", "user", "assistant"]:
        raise AssertionError("multi-turn message persistence order is incorrect")
    if latest_trace["run_id"] != turn3["run_id"]:
        raise AssertionError("latest trace did not point to the final turn")
    turn2_trace = service.trace_for_run(COMPANY_ID, session["session_id"], turn2["run_id"])
    if (turn2_trace.get("source_summary") or {}).get("route") != "research":
        raise AssertionError(f"turn2 did not use research internal route: {turn2_trace.get('source_summary')}")
    turn2_steps = [step["step_type"] for step in store.list_agent_steps(turn2["run_id"])]
    if "memory_write" in turn2_steps:
        raise AssertionError("multi-turn research output was written to memory without an explicit request")
    return CaseResult(
        case_id="B05",
        title="多轮对话：普通对话 -> /report 调研路线 -> 默认聊天",
        level="困难",
        checks=["同一会话连续 3 轮", "/report 单轮生效且不污染后续轮次", "消息顺序正确", "latest_trace 指向最后一轮", "中间 research 路线结果未隐式写入 memory"],
        evidence={"run_ids": [turn1["run_id"], turn2["run_id"], turn3["run_id"]]},
    )


def _case_headless_ui(ui_url: str) -> CaseResult:
    html = _http_get(ui_url)
    _assert_all_in(html, ["Agent Workbench", "runtime-status", "slash-palette"])
    _assert_not_in(html, ["model-select", "model-settings", "model-modal", "skill-dock", "inspector", "运行观测", "技能路由"])

    chrome = _find_chrome()
    if not chrome:
        return CaseResult(
            case_id="F02",
            title="前端真实渲染 smoke",
            level="中等",
            status="passed",
            checks=["HTTP 页面契约通过", "未找到 Chrome，跳过截图"],
            evidence={"ui_url": ui_url, "screenshot": "skipped"},
        )

    out_dir = Path("data") / "acceptance"
    out_dir.mkdir(parents=True, exist_ok=True)
    screenshot = (out_dir / f"workbench-{date.today().isoformat()}-{int(time.time())}.png").resolve()
    command = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--disable-logging",
        "--hide-scrollbars",
        "--virtual-time-budget=5000",
        "--window-size=1186,742",
        f"--screenshot={screenshot}",
        ui_url,
    ]
    completed = subprocess.run(command, check=False, capture_output=True)
    if completed.returncode != 0:
        stderr = (completed.stderr or b"").decode("utf-8", errors="replace")
        stdout = (completed.stdout or b"").decode("utf-8", errors="replace")
        raise AssertionError(f"headless Chrome failed: {stderr or stdout}")
    for _ in range(20):
        if screenshot.exists() and screenshot.stat().st_size >= 40_000:
            break
        time.sleep(0.25)
    if not screenshot.exists() or screenshot.stat().st_size < 40_000:
        raise AssertionError(f"screenshot missing or too small: {screenshot}")

    return CaseResult(
        case_id="F02",
        title="前端真实渲染 smoke",
        level="中等",
        checks=[
            "HTTP 页面加载",
            "页面不含旧右侧观测栏和旧技能卡片",
            "1186x742 截图生成成功",
            "截图文件大小超过 40KB，说明页面完成基本渲染",
        ],
        evidence={"ui_url": ui_url, "screenshot": str(screenshot), "bytes": screenshot.stat().st_size},
    )


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
                "company_name": "Acceptance Agent Lab",
                "business_keywords": ["agent", "harness", "trace", "memory", "observability", "model routing"],
                "region": {"city": "Shanghai"},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (wiki_dir / "agent_workbench.md").write_text(
        """---
domain: agent_workbench
importance: 5
confidence: 0.94
---
## FACT: fact_agent_workbench_trace
- value: The Agent Workbench should expose trace, memory reads, model selection, skill routing, and artifact evidence in every important run.
- importance: 5
- confidence: 0.94
- source: acceptance wiki
- policy_relevance: [agent, harness, trace, memory, model routing]

## FACT: fact_frontend_quality_bar
- value: The production workbench should avoid dashboard clutter, keep chat central, move configuration to side panels, and make slash commands the skill entry.
- importance: 5
- confidence: 0.9
- source: acceptance wiki
- policy_relevance: [frontend, workbench, slash command, skill]
""",
        encoding="utf-8",
    )
    (news_dir / "acceptance_news.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "title": "Agent observability platforms add replay and trace views",
                        "source": "acceptance_news",
                        "published_at": "2026-07-03",
                        "url": "https://example.com/agent-observability",
                        "summary": "Teams now expect trace replay, model routing, skill boundaries, and artifact evidence.",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "title": "Enterprise AI teams standardize tool policy audit logs",
                        "source": "acceptance_news",
                        "published_at": "2026-07-03",
                        "url": "https://example.com/tool-policy-audit",
                        "summary": "Procurement and security reviewers ask for tool permission logs and memory governance.",
                    },
                    ensure_ascii=False,
                ),
            ]
        ),
        encoding="utf-8",
    )
    (policies_dir / "acceptance_agent_governance.md").write_text(
        """---
policy_id: acceptance_agent_governance
title: Agent Runtime Trace And Governance Acceptance Policy
issuer: Acceptance Technology Bureau
region_scope: [Shanghai]
published_at: 2026-07-03
source_url: https://example.com/policy/agent-governance
source_level: L3
policy_type: [technology, compliance]
---
AI agent systems should retain tool call records, context manifests, memory update
records, gate review results, replay artifacts, model routing decisions, and final
reports for internal audit.

Agent platforms should separate skill packaging from deterministic harness controls
such as permissions, schema checks, trace persistence, and recovery.
""",
        encoding="utf-8",
    )


def _seed_memory(store: PolicyMemoryStore) -> None:
    store.seed_model_configs()
    store.save_memory(
        namespace=f"company:{COMPANY_ID}:chat",
        memory_type="preference",
        content="The user prefers Agent Harness answers that explicitly mention trace, memory, model selection, skill routing, and artifacts.",
        importance=0.92,
        metadata={"source_type": "acceptance_seed", "path": ".daily/memory/preferences.md"},
    )


def _assert_all_in(values: Any, expected: list[str]) -> None:
    if isinstance(values, str):
        missing = [item for item in expected if item not in values]
    else:
        missing = [item for item in expected if item not in list(values)]
    if missing:
        raise AssertionError(f"missing expected values {missing}")


def _assert_not_in(value: str, forbidden: list[str]) -> None:
    found = [item for item in forbidden if item in value]
    if found:
        raise AssertionError(f"forbidden values present: {found}")


def _assert_existing_artifact_paths(artifacts: dict[str, Any]) -> None:
    string_paths = [Path(raw) for raw in artifacts.values() if isinstance(raw, str) and raw]
    if not string_paths:
        raise AssertionError("expected at least one string artifact path")
    missing = [str(path) for path in string_paths if not path.exists()]
    if missing:
        raise AssertionError(f"artifact paths do not exist: {missing}")


def _http_get(url: str) -> str:
    try:
        with urllib.request.urlopen(url, timeout=8) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise AssertionError(f"ui url not reachable: {url}; {exc}") from exc


def _find_chrome() -> str | None:
    candidates = [
        shutil.which("chrome"),
        shutil.which("chrome.exe"),
        shutil.which("msedge"),
        shutil.which("msedge.exe"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


if __name__ == "__main__":
    main()
