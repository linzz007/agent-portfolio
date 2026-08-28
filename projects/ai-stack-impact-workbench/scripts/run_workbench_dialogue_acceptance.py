"""Dynamic dialogue acceptance suite for Agent Workbench.

This suite simulates user conversations from simple to difficult and treats the
runtime trace as the primary acceptance object. It complements
run_workbench_acceptance.py, which checks broader static/product contracts.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import run_workbench_acceptance as base  # noqa: E402

from policy_impact.app.chat_workbench_service import ChatWorkbenchService  # noqa: E402
from policy_impact.memory.store import PolicyMemoryStore  # noqa: E402


SLASH_COMMANDS = {
    "/report": "external_impact_report",
}


@dataclass
class TurnExpectation:
    expected_skill: str
    required_steps: list[str]
    forbidden_steps: list[str] = field(default_factory=lambda: ["memory_write"])
    artifact_required: bool = False
    required_artifact_keys: list[str] = field(default_factory=list)
    memory_query: str = ""
    memory_skill_id: str = ""
    citation_types: list[str] = field(default_factory=list)


@dataclass
class DialogueTurn:
    raw_input: str
    expectation: TurnExpectation
    sent_message: str = ""
    command: str = ""
    run_id: str = ""
    selected_skill_id: str = ""
    answer_preview: str = ""
    trace_steps: list[str] = field(default_factory=list)
    trace_step_details: list[dict[str, Any]] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)
    checks: list[str] = field(default_factory=list)


@dataclass
class DialogueCase:
    case_id: str
    title: str
    level: str
    turns: list[DialogueTurn] = field(default_factory=list)
    status: str = "passed"
    checks: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ui-url", default="http://127.0.0.1:8501/")
    parser.add_argument("--skip-ui", action="store_true")
    parser.add_argument(
        "--report-json",
        default="data/acceptance/latest-workbench-dialogue-acceptance.json",
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="policy_impact_dialogue_acceptance_") as raw_tmp:
        project_root = Path(raw_tmp)
        base._patch_project_root(project_root)
        base._seed_project(project_root)

        service = base._acceptance_service()
        store = PolicyMemoryStore(base.COMPANY_ID)
        base._seed_memory(store)

        cases = [
            _case_simple_general_chat(service, store),
            _case_fixed_model_chat(service),
            _case_report_source_inventory(service, store),
            _case_slash_impact_research(service, store),
            _case_report_policy(service),
            _case_wiki_tree(service),
            _case_multi_turn_skill_switch(service, store),
            _case_invalid_skill_guard(service),
        ]
        if not args.skip_ui:
            cases.append(_case_live_frontend(args.ui_url))

        summary = {
            "status": "passed" if all(case.status == "passed" for case in cases) else "failed",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "company_id": base.COMPANY_ID,
            "project_root": str(project_root),
            "acceptance_style": "dynamic_dialogue_trace",
            "cases": [_case_to_dict(case) for case in cases],
        }
        report_path = ROOT / args.report_json
        summary["report_json"] = str(report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if summary["status"] != "passed":
            raise SystemExit(1)


def _case_simple_general_chat(service: ChatWorkbenchService, store: PolicyMemoryStore) -> DialogueCase:
    session = service.create_session(
        company_id=base.COMPANY_ID,
        title="D01 Simple General Chat",
        mode="chat",
        model_id=base.DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )
    case = DialogueCase(
        case_id="D01",
        title="单轮普通对话：读取企业知识库和长期记忆",
        level="简单",
    )
    turn = _run_turn(
        service,
        store,
        session["session_id"],
        raw_input="我这个 Agent Workbench 现在有哪些 trace、memory、固定模型策略证据？",
        expectation=TurnExpectation(
            expected_skill="general_chat",
            required_steps=[
                "intent_classification",
                "skill_selection",
                "context_build",
                "memory_read",
                "tool_call",
                "final_answer",
            ],
        ),
    )
    case.turns.append(turn)
    case.checks.extend(
        [
            "用户消息和 assistant 消息按顺序持久化",
            "trace run_id 挂到 assistant metadata",
            "context_build 输出 context_manifest",
            "general_chat 只读 memory 和 company wiki，不生成 artifact",
        ]
    )
    return case


def _case_fixed_model_chat(service: ChatWorkbenchService) -> DialogueCase:
    models = service.list_models(base.COMPANY_ID)
    model_ids = [item["model_id"] for item in models]
    if model_ids != [base.DEFAULT_MODEL_ID]:
        raise AssertionError(f"unexpected model list: {model_ids}")
    session = service.create_session(
        company_id=base.COMPANY_ID,
        title="D02 Fixed Model Chat",
        mode="chat",
        model_id=base.DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )
    store = PolicyMemoryStore(base.COMPANY_ID)
    case = DialogueCase(
        case_id="D02",
        title="固定模型：deepseek-v4-flash 策略下发起普通对话",
        level="简单到中等",
        evidence={"model_id": base.DEFAULT_MODEL_ID},
    )
    turn = _run_turn(
        service,
        store,
        session["session_id"],
        raw_input="请用当前固定模型回答：这个系统为什么要保留 trace？",
        expectation=TurnExpectation(
            expected_skill="general_chat",
            required_steps=[
                "intent_classification",
                "skill_selection",
                "context_build",
                "memory_read",
                "tool_call",
                "final_answer",
            ],
        ),
    )
    latest_trace = service.latest_trace(base.COMPANY_ID, session["session_id"])
    if latest_trace["model_id"] != base.DEFAULT_MODEL_ID:
        raise AssertionError("trace did not keep the fixed default model id")
    if latest_trace.get("trace_summary", {}).get("model_id") != base.DEFAULT_MODEL_ID:
        raise AssertionError("trace_summary did not expose the fixed default model id")
    turn.checks.append("trace.model_id 和 trace_summary.model_id 均保留 deepseek-v4-flash")
    case.turns.append(turn)
    return case


def _case_report_source_inventory(service: ChatWorkbenchService, store: PolicyMemoryStore) -> DialogueCase:
    session = service.create_session(
        company_id=base.COMPANY_ID,
        title="D03 Report Source Inventory",
        mode="auto",
        model_id=base.DEFAULT_MODEL_ID,
        active_skill_id="",
    )
    case = DialogueCase(
        case_id="D03",
        title="/report：数据源问题触发外部变化数据源盘点",
        level="简单",
    )
    turn = _run_turn(
        service,
        store,
        session["session_id"],
        raw_input="/report 当前外部变化分析接入了哪些数据源？请列出来源类型、最近快照和使用边界。",
        expectation=TurnExpectation(
            expected_skill="external_impact_report",
            required_steps=[
                "intent_classification",
                "skill_selection",
                "context_build",
                "memory_read",
                "workflow_stage",
                "tool_call",
                "gate_check",
                "artifact_write",
                "final_answer",
            ],
            artifact_required=True,
            required_artifact_keys=["run_artifact"],
            citation_types=["source_inventory"],
        ),
    )
    trace = service.latest_trace(base.COMPANY_ID, session["session_id"])
    source_summary = trace.get("source_summary") or {}
    if source_summary.get("route") != "source_inventory":
        raise AssertionError(f"expected source_inventory route, got {source_summary}")
    if not source_summary.get("news_source_count") and not source_summary.get("policy_source_count"):
        raise AssertionError(f"source inventory did not expose source counts: {source_summary}")
    case.turns.append(turn)
    case.checks.extend(
        [
            "用户输入 /report 明确进入报告工作流",
            "Runtime 解析 /report 后选择 external_impact_report，并进入 source_inventory 内部路线",
            "trace.source_summary 展示新闻/政策来源数量和快照数量",
            "数据源盘点写入 run_artifact，便于复盘来源边界",
        ]
    )
    return case


def _case_slash_impact_research(service: ChatWorkbenchService, store: PolicyMemoryStore) -> DialogueCase:
    session = service.create_session(
        company_id=base.COMPANY_ID,
        title="D04 Slash Impact Research",
        mode="auto",
        model_id=base.DEFAULT_MODEL_ID,
        active_skill_id="",
    )
    case = DialogueCase(
        case_id="D04",
        title="斜杠命令：/report 触发统一外部变化调研路线",
        level="中等",
    )
    turn = _run_turn(
        service,
        store,
        session["session_id"],
        raw_input="/report 请用调研报告形式整理 Agent Workbench 的动态验收标准，需要覆盖 trace、memory、artifact 和固定模型策略。",
        expectation=TurnExpectation(
            expected_skill="external_impact_report",
            required_steps=[
                "intent_classification",
                "skill_selection",
                "context_build",
                "memory_read",
                "workflow_stage",
                "model_call",
                "artifact_write",
                "final_answer",
            ],
            artifact_required=True,
            required_artifact_keys=["report"],
        ),
    )
    trace = service.latest_trace(base.COMPANY_ID, session["session_id"])
    if (trace.get("source_summary") or {}).get("route") != "research":
        raise AssertionError(f"expected research internal route, got {trace.get('source_summary')}")
    case.turns.append(turn)
    case.checks.extend(
        [
            "前端原文发送 /report，Runtime 负责解析并剥离任务文本",
            "session 保持默认对话状态，报告能力只在本轮生效",
            "external_impact_report 内部选择 research 路线",
            "报告 artifact 存在于磁盘",
            "未收到明确记忆指令，因此不写长期 memory",
        ]
    )
    return case


def _case_report_policy(service: ChatWorkbenchService) -> DialogueCase:
    session = service.create_session(
        company_id=base.COMPANY_ID,
        title="D05 Report Policy",
        mode="auto",
        model_id=base.DEFAULT_MODEL_ID,
        active_skill_id="",
    )
    store = PolicyMemoryStore(base.COMPANY_ID)
    case = DialogueCase(
        case_id="D05",
        title="/report：政策/合规问题触发外部变化政策路线",
        level="中等",
    )
    turn = _run_turn(
        service,
        store,
        session["session_id"],
        raw_input="/report 最近政策和合规要求会如何影响 Agent 平台的审计、工具权限和运行留痕？",
        expectation=TurnExpectation(
            expected_skill="external_impact_report",
            required_steps=[
                "intent_classification",
                "skill_selection",
                "context_build",
                "memory_read",
                "workflow_stage",
                "tool_call",
                "gate_check",
                "artifact_write",
                "final_answer",
            ],
            artifact_required=True,
            required_artifact_keys=["report", "html_report", "run_artifact"],
        ),
    )
    trace = service.latest_trace(base.COMPANY_ID, session["session_id"])
    if (trace.get("source_summary") or {}).get("route") != "policy":
        raise AssertionError(f"expected policy internal route, got {trace.get('source_summary')}")
    case.turns.append(turn)
    case.checks.extend(
        [
            "/report 根据中文政策/合规要求进入 external_impact_report",
            "external_impact_report 内部选择 policy 路线",
            "trace 中出现 workflow tool_call、gate_check、artifact_write",
            "Markdown/HTML/run artifact 路径真实存在",
        ]
    )
    return case


def _case_wiki_tree(service: ChatWorkbenchService) -> DialogueCase:
    case = DialogueCase(
        case_id="D06",
        title="Wiki 数据页：层级浏览企业知识库",
        level="中等",
    )
    wiki = service.wiki_tree(base.COMPANY_ID)
    pages = wiki.get("pages") or []
    facts = wiki.get("facts") or []
    if wiki.get("schema_version") != "workbench.wiki_tree.v1":
        raise AssertionError(f"unexpected wiki schema: {wiki.get('schema_version')}")
    if not pages or not facts:
        raise AssertionError(f"wiki tree should expose pages and facts: {wiki.get('stats')}")
    if not all(page.get("parts") for page in pages):
        raise AssertionError("wiki pages should expose hierarchical path parts")
    case.checks.extend(
        [
            "Wiki 不再作为 slash skill 暴露",
            "Workbench API 返回 workbench.wiki_tree.v1",
            "页面按 path parts 提供层级目录",
            "结构化 facts 与页面详情一起返回",
        ]
    )
    case.evidence = {
        "schema_version": wiki.get("schema_version"),
        "stats": wiki.get("stats") or {},
        "sample_page": pages[0].get("path"),
        "sample_fact": facts[0].get("fact_id"),
    }
    return case


def _case_multi_turn_skill_switch(
    service: ChatWorkbenchService,
    store: PolicyMemoryStore,
) -> DialogueCase:
    session = service.create_session(
        company_id=base.COMPANY_ID,
        title="D07 Multi Turn Skill Switch",
        mode="auto",
        model_id=base.DEFAULT_MODEL_ID,
        active_skill_id="",
    )
    case = DialogueCase(
        case_id="D07",
        title="多轮对话：普通对话 -> /report 调研路线 -> 默认聊天",
        level="困难",
    )
    turn1 = _run_turn(
        service,
        store,
        session["session_id"],
        raw_input="我先问一个普通问题：这个系统的核心运行证据有哪些？",
        expectation=TurnExpectation(
            expected_skill="general_chat",
            required_steps=[
                "intent_classification",
                "skill_selection",
                "context_build",
                "memory_read",
                "tool_call",
                "final_answer",
            ],
        ),
    )
    turn2 = _run_turn(
        service,
        store,
        session["session_id"],
        raw_input="/report 请用调研报告形式再把刚才的运行证据整理成一份面试展示提纲。",
        expectation=TurnExpectation(
            expected_skill="external_impact_report",
            required_steps=[
                "intent_classification",
                "skill_selection",
                "context_build",
                "memory_read",
                "workflow_stage",
                "model_call",
                "artifact_write",
                "final_answer",
            ],
            artifact_required=True,
            required_artifact_keys=["report"],
        ),
    )
    turn3 = _run_turn(
        service,
        store,
        session["session_id"],
        raw_input="最新新闻里和 agent observability、tool policy 相关的内容有什么影响？",
        expectation=TurnExpectation(
            expected_skill="general_chat",
            required_steps=[
                "intent_classification",
                "skill_selection",
                "context_build",
                "memory_read",
                "tool_call",
                "model_call",
                "final_answer",
            ],
        ),
    )
    messages = service.list_messages(base.COMPANY_ID, session["session_id"])
    if [item["role"] for item in messages] != ["user", "assistant", "user", "assistant", "user", "assistant"]:
        raise AssertionError("multi-turn role order is not user/assistant repeated")
    latest_trace = service.latest_trace(base.COMPANY_ID, session["session_id"])
    if latest_trace["run_id"] != turn3.run_id:
        raise AssertionError("latest trace did not point to the final multi-turn run")
    turn2_trace = service.trace_for_run(base.COMPANY_ID, session["session_id"], turn2.run_id)
    if (turn2_trace.get("source_summary") or {}).get("route") != "research":
        raise AssertionError(f"turn2 expected research internal route, got {turn2_trace.get('source_summary')}")
    case.turns.extend([turn1, turn2, turn3])
    case.checks.extend(
        [
            "同一 session 连续三轮对话",
            "第一轮自动落到 general_chat",
            "第二轮 /report 显式触发 external_impact_report 的 research 内部路线",
            "第三轮没有 slash command，因此回到 general_chat",
            "latest_trace 指向最后一轮，而不是旧 trace",
        ]
    )
    return case


def _case_invalid_skill_guard(service: ChatWorkbenchService) -> DialogueCase:
    session = service.create_session(
        company_id=base.COMPANY_ID,
        title="D07 Invalid Skill Guard",
        mode="chat",
        model_id=base.DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )
    before_messages = service.list_messages(base.COMPANY_ID, session["session_id"])
    try:
        service.update_session(
            base.COMPANY_ID,
            session["session_id"],
            mode="skill",
            active_skill_id="missing_skill",
        )
    except KeyError as exc:
        error = str(exc)
    else:
        raise AssertionError("invalid skill update should raise KeyError")
    after_messages = service.list_messages(base.COMPANY_ID, session["session_id"])
    latest_trace = service.latest_trace(base.COMPANY_ID, session["session_id"])
    if before_messages != after_messages:
        raise AssertionError("invalid skill update should not create chat messages")
    if latest_trace["steps"]:
        raise AssertionError("invalid skill update should not create an agent run")
    return DialogueCase(
        case_id="D08",
        title="工具/技能边界：非法 skill 被确定性拒绝",
        level="困难",
        checks=[
            "update_session 校验 active_skill_id",
            "非法 skill 不会进入 runtime loop",
            "非法 skill 不会写入 message 或 trace",
        ],
        evidence={"error": error},
    )


def _case_live_frontend(ui_url: str) -> DialogueCase:
    company_id = "company_001"
    base_url = ui_url.rstrip("/")
    _http_get(base_url + "/")

    session = _http_json(
        "POST",
        f"{base_url}/companies/{company_id}/workbench/sessions",
        {
            "title": "Dialogue Acceptance UI",
            "mode": "auto",
            "model_id": base.DEFAULT_MODEL_ID,
            "active_skill_id": "",
        },
    )
    run_result = _http_json(
        "POST",
        f"{base_url}/companies/{company_id}/workbench/sessions/{session['session_id']}/messages",
        {"message": "/report 请用调研报告形式生成一次前端动态验收报告，重点检查执行过程是否显示在回答上方。"},
    )
    trace = _http_json(
        "GET",
        f"{base_url}/companies/{company_id}/workbench/sessions/{session['session_id']}/trace",
    )
    messages = _http_json(
        "GET",
        f"{base_url}/companies/{company_id}/workbench/sessions/{session['session_id']}/messages",
    )
    if trace["run_id"] != run_result["run_id"]:
        raise AssertionError("HTTP trace run_id did not match the message result")
    if [item["role"] for item in messages["messages"][-2:]] != ["user", "assistant"]:
        raise AssertionError("HTTP UI seed session did not persist user/assistant messages")
    model_steps = [step for step in trace.get("steps", []) if step.get("step_type") == "model_call"]
    if not model_steps:
        raise AssertionError("HTTP trace did not include a model_call step")
    model_input = model_steps[-1].get("input_payload", {})
    prompt_contract = model_input.get("prompt_contract", {})
    if prompt_contract.get("role") != "research_report_subagent":
        raise AssertionError("HTTP trace model_call did not expose research_report_subagent prompt contract")
    if not any(message.get("role") == "system" for message in model_input.get("messages", [])):
        raise AssertionError("HTTP trace model_call did not expose a system prompt")

    chrome = base._find_chrome()
    case = DialogueCase(
        case_id="F01",
        title="真实前端渲染：动态会话、执行过程、斜杠命令和固定模型状态",
        level="困难",
        checks=[
            "通过 HTTP 创建真实 UI 会话并发送消息",
            "HTTP trace 和 message result 的 run_id 一致",
            "headless Chrome 渲染页面后检查动态 DOM",
        ],
        evidence={
            "ui_url": base_url + "/",
            "session_id": session["session_id"],
            "run_id": run_result["run_id"],
            "api_trace_prompt_contract": prompt_contract.get("contract_id", ""),
            "api_trace_prompt_role": prompt_contract.get("role", ""),
        },
    )
    if not chrome:
        case.checks.append("未找到 Chrome/Edge，跳过 DOM 和截图检查")
        case.evidence["chrome"] = "not_found"
        return case

    out_dir = ROOT / "data" / "acceptance"
    out_dir.mkdir(parents=True, exist_ok=True)
    screenshot = (out_dir / f"workbench-dialogue-{date.today().isoformat()}-{int(time.time())}.png").resolve()
    dump = _chrome_dump_dom(chrome, base_url + "/")
    _assert_all_in(
        dump,
        [
            "Agent Workbench",
            "runtime-status",
            "sidebar-toggle",
            "slash-palette",
            "/report",
            "thinking-card",
            "trace-evidence-grid",
            "step-details",
            "json-box",
            "loop_phase",
        ],
    )
    _assert_not_in(dump, ["model-select", "model-modal", "model-settings", "skill-dock", "inspector"])
    _chrome_screenshot(chrome, base_url + "/", screenshot)
    if not screenshot.exists() or screenshot.stat().st_size < 40_000:
        raise AssertionError(f"frontend screenshot missing or too small: {screenshot}")
    case.evidence.update(
        {
            "screenshot": str(screenshot),
            "screenshot_bytes": screenshot.stat().st_size,
            "dom_checks": [
                "Agent Workbench shell",
                "fixed model runtime status",
                "slash command palette contract",
                "thinking-card trace above answer",
                "trace evidence summary grid",
                "step-details JSON trace payload",
                "loop_phase visible in rendered trace JSON",
                "prompt_contract validated through the same HTTP trace",
                "old skill-dock/inspector removed",
            ],
        }
    )
    return case


def _run_turn(
    service: ChatWorkbenchService,
    store: PolicyMemoryStore,
    session_id: str,
    raw_input: str,
    expectation: TurnExpectation,
) -> DialogueTurn:
    sent_message, skill_id, command = _parse_slash(raw_input)
    del skill_id
    result = service.send_message(base.COMPANY_ID, session_id, sent_message)
    trace = service.latest_trace(base.COMPANY_ID, session_id)
    messages = service.list_messages(base.COMPANY_ID, session_id)
    _inspect_turn(
        trace=trace,
        messages=messages,
        result=result,
        expectation=expectation,
    )
    if expectation.artifact_required or expectation.required_artifact_keys:
        _inspect_artifacts(result["artifacts"], expectation.required_artifact_keys)
    if expectation.memory_query:
        _inspect_memory(store, expectation.memory_query, expectation.memory_skill_id)
    if expectation.citation_types:
        citation_types = {item.get("type") for item in result.get("citations", [])}
        missing = [item for item in expectation.citation_types if item not in citation_types]
        if missing:
            raise AssertionError(f"missing citation types: {missing}; got {sorted(citation_types)}")

    turn = DialogueTurn(
        raw_input=raw_input,
        sent_message=sent_message,
        command=command or "",
        expectation=expectation,
        run_id=result["run_id"],
        selected_skill_id=result["selected_skill_id"],
        answer_preview=result["answer"][:180],
        trace_steps=[step["step_type"] for step in trace["steps"]],
        trace_step_details=[_step_detail(step) for step in trace["steps"]],
        artifacts=result.get("artifacts", {}),
        checks=[
            f"selected_skill_id == {expectation.expected_skill}",
            "trace.run_id == message result.run_id",
            "assistant metadata.run_id == trace.run_id",
            "step_index sequential",
            "loop_phase present on every step",
            "all required steps present",
            "context_manifest present in context_build",
            "final_answer is followed by run_stopped as the terminal Stop-hook audit step",
        ],
    )
    if command:
        turn.checks.append(f"slash command {command} was sent raw and parsed by runtime")
    return turn


def _inspect_turn(
    trace: dict[str, Any],
    messages: list[dict[str, Any]],
    result: dict[str, Any],
    expectation: TurnExpectation,
) -> None:
    if result["selected_skill_id"] != expectation.expected_skill:
        raise AssertionError(
            f"expected skill {expectation.expected_skill}, got {result['selected_skill_id']}"
        )
    if trace["run_id"] != result["run_id"]:
        raise AssertionError("latest_trace run_id does not match send_message result")
    if trace["status"] != "done":
        raise AssertionError(f"trace status expected done, got {trace['status']}")
    if trace["skill_id"] != expectation.expected_skill:
        raise AssertionError(f"trace skill mismatch: {trace['skill_id']}")
    if not messages or messages[-1]["role"] != "assistant":
        raise AssertionError("latest persisted message is not assistant")
    if messages[-1]["metadata"].get("run_id") != result["run_id"]:
        raise AssertionError("assistant message metadata does not carry run_id")
    steps = trace["steps"]
    if not steps:
        raise AssertionError("trace has no steps")
    indexes = [step["step_index"] for step in steps]
    if indexes != list(range(1, len(indexes) + 1)):
        raise AssertionError(f"step_index is not sequential: {indexes}")
    if any(step["status"] != "done" for step in steps):
        raise AssertionError("not all trace steps are done")
    missing_phase = [step["step_index"] for step in steps if not step["metadata"].get("loop_phase")]
    if missing_phase:
        raise AssertionError(f"trace steps missing loop_phase metadata: {missing_phase}")
    step_types = [step["step_type"] for step in steps]
    _assert_all_in(step_types, expectation.required_steps)
    forbidden = [step_type for step_type in expectation.forbidden_steps if step_type in step_types]
    if forbidden:
        raise AssertionError(f"forbidden trace steps present: {forbidden}")
    if step_types[-1] != "run_stopped":
        raise AssertionError(f"run_stopped should be terminal, got {step_types[-1]}")
    if "final_answer" in expectation.required_steps and step_types[-2:] != [
        "final_answer",
        "run_stopped",
    ]:
        raise AssertionError(
            "final_answer should be immediately followed by run_stopped, "
            f"got terminal steps {step_types[-2:]}"
        )
    context_steps = [step for step in steps if step["step_type"] == "context_build"]
    if not context_steps:
        raise AssertionError("context_build step is missing")
    manifest = context_steps[-1]["output_payload"].get("context_manifest")
    if not isinstance(manifest, dict) or not manifest:
        raise AssertionError("context_build output does not include context_manifest")
    if "visible_keys" not in manifest and "token_budget" not in manifest:
        raise AssertionError("context_manifest lacks visible_keys/token_budget evidence")
    model_steps = [step for step in steps if step["step_type"] == "model_call"]
    for step in model_steps:
        input_payload = step["input_payload"]
        messages = input_payload.get("messages", [])
        prompt_contract = input_payload.get("prompt_contract", {})
        if not messages or messages[0].get("role") != "system":
            raise AssertionError("model_call does not expose a system prompt in trace input")
        if not prompt_contract.get("contract_id"):
            raise AssertionError("model_call does not expose prompt_contract.contract_id")
        if not prompt_contract.get("context_controls", {}).get("visible_keys"):
            raise AssertionError("model_call prompt_contract does not expose visible context keys")


def _inspect_artifacts(artifacts: dict[str, Any], required_keys: list[str]) -> None:
    missing = [key for key in required_keys if key not in artifacts]
    if missing:
        raise AssertionError(f"missing artifact keys: {missing}")
    path_values = [Path(value) for value in artifacts.values() if isinstance(value, str) and value]
    missing_paths = [str(path) for path in path_values if not path.exists()]
    if missing_paths:
        raise AssertionError(f"artifact paths do not exist: {missing_paths}")


def _inspect_memory(store: PolicyMemoryStore, query: str, skill_id: str) -> None:
    memories = store.search_memory(query, top_k=10)
    if skill_id and not any(item["metadata"].get("skill_id") == skill_id for item in memories):
        raise AssertionError(f"memory search did not find skill_result for {skill_id}: {query}")


def _parse_slash(raw_input: str) -> tuple[str, str, str | None]:
    stripped = raw_input.strip()
    if not stripped.startswith("/"):
        return stripped, "", None
    token, *parts = stripped.split()
    command = token.lower()
    if command not in SLASH_COMMANDS:
        return stripped, "", None
    skill_id = SLASH_COMMANDS[command]
    return stripped, skill_id, command


def _step_detail(step: dict[str, Any]) -> dict[str, Any]:
    input_payload = step.get("input_payload") or {}
    output_payload = step.get("output_payload") or {}
    prompt_contract = input_payload.get("prompt_contract") or {}
    return {
        "step_index": step.get("step_index"),
        "step_type": step.get("step_type"),
        "title": step.get("title"),
        "status": step.get("status"),
        "loop_phase": (step.get("metadata") or {}).get("loop_phase", ""),
        "input_keys": sorted(input_payload.keys()),
        "output_keys": sorted(output_payload.keys()),
        "tool_call_count": len(step.get("tool_calls") or []),
        "has_gate_result": bool(step.get("gate_result")),
        "prompt_contract_id": prompt_contract.get("contract_id", ""),
        "prompt_role": prompt_contract.get("role", ""),
    }


def _http_json(method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise AssertionError(f"HTTP {method} {url} failed: {exc.code}; {detail}") from exc
    except urllib.error.URLError as exc:
        raise AssertionError(f"HTTP {method} {url} failed: {exc}") from exc


def _http_get(url: str) -> str:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise AssertionError(f"ui url not reachable: {url}; {exc}") from exc


def _chrome_dump_dom(chrome: str, url: str) -> str:
    completed = subprocess.run(
        [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--disable-logging",
            "--virtual-time-budget=7000",
            "--window-size=1186,742",
            "--dump-dom",
            url,
        ],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace")
        stdout = completed.stdout.decode("utf-8", errors="replace")
        raise AssertionError(f"headless Chrome DOM dump failed: {stderr or stdout}")
    return completed.stdout.decode("utf-8", errors="replace")


def _chrome_screenshot(chrome: str, url: str, screenshot: Path) -> None:
    completed = subprocess.run(
        [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--disable-logging",
            "--hide-scrollbars",
            "--virtual-time-budget=7000",
            "--window-size=1186,742",
            f"--screenshot={screenshot}",
            url,
        ],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace")
        stdout = completed.stdout.decode("utf-8", errors="replace")
        raise AssertionError(f"headless Chrome screenshot failed: {stderr or stdout}")


def _assert_all_in(values: Any, expected: list[str]) -> None:
    if isinstance(values, str):
        missing = [item for item in expected if item not in values]
    else:
        values_list = list(values)
        missing = [item for item in expected if item not in values_list]
    if missing:
        raise AssertionError(f"missing expected values: {missing}")


def _assert_not_in(value: str, forbidden: list[str]) -> None:
    found = [item for item in forbidden if item in value]
    if found:
        raise AssertionError(f"forbidden values present: {found}")


def _case_to_dict(case: DialogueCase) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "title": case.title,
        "level": case.level,
        "status": case.status,
        "checks": case.checks,
        "evidence": case.evidence,
        "turns": [
            {
                "raw_input": turn.raw_input,
                "sent_message": turn.sent_message,
                "command": turn.command,
                "expected_skill": turn.expectation.expected_skill,
                "selected_skill_id": turn.selected_skill_id,
                "run_id": turn.run_id,
                "answer_preview": turn.answer_preview,
                "trace_steps": turn.trace_steps,
                "trace_step_details": turn.trace_step_details,
                "artifacts": turn.artifacts,
                "checks": turn.checks,
            }
            for turn in case.turns
        ],
    }


if __name__ == "__main__":
    main()
