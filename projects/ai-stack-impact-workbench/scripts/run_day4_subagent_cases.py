"""Run Day 4 subagent learning cases against the local Workbench API.

Keep this as a UTF-8 file because piping Chinese prompts through PowerShell
stdin can turn them into question marks and invalidate subagent routing tests.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from urllib import error, request


ROOT = Path(__file__).resolve().parents[1]
COMPANY_ID = "company_001"
OUT_DIR = ROOT / "data" / "acceptance"


CASES = [
    {
        "id": "D4-C1-no-subagent",
        "title": "Day4 不触发子智能体",
        "message": "请用两三句话说明这个 Workbench 能帮我做什么。",
        "expect_skill": "general_chat",
        "expect_roles": [],
        "expect_report": False,
    },
    {
        "id": "D4-C2-analysis-skeptic",
        "title": "Day4 分析和质疑双角色",
        "message": "我准备把这个 Workbench 写进简历，请从分析和质疑两个角度判断它是否能体现 Agent Harness 能力。",
        "expect_skill": "general_chat",
        "expect_roles": ["analyst", "skeptic"],
        "expect_report": False,
    },
    {
        "id": "D4-C3-source-verifier",
        "title": "Day4 来源和核验角色",
        "message": "请帮我核验：这个项目的 subagent 是否真的有隔离上下文和工具权限？请给出证据链和验证结论。",
        "expect_skill": "general_chat",
        "expect_roles": ["collector", "verifier"],
        "expect_report": False,
    },
    {
        "id": "D4-C4-report-workflow-control",
        "title": "Day4 report workflow 对照",
        "message": "/report 请分析最近一周外部技术变化对示例公司 AI 产品的影响，并生成带来源的报告。",
        "expect_skill": "external_impact_report",
        "expect_roles": [],
        "expect_report": True,
    },
]


def http(base_url: str, method: str, path: str, payload: dict | None = None, timeout: int = 240) -> dict:
    body = None
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(base_url + path, data=body, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} HTTP {exc.code}: {raw}") from exc


def create_session(base_url: str, case: dict) -> dict:
    return http(
        base_url,
        "POST",
        f"/companies/{COMPANY_ID}/workbench/sessions",
        {
            "title": case["title"],
            "mode": "auto",
            "model_id": "deepseek-v4-flash",
            "active_skill_id": "general_chat",
        },
    )


def summarize_case(base_url: str, case: dict) -> dict:
    session = create_session(base_url, case)
    session_id = session["session_id"]
    started = time.perf_counter()
    result = http(
        base_url,
        "POST",
        f"/companies/{COMPANY_ID}/workbench/sessions/{session_id}/messages",
        {"message": case["message"]},
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    run_id = str(result.get("run_id") or "")
    trace = http(
        base_url,
        "GET",
        f"/companies/{COMPANY_ID}/workbench/sessions/{session_id}/runs/{run_id}/trace",
    )

    steps = trace.get("steps") or []
    step_types = [step.get("step_type") for step in steps]
    subagent_steps = [
        step
        for step in steps
        if step.get("step_type") in {"subagent_plan", "subagent_context", "subagent_delegate"}
    ]
    delegate_steps = [step for step in steps if step.get("step_type") == "subagent_delegate"]
    model_steps = [step for step in steps if step.get("step_type") == "model_call"]
    roles: list[str] = []
    delegate_outputs: list[dict] = []
    for step in delegate_steps:
        input_payload = step.get("input_payload") or {}
        output_payload = step.get("output_payload") or {}
        role = str(input_payload.get("role") or output_payload.get("role") or "").strip()
        if role:
            roles.append(role)
        output = output_payload.get("output") if isinstance(output_payload.get("output"), dict) else {}
        delegate_outputs.append(
            {
                "role": role,
                "status": step.get("status"),
                "title": step.get("title"),
                "trigger": input_payload.get("trigger"),
                "reason": input_payload.get("reason"),
                "output_schema": output_payload.get("output_schema"),
                "output_keys": sorted(output.keys()),
                "tool_count": len(step.get("tool_calls") or []),
            }
        )

    usage = [
        (step.get("metadata") or {}).get("usage") or {}
        for step in model_steps
        if (step.get("metadata") or {}).get("usage")
    ]
    artifacts = result.get("artifacts") or {}
    checks = {
        "selected_skill_ok": result.get("selected_skill_id") == case["expect_skill"],
        "expected_roles_present": all(role in roles for role in case["expect_roles"]),
        "unexpected_delegate_count_ok": bool(case["expect_roles"]) or len(delegate_steps) == 0,
        "report_artifact_ok": (
            bool(artifacts.get("report_html") or artifacts.get("report"))
            if case["expect_report"]
            else True
        ),
        "has_trace": bool(run_id) and bool(steps),
        "answer_nonempty": bool(str(result.get("answer") or "").strip()),
    }
    return {
        "id": case["id"],
        "session_id": session_id,
        "run_id": run_id,
        "message": case["message"],
        "selected_skill_id": result.get("selected_skill_id"),
        "elapsed_ms": elapsed_ms,
        "answer_preview": str(result.get("answer") or "")[:900],
        "step_count": len(steps),
        "step_types": step_types,
        "subagent_step_count": len(subagent_steps),
        "delegate_count": len(delegate_steps),
        "delegate_roles": roles,
        "delegate_outputs": delegate_outputs,
        "model_call_count": len(model_steps),
        "usage": usage,
        "artifacts": artifacts,
        "checks": checks,
        "status": "passed" if all(checks.values()) else "failed",
    }


def summarize_case_direct(case: dict) -> dict:
    import sys

    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "src"))

    from run_policy_api import load_local_env
    from policy_impact.app.chat_workbench_service import ChatWorkbenchService

    load_local_env(ROOT)
    service = ChatWorkbenchService()
    session = service.create_session(
        COMPANY_ID,
        title=case["title"],
        mode="auto",
        model_id="deepseek-v4-flash",
        active_skill_id="general_chat",
    )
    session_id = session["session_id"]
    started = time.perf_counter()
    result = service.send_message(COMPANY_ID, session_id, case["message"])
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    run_id = str(result.get("run_id") or "")
    trace = service.trace_for_run(COMPANY_ID, session_id, run_id)

    steps = trace.get("steps") or []
    step_types = [step.get("step_type") for step in steps]
    subagent_steps = [
        step
        for step in steps
        if step.get("step_type") in {"subagent_plan", "subagent_context", "subagent_delegate"}
    ]
    delegate_steps = [step for step in steps if step.get("step_type") == "subagent_delegate"]
    model_steps = [step for step in steps if step.get("step_type") == "model_call"]
    roles: list[str] = []
    delegate_outputs: list[dict] = []
    for step in delegate_steps:
        input_payload = step.get("input_payload") or {}
        output_payload = step.get("output_payload") or {}
        role = str(input_payload.get("role") or output_payload.get("role") or "").strip()
        if role:
            roles.append(role)
        output = output_payload.get("output") if isinstance(output_payload.get("output"), dict) else {}
        delegate_outputs.append(
            {
                "role": role,
                "status": step.get("status"),
                "title": step.get("title"),
                "trigger": input_payload.get("trigger"),
                "reason": input_payload.get("reason"),
                "output_schema": output_payload.get("output_schema"),
                "output_keys": sorted(output.keys()),
                "tool_count": len(step.get("tool_calls") or []),
            }
        )

    usage = [
        (step.get("metadata") or {}).get("usage") or {}
        for step in model_steps
        if (step.get("metadata") or {}).get("usage")
    ]
    artifacts = result.get("artifacts") or {}
    checks = {
        "selected_skill_ok": result.get("selected_skill_id") == case["expect_skill"],
        "expected_roles_present": all(role in roles for role in case["expect_roles"]),
        "unexpected_delegate_count_ok": bool(case["expect_roles"]) or len(delegate_steps) == 0,
        "report_artifact_ok": (
            bool(artifacts.get("report_html") or artifacts.get("report"))
            if case["expect_report"]
            else True
        ),
        "has_trace": bool(run_id) and bool(steps),
        "answer_nonempty": bool(str(result.get("answer") or "").strip()),
    }
    return {
        "id": case["id"],
        "session_id": session_id,
        "run_id": run_id,
        "message": case["message"],
        "selected_skill_id": result.get("selected_skill_id"),
        "elapsed_ms": elapsed_ms,
        "answer_preview": str(result.get("answer") or "")[:900],
        "step_count": len(steps),
        "step_types": step_types,
        "subagent_step_count": len(subagent_steps),
        "delegate_count": len(delegate_steps),
        "delegate_roles": roles,
        "delegate_outputs": delegate_outputs,
        "model_call_count": len(model_steps),
        "usage": usage,
        "artifacts": artifacts,
        "checks": checks,
        "status": "passed" if all(checks.values()) else "failed",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8502")
    parser.add_argument("--direct", action="store_true")
    parser.add_argument(
        "--report-json",
        default="data/acceptance/latest-day4-subagent-runtime-cases.json",
    )
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "direct_service" if args.direct else "api",
        "base_url": args.base_url if not args.direct else "",
        "company_id": COMPANY_ID,
        "cases": [
            summarize_case_direct(case) if args.direct else summarize_case(args.base_url, case)
            for case in CASES
        ],
    }
    summary["status"] = (
        "passed" if all(case["status"] == "passed" for case in summary["cases"]) else "failed"
    )
    report_path = ROOT / args.report_json
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    timestamped_path = OUT_DIR / f"day4-subagent-runtime-cases-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    timestamped_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "status": summary["status"],
                "report_path": str(report_path),
                "timestamped_report_path": str(timestamped_path),
                "cases": [
                    {
                        "id": case["id"],
                        "status": case["status"],
                        "skill": case["selected_skill_id"],
                        "elapsed_ms": case["elapsed_ms"],
                        "steps": case["step_count"],
                        "delegates": case["delegate_roles"],
                        "checks": case["checks"],
                        "answer_preview": case["answer_preview"][:180],
                    }
                    for case in summary["cases"]
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
