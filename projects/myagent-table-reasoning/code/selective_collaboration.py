"""Candidate comparison and fallback helpers for selective collaboration."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import re
from typing import Any, Dict, List, Sequence

from answer_contracts import normalize_contract_value, validate_contract_value


@dataclass
class CandidateAnswer:
    name: str
    raw_answer: Any
    normalized_answer: Any
    is_valid: bool
    reasoning_summary: str = ""
    evidence_refs: List[Dict[str, object]] = field(default_factory=list)
    executable_program: str = ""
    execution_result: Any = None
    confidence: float = 0.0
    token_usage: int = 0
    failure: str = ""

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class AgreementDecision:
    selected: CandidateAnswer | None
    candidates: List[CandidateAnswer]
    agreement: bool
    requires_fallback: bool
    disagreement: float
    verification_gap: float
    reason: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "selected": self.selected.to_dict() if self.selected else None,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "agreement": self.agreement,
            "requires_fallback": self.requires_fallback,
            "disagreement": self.disagreement,
            "verification_gap": self.verification_gap,
            "reason": self.reason,
        }


def normalize_atom(value: Any) -> str:
    text = str(value if value is not None else "").strip().lower()
    text = text.replace('\\"', '"')
    text = re.sub(r"\s+", " ", text)
    text = text.strip(" \t\r\n\"'`.,;:()[]{}")
    return text


def _numeric_value(value: Any) -> float | None:
    text = normalize_atom(value).replace(",", "")
    match = re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return None
    return float(text)


def to_sequence(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [normalize_atom(item) for item in value if normalize_atom(item)]
    text = normalize_atom(value)
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [normalize_atom(item) for item in parsed if normalize_atom(item)]
        except json.JSONDecodeError:
            pass
    if "," in text or ";" in text or "|" in text:
        return [normalize_atom(item) for item in re.split(r"\s*[,;|]\s*", text) if normalize_atom(item)]
    return [text]


def _first_json_object(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        raise json.JSONDecodeError("empty response", raw, 0)
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.I | re.S)
    if fenced:
        payload = json.loads(fenced.group(1))
        if isinstance(payload, dict):
            return payload

    match = re.search(r"\{.*\}", raw, flags=re.S)
    if match:
        payload = json.loads(match.group(0))
        if isinstance(payload, dict):
            return payload
    raise json.JSONDecodeError("no JSON object found", raw, 0)


def _truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    omitted = len(text) - max_chars
    return f"{text[:max_chars]}... [truncated {omitted} chars]"


def _compact_prompt_value(value: Any, *, max_string: int = 2500, max_items: int = 24, depth: int = 0) -> Any:
    if isinstance(value, str):
        return _truncate_text(value, max_string)
    if isinstance(value, dict):
        if depth >= 4:
            return _truncate_text(json.dumps(value, ensure_ascii=False, default=str), max_string)
        return {
            str(key): _compact_prompt_value(
                item,
                max_string=max_string,
                max_items=max_items,
                depth=depth + 1,
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        items = [
            _compact_prompt_value(
                item,
                max_string=max_string,
                max_items=max_items,
                depth=depth + 1,
            )
            for item in list(value)[:max_items]
        ]
        if len(value) > max_items:
            items.append(f"... [truncated {len(value) - max_items} items]")
        return items
    return value


def answer_similarity(left: Any, right: Any, answer_contract) -> float:
    left_norm = normalize_contract_value(left, answer_contract)
    right_norm = normalize_contract_value(right, answer_contract)

    left_num = _numeric_value(left_norm)
    right_num = _numeric_value(right_norm)
    if left_num is not None and right_num is not None:
        return 1.0 if abs(left_num - right_num) <= 1e-6 else 0.0

    kind = getattr(answer_contract, "kind", "")
    if kind == "tuple":
        left_seq = to_sequence(left_norm)
        right_seq = to_sequence(right_norm)
        if not left_seq and not right_seq:
            return 1.0
        return 1.0 if left_seq == right_seq else 0.0

    if kind == "list":
        left_set = set(to_sequence(left_norm))
        right_set = set(to_sequence(right_norm))
        if not left_set and not right_set:
            return 1.0
        if not left_set or not right_set:
            return 0.0
        return len(left_set & right_set) / len(left_set | right_set)

    return 1.0 if normalize_atom(left_norm) == normalize_atom(right_norm) else 0.0


def _has_execution_result(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value != ""
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    size = getattr(value, "size", None)
    if size is not None:
        try:
            return int(size) > 0
        except (TypeError, ValueError):
            pass
    try:
        return len(value) > 0
    except TypeError:
        return True


def verification_gap(candidate: CandidateAnswer, evidence_pack: Any | None = None) -> float:
    if not candidate.is_valid:
        return 1.0
    if _has_execution_result(candidate.execution_result):
        return 0.0
    evidence_text = ""
    if evidence_pack is not None:
        if hasattr(evidence_pack, "to_dict"):
            evidence_text = json.dumps(evidence_pack.to_dict(), ensure_ascii=False)
        else:
            evidence_text = json.dumps(evidence_pack, ensure_ascii=False, default=str)
    if not evidence_text:
        return 0.5
    atoms = to_sequence(candidate.normalized_answer)
    if atoms and all(atom and atom in evidence_text.lower() for atom in atoms):
        return 0.0
    return 0.5


class AgreementJudge:
    def __init__(self, answer_contract, evidence_pack: Any | None = None) -> None:
        self.answer_contract = answer_contract
        self.evidence_pack = evidence_pack

    def decide(self, candidates: Sequence[CandidateAnswer]) -> AgreementDecision:
        candidate_list = list(candidates)
        valid = [candidate for candidate in candidate_list if candidate.is_valid]
        if not valid:
            return AgreementDecision(
                selected=None,
                candidates=candidate_list,
                agreement=False,
                requires_fallback=True,
                disagreement=1.0,
                verification_gap=1.0,
                reason="no_valid_candidate",
            )
        if len(valid) == 1:
            selected = valid[0]
            return AgreementDecision(
                selected=selected,
                candidates=candidate_list,
                agreement=True,
                requires_fallback=False,
                disagreement=0.0,
                verification_gap=verification_gap(selected, self.evidence_pack),
                reason="single_valid_candidate",
            )

        ranked = sorted(valid, key=lambda item: (item.confidence, -verification_gap(item, self.evidence_pack)), reverse=True)
        first, second = ranked[0], ranked[1]
        similarity = answer_similarity(first.normalized_answer, second.normalized_answer, self.answer_contract)
        gap = max(verification_gap(first, self.evidence_pack), verification_gap(second, self.evidence_pack))
        if similarity >= 1.0:
            return AgreementDecision(
                selected=first,
                candidates=candidate_list,
                agreement=True,
                requires_fallback=False,
                disagreement=0.0,
                verification_gap=gap,
                reason="valid_candidates_agree",
            )
        return AgreementDecision(
            selected=None,
            candidates=candidate_list,
            agreement=False,
            requires_fallback=True,
            disagreement=1.0 - similarity,
            verification_gap=gap,
            reason="valid_candidates_disagree",
        )


class ThinkingSolver:
    def __init__(self, llm) -> None:
        self.llm = llm

    def _call_llm(self, prompt: str) -> str:
        if hasattr(self.llm, "complete"):
            return self.llm.complete(prompt, temperature=0.0, max_tokens=512)
        return self.llm(prompt)

    @staticmethod
    def _candidate_prompt_payload(candidate: CandidateAnswer) -> Dict[str, Any]:
        return {
            "name": candidate.name,
            "answer": _compact_prompt_value(candidate.normalized_answer, max_string=600),
            "is_valid": bool(candidate.is_valid),
            "confidence": float(candidate.confidence or 0.0),
            "reasoning_summary": _truncate_text(str(candidate.reasoning_summary or ""), 600),
            "failure": _truncate_text(str(candidate.failure or ""), 300),
        }

    def solve(
        self,
        question: str,
        evidence: Dict[str, object],
        candidates: Sequence[CandidateAnswer],
        answer_contract,
        style: str = "direct",
    ) -> CandidateAnswer:
        contract_payload = (
            answer_contract.as_dict() if hasattr(answer_contract, "as_dict") else dict(answer_contract)
        )
        candidate_payload = [
            self._candidate_prompt_payload(candidate)
            for candidate in candidates
        ]
        evidence_payload = _compact_prompt_value(evidence, max_string=1400, max_items=16)
        style_instructions = {
            "direct": (
                "Use a direct recomputation path. Identify the exact rows and columns "
                "needed, then return the requested final answer."
            ),
            "audit": (
                "Act as a skeptical auditor. First look for ways each candidate could "
                "be wrong: wrong row, wrong column, missing comparison peer, wrong "
                "aggregation, answer-shape mismatch, or unsupported label. Then return "
                "the corrected answer."
            ),
            "program": (
                "Mentally construct a minimal dataframe-style calculation from the "
                "table evidence. Compare all relevant rows or columns before returning "
                "the final answer."
            ),
        }.get(style, style)
        prompt = (
            "You are the final high-risk verifier for a table QA system.\n"
            "Recompute the answer independently from the supplied table evidence. "
            "Do not trust the candidate summaries when they conflict with the table.\n"
            "Use only the provided evidence, table text, answer contract, and candidate summaries.\n"
            f"{style_instructions}\n"
            "Return JSON with keys answer, confidence, and reasoning_summary.\n"
            "The answer field must obey the answer contract exactly.\n"
            "Return JSON only.\n\n"
            f"Question: {question}\n"
            f"Answer contract: {json.dumps(contract_payload, ensure_ascii=False)}\n"
            f"Evidence: {json.dumps(evidence_payload, ensure_ascii=False, default=str)}\n"
            f"Candidates: {json.dumps(candidate_payload, ensure_ascii=False, default=str)}\n"
        )
        try:
            payload = _first_json_object(self._call_llm(prompt))
            answer = payload.get("answer", "")
            normalized = normalize_contract_value(answer, answer_contract)
            is_valid, reason = validate_contract_value(normalized, answer_contract)
            return CandidateAnswer(
                name=f"thinking_{style}",
                raw_answer=answer,
                normalized_answer=normalized,
                is_valid=is_valid,
                reasoning_summary=str(payload.get("reasoning_summary", "")),
                confidence=float(payload.get("confidence", 0.0) or 0.0),
                failure="" if is_valid else reason,
            )
        except Exception as exc:
            return CandidateAnswer(
                name=f"thinking_{style}",
                raw_answer="",
                normalized_answer="",
                is_valid=False,
                failure=f"thinking_parse_error:{exc}",
            )
