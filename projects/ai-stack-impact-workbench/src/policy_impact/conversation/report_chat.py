"""Report chat with evidence lookup and correction proposals."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from policy_impact.company_wiki.loader import load_company_knowledge
from policy_impact.company_wiki.updater import apply_update, create_update_proposal, CorrectionProposal
from policy_impact.memory.store import PolicyMemoryStore
from policy_impact.rag.retriever import HybridRetriever


class ReportChatService:
    def __init__(self, company_id: str) -> None:
        self.company_id = company_id
        self.store = PolicyMemoryStore(company_id)

    def ask(self, question: str, report_id: str | None = None) -> dict[str, Any]:
        report = self._resolve_report(report_id)
        self.store.save_report_message(report["report_id"], "user", question)
        correction = self._maybe_create_correction(question)
        if correction:
            answer = (
                "我识别到这更像企业知识库纠错，不会直接覆盖原文件。"
                f"已生成待确认更新：{correction['correction_id']}。"
            )
            citations = [{"type": "correction_proposal", "id": correction["correction_id"]}]
        else:
            answer, citations = self._answer_with_context(question, report["report_path"])
        self.store.save_report_message(report["report_id"], "assistant", answer, citations=citations)
        return {
            "report_id": report["report_id"],
            "answer": answer,
            "citations": citations,
            "correction_proposal": correction,
        }

    def apply_correction(self, correction_id: str) -> dict[str, Any]:
        stored = self.store.get_correction_proposal(correction_id)
        if not stored:
            raise KeyError(f"correction not found: {correction_id}")
        payload = stored["payload"]
        result = apply_update(CorrectionProposal(**payload))
        self.store.update_correction_status(correction_id, "applied")
        self.store.save_memory(
            namespace=f"company:{self.company_id}:corrections",
            memory_type="correction",
            content=f"Applied correction {correction_id}: {payload.get('old_claim')} -> {payload.get('new_claim')}",
            importance=0.8,
            metadata=payload,
        )
        return result | {"correction_id": correction_id}

    def pending_corrections(self) -> list[dict[str, Any]]:
        return self.store.list_correction_proposals(status="pending")

    def _resolve_report(self, report_id: str | None) -> dict[str, str]:
        if report_id:
            latest = self.store.latest_report_session()
            if latest and latest["report_id"] == report_id:
                return {"report_id": report_id, "report_path": latest["report_path"]}
        latest = self.store.latest_report_session()
        if latest:
            return {"report_id": latest["report_id"], "report_path": latest["report_path"]}
        raise FileNotFoundError("no report session found; run policy analysis first")

    def _answer_with_context(self, question: str, report_path: str) -> tuple[str, list[dict[str, Any]]]:
        chunks = self._build_report_chunks(report_path)
        chunks.extend(self._build_company_fact_chunks())
        chunks.extend(self._build_memory_chunks(question))
        hits = HybridRetriever(chunks).retrieve(question, top_k=5) if chunks else []
        if not hits:
            return "当前报告、企业事实和长期记忆里都没有找到足够证据，需要补充企业信息或重新分析。", []

        evidence_lines = []
        citations = []
        for hit in hits:
            text = hit.get("compressed_text") or hit.get("text") or ""
            source_type = hit.get("metadata", {}).get("source_type", "unknown")
            evidence_lines.append(f"- [{source_type}] {text}")
            citations.append(
                {
                    "type": source_type,
                    "path": hit.get("metadata", {}).get("path"),
                    "chunk_id": hit.get("chunk_id"),
                    "fact_id": hit.get("metadata", {}).get("fact_id"),
                    "memory_id": hit.get("metadata", {}).get("memory_id"),
                }
            )
        answer = "基于当前报告、企业知识库和长期记忆，可以这样判断：\n" + "\n".join(evidence_lines)
        return answer, citations

    @staticmethod
    def _build_report_chunks(report_path: str) -> list[dict[str, Any]]:
        path = Path(report_path)
        report_text = path.read_text(encoding="utf-8") if path.exists() else ""
        return [
            {
                "chunk_id": f"report_{idx}",
                "doc_id": path.name,
                "text": part,
                "metadata": {"path": str(path), "source_type": "report_section"},
            }
            for idx, part in enumerate(report_text.split("\n\n"))
            if part.strip()
        ]

    def _build_company_fact_chunks(self) -> list[dict[str, Any]]:
        knowledge = load_company_knowledge(self.company_id)
        chunks = []
        for fact in knowledge.get("facts", []):
            chunks.append(
                {
                    "chunk_id": f"fact_{fact.get('fact_id')}",
                    "doc_id": str(fact.get("source_path")),
                    "text": f"{fact.get('value', '')}\n{fact.get('text', '')}\n{' '.join(str(x) for x in fact.get('policy_relevance', []))}",
                    "metadata": {
                        "source_type": "company_fact",
                        "path": fact.get("source_path"),
                        "fact_id": fact.get("fact_id"),
                    },
                }
            )
        return chunks

    def _build_memory_chunks(self, question: str) -> list[dict[str, Any]]:
        rows = self.store.search_memory(question, top_k=8)
        return [
            {
                "chunk_id": f"memory_{row.get('id')}",
                "doc_id": "memory",
                "text": str(row.get("content", "")),
                "metadata": {
                    "source_type": "memory_item",
                    "memory_id": row.get("id"),
                    "path": "company_memory.sqlite",
                },
            }
            for row in rows
        ]

    def _maybe_create_correction(self, text: str) -> dict[str, Any] | None:
        if not any(token in text for token in ("纠正", "不是", "改成", "应该是", "已经有", "补充")):
            return None
        knowledge = load_company_knowledge(self.company_id)
        facts = knowledge.get("facts", [])
        if not facts:
            return None
        target = self._choose_target_fact(text, facts)
        new_claim = self._extract_new_claim(text)
        if not new_claim:
            return None
        proposal = create_update_proposal(
            company_id=self.company_id,
            target_file=str(target.get("source_path", "")),
            target_fact_id=str(target.get("fact_id", "")),
            old_claim=str(target.get("value", "")),
            new_claim=new_claim,
        )
        payload = proposal.to_dict()
        self.store.save_correction_proposal(payload)
        return payload

    @staticmethod
    def _choose_target_fact(text: str, facts: list[dict[str, Any]]) -> dict[str, Any]:
        chunks = [
            {
                "chunk_id": str(fact.get("fact_id")),
                "doc_id": str(fact.get("source_path")),
                "text": f"{fact.get('value', '')}\n{fact.get('text', '')}",
                "metadata": fact,
            }
            for fact in facts
        ]
        hits = HybridRetriever(chunks).retrieve(text, top_k=1)
        return hits[0].get("metadata", facts[0]) if hits else facts[0]

    @staticmethod
    def _extract_new_claim(text: str) -> str:
        patterns = [
            r"改成(?P<value>.+)$",
            r"应该是(?P<value>.+)$",
            r"不是.+?是(?P<value>.+)$",
            r"已经有(?P<value>.+)$",
            r"补充(?P<value>.+)$",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group("value").strip(" ：:。")
        return text.strip()
