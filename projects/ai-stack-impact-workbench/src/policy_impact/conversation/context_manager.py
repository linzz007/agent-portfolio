"""Conversation context compression for local chat sessions."""

from __future__ import annotations

from typing import Any

from policy_impact.memory.store import PolicyMemoryStore
from policy_impact.rag.lexical import tokenize


class ConversationContextManager:
    """Keep recent turns verbatim and compress older turns into SQLite memory."""

    def __init__(self, company_id: str) -> None:
        self.company_id = company_id
        self.store = PolicyMemoryStore(company_id)

    @staticmethod
    def report_id(session_id: str) -> str:
        return f"main_chat:{session_id}"

    def load_context(self, session_id: str, max_turns: int = 8) -> dict[str, Any]:
        report_id = self.report_id(session_id)
        messages = self.store.list_report_messages(report_id)
        if len(messages) <= max_turns:
            return {"report_id": report_id, "recent_messages": messages, "summary_memory_id": None}

        old_messages = messages[: -max_turns]
        recent_messages = messages[-max_turns:]
        summary = self._compress_messages(old_messages)
        memory_id = self.store.save_memory(
            namespace=f"company:{self.company_id}:conversation",
            memory_type="conversation_summary",
            content=summary,
            importance=0.55,
            metadata={"session_id": session_id, "message_count": len(old_messages)},
        )
        return {"report_id": report_id, "recent_messages": recent_messages, "summary_memory_id": memory_id}

    def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        citations: list[dict[str, Any]] | None = None,
    ) -> str:
        return self.store.save_report_message(
            self.report_id(session_id),
            role=role,
            content=content,
            citations=citations,
        )

    @staticmethod
    def _compress_messages(messages: list[dict[str, Any]]) -> str:
        joined = "\n".join(f"{item.get('role')}: {item.get('content')}" for item in messages)
        tokens = tokenize(joined)
        keyword_counts: dict[str, int] = {}
        for token in tokens:
            if len(token) <= 1 and token.encode("utf-8").isalnum():
                continue
            keyword_counts[token] = keyword_counts.get(token, 0) + 1
        keywords = [
            key
            for key, _count in sorted(keyword_counts.items(), key=lambda item: item[1], reverse=True)[:12]
        ]
        excerpt = joined[:900]
        return "对话压缩摘要：\n" + excerpt + ("\n关键词：" + ", ".join(keywords) if keywords else "")

