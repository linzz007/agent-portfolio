"""Gold-free evidence construction for selective table reasoning."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
import re
from typing import Dict, List, Mapping, Sequence

import pandas as pd


TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
MISSING_MARKERS = {"", "nan", "none", "n/a", "na", "tba", "unknown"}
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "by",
    "for",
    "from",
    "had",
    "has",
    "have",
    "how",
    "in",
    "is",
    "no",
    "of",
    "on",
    "or",
    "the",
    "to",
    "was",
    "were",
    "what",
    "when",
    "which",
    "who",
    "with",
}


@dataclass(frozen=True)
class EvidenceRef:
    row_index: int
    column: str
    value: str
    match_type: str

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class EvidencePack:
    dataset_name: str
    candidate_rows: List[EvidenceRef] = field(default_factory=list)
    candidate_columns: List[str] = field(default_factory=list)
    entity_matches: Dict[str, List[EvidenceRef]] = field(default_factory=dict)
    operation_hints: List[str] = field(default_factory=list)
    missing_or_ambiguous_items: List[str] = field(default_factory=list)
    structure_signals: Dict[str, float] = field(default_factory=dict)
    ambiguity_signals: Dict[str, float] = field(default_factory=dict)
    gap_signals: Dict[str, float] = field(default_factory=dict)
    operation_signals: Dict[str, float] = field(default_factory=dict)
    provenance: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {
            "dataset_name": self.dataset_name,
            "candidate_rows": [row.to_dict() for row in self.candidate_rows],
            "candidate_columns": list(self.candidate_columns),
            "entity_matches": {
                key: [ref.to_dict() for ref in refs] for key, refs in self.entity_matches.items()
            },
            "operation_hints": list(self.operation_hints),
            "missing_or_ambiguous_items": list(self.missing_or_ambiguous_items),
            "structure_signals": dict(self.structure_signals),
            "ambiguity_signals": dict(self.ambiguity_signals),
            "gap_signals": dict(self.gap_signals),
            "operation_signals": dict(self.operation_signals),
            "provenance": dict(self.provenance),
        }


def _tokens(text: object) -> List[str]:
    return [token.lower() for token in TOKEN_RE.findall(str(text or ""))]


def _content_tokens(text: object) -> List[str]:
    return [token for token in _tokens(text) if token not in STOPWORDS and len(token) > 1]


def _token_matches(query_token: str, value_tokens: Sequence[str]) -> bool:
    for value_token in value_tokens:
        if query_token == value_token:
            return True
        if len(query_token) >= 4 and len(value_token) >= 4:
            if query_token.startswith(value_token) or value_token.startswith(query_token):
                return True
    return False


def _safe_value(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def _semantic_type(series: pd.Series) -> str:
    numeric = pd.to_numeric(series, errors="coerce").notna().sum()
    non_null = series.notna().sum()
    if non_null and numeric / max(1, non_null) >= 0.8:
        return "numeric"
    return "text"


def _safe_schema(schema: Mapping[str, object] | None) -> Dict[str, object]:
    result: Dict[str, object] = {}
    for key, value in dict(schema or {}).items():
        lowered = str(key).lower()
        if any(blocked in lowered for blocked in ("gold", "answer", "label", "correct")):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[str(key)] = value
        elif isinstance(value, (list, tuple)):
            result[str(key)] = list(value)[:12]
        elif isinstance(value, dict):
            result[str(key)] = {
                str(inner_key): inner_value
                for inner_key, inner_value in list(value.items())[:12]
                if not any(blocked in str(inner_key).lower() for blocked in ("gold", "answer", "label", "correct"))
            }
    return result


class EvidenceBuilder:
    def __init__(self, max_candidates: int = 24) -> None:
        self.max_candidates = max(1, int(max_candidates))

    def build(
        self,
        question: str,
        df: pd.DataFrame,
        schema: Mapping[str, object] | None,
        dataset_name: str,
        answer_contract,
    ) -> EvidencePack:
        question_tokens = _content_tokens(question)
        refs_by_token: Dict[str, List[EvidenceRef]] = {token: [] for token in question_tokens}
        row_refs: List[EvidenceRef] = []
        candidate_columns: List[str] = []
        seen_refs: set[tuple[int, str, str]] = set()

        column_matched_tokens: set[str] = set()
        for column in df.columns:
            column_name = str(column)
            column_tokens = _tokens(column_name)
            matched_column_tokens = [
                token for token in question_tokens if _token_matches(token, column_tokens)
            ]
            if matched_column_tokens:
                candidate_columns.append(column_name)
                column_matched_tokens.update(matched_column_tokens)

        for row_index, row in df.iterrows():
            for column in df.columns:
                value = _safe_value(row[column])
                value_tokens = _tokens(value)
                if not value_tokens:
                    continue
                matched = [
                    token for token in question_tokens if _token_matches(token, value_tokens)
                ]
                if not matched:
                    continue
                ref = EvidenceRef(
                    row_index=int(row_index),
                    column=str(column),
                    value=value,
                    match_type="cell_token",
                )
                key = (ref.row_index, ref.column, ref.value)
                if key not in seen_refs:
                    row_refs.append(ref)
                    seen_refs.add(key)
                if str(column) not in candidate_columns:
                    candidate_columns.append(str(column))
                for token in matched:
                    refs_by_token.setdefault(token, []).append(ref)

        candidate_rows = row_refs[: self.max_candidates]
        entity_matches = {
            token: refs[: self.max_candidates]
            for token, refs in refs_by_token.items()
            if refs and len(token) > 2
        }

        missing_tokens = [
            token
            for token in question_tokens
            if len(token) > 3 and not refs_by_token.get(token) and token not in column_matched_tokens
        ]
        if missing_tokens:
            missing_or_ambiguous = [f"unmatched:{token}" for token in missing_tokens[:8]]
        else:
            missing_or_ambiguous = []

        matched_cells = len(row_refs)
        row_indexes = sorted({ref.row_index for ref in row_refs})
        if len(row_indexes) <= 1 or len(df) <= 1:
            dispersion = 0.0
        else:
            span = row_indexes[-1] - row_indexes[0] + 1
            dispersion = min(1.0, span / max(1, len(df)))

        column_types = {
            _semantic_type(df[column])
            for column in candidate_columns
            if column in df.columns
        }
        type_signal = 0.5 if {"numeric", "text"}.issubset(column_types) else 0.0

        candidate_frame = df
        if row_indexes:
            candidate_frame = df.iloc[row_indexes]
        missing_count = 0
        total_candidate_values = 0
        for column in candidate_columns or [str(column) for column in df.columns]:
            if column not in candidate_frame.columns:
                continue
            for value in candidate_frame[column].tolist():
                total_candidate_values += 1
                if _safe_value(value).lower() in MISSING_MARKERS:
                    missing_count += 1
        missing_ratio = missing_count / max(1, total_candidate_values)

        text = str(question or "").lower()
        operation_hints: List[str] = []
        steps = 0.0
        dependency = 0.0
        contract = 0.0
        unit = 0.0
        logic = 0.0

        if re.search(r"\b(count|number|how many|two|three|four|five)\b", text):
            operation_hints.append("count")
            steps = max(steps, 0.5)
        if re.search(r"\b(maximum|minimum|most|least|highest|lowest|first|last|before|after)\b", text):
            operation_hints.append("ordering")
            dependency = 1.0
        if re.search(r"\b(and|or|not|no|only|either|both)\b", text):
            operation_hints.append("logic")
            logic = max(logic, 0.5)
        if re.search(
            r"\b(consistently|correlation|inverse|ranked?|top\s+\d+|"
            r"from\s+\d{4}\s+to\s+\d{4}|1[89]00s)\b",
            text,
        ):
            operation_hints.append("temporal_consistency")
            dependency = max(dependency, 0.8)
            logic = max(logic, 0.8)
        if re.search(r"\b(average|mean|percent|percentage|rate|ratio|unit|miles|kg)\b|%", text):
            operation_hints.append("unit")
            unit = max(unit, 0.5)
        if getattr(answer_contract, "kind", "") in {"list", "tuple"} or re.search(
            r"\b(list|which|how many by)\b",
            text,
        ):
            operation_hints.append("contract")
            contract = max(contract, 0.5)

        pack = EvidencePack(
            dataset_name=str(dataset_name or "").lower(),
            candidate_rows=candidate_rows,
            candidate_columns=candidate_columns[: self.max_candidates],
            entity_matches=entity_matches,
            operation_hints=operation_hints,
            missing_or_ambiguous_items=missing_or_ambiguous,
            structure_signals={
                "coverage": min(1.0, math.log2(max(1, matched_cells) + 1) / 6.0),
                "dispersion": dispersion,
                "type": type_signal,
            },
            ambiguity_signals={
                "entity": 1.0 if question_tokens and not row_refs else 0.0,
                "column": min(1.0, max(0, len(candidate_columns) - 1) / 4.0),
                "temporal": 1.0 if re.search(r"\b(before|after|last|first|latest|earliest)\b", text) else 0.0,
                "reference": 1.0 if re.search(r"\b(same|other|that|those|it|they)\b", text) else 0.0,
            },
            gap_signals={
                "entity": min(1.0, len(missing_tokens) / max(1, len(question_tokens))),
                "column": 1.0 if not candidate_columns else 0.0,
                "missing": min(1.0, missing_ratio),
                "stability": 0.5 if len(candidate_rows) >= self.max_candidates else 0.0,
            },
            operation_signals={
                "steps": steps,
                "dependency": dependency,
                "contract": contract,
                "unit": unit,
                "logic": logic,
            },
            provenance={
                "rows": int(len(df)),
                "columns": [str(column) for column in df.columns],
                "schema": _safe_schema(schema),
            },
        )
        return pack
