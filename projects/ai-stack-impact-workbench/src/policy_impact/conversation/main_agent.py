"""Main conversation agent that routes between local skills and normal chat."""

from __future__ import annotations

from typing import Any

from policy_impact.conversation.context_manager import ConversationContextManager
from policy_impact.conversation.report_chat import ReportChatService
from policy_impact.harness.tool_gateway import ToolGateway
from policy_impact.mcp_server.registry import register_all_tools
from policy_impact.skill_executors.policy_weekly_impact import run_policy_weekly_impact
from policy_impact.skill_executors.recent_news_report import run_recent_news_report


class MainAgentService:
    """A deterministic ReAct-style router with auditable tool calls.

    This is not pretending every turn needs an LLM. It keeps the tool loop explicit:
    observe user intent, choose one action, run the allowed tool/skill, then answer
    with citations or artifact paths.
    """

    def __init__(self, company_id: str) -> None:
        self.company_id = company_id
        self.context = ConversationContextManager(company_id)
        register_all_tools()

    def ask(self, message: str, session_id: str = "main") -> dict[str, Any]:
        ctx = self.context.load_context(session_id)
        self.context.save_message(session_id, "user", message)
        intent = self._classify_intent(message)

        if intent == "policy_report":
            response = self._run_policy_report()
        elif intent == "news_report":
            response = self._run_news_report()
        elif intent == "correction":
            response = self._create_correction(message)
        elif intent == "wiki_blueprint":
            response = self._wiki_blueprint()
        else:
            response = self._answer_general(message)

        citations = response.get("citations", [])
        if ctx.get("summary_memory_id"):
            citations.append({"type": "conversation_summary", "memory_id": ctx["summary_memory_id"]})
        self.context.save_message(session_id, "assistant", response["answer"], citations=citations)
        response["intent"] = intent
        response["session_id"] = session_id
        response["citations"] = citations
        return response

    @staticmethod
    def _classify_intent(message: str) -> str:
        text = message.lower()
        if any(token in message for token in ("纠正", "不是", "改成", "应该是", "已经有", "补充")):
            return "correction"
        if any(token in message for token in ("怎么写企业信息", "知识库怎么写", "wiki", "企业信息模板", "引导填写")):
            return "wiki_blueprint"
        if "新闻" in message or "舆情" in message or "行业动态" in message:
            return "news_report"
        if "政策" in message and any(token in message for token in ("报告", "影响", "最近", "本周", "分析")):
            return "policy_report"
        if "recent policy" in text:
            return "policy_report"
        if "recent news" in text:
            return "news_report"
        return "general_chat"

    def _run_policy_report(self) -> dict[str, Any]:
        result = run_policy_weekly_impact(self.company_id)
        answer = (
            "已完成最近一周政策影响分析。"
            f"状态：{result.state.current_stage}；影响判断：{len(result.state.impact_assessments)} 条；"
            f"HTML 报告：{result.state.report_paths.get('html_report')}。"
        )
        return {
            "answer": answer,
            "artifacts": result.state.report_paths,
            "citations": [{"type": "run_artifact", "path": result.run_artifact_path}],
        }

    def _run_news_report(self) -> dict[str, Any]:
        result = run_recent_news_report(self.company_id)
        answer = (
            "已完成最近新闻/市场信号分析。"
            f"结构化事件：{result['event_count']} 条；HTML 报告：{result['html_report_path']}。"
        )
        return {
            "answer": answer,
            "artifacts": {
                "report": result["report_path"],
                "html_report": result["html_report_path"],
                "run_artifact": result["artifact_path"],
            },
            "citations": [{"type": "news_artifact", "path": result["artifact_path"]}],
        }

    def _create_correction(self, message: str) -> dict[str, Any]:
        correction = ReportChatService(self.company_id)._maybe_create_correction(message)
        if not correction:
            return {
                "answer": "我识别到你在纠正企业信息，但没有定位到足够明确的企业 FACT。请带上要修改的事实或字段。",
                "citations": [],
            }
        return {
            "answer": f"已生成企业知识库待确认更新：{correction['correction_id']}。确认后才会写回 Markdown。",
            "correction_proposal": correction,
            "citations": [{"type": "correction_proposal", "id": correction["correction_id"]}],
        }

    def _wiki_blueprint(self) -> dict[str, Any]:
        gateway = ToolGateway()
        blueprint = gateway.call("main_agent", "company_wiki_blueprint", company_id=self.company_id)
        missing = blueprint.get("missing_files", [])
        questions = blueprint.get("next_questions", [])
        answer = "企业信息文件夹建议按高/中/低重要性维护。\n"
        if missing:
            answer += "当前缺少文件：" + "、".join(missing) + "\n"
        if questions:
            answer += "下一步优先补充：\n" + "\n".join(f"- {item}" for item in questions[:5])
        return {
            "answer": answer,
            "blueprint": blueprint,
            "tool_calls": [item.to_dict() for item in gateway.calls],
            "citations": [{"type": "company_wiki_blueprint", "path": "data/companies"}],
        }

    def _answer_general(self, message: str) -> dict[str, Any]:
        gateway = ToolGateway()
        facts = gateway.call(
            "main_agent",
            "company_wiki_search",
            company_id=self.company_id,
            query=message,
            top_k=5,
        )
        memories = gateway.call(
            "main_agent",
            "memory_search",
            company_id=self.company_id,
            query=message,
            top_k=5,
        )
        evidence_lines = []
        citations = []
        for fact in facts[:4]:
            evidence_lines.append(f"- 企业事实：{fact.get('value', '')}")
            citations.append(
                {
                    "type": "company_fact",
                    "fact_id": fact.get("fact_id"),
                    "path": fact.get("source_path"),
                }
            )
        for item in memories[:3]:
            evidence_lines.append(f"- 长期记忆：{item.get('content', '')}")
            citations.append({"type": "memory_item", "memory_id": item.get("id")})
        if not evidence_lines:
            answer = "我没有在企业知识库或本地记忆中找到足够依据。可以先让我生成企业信息填写指引，或补充关键事实。"
        else:
            answer = "基于当前企业知识库和本地记忆：\n" + "\n".join(evidence_lines)
        return {
            "answer": answer,
            "tool_calls": [item.to_dict() for item in gateway.calls],
            "citations": citations,
        }

