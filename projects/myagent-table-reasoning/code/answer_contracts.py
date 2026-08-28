"""Gold-independent answer-shape inference and validation for table QA."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
import re
from typing import Any, Optional, Tuple


LABELS_BY_MODE = {
    "true_false": ("true", "false"),
    "yes_no": ("Yes", "No"),
    "better_worse_equal": ("better", "worse", "equal"),
    "better_worse": ("better", "worse"),
    "more_less_equal": ("more", "less", "equal"),
    "increase_decrease_no_change": ("Increase", "Decrease", "No change"),
}

_LIST_NOUNS = (
    "teams",
    "countries",
    "players",
    "songs",
    "names",
    "nations",
    "years",
    "cities",
    "states",
    "elements",
    "manufacturers",
    "people",
)

_REASONING_PATTERN = re.compile(
    r"\b(average|mean|ratio|percentage|percent|proportion|difference|total|sum|"
    r"over|under|more|less|at least|at most|higher|lower|greater|fewer|equal|"
    r"best|worst|most|least|both|compared|compare|comparison|trend|outlier|"
    r"increase|decrease|better|worse|tend|generally|usually)\b",
    flags=re.I,
)

_NUMERIC_OPERATION_PATTERN = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:%|percent|percentage|of\b|point|points|game|games|"
    r"appearance|appearances|time|times|race|races)",
    flags=re.I,
)


@dataclass(frozen=True)
class AnswerContract:
    kind: str
    allowed_labels: Tuple[str, ...] = ()
    reasoning_required: bool = False
    instructions: str = ""
    decimal_places: Optional[int] = None
    arity: Optional[int] = None

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["allowed_labels"] = list(self.allowed_labels)
        return data


def _is_list_question(question: str) -> bool:
    nouns = "|".join(re.escape(noun) for noun in _LIST_NOUNS)
    return bool(
        re.search(rf"\b(?:what|which)\s+(?:were\s+|are\s+)?(?:the\s+)?(?:{nouns})\b", question, flags=re.I)
    )


def _requires_reasoning(question: str, kind: str, answer_mode: str) -> bool:
    if kind == "list":
        return True
    if not answer_mode:
        return False
    core_question = re.split(
        r"\banswer\s+with\s+only\b",
        question or "",
        maxsplit=1,
        flags=re.I,
    )[0]
    return bool(
        _REASONING_PATTERN.search(core_question)
        or _NUMERIC_OPERATION_PATTERN.search(core_question)
    )


def infer_answer_contract(
    question: str,
    answer_mode: str = "",
    *,
    allowed_labels: Tuple[str, ...] = (),
    kind_override: str = "",
    arity: Optional[int] = None,
    decimal_places: Optional[int] = None,
    reasoning_required: Optional[bool] = None,
    extra_instructions: str = "",
) -> AnswerContract:
    text = question or ""
    labels = tuple(allowed_labels or LABELS_BY_MODE.get(answer_mode, ()))
    kind = kind_override or ("label" if labels else "")
    if kind == "label":
        instructions = (
            "Assign final_answer_value to exactly one label from: "
            + ", ".join(labels)
            + ". Do not return an explanation or an intermediate number."
        )
    elif kind == "tuple":
        tuple_arity = arity or 2
        instructions = (
            f"Assign final_answer_value to a Python list or tuple with exactly "
            f"{tuple_arity} values in the same order requested. Return values only, "
            "without explanatory words."
        )
    elif kind == "list" or (not kind and _is_list_question(text)):
        instructions = (
            "Assign final_answer_value to a Python list of the requested entity "
            "names. Return the entities, not their count."
        )
        kind = "list"
    else:
        instructions = (
            "Assign final_answer_value to one concise scalar value or text answer, "
            "not an explanatory sentence."
        )
        kind = "scalar"
        if re.match(r"\s*(?:who|which|what)\b", text, flags=re.I):
            instructions += (
                " If the question asks for an entity, item, group, team, film, "
                "country, or combination selected by a numeric comparison, return "
                "that requested name or combination, not the intermediate numeric value."
            )

    inferred_decimal_places = decimal_places
    if (
        inferred_decimal_places is None
        and kind == "scalar"
        and re.search(r"\b(average|mean)\b", text, flags=re.I)
    ):
        if not re.search(
            r"\b(?:round(?:ed|ing)?\s+(?:to|at)|nearest|decimal places?|precision)\b",
            text,
            flags=re.I,
        ):
            inferred_decimal_places = 2
            instructions += (
                " For an unqualified numeric average, round the final scalar to "
                "two decimal places."
            )

    if decimal_places is not None and kind == "scalar":
        instructions += (
            f" Round the final numeric scalar to {decimal_places} decimal places."
        )

    if re.search(r"\bsame\s+(?:group|category|class)\s+as\b", text, flags=re.I):
        instructions += (
            " Exclude the named reference item itself unless the question explicitly "
            "asks to include it."
        )

    if extra_instructions.strip():
        instructions += " " + extra_instructions.strip()

    return AnswerContract(
        kind=kind,
        allowed_labels=tuple(labels),
        reasoning_required=(
            bool(reasoning_required)
            if reasoning_required is not None
            else _requires_reasoning(text, kind, answer_mode)
        ),
        instructions=instructions,
        decimal_places=inferred_decimal_places,
        arity=arity,
    )


def _scalar_item(value: Any) -> Any:
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            return value
    return value


def _clean_text(value: Any) -> str:
    text = str(_scalar_item(value)).strip()
    text = text.replace('\\"', '"')
    text = text.strip(" \t\r\n\\\"'")
    text = re.sub(
        r"(?<=\d)\.0(?=\s*(?:thousand|million|billion|trillion)\b)",
        "",
        text,
        flags=re.I,
    )
    return text.strip()


def normalize_contract_value(value: Any, contract: AnswerContract) -> Any:
    value = _scalar_item(value)
    if contract.kind == "tuple":
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("[") or text.startswith("("):
                try:
                    candidate = text
                    if text.startswith("(") and text.endswith(")"):
                        candidate = "[" + text[1:-1] + "]"
                    parsed = json.loads(candidate)
                    if isinstance(parsed, list):
                        value = parsed
                except json.JSONDecodeError:
                    pass
            if isinstance(value, str):
                value = re.split(r"\s*[,;|]\s*", text)
        if isinstance(value, tuple):
            value = list(value)
        if isinstance(value, list):
            return [
                _clean_text(item) if isinstance(item, str) else _scalar_item(item)
                for item in value
            ]
        return value

    if contract.kind == "list":
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, list):
                        value = parsed
                except json.JSONDecodeError:
                    pass
            if isinstance(value, str):
                value = re.split(r"\s*[,;|]\s*", text)
        if isinstance(value, (tuple, set)):
            value = list(value)
        if isinstance(value, list):
            return [_clean_text(item) for item in value if _clean_text(item)]
        return value

    if contract.kind == "label":
        canonical = {label.lower(): label for label in contract.allowed_labels}
        if isinstance(value, bool) and set(canonical) == {"true", "false"}:
            return canonical["true" if value else "false"]
        if isinstance(value, (int, float)) and set(canonical) == {"true", "false"}:
            if float(value) in {0.0, 1.0}:
                return canonical["true" if float(value) == 1.0 else "false"]
        text = _clean_text(value)
        normalized = re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()
        if normalized in canonical:
            return canonical[normalized]
        matches = [
            label
            for key, label in canonical.items()
            if re.search(rf"\b{re.escape(key)}\b", normalized)
        ]
        if len(matches) == 1:
            return matches[0]
        return text

    if isinstance(value, (list, tuple)) and len(value) == 1:
        value = value[0]
    if contract.decimal_places is not None:
        try:
            return round(float(_scalar_item(value)), contract.decimal_places)
        except (TypeError, ValueError):
            pass
    return _clean_text(value) if isinstance(value, str) else _scalar_item(value)


def validate_contract_value(
    value: Any,
    contract: AnswerContract,
) -> tuple[bool, str]:
    if contract.kind == "tuple":
        expected = contract.arity or 2
        if not isinstance(value, (list, tuple)) or len(value) != expected:
            return False, f"Answer contract requires exactly {expected} values."
        if any(item is None or not str(item).strip() for item in value):
            return False, "Tuple answer contains an empty value."
        return True, ""

    if contract.kind == "list":
        if not isinstance(value, list) or not value:
            return False, "Answer contract requires a non-empty list of entities."
        if any(not str(item).strip() for item in value):
            return False, "Answer list contains an empty entity."
        return True, ""

    if contract.kind == "label":
        allowed = {label.lower() for label in contract.allowed_labels}
        if str(value).strip().lower() not in allowed:
            return False, (
                "Answer contract requires exactly one allowed label: "
                + ", ".join(contract.allowed_labels)
                + "."
            )
        return True, ""

    if isinstance(value, float) and math.isnan(value):
        return False, "Answer contract requires one non-NaN scalar value."
    if value is None or (isinstance(value, str) and not value.strip()):
        return False, "Answer contract requires one non-empty scalar value."
    if isinstance(value, (list, tuple, set, dict)) and len(value) != 1:
        return False, "Answer contract requires one scalar value, not a collection."
    return True, ""
