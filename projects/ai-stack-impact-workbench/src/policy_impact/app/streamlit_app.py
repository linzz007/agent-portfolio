"""Streamlit UI for the single-user Agent Workbench."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from policy_impact.app.chat_workbench_service import ChatWorkbenchService
from policy_impact.app.service import PolicyImpactService
from policy_impact.harness.model_config import DEFAULT_MODEL_ID


CSS = """
<style>
.block-container {
  max-width: 1680px;
  padding-top: 1rem;
  padding-bottom: 2rem;
}
div[data-testid="stToolbar"] {
  display: none;
}
section[data-testid="stSidebar"] {
  background: #111827;
}
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] span,
section[data-testid="stSidebar"] p {
  color: #e5e7eb !important;
}
.workbench-title {
  font-size: 26px;
  line-height: 1.2;
  font-weight: 760;
  margin: 0 0 4px 0;
}
.workbench-subtitle {
  color: #667085;
  font-size: 13px;
  margin-bottom: 14px;
}
.metric-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 12px;
}
.metric-box {
  border: 1px solid #d9dee8;
  border-radius: 8px;
  background: #fff;
  padding: 11px 13px;
}
.metric-label {
  color: #667085;
  font-size: 12px;
  margin-bottom: 4px;
}
.metric-value {
  color: #111827;
  font-size: 20px;
  font-weight: 760;
}
.trace-chip {
  display: inline-block;
  border: 1px solid #d9dee8;
  border-radius: 999px;
  padding: 2px 8px;
  margin-right: 5px;
  font-size: 12px;
  color: #344054;
  background: #f8fafc;
}
.artifact-path {
  font-size: 12px;
  color: #475467;
  word-break: break-all;
}
div[data-testid="stButton"] button {
  border-radius: 7px;
  font-weight: 650;
}
@media (max-width: 900px) {
  .metric-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
"""


MODE_LABELS = {
    "auto": "自动选择",
    "chat": "普通对话",
    "skill": "指定技能",
    "policy": "政策分析",
    "news": "新闻报告",
    "research": "调研报告",
    "wiki": "知识库规划",
}

SKILL_LABELS = {
    "": "自动选择",
    "general_chat": "普通对话",
    "policy_weekly_impact": "政策影响分析",
    "recent_news_report": "新闻影响报告",
    "research_report": "调研报告生成",
    "company_wiki_blueprint": "企业知识库规划",
}

MODEL_LABELS = {
    DEFAULT_MODEL_ID: DEFAULT_MODEL_ID,
}

STATUS_LABELS = {
    "running": "运行中",
    "done": "完成",
    "failed": "失败",
    "blocked": "阻塞",
}

STEP_TYPE_LABELS = {
    "intent_classification": "意图识别",
    "skill_selection": "技能选择",
    "context_build": "上下文构建",
    "memory_read": "读取记忆",
    "memory_write": "写入记忆",
    "tool_call": "工具调用",
    "gate_check": "门控检查",
    "model_call": "模型调用",
    "artifact_write": "写入产物",
    "final_answer": "最终回答",
}

STEP_TITLE_LABELS = {
    "Classify turn intent": "识别本轮意图",
    "Select skill": "选择执行技能",
    "Build context manifest": "构建上下文清单",
    "Read long-term memory": "读取长期记忆",
    "Run memory and wiki search tools": "调用记忆和企业知识库检索工具",
    "Run policy workflow tools": "运行政策分析工作流工具",
    "Collect workflow gate results": "汇总工作流门控结果",
    "Attach policy workflow artifacts": "挂载政策分析产物",
    "Run recent news report skill": "运行新闻报告技能",
    "Check news report output": "检查新闻报告输出",
    "Attach recent news artifacts": "挂载新闻报告产物",
    "Read memory for research report": "为调研报告读取记忆",
    "Draft research report outline": "生成调研报告提纲",
    "Write research report artifact": "写入调研报告产物",
    "Store research report memory": "保存调研报告记忆",
    "Build company wiki blueprint": "生成企业知识库规划",
    "Return wiki blueprint payload": "返回知识库规划产物",
    "Persist final answer": "保存最终回答",
}

ARTIFACT_LABELS = {
    "report": "Markdown 报告",
    "html_report": "HTML 报告",
    "run_artifact": "运行产物",
    "wiki_blueprint": "知识库规划",
}

SESSION_TITLE_LABELS = {
    "Policy": "政策分析",
    "Research": "调研报告",
    "General chat": "普通对话",
    "General Chat": "普通对话",
    "Agent Workbench": "智能体工作台",
    "Invalid Skill": "异常技能",
}


def _metric(label: str, value: Any) -> str:
    return (
        '<div class="metric-box">'
        f'<div class="metric-label">{label}</div>'
        f'<div class="metric-value">{value}</div>'
        "</div>"
    )


def _safe_overview(service: PolicyImpactService, company_id: str) -> dict[str, Any]:
    try:
        return service.company_overview(company_id)
    except FileNotFoundError:
        return {
            "profile": {"company_id": company_id, "company_name": company_id},
            "fact_count": 0,
            "high_importance_fact_count": 0,
            "open_questions": [],
        }


def _ensure_session(
    workbench: ChatWorkbenchService,
    company_id: str,
    session_key: str,
) -> str:
    import streamlit as st

    sessions = workbench.list_sessions(company_id)
    session_ids = {item["session_id"] for item in sessions}
    if st.session_state.get(session_key) in session_ids:
        return st.session_state[session_key]
    if sessions:
        st.session_state[session_key] = sessions[0]["session_id"]
        return sessions[0]["session_id"]
    session = workbench.create_session(
        company_id=company_id,
        title="默认对话",
        mode="auto",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="",
    )
    st.session_state[session_key] = session["session_id"]
    return session["session_id"]


def _localized_session_title(title: str) -> str:
    return SESSION_TITLE_LABELS.get(title, title or "未命名会话")


def _session_title(session: dict[str, Any]) -> str:
    count = session.get("message_count", 0)
    return f"{_localized_session_title(str(session.get('title') or ''))} ({count})"


def _status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)


def _mode_label(mode: str) -> str:
    return MODE_LABELS.get(mode, mode)


def _model_label(model_id: str) -> str:
    return MODEL_LABELS.get(model_id, model_id)


def _skill_label(skill_id: str) -> str:
    return SKILL_LABELS.get(skill_id, skill_id)


def _step_type_label(step_type: str) -> str:
    return STEP_TYPE_LABELS.get(step_type, step_type)


def _step_title_label(title: str) -> str:
    return STEP_TITLE_LABELS.get(title, title)


def _artifact_label(name: str) -> str:
    return ARTIFACT_LABELS.get(name, name)


def _localize_assistant_content(content: str) -> str:
    policy_match = re.fullmatch(
        r"Policy weekly impact workflow finished\. Policies: (?P<policies>\d+), assessments: (?P<assessments>\d+)\.",
        content,
    )
    if policy_match:
        return (
            "政策影响分析已完成。"
            f"政策数：{policy_match.group('policies')}，"
            f"评估项：{policy_match.group('assessments')}。"
        )

    news_match = re.fullmatch(r"Recent news report finished\. Events: (?P<events>\d+)\.", content)
    if news_match:
        return f"新闻影响报告已完成。结构化事件数：{news_match.group('events')}。"

    if content.startswith("Research report generated: "):
        return "调研报告已生成：" + content.removeprefix("Research report generated: ")

    if content.startswith("Company wiki blueprint generated."):
        return content.replace("Company wiki blueprint generated.", "企业知识库规划已生成。").replace(
            " Missing files: ",
            " 缺失文件：",
        )

    if content.startswith("Based on company wiki and memory:"):
        return (
            content.replace("Based on company wiki and memory:", "根据企业知识库和长期记忆：")
            .replace("- Company fact:", "- 企业事实：")
            .replace("- Memory:", "- 长期记忆：")
        )

    if content.startswith("I did not find high-confidence"):
        return "本轮没有检索到高置信企业事实或长期记忆。可以先补充企业知识库，或让我生成企业知识库规划。"

    return content


def _display_message_content(message: dict[str, Any]) -> str:
    content = str(message.get("content", ""))
    if message.get("role") == "assistant":
        return _localize_assistant_content(content)
    return content


def _render_trace(trace: dict[str, Any]) -> None:
    import streamlit as st

    if not trace.get("run_id"):
        st.info("当前会话还没有智能体运行记录。发送一条消息后，这里会展示执行追踪。")
        return
    st.caption(f"运行 ID：{trace['run_id']}")
    st.markdown(
        " ".join(
            [
                f'<span class="trace-chip">{_status_label(str(trace.get("status", "")))}</span>',
                f'<span class="trace-chip">{_skill_label(str(trace.get("skill_id", "")))}</span>',
                f'<span class="trace-chip">{_model_label(str(trace.get("model_id", "")))}</span>',
            ]
        ),
        unsafe_allow_html=True,
    )
    steps = trace.get("steps", [])
    for step in steps:
        status = _status_label(str(step.get("status", "")))
        title = _step_title_label(str(step.get("title", "")))
        label = f"{step['step_index']}. {title} · {status}"
        with st.expander(label, expanded=step["step_index"] >= max(1, len(steps) - 1)):
            st.json(
                {
                    "步骤类型": f"{_step_type_label(str(step['step_type']))} ({step['step_type']})",
                    "输入": step.get("input_payload", {}),
                    "输出": step.get("output_payload", {}),
                    "工具调用": step.get("tool_calls", []),
                    "门控结果": step.get("gate_result", {}),
                    "元数据": step.get("metadata", {}),
                    "创建时间": step.get("created_at", ""),
                }
            )


def _render_messages(messages: list[dict[str, Any]]) -> None:
    import streamlit as st

    if not messages:
        st.info("开始一次对话。你可以让它聊天、调研、运行政策/新闻技能，并在右侧查看每一步执行追踪。")
        return
    for message in messages:
        role = "assistant" if message["role"] == "assistant" else "user"
        with st.chat_message(role):
            st.markdown(_display_message_content(message))
            metadata = message.get("metadata") or {}
            artifacts = metadata.get("artifacts") or {}
            if artifacts:
                with st.expander("产物", expanded=False):
                    for name, value in artifacts.items():
                        if isinstance(value, str):
                            st.markdown(f"**{_artifact_label(name)}**")
                            st.markdown(f'<div class="artifact-path">{value}</div>', unsafe_allow_html=True)
                        else:
                            st.json({_artifact_label(name): value})
            citations = metadata.get("citations") or []
            if citations:
                with st.expander("引用", expanded=False):
                    st.json(citations)


def _render_legacy_tools(
    service: PolicyImpactService,
    company_id: str,
    report: dict[str, Any] | None,
) -> None:
    import streamlit as st

    st.subheader("业务技能与评测")
    tool_a, tool_b, tool_c = st.columns(3)
    if tool_a.button("运行政策影响分析", use_container_width=True):
        with st.spinner("正在运行政策工作流"):
            st.session_state["legacy_policy_result"] = service.run_weekly_analysis(company_id)
            st.rerun()
    if tool_b.button("运行新闻影响报告", use_container_width=True):
        with st.spinner("正在运行新闻技能"):
            st.session_state["legacy_news_result"] = service.run_news_analysis(company_id)
            st.rerun()
    if tool_c.button("生成企业知识库指引", use_container_width=True):
        st.session_state["legacy_wiki_blueprint"] = service.wiki_blueprint(company_id)

    result_cols = st.columns(3)
    with result_cols[0]:
        st.caption("政策运行结果")
        st.json(st.session_state.get("legacy_policy_result") or {})
    with result_cols[1]:
        st.caption("新闻运行结果")
        st.json(st.session_state.get("legacy_news_result") or {})
    with result_cols[2]:
        st.caption("知识库指引")
        blueprint = st.session_state.get("legacy_wiki_blueprint") or {}
        st.json(
            {
                "缺失文件": blueprint.get("missing_files", []),
                "下一步问题": (blueprint.get("next_questions") or [])[:5],
            }
        )

    detail_tabs = st.tabs(["最新报告", "HTML 报告", "评测", "基准对比", "回放", "报告追问/纠错"])
    with detail_tabs[0]:
        if report:
            st.markdown(report.get("content", ""))
        else:
            st.info("还没有报告。")
    with detail_tabs[1]:
        html_path = report.get("html_report_path") if report else None
        if html_path and Path(html_path).exists():
            import streamlit.components.v1 as components

            components.html(Path(html_path).read_text(encoding="utf-8"), height=720, scrolling=True)
        else:
            st.info("还没有 HTML 报告。")
    with detail_tabs[2]:
        st.json(service.latest_eval_report())
    with detail_tabs[3]:
        st.json(service.latest_benchmark_report())
    with detail_tabs[4]:
        st.json(service.latest_replay_report())
    with detail_tabs[5]:
        question = st.text_area("报告追问或纠错", height=100)
        if st.button("发送报告问题", use_container_width=True) and question.strip():
            st.session_state["report_chat_result"] = service.ask_report(company_id, question.strip())
        if st.session_state.get("report_chat_result"):
            st.json(st.session_state["report_chat_result"])
        corrections = service.pending_corrections(company_id)
        if corrections:
            st.caption("待确认写回")
            for item in corrections[:5]:
                payload = item["payload"]
                st.write(f"{payload['old_claim']} -> {payload['new_claim']}")
                if st.button("确认写回", key=f"apply_{payload['correction_id']}"):
                    st.session_state["apply_result"] = service.apply_correction(
                        company_id,
                        payload["correction_id"],
                    )
                    st.rerun()


def main() -> None:
    import streamlit as st

    service = PolicyImpactService()
    workbench = ChatWorkbenchService()
    st.set_page_config(page_title="智能体工作台", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)

    companies = service.list_companies() or ["company_001"]
    company_id = st.sidebar.selectbox("企业", companies)
    session_key = f"workbench_session_id:{company_id}"
    current_session_id = _ensure_session(workbench, company_id, session_key)

    if st.sidebar.button("新建对话", use_container_width=True):
        session = workbench.create_session(
            company_id=company_id,
            title="新对话",
            mode="auto",
            model_id=DEFAULT_MODEL_ID,
            active_skill_id="",
        )
        st.session_state[session_key] = session["session_id"]
        st.rerun()

    sessions = workbench.list_sessions(company_id)
    session_ids = [item["session_id"] for item in sessions]
    if current_session_id not in session_ids and session_ids:
        current_session_id = session_ids[0]
        st.session_state[session_key] = current_session_id
    selected_session_id = st.sidebar.radio(
        "会话",
        session_ids,
        format_func=lambda sid: _session_title(next(item for item in sessions if item["session_id"] == sid)),
        index=session_ids.index(current_session_id) if current_session_id in session_ids else 0,
    )
    if selected_session_id != current_session_id:
        st.session_state[session_key] = selected_session_id
        st.rerun()
    current_session = workbench.get_session(company_id, selected_session_id)

    models = workbench.list_models(company_id)
    fixed_model = next((item for item in models if item["model_id"] == DEFAULT_MODEL_ID), {"model_id": DEFAULT_MODEL_ID, "metadata": {}})
    skills = workbench.list_skills()
    skill_ids = [item["id"] for item in skills]
    mode_options = ["auto", "chat", "skill", "policy", "news", "research", "wiki"]
    selected_mode = st.sidebar.selectbox(
        "模式",
        mode_options,
        index=mode_options.index(current_session["mode"]) if current_session["mode"] in mode_options else 0,
        format_func=_mode_label,
    )
    selected_model = DEFAULT_MODEL_ID
    model_metadata = fixed_model.get("metadata") or {}
    model_status = "已就绪" if model_metadata.get("available") else "未就绪"
    st.sidebar.caption(f"固定模型：{DEFAULT_MODEL_ID} · {model_status}")
    skill_options = [""] + skill_ids
    selected_skill = st.sidebar.selectbox(
        "技能",
        skill_options,
        index=skill_options.index(current_session["active_skill_id"])
        if current_session["active_skill_id"] in skill_options
        else 0,
        format_func=_skill_label,
    )
    title = st.sidebar.text_input("标题", value=_localized_session_title(str(current_session["title"])))
    title_to_save = title
    if (
        selected_mode != current_session["mode"]
        or current_session["model_id"] != DEFAULT_MODEL_ID
        or selected_skill != current_session["active_skill_id"]
        or title_to_save != current_session["title"]
    ):
        current_session = workbench.update_session(
            company_id=company_id,
            session_id=selected_session_id,
            title=title_to_save,
            mode=selected_mode,
            model_id=DEFAULT_MODEL_ID,
            active_skill_id=selected_skill,
        )

    overview = _safe_overview(service, company_id)
    report = service.latest_report(company_id)

    st.markdown('<div class="workbench-title">智能体工作台</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="workbench-subtitle">'
        "单用户对话入口。所有技能、工具、记忆、上下文和产物都会进入可审计的智能体运行记录。"
        "</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="metric-strip">'
        + _metric("企业事实", overview["fact_count"])
        + _metric("高重要事实", overview["high_importance_fact_count"])
        + _metric("会话数", len(sessions))
        + _metric("最新报告", "有" if report else "无")
        + "</div>",
        unsafe_allow_html=True,
    )

    chat_col, trace_col = st.columns([1.55, 1.0], gap="large")
    with chat_col:
        messages = workbench.list_messages(company_id, selected_session_id)
        _render_messages(messages)
        prompt = st.chat_input("输入你的问题，或让某个技能执行任务")
        if prompt:
            with st.spinner("智能体运行中"):
                workbench.send_message(company_id, selected_session_id, prompt)
            st.rerun()

    with trace_col:
        st.subheader("执行追踪")
        trace = workbench.latest_trace(company_id, selected_session_id)
        _render_trace(trace)

    with st.expander("企业画像与旧业务能力", expanded=False):
        profile = overview.get("profile", {})
        st.json(
            {
                "企业画像": profile,
                "事实数量": overview["fact_count"],
                "高重要事实数量": overview["high_importance_fact_count"],
                "待补充问题": overview["open_questions"][:5],
            }
        )
        _render_legacy_tools(service, company_id, report)


if __name__ == "__main__":
    main()
