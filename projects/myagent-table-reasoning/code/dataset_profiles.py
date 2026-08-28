"""Gold-independent dataset contracts and prompt constraints."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Optional, Tuple

import pandas as pd


DEFAULT_MISSING_MARKERS = ("", "nan", "none", "n/a", "tba", "unknown")


@dataclass(frozen=True)
class DatasetHints:
    dataset: str
    answer_mode: str = ""
    allowed_labels: Tuple[str, ...] = ()
    kind: str = ""
    arity: Optional[int] = None
    decimal_places: Optional[int] = None
    reasoning_required: Optional[bool] = None
    prompt_instructions: str = ""
    missing_markers: Tuple[str, ...] = DEFAULT_MISSING_MARKERS


def _normalize_dataset(dataset: str) -> str:
    normalized = (dataset or "").strip().lower()
    aliases = {
        "wiki_table_questions": "wtq",
        "tablefact": "tabfact",
        "table-fact-checking": "tabfact",
        "scitab": "tabfact",
        "crt-qa": "crt",
    }
    return aliases.get(normalized, normalized)


def _is_plural_entity_question(question: str) -> bool:
    match = re.search(
        r"\b(?:which|what)\s+(?:(?:of\s+(?:the|these|those)|the)\s+)?"
        r"(?P<head>[a-z-]+)\b",
        question,
        flags=re.I,
    )
    if not match:
        return False
    head = match.group("head").lower()
    if head in {"is", "was", "are", "were", "has", "have", "did", "does"}:
        return False
    return head.endswith("s") and not head.endswith("ss")


def _wtq_alternative_labels(question: str) -> Tuple[str, ...]:
    match = re.search(
        r"\bmore\b.*?\bin\s+(?:the\s+)?([a-z0-9-]+)\s+or\s+"
        r"(?:the\s+)?([a-z0-9-]+)\b",
        question,
        flags=re.I,
    )
    if not match:
        return ()
    first, second = (part.lower() for part in match.groups())
    return (first, second, "equal")


def _integer_average_precision(question: str, df: pd.DataFrame) -> Optional[int]:
    if not re.search(r"\b(average|mean)\b", question, flags=re.I):
        return None
    if re.search(r"\b(percent|percentage|rate|ratio)\b", question, flags=re.I):
        return 3
    if re.search(r"\b(nearest\s+whole|whole\s+number|integer|round(?:ed)?\s+to\s+0)\b", question, flags=re.I):
        return 0
    return 2


def _crt_decimal_precision(question: str, df: pd.DataFrame) -> Optional[int]:
    text = question or ""
    if re.search(r"\bcorrelation\s+coefficient\b", text, flags=re.I):
        return 4
    explicit = re.search(r"\b(\d+)\s+decimal\s+places?\b", text, flags=re.I)
    if explicit:
        return int(explicit.group(1))
    if re.search(r"\b(nearest\s+whole|whole\s+number|integer|round(?:ed)?\s+to\s+0)\b", text, flags=re.I):
        return 0
    if re.search(r"\b(average|mean)\b", text, flags=re.I):
        return 3
    if re.search(r"\b(percent|percentage|rate|ratio)\b", text, flags=re.I):
        return 3
    return None


def infer_dataset_hints(
    dataset: str,
    question: str,
    df: pd.DataFrame,
) -> DatasetHints:
    """Infer runtime hints without accepting identifiers or target answers."""
    name = _normalize_dataset(dataset)
    text = question or ""

    if name == "wtq":
        is_list = _is_plural_entity_question(text)
        alternative_labels = _wtq_alternative_labels(text)
        relational = bool(
            re.search(
                r"^\s*this\s+.+?\bhas\b|\b(?:who|which)\b.+?\b(?:has|had|with)\b|"
                r"\b(?:difference|average|total|most|least|more|fewer)\b",
                text,
                flags=re.I,
            )
        )
        return DatasetHints(
            dataset=name,
            allowed_labels=alternative_labels,
            kind="label" if alternative_labels else ("list" if is_list else ""),
            reasoning_required=True if is_list or alternative_labels or relational else None,
            prompt_instructions=(
                "WTQ answers are denotations. Preserve every tied or requested entity "
                "as a structured list, use the exact complete table cell text for entity "
                "answers, and ignore missing markers during arithmetic."
            ),
        )

    if name == "tabfact":
        numeric_or_comparative = bool(
            re.search(
                r"\d|\b(more|less|fewer|older|younger|highest|lowest|most|least|"
                r"total|average|difference|percent|ratio|after|before|earlier|later)\b",
                text,
                flags=re.I,
            )
        )
        return DatasetHints(
            dataset=name,
            answer_mode="true_false",
            reasoning_required=True if numeric_or_comparative else False,
            prompt_instructions=(
                "TabFact requires exactly true or false. Recompute numeric, count, "
                "comparison, temporal, and superlative claims from the table. Verify "
                "every entity, value, date, and relation in a compound statement. A "
                "row may express the relation named by the table context even when no "
                "separate relation column exists."
            ),
        )

    if name == "crt":
        tuple_question = len(re.findall(r"\bhow many\b", text, flags=re.I)) >= 2
        home_away = bool(re.search(r"\bhome\s+or\s+away\b", text, flags=re.I))
        relational = bool(
            re.search(
                r"\b(multiple|outliers?|correlation|relationship|ratio|percentage|"
                r"percent|compare|compared|average|mean|difference)\b",
                text,
                flags=re.I,
            )
        )
        return DatasetHints(
            dataset=name,
            allowed_labels=("home", "away") if home_away else (),
            kind="tuple" if tuple_question else ("label" if home_away else ""),
            arity=2 if tuple_question else None,
            decimal_places=_crt_decimal_precision(text, df),
            reasoning_required=True if tuple_question or home_away or relational else None,
            prompt_instructions=(
                "CRT mixes scalar, alternative-label, and multi-value answers. "
                "Use the exact requested answer shape and preserve requested units such "
                "as percent signs. Compare like-for-like units. When one group is "
                "compared with other groups, compare its per-group value with each peer "
                "or the requested peer baseline; do not sum all peers unless the question "
                "explicitly asks for their combined total."
            ),
        )

    return DatasetHints(dataset=name)
