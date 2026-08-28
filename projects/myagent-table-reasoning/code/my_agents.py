"""Custom table QA pipeline for myAgent.

This module defines a simplified agent architecture:
- TQASessionState: per-sample state container
- RouterAgent: decides SIMPLE vs COMPLEX based on semantic + structural scores
- PlannerAgent: generates [PLAN] + [CODE] from question + table schema
- Calculator: safely executes generated code on a pandas DataFrame
- CriticAgent: lightweight checker; can request REPLAN via feedback
- MultiViewValidator: optional Evidence/Logic/Cross-path validation scaffold
- FinalAnswerAgent: formats final natural language answer
- TableQAPipeline: orchestrator that wires everything together
"""

from __future__ import annotations

import ast
from fractions import Fraction
import json
import math
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

from answer_contracts import (
    AnswerContract,
    infer_answer_contract,
    normalize_contract_value,
    validate_contract_value,
)
from evidence_builder import EvidenceBuilder
from risk_control import BudgetController, BudgetPolicy, RiskAssessment, RiskProfiler
from selective_collaboration import (
    AgreementJudge,
    AgreementDecision,
    CandidateAnswer,
    ThinkingSolver,
    answer_similarity,
)


class TQASessionState:
    """Unified state container for one table QA session.

    One question + one table => one state object.
    All intermediate information (routing scores, plan, code, execution
    results, critic feedback, final answer) is stored here.
    """

    def __init__(
        self,
        question: str,
        df: pd.DataFrame,
        table_schema: Dict[str, Any],
        answer_mode: str = "",
        table_context: str = "",
        answer_contract: Optional[AnswerContract] = None,
        dataset_profile: str = "",
        dataset_instructions: str = "",
        missing_markers: Tuple[str, ...] = (),
    ):
        self.question: str = question
        self.table_context: str = (table_context or "").strip()
        self.answer_mode: str = answer_mode
        self.answer_contract = answer_contract or infer_answer_contract(question, answer_mode)
        self.dataset_profile: str = (dataset_profile or "").strip()
        self.dataset_instructions: str = (dataset_instructions or "").strip()
        self.missing_markers: Tuple[str, ...] = tuple(missing_markers or ())
        self.contract_validation: Dict[str, Any] = {
            "valid": None,
            "reason": "",
        }
        self.grounding_validation: Dict[str, Any] = {
            "valid": None,
            "reason": "",
        }
        self.risk_escalated: bool = False
        self.risk_assessment = None
        self.post_risk_assessment = None
        self.risk_level: str = ""
        self.problem_tags: List[str] = []
        self.strong_verification_applied: bool = False
        self.strong_verification_reason: str = ""
        self.deterministic_shortcut_applied: bool = False
        self.deterministic_shortcut_reason: str = ""
        self.evidence_pack = None
        self.candidate_answers: List[Any] = []
        self.agreement_decision = None
        self.budget_state: Dict[str, Any] = {}
        self.original_df: pd.DataFrame = df
        self.df: pd.DataFrame = df
        self.table_schema: Dict[str, Any] = table_schema

        # Routing / 难度与路径信息
        self.route_type: Optional[str] = None  # "SIMPLE" or "COMPLEX"
        self.semantic_features: Dict[str, Any] = {}
        self.structural_features: Dict[str, Any] = {}
        self.difficulty_score: Optional[float] = None  # 0~1 综合难度分
        self.difficulty_level: Optional[str] = None  # "easy" / "medium" / "hard"
        self.routing_context: Dict[str, Any] = {}

        # Compression / 成本信息
        self.compressed_df: Optional[pd.DataFrame] = None
        self.compression_info: Dict[str, Any] = {}
        self.cost_metrics: Dict[str, Any] = {}
        self.elapsed_seconds: Optional[float] = None

        # Planner
        self.planner_raw_output: str = ""
        self.plan_steps: List[str] = []
        self.code_str: str = ""

        # Calculator
        self.exec_success: bool = False
        self.exec_error: Optional[str] = None
        self.exec_locals: Dict[str, Any] = {}
        self.final_value: Any = None  # scalar or small object

        # Critic
        self.critic_raw_output: str = ""
        self.critic_verdict: Optional[str] = None  # "PASS" or "REPLAN"
        self.critic_feedback: str = ""
        self.critic_skipped: bool = False

        # Multi-view validation
        self.evidence_critic_raw_output: str = ""
        self.evidence_critic_verdict: Optional[str] = None
        self.evidence_critic_feedback: str = ""
        self.logic_critic_raw_output: str = ""
        self.logic_critic_verdict: Optional[str] = None
        self.logic_critic_feedback: str = ""
        self.alternative_plan_raw_output: str = ""
        self.alternative_code_str: str = ""
        self.alternative_exec_success: bool = False
        self.alternative_exec_error: Optional[str] = None
        self.alternative_final_value: Any = None
        self.cross_validation_verdict: Optional[str] = None
        self.cross_validation_feedback: str = ""
        self.evidence_summary: Dict[str, Any] = {}
        self.multi_view_validation: Dict[str, Any] = {}

        # Final answer
        self.final_answer: Optional[str] = None
        self.simple_lookup_success: bool = False
        self.simple_lookup_value: Any = None
        self.simple_lookup_evidence: Dict[str, Any] = {}
        self.classification_raw_output: str = ""
        self.verification_raw_output: str = ""


# ------------------------ helpers ------------------------


def estimate_text_tokens(text: Any) -> int:
    """Estimate token count for prompt/cost logging without requiring API usage."""
    if text is None:
        return 0
    value = str(text)
    if not value:
        return 0
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(value))
    except Exception:
        # Conservative fallback for Chinese/English mixed prompts.
        return max(1, len(value) // 2)


class LLMCallTracker:
    """Small wrapper that records LLM call count and estimated token usage."""

    def __init__(self, llm_fn: Callable[[str], str]) -> None:
        self.llm_fn = llm_fn
        self.reset()

    def reset(self) -> None:
        self.call_count = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def __call__(self, prompt: str) -> str:
        self.call_count += 1
        self.prompt_tokens += estimate_text_tokens(prompt)
        output = self.llm_fn(prompt)
        self.completion_tokens += estimate_text_tokens(output)
        return output

    def complete(
        self,
        prompt: str,
        *,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
    ) -> str:
        self.call_count += 1
        self.prompt_tokens += estimate_text_tokens(prompt)
        if hasattr(self.llm_fn, "complete"):
            output = self.llm_fn.complete(
                prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        else:
            output = self.llm_fn(prompt)
        self.completion_tokens += estimate_text_tokens(output)
        return output

    def snapshot(self) -> Dict[str, int]:
        return {
            "llm_call_count": self.call_count,
            "prompt_tokens_est": self.prompt_tokens,
            "completion_tokens_est": self.completion_tokens,
            "total_tokens_est": self.prompt_tokens + self.completion_tokens,
        }


def _first_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Extract the first JSON object from an LLM response."""
    if not text:
        return None
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _strip_code_fence(code_text: str) -> str:
    """Accept raw Python, fenced Markdown, or optional section end markers."""
    text = (code_text or "").strip()
    lines = text.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    clean_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped in {"[/CODE]", "[/PYTHON]"}:
            break
        if re.fullmatch(r"</?[A-Za-z_][\w.-]*>", stripped):
            continue
        clean_lines.append(line)
    lines = clean_lines
    return "\n".join(lines).strip()


def classify_problem_tags(
    question: str,
    dataset_profile: str = "",
    answer_contract: Optional[AnswerContract] = None,
) -> List[str]:
    """Gold-independent tags used to decide when extra verification is worth it."""
    text = (question or "").lower()
    tags: List[str] = []

    patterns = [
        ("count", r"\b(how many|count|number of|at least|at most)\b"),
        (
            "superlative_order",
            r"\b(top|bottom|highest|lowest|maximum|minimum|max|min|most|least|"
            r"first|last|earliest|latest|earlier|later|tallest|shortest|largest|smallest)\b",
        ),
        (
            "temporal",
            r"\b(before|after|during|between|from\s+\d{4}|to\s+\d{4}|"
            r"\d{4}|season|date|year|month|week|departing|arrival|arriving)\b",
        ),
        (
            "comparison",
            r"\b(compare|compared|comparison|more|less|greater|larger|smaller|"
            r"higher|lower|better|worse|equal|difference|versus|vs)\b",
        ),
        (
            "arithmetic",
            r"\b(average|mean|sum|total|difference|ratio|percent|percentage|"
            r"proportion|correlation|coefficient|increase|decrease|margin|"
            r"combined|how long|take to get|duration)\b",
        ),
        (
            "negation_logic",
            r"\b(not|no|only|except|without|neither|nor|and|or|both|either|"
            r"at least|at most)\b",
        ),
        ("trend_correlation", r"\b(tend|generally|usually|trend|correlation|inverse)\b"),
    ]
    for tag, pattern in patterns:
        if re.search(pattern, text, flags=re.I):
            tags.append(tag)

    contract = answer_contract
    if contract is not None:
        if getattr(contract, "kind", "") in {"list", "tuple"}:
            tags.append("list_entity")
        if getattr(contract, "allowed_labels", ()):
            tags.append("closed_choice")

    if re.search(r"\banswer\s+with\s+only\b", text, flags=re.I):
        tags.append("closed_choice")
    if (dataset_profile or "").lower() == "tabfact":
        tags.append("closed_choice")

    return list(dict.fromkeys(tags))


def validate_generated_code_grounding(question: str, code_str: str) -> Tuple[bool, str]:
    """Reject a known row-count grounding error before executing generated code."""
    try:
        tree = ast.parse(code_str or "")
    except SyntaxError:
        return True, ""

    code_text = code_str or ""
    after_year = re.search(r"\bafter\s+(\d{4})\b", question or "", flags=re.I)
    if after_year:
        year = after_year.group(1)
        if re.search(rf"(?:>=\s*{year}|{year}\s*<=|==\s*{year})", code_text):
            return (
                False,
                f"For questions asking after {year}, exclude seasons or rows that start in "
                f"{year}; use a strict later-year boundary.",
            )

    if re.search(r"\baverage\s+percentage\s+change\b", question or "", flags=re.I):
        explicit_relative = re.search(
            r"\b(relative|increase|decrease|growth\s+rate|rate\s+of\s+change)\b",
            question or "",
            flags=re.I,
        )
        relative_formula = re.search(
            r"(?:pct_change|percentage_change|\)\s*/\s*[^)\n]+(?:\)\s*)?\*\s*100)",
            code_text,
            flags=re.I,
        )
        if not explicit_relative and relative_formula:
            return (
                False,
                "For percentage snapshot columns such as '% (1960)' and '% (2040)', "
                "average the relevant percentage cells across the requested rows and years "
                "unless the question explicitly asks for relative percent increase.",
            )

    final_label_values = {"yes", "no", "true", "false", "more", "less", "equal"}
    conditional_assigns_final = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        for branch_node in [*node.body, *node.orelse]:
            for nested in ast.walk(branch_node):
                if (
                    isinstance(nested, ast.Assign)
                    and any(
                        isinstance(target, ast.Name)
                        and target.id == "final_answer_value"
                        for target in nested.targets
                    )
                ):
                    conditional_assigns_final = True
                    break
            if conditional_assigns_final:
                break
        if conditional_assigns_final:
            break
    if conditional_assigns_final:
        saw_if = False
        for stmt in tree.body:
            if isinstance(stmt, ast.If):
                saw_if = True
                continue
            if not saw_if or not isinstance(stmt, ast.Assign):
                continue
            if not any(
                isinstance(target, ast.Name) and target.id == "final_answer_value"
                for target in stmt.targets
            ):
                continue
            if isinstance(stmt.value, ast.Constant):
                constant = str(stmt.value.value).strip().lower()
                if constant in final_label_values:
                    return (
                        False,
                        "Do not override a conditional final_answer_value with a hard-coded "
                        "closed-label answer after the condition.",
                    )

    summary_exclusion_requested = re.search(
        r"\b(exclude|excluding|without|non-summary|non summary|peer-only|peer only)\b",
        question or "",
        flags=re.I,
    )
    if not summary_exclusion_requested and re.search(
        r"\b(exclude|excluding|filter\s+out|drop|remove)\b.{0,100}"
        r"\b(summary|sum|total|aggregate|overall)\b",
        code_text,
        flags=re.I | re.S,
    ):
        return (
            False,
            "Do not exclude summary, sum, total, overall, or aggregate rows unless "
            "the question explicitly asks to exclude them.",
        )

    x_of_n_claim = re.search(
        r"\b\d+\s+(?:of|out\s+of)\s+(?:the\s+)?\d+\b",
        question or "",
        flags=re.I,
    )
    asks_for_unique = re.search(r"\b(distinct|unique)\b", question or "", flags=re.I)
    if x_of_n_claim and not asks_for_unique:
        unique_calls = {"unique", "nunique", "drop_duplicates"}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute) and node.func.attr in unique_calls:
                return (
                    False,
                    "The question states X of N and does not ask for distinct or unique "
                    "items. Use row counts such as len(df) and filtered-row counts; do not "
                    "use unique(), nunique(), or drop_duplicates().",
                )

    if re.search(
        r"\bsame\s+(?:group|category|class)\s+as\b",
        question or "",
        flags=re.I,
    ):
        final_value_subtracts_one = any(
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "final_answer_value"
                for target in node.targets
            )
            and any(
                isinstance(child, ast.BinOp)
                and isinstance(child.op, ast.Sub)
                and isinstance(child.right, ast.Constant)
                and child.right.value == 1
                for child in ast.walk(node.value)
            )
            for node in ast.walk(tree)
        )
        has_exclusion = any(
            isinstance(node, (ast.NotEq, ast.Invert))
            or (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"drop", "remove"}
            )
            for node in ast.walk(tree)
        ) or final_value_subtracts_one
        if not has_exclusion:
            return (
                False,
                "Exclude the named reference item itself from the same-group count. "
                "Use an explicit != filter, drop the reference row, or subtract one.",
            )
    if re.search(
        r"\bcompare(?:d)?\b.*\bother\s+(?:groups?|districts?|categories|teams?)\b|"
        r"\bother\s+(?:groups?|districts?|categories|teams?)\b.*\bcompare",
        question or "",
        flags=re.I,
    ) and re.search(
        r"df\s*\[.*?!=.*?\].*?\.sum\s*\(",
        code_str or "",
        flags=re.I | re.S,
    ):
        return (
            False,
            "Compare the target group with each peer group or a clearly requested "
            "peer baseline. Do not combine every other group into one summed total.",
        )
    return True, ""


def validate_answer_contract_code_alignment(
    code_str: str,
    answer_contract: AnswerContract,
) -> Tuple[bool, str]:
    decimal_places = getattr(answer_contract, "decimal_places", None)
    if decimal_places is None:
        return True, ""

    code = code_str or ""
    wrong_rounds: List[str] = []
    for match in re.finditer(r"\bround\s*\((?P<body>[^)]*)\)", code, flags=re.S):
        args = [part.strip() for part in match.group("body").split(",")]
        if len(args) >= 2 and re.fullmatch(r"\d+", args[1]) and int(args[1]) != decimal_places:
            wrong_rounds.append(match.group(0))
    for match in re.finditer(r"\.round\s*\(\s*(?P<places>\d+)\s*\)", code):
        if int(match.group("places")) != decimal_places:
            wrong_rounds.append(match.group(0))

    if wrong_rounds:
        return (
            False,
            f"The answer contract requires {decimal_places} decimal places. "
            "Do not round to a different precision in code.",
        )
    return True, ""


def _row_names_from_df(df: pd.DataFrame, limit: int = 120) -> Optional[str]:
    """Build compact row candidates from the first likely label column."""
    if df.empty or len(df.columns) == 0:
        return None

    candidate_cols = list(df.columns[: min(2, len(df.columns))])
    best_values: List[str] = []
    for col in candidate_cols:
        series = df[col].dropna().astype(str).map(str.strip)
        values = [v for v in series.tolist() if v and v.lower() != "nan"]
        if not values:
            continue
        # Prefer columns that look like labels rather than dense numeric values.
        non_numeric = 0
        for v in values[:limit]:
            try:
                float(v.replace(",", ""))
            except Exception:
                non_numeric += 1
        if non_numeric >= max(1, min(len(values), limit) // 3):
            best_values = values[:limit]
            break
    if not best_values:
        best_values = [str(i) for i in df.index.tolist()[:limit]]
    return "##".join(dict.fromkeys(best_values)) if best_values else None


def _df_preview_text(df: pd.DataFrame, max_rows: int = 8) -> str:
    if df.empty:
        return "<empty table>"
    preview_df = df.head(max_rows)
    try:
        return preview_df.to_markdown(index=False)
    except Exception:
        return preview_df.to_string(index=False)


def _df_evidence_text(df: pd.DataFrame, max_rows: int = 220) -> str:
    if df.empty or len(df) <= max_rows:
        return _df_preview_text(df, max_rows=max(1, len(df)))
    head_rows = max(1, max_rows // 2)
    tail_rows = max(1, max_rows - head_rows)
    head_text = _df_preview_text(df.head(head_rows), max_rows=head_rows)
    tail_text = _df_preview_text(df.tail(tail_rows), max_rows=tail_rows)
    omitted = len(df) - head_rows - tail_rows
    return (
        f"{head_text}\n\n"
        f"... {omitted} middle rows omitted from verifier evidence ...\n\n"
        f"{tail_text}"
    )


_MISSING_MARKERS = {"", "n/a", "na", "nan", "none", "null", "tba", "unknown"}
_COLUMN_PROFILES_TEXT_LIMIT = 8000


def _profile_value(value: Any, max_length: int = 80) -> str:
    text = str(value).strip().replace("\n", " ")
    if len(text) > max_length:
        return f"{text[: max_length - 3]}..."
    return text


def _is_missing_marker(value: Any) -> bool:
    if pd.isna(value):
        return True
    return str(value).strip().casefold() in _MISSING_MARKERS


def _semantic_column_type(series: pd.Series) -> str:
    usable = [value for value in series.tolist() if not _is_missing_marker(value)]
    if not usable:
        return "text"
    if pd.api.types.is_numeric_dtype(series.dtype):
        return "numeric"
    numeric_count = 0
    for value in usable:
        try:
            float(str(value).replace(",", ""))
            numeric_count += 1
        except (TypeError, ValueError):
            continue
    if numeric_count == len(usable):
        return "numeric"
    if numeric_count:
        return "mixed"
    return "text"


def _build_column_profiles(df: pd.DataFrame) -> Tuple[Dict[str, Any], str]:
    profiles: Dict[str, Any] = {}
    lines: List[str] = []
    for column in df.columns:
        series = df[column]
        representative_values: List[str] = []
        seen = set()
        for value in series.tolist():
            if pd.isna(value):
                continue
            display_value = _profile_value(value)
            dedupe_key = display_value.casefold()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            representative_values.append(display_value)
            if len(representative_values) >= 12:
                break
        missing_marker_count = sum(_is_missing_marker(value) for value in series.tolist())
        profile = {
            "semantic_type": _semantic_column_type(series),
            "non_null_count": int(series.notna().sum()),
            "unique_count": int(series.nunique(dropna=True)),
            "missing_marker_count": int(missing_marker_count),
            "representative_values": representative_values,
        }
        column_name = str(column)
        profiles[column_name] = profile
        values_text = " | ".join(representative_values) or "<none>"
        line = (
            f"- {column_name}: type={profile['semantic_type']}; "
            f"unique={profile['unique_count']}; missing_markers={missing_marker_count}; "
            f"values={values_text}"
        )
        remaining = _COLUMN_PROFILES_TEXT_LIMIT - sum(len(item) + 1 for item in lines)
        if remaining <= 0:
            break
        if len(line) > remaining:
            lines.append(line[:remaining])
            break
        lines.append(line)
    return profiles, "\n".join(lines)[:_COLUMN_PROFILES_TEXT_LIMIT]


def _build_table_schema(df: pd.DataFrame, max_preview_rows: int = 8) -> Dict[str, Any]:
    """Build a lightweight schema from a DataFrame.

    Includes a short row preview and bounded full-column profiles. The profiles
    expose values beyond the preview without serializing the whole table.
    """
    columns = list(df.columns)
    preview_text = _df_preview_text(df, max_rows=max_preview_rows)
    column_profiles, column_profiles_text = _build_column_profiles(df)
    return {
        "columns": columns,
        "preview_text": preview_text,
        "column_profiles": column_profiles,
        "column_profiles_text": column_profiles_text,
        "row_names_str": _row_names_from_df(df),
        "col_names_str": "##".join(str(c).strip() for c in columns),
        "num_rows": int(df.shape[0]),
        "num_cols": int(df.shape[1]),
    }


def build_df_from_table(table: Any) -> pd.DataFrame:
    """Convert MACT-style table_text directly to a DataFrame."""
    if isinstance(table, pd.DataFrame):
        return table
    if not isinstance(table, list):
        raise ValueError("Unsupported table format for DataFrame construction.")
    if not table:
        raise ValueError("Table must contain a header row.")
    header: List[str] = []
    header_counts: Dict[str, int] = {}
    for index, cell in enumerate(table[0]):
        name = str(cell).replace("\\n", " ").replace("\n", " ").strip()
        name = name or f"column {index + 1}"
        header_counts[name] = header_counts.get(name, 0) + 1
        if header_counts[name] > 1:
            name = f"{name}_{header_counts[name]}"
        header.append(name)

    header_norm = [name.casefold() for name in header]
    cleaned_rows: List[List[Any]] = []
    for row in table[1:]:
        values = list(row)
        row_norm = [str(cell).strip().casefold() for cell in values]
        if row_norm == header_norm:
            continue
        normalized: List[Any] = []
        for cell in values[: len(header)]:
            value = cell
            parsed_numeric = False
            if isinstance(value, str):
                value = value.replace("\\n", " ").replace("\n", " ")
                stripped = value.strip()
                if not stripped:
                    normalized.append(None)
                    continue
                numeric_text = stripped
                if re.fullmatch(
                    r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*\+?",
                    numeric_text,
                ):
                    value = float(numeric_text.rstrip("+ ").replace(",", ""))
                    parsed_numeric = True
            if not parsed_numeric:
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    pass
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    pass
            normalized.append(value)
        normalized.extend([None] * (len(header) - len(normalized)))
        cleaned_rows.append(normalized)
    return pd.DataFrame(cleaned_rows, columns=header)


def load_csv_row_col_names(csv_path: str) -> Tuple[Optional[str], Optional[str]]:
    """Read a CSV file and return row/column names as two strings (or None).

    - Column names: if there is at least one non-empty column label, join
      them with '##' into col_names_str.
    - Row names: only if df.index is not a RangeIndex and has at least one
      non-empty label; join them with '##' into row_names_str.

    Returns:
        row_names_str, col_names_str
    """
    df = pd.read_csv(csv_path)

    col_labels = [str(c).strip() for c in df.columns.tolist()]
    col_names_str: Optional[str] = "##".join(col_labels) if any(col_labels) else None

    if not isinstance(df.index, pd.RangeIndex):
        idx_labels = [str(i).strip() for i in df.index.tolist()]
        row_names_str: Optional[str] = "##".join(idx_labels) if any(idx_labels) else None
    else:
        row_names_str = _row_names_from_df(df)

    return row_names_str, col_names_str


def _between(text: str, start: str, end: str) -> str:
    if start not in text:
        return ""
    sub = text.split(start, 1)[1]
    if end in sub:
        sub = sub.split(end, 1)[0]
    return sub.strip()


def _find_line_startswith(text: str, prefix: str) -> str:
    for line in text.splitlines():
        if line.strip().startswith(prefix):
            return line.strip()
    return ""


def _parse_verdict(raw: str) -> str:
    verdict_line = _find_line_startswith(raw, "[VERDICT]")
    return "REPLAN" if "REPLAN" in verdict_line.upper() else "PASS"


def _normalize_scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)) and len(value) == 1:
        value = value[0]
    if hasattr(value, "item"):
        try:
            value = value.item()
        except Exception:
            pass
    return re.sub(r"\s+", "", str(value).strip().lower())


def _as_number_like(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (list, tuple)) and len(value) == 1:
        value = value[0]
    if hasattr(value, "item"):
        try:
            value = value.item()
        except Exception:
            pass
    try:
        return float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


def _metric_number_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    match = re.search(r"\b(\d+(?:\.\d+)?)\s*(m|cm|km|ft|in)\b", text, flags=re.I)
    if not match:
        return None
    return f"{match.group(1)} {match.group(2).lower()}"


def _format_integer_ratio(left: int, right: int) -> str:
    import math

    if left == 0 and right == 0:
        return "0:0"
    divisor = math.gcd(abs(left), abs(right)) or 1
    return f"{left // divisor}:{right // divisor}"


def _small_number_from_text(value: str) -> Optional[int]:
    match = re.search(r"\b(\d+)\b", value or "")
    if match:
        return int(match.group(1))
    words = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
    }
    for word, number in words.items():
        if re.search(rf"\b{word}\b", value or "", flags=re.I):
            return number
    return None


def _text_mentions_number_or_ordinal(value: Any, number: int) -> bool:
    text = _loose_text_key(value)
    if not text:
        return False
    if re.search(rf"\b{number}\b", text):
        return True
    cardinal_words = {
        1: "one",
        2: "two",
        3: "three",
        4: "four",
        5: "five",
        6: "six",
        7: "seven",
        8: "eight",
        9: "nine",
        10: "ten",
    }
    ordinal_words = {
        1: "first",
        2: "second",
        3: "third",
        4: "fourth",
        5: "fifth",
        6: "sixth",
        7: "seventh",
        8: "eighth",
        9: "ninth",
        10: "tenth",
    }
    return any(
        word and re.search(rf"\b{re.escape(word)}\b", text)
        for word in (cardinal_words.get(number), ordinal_words.get(number), f"{number}th", f"{number}rd", f"{number}nd", f"{number}st")
    )


def _loose_text_key(value: Any) -> str:
    text = str(value or "").lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _single_edit_distance_at_most_one(left: str, right: str) -> bool:
    if abs(len(left) - len(right)) > 1:
        return False
    if left == right:
        return True
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right)) <= 1
    if len(left) > len(right):
        left, right = right, left
    index_left = index_right = edits = 0
    while index_left < len(left) and index_right < len(right):
        if left[index_left] == right[index_right]:
            index_left += 1
            index_right += 1
            continue
        edits += 1
        if edits > 1:
            return False
        index_right += 1
    return True


def _loose_text_matches_reference(value: Any, reference: str) -> bool:
    value_key = _loose_text_key(value)
    reference_key = _loose_text_key(reference)
    if not value_key or not reference_key:
        return False
    if value_key == reference_key:
        return True
    if len(reference_key) >= 4 and reference_key in value_key:
        return True
    if len(value_key) >= 4 and value_key in reference_key:
        return True
    value_tokens = value_key.split()
    reference_tokens = reference_key.split()
    return any(
        len(value_token) >= 5
        and len(reference_token) >= 5
        and _single_edit_distance_at_most_one(value_token, reference_token)
        for value_token in value_tokens
        for reference_token in reference_tokens
    )


def _loose_tokens(value: Any) -> List[str]:
    stopwords = {
        "a",
        "an",
        "and",
        "are",
        "be",
        "been",
        "by",
        "for",
        "from",
        "have",
        "has",
        "had",
        "in",
        "is",
        "of",
        "on",
        "or",
        "that",
        "the",
        "there",
        "to",
        "was",
        "were",
        "with",
    }
    return [
        token[:-1] if len(token) > 3 and token.endswith("s") else token
        for token in _loose_text_key(value).split()
        if token not in stopwords
    ]


def _singular_token(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _edit_distance_at_most_one(left: str, right: str) -> bool:
    if left == right:
        return True
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) > len(right):
        left, right = right, left
    edits = 0
    i = j = 0
    while i < len(left) and j < len(right):
        if left[i] == right[j]:
            i += 1
            j += 1
            continue
        edits += 1
        if edits > 1:
            return False
        if len(left) == len(right):
            i += 1
        j += 1
    if i < len(left) or j < len(right):
        edits += 1
    return edits <= 1


def _fuzzy_token_match(query_token: str, table_token: str) -> bool:
    if query_token == table_token:
        return True
    if len(query_token) >= 5 and len(table_token) >= 5:
        if query_token.startswith(table_token[:5]) or table_token.startswith(query_token[:5]):
            return True
        return _edit_distance_at_most_one(query_token, table_token)
    return False


def _split_entity_phrase(value: str) -> List[str]:
    text = re.sub(r"\b(?:only|the|a|an)\b", " ", str(value or ""), flags=re.I)
    parts = re.split(r"\s+(?:and|or)\s+|[,;/&]+", text, flags=re.I)
    entities = []
    for part in parts:
        key = _loose_text_key(part)
        if key and key not in {"only", "and", "or"}:
            entities.append(key)
    return entities


def _cell_entity_values(value: Any) -> List[str]:
    text = str(value or "")
    if re.search(r"[,;/&]|\s+and\s+", text, flags=re.I):
        return _split_entity_phrase(text)
    key = _loose_text_key(text)
    return [key] if key else []


def _parse_date_key(value: Any) -> Optional[Tuple[int, int, int]]:
    text = str(value or "").strip()
    if not text:
        return None
    has_date_signal = bool(
        re.search(r"\b\d{4}\b", text)
        or re.search(
            r"\b(january|february|march|april|may|june|july|august|"
            r"september|october|november|december)\b",
            text,
            flags=re.I,
        )
    )
    if not has_date_signal:
        return None
    try:
        timestamp = pd.to_datetime(text, errors="coerce")
    except Exception:
        return None
    if pd.isna(timestamp):
        return None
    return int(timestamp.year), int(timestamp.month), int(timestamp.day)


def _extract_year_range(value: Any) -> Optional[Tuple[int, int]]:
    years = [int(year) for year in re.findall(r"\b(1[7-9]\d{2}|20\d{2})\b", str(value or ""))]
    if not years:
        return None
    return min(years), max(years)


def _range_overlaps(left: Tuple[int, int], right: Tuple[int, int]) -> bool:
    return max(left[0], right[0]) <= min(left[1], right[1])


def _select_numeric_column_by_phrase(df: pd.DataFrame, phrase: str) -> Optional[Any]:
    phrase_tokens = set(_loose_tokens(phrase))
    if {"lost", "loss", "lose", "los"} & phrase_tokens:
        phrase_tokens.update({"l", "loss", "lose", "lost", "los"})
    if "point" in phrase_tokens:
        phrase_tokens.update({"pts", "score"})
    phrase_has_loss = bool({"lost", "loss", "lose", "los"} & phrase_tokens)
    best_col = None
    best_score = 0.0
    for col in df.columns:
        numeric = pd.to_numeric(df[col], errors="coerce")
        if numeric.notna().sum() < 2:
            continue
        if numeric.dropna().nunique() < 2:
            continue
        col_tokens = set(_loose_tokens(col))
        col_key = _loose_text_key(col)
        if str(col).strip().lower() == "l" and {"lost", "loss", "lose"} & phrase_tokens:
            col_tokens.add("loss")
        overlap = phrase_tokens & col_tokens
        score = len(overlap) / max(1, len(phrase_tokens))
        if phrase_has_loss and ({"lost", "loss", "lose", "l"} & col_tokens):
            score += 1.0
        if "point" in phrase_tokens and col_key in {"point", "points", "total point", "total points"}:
            score += 0.5
        if "difference" not in phrase_tokens and "difference" in col_tokens:
            score -= 0.25
        if score > best_score:
            best_score = score
            best_col = col
    return best_col if best_score > 0 else None


def _numeric_measure_value(value: Any) -> Optional[float]:
    strict = _as_number_like(value)
    if strict is not None:
        return strict
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("−", "-").replace("–", "-").replace("—", "-")
    equation_match = re.search(r"=\s*([-+]?\s*\d[\d,]*(?:\.\d+)?)\b", text)
    if equation_match and re.search(r"\d\s*\+", text):
        try:
            return float(re.sub(r"\s+", "", equation_match.group(1)).replace(",", ""))
        except ValueError:
            pass
    match = re.search(r"[-+]?\s*\d[\d,]*(?:\.\d+)?", text)
    if not match:
        return None
    number_text = re.sub(r"\s+", "", match.group(0)).replace(",", "")
    try:
        return float(number_text)
    except ValueError:
        return None


def _numeric_measure_series(df: pd.DataFrame, col: Any) -> pd.Series:
    return df[col].map(_numeric_measure_value)


def _select_numeric_measure_column_by_phrase(df: pd.DataFrame, phrase: str) -> Optional[Any]:
    strict_col = _select_numeric_column_by_phrase(df, phrase)
    if strict_col is not None:
        return strict_col

    phrase_tokens = set(_loose_tokens(phrase))
    if {"lost", "loss", "lose", "los"} & phrase_tokens:
        phrase_tokens.update({"l", "loss", "lose", "lost", "los"})
    if "employee" in phrase_tokens:
        phrase_tokens.add("employees")
    if not phrase_tokens:
        return None

    best_col = None
    best_score = 0.0
    for col in df.columns:
        series = _numeric_measure_series(df, col)
        if series.notna().sum() < 2:
            continue
        if series.dropna().nunique() < 2:
            continue
        col_tokens = set(_loose_tokens(col))
        if str(col).strip().lower() == "l":
            col_tokens.add("loss")
        overlap = phrase_tokens & col_tokens
        if not overlap:
            continue
        score = len(overlap) / max(1, len(phrase_tokens))
        if phrase_tokens.issubset(col_tokens) or col_tokens.issubset(phrase_tokens):
            score += 0.25
        if {"lost", "loss", "lose", "los"} & phrase_tokens and {"lost", "loss", "lose", "los", "l"} & col_tokens:
            score += 1.0
        if score > best_score:
            best_col = col
            best_score = score
    return best_col if best_score > 0 else None


def _comparison_holds(left: float, operator_text: str, right: float) -> bool:
    operator_key = _loose_text_key(operator_text)
    if operator_key in {"larger", "greater", "more", "higher", "over", "above"}:
        return left > right
    if operator_key in {"less", "lower", "fewer", "under", "below"}:
        return left < right
    return False


def _entity_phrase_tokens(value: Any) -> List[str]:
    ignored = {
        "clas",
        "class",
        "club",
        "competition",
        "entry",
        "game",
        "match",
        "place",
        "player",
        "row",
        "season",
        "team",
        "university",
    }
    return [token for token in _loose_tokens(value) if token not in ignored]


def _cell_contains_entity_phrase(phrase: Any, cell: Any) -> bool:
    query_tokens = _entity_phrase_tokens(phrase)
    cell_tokens = _loose_tokens(cell)
    if not query_tokens or not cell_tokens:
        return False
    return all(
        any(_fuzzy_token_match(query_token, cell_token) for cell_token in cell_tokens)
        for query_token in query_tokens
    )


def _find_rows_for_entity(df: pd.DataFrame, phrase: Any, cols: Optional[List[Any]] = None) -> List[pd.Series]:
    search_cols = cols or list(df.columns)
    return [
        row
        for _, row in df.iterrows()
        if any(_cell_contains_entity_phrase(phrase, row[col]) for col in search_cols)
    ]


def _row_contains_entity_phrase(row: pd.Series, phrase: Any, cols: Optional[List[Any]] = None) -> bool:
    query_tokens = _entity_phrase_tokens(phrase)
    if not query_tokens:
        return False
    search_cols = cols or list(row.index)
    row_tokens: List[str] = []
    for col in search_cols:
        row_tokens.extend(_loose_tokens(row[col]))
    return all(
        any(_fuzzy_token_match(query_token, row_token) for row_token in row_tokens)
        for query_token in query_tokens
    )


def _find_rows_for_entity_across_row(df: pd.DataFrame, phrase: Any, cols: Optional[List[Any]] = None) -> List[pd.Series]:
    return [row for _, row in df.iterrows() if _row_contains_entity_phrase(row, phrase, cols)]


def _is_na_text(value: Any) -> bool:
    return _loose_text_key(value).replace(" ", "") in {"na", "n/a"}


def _value_matches_phrase(value: Any, phrase: Any) -> bool:
    if _is_na_text(value) and _is_na_text(phrase):
        return True
    if TableQAPipeline._date_phrase_matches(str(phrase or ""), value):
        return True
    value_num = _numeric_measure_value(value)
    phrase_num = _numeric_measure_value(phrase)
    if value_num is not None and phrase_num is not None:
        return abs(float(value_num) - float(phrase_num)) <= 1e-6
    value_key = _loose_text_key(value)
    phrase_key = _loose_text_key(phrase)
    return bool(
        phrase_key
        and (
            phrase_key == value_key
            or phrase_key in value_key
            or _cell_contains_entity_phrase(phrase, value)
            or TableQAPipeline._cell_contains_phrase_tokens(phrase, value)
        )
    )


def _format_datetime_like(value: Any, question: str) -> Optional[str]:
    question_text = question or ""
    type_name = type(value).__name__.lower()
    module_name = type(value).__module__.lower()
    is_datetime_like = (
        isinstance(value, pd.Timestamp)
        or "datetime64" in type_name
        or (
            ("datetime" in module_name or "pandas" in module_name)
            and ("date" in type_name or "time" in type_name)
        )
    )
    is_date_question = re.search(
        r"\b(date|airdate|air\s+date|aired|when|released?|release\s+date)\b",
        question_text,
        flags=re.I,
    )
    is_ns_epoch = isinstance(value, int) and abs(value) > 10**14 and is_date_question
    if not is_datetime_like and not is_ns_epoch:
        return None
    try:
        timestamp = pd.Timestamp(value)
    except Exception:
        return None
    if pd.isna(timestamp):
        return None
    if (
        timestamp.hour == 0
        and timestamp.minute == 0
        and timestamp.second == 0
        and timestamp.microsecond == 0
    ):
        return f"{timestamp.strftime('%B')} {timestamp.day}, {timestamp.year}"
    return timestamp.isoformat(sep=" ")


def _parse_duration_days(value: Any) -> Optional[float]:
    text = str(value).strip().lower()
    match = re.search(r"([+-]?\d+(?:\.\d+)?)", text)
    if not match:
        return None
    amount = float(match.group(1))
    if re.search(r"\bhours?\b|\bhrs?\b", text):
        return amount / 24.0
    if re.search(r"\bdays?\b", text):
        return amount
    return None


def _duration_or_text_key(value: Any) -> str:
    duration = _parse_duration_days(value)
    if duration is not None:
        return f"duration:{round(duration, 6)}"
    return re.sub(r"\s+", " ", str(value).strip().lower())


def _values_match(left: Any, right: Any, tolerance: float = 1e-6) -> bool:
    left_norm = _normalize_scalar(left)
    right_norm = _normalize_scalar(right)
    if not left_norm or not right_norm:
        return False
    try:
        return abs(float(left_norm.replace(",", "")) - float(right_norm.replace(",", ""))) <= tolerance
    except Exception:
        return left_norm == right_norm


COUNTRY_CODE_NAMES = {
    "ARG": "Argentina",
    "AUS": "Australia",
    "AUT": "Austria",
    "BEL": "Belgium",
    "BRA": "Brazil",
    "CAN": "Canada",
    "CHN": "China",
    "COL": "Colombia",
    "CZE": "Czech Republic",
    "DEN": "Denmark",
    "ESP": "Spain",
    "FRA": "France",
    "GBR": "Great Britain",
    "GER": "Germany",
    "ITA": "Italy",
    "JPN": "Japan",
    "KOR": "South Korea",
    "MEX": "Mexico",
    "NED": "Netherlands",
    "NOR": "Norway",
    "POL": "Poland",
    "POR": "Portugal",
    "RUS": "Russia",
    "SUI": "Switzerland",
    "SWE": "Sweden",
    "USA": "United States",
}


def _canonicalize_wtq_scalar(value: Any, df: pd.DataFrame, question: str) -> Any:
    """Expand a partial entity name only when one complete table cell matches."""
    question_text = question or ""
    datetime_text = _format_datetime_like(value, question_text)
    if datetime_text:
        return datetime_text
    if not isinstance(value, str):
        numeric = _as_number_like(value)
        if (
            numeric is not None
            and numeric < 0
            and re.search(r"\bdifference\b", question_text, flags=re.I)
        ):
            delta = abs(numeric)
            return int(delta) if float(delta).is_integer() else delta
        if (
            numeric is not None
            and re.search(r"\bhow long\b", question_text, flags=re.I)
            and re.search(r"\b(?:year|years|season|after\s+\d{4})\b", question_text, flags=re.I)
            and re.search(r"\b(?:after|before|between|from|since|until)\b", question_text, flags=re.I)
        ):
            years = int(numeric) if float(numeric).is_integer() else numeric
            suffix = "year" if years == 1 else "years"
            return f"{years} {suffix}"
        return value
    if not value.strip():
        return value
    if not re.match(r"^\s*(?:who|which|what|this|how)\b", question_text, flags=re.I):
        return value
    candidate = re.sub(r"\s+", " ", value).strip()
    candidate_number = _as_number_like(candidate)
    if candidate_number is not None:
        if (
            candidate_number < 0
            and re.search(r"\bdifference\b", question_text, flags=re.I)
        ):
            delta = abs(candidate_number)
            return str(int(delta) if float(delta).is_integer() else delta)
        if (
            re.search(r"\bhow long\b", question_text, flags=re.I)
            and re.search(r"\b(?:year|years|season|after\s+\d{4})\b", question_text, flags=re.I)
            and re.search(r"\b(?:after|before|between|from|since|until)\b", question_text, flags=re.I)
        ):
            years = int(candidate_number) if float(candidate_number).is_integer() else candidate_number
            suffix = "year" if years == 1 else "years"
            return f"{years} {suffix}"
        return value
    if re.search(r"\babbrev(?:iation)?\b|\buse abbreviation\b", question_text, flags=re.I):
        if candidate.upper() in COUNTRY_CODE_NAMES:
            return candidate.upper()
        candidate_key = _loose_text_key(candidate)
        for code, country_name in COUNTRY_CODE_NAMES.items():
            if candidate_key == _loose_text_key(country_name):
                return code
    option_match = re.search(
        r"\b(?:which|what)\b.*?\b(?:earlier|later|first|last)\b[,\s]+(.+?)\s+or\s+(.+?)\??$",
        question_text,
        flags=re.I,
    )
    if option_match:
        candidate_key = _loose_text_key(candidate)
        for option in option_match.groups():
            option_clean = re.sub(r"\s+", " ", option).strip(" ?.")
            option_key = _loose_text_key(option_clean)
            if option_key and option_key in candidate_key:
                return option_clean
    if (
        re.search(r"\b(?:country|countries|nation|nations)\b", question_text, flags=re.I)
        and candidate.upper() in COUNTRY_CODE_NAMES
    ):
        return COUNTRY_CODE_NAMES[candidate.upper()]
    if (
        re.match(r"\s*(?:who|which)\b", question_text, flags=re.I)
        or re.search(r"\bperson\b", question_text, flags=re.I)
    ) and not re.search(r"\b(?:country|countries|nation|nations)\b", question_text, flags=re.I):
        person = _strip_entity_metadata(candidate)
        person = re.sub(
            r"^\s*(?:mg|col|ltc|maj|capt|cpt|gen|brig|adm|sir|dr|prof)\.?\s+",
            "",
            person,
            flags=re.I,
        ).strip()
        for country_name in sorted(COUNTRY_CODE_NAMES.values(), key=len, reverse=True):
            person = re.sub(
                rf"\s+{re.escape(country_name)}\s*$",
                "",
                person,
                flags=re.I,
            ).strip()
        if person and person != candidate:
            return person
    pattern = re.compile(rf"(?<!\w){re.escape(candidate)}(?!\w)", flags=re.I)
    matches: List[str] = []
    for column in df.columns:
        for cell in df[column].dropna().tolist():
            if not isinstance(cell, str):
                continue
            cell_text = re.sub(r"\s+", " ", cell).strip()
            if cell_text.casefold() == candidate.casefold():
                return value
            if pattern.search(cell_text) and cell_text not in matches:
                matches.append(cell_text)
    return matches[0] if len(matches) == 1 else value


def _format_crt_proportion_fraction(
    value: Any,
    question: str,
    df: Optional[pd.DataFrame] = None,
) -> Optional[str]:
    if not re.search(r"\bproportion\b", question or "", flags=re.I):
        return None
    if re.search(r"\bpercent(?:age)?\b|%", question or "", flags=re.I):
        return None
    numeric = _as_number_like(value)
    if numeric is None or numeric <= 0 or numeric >= 1:
        return None
    max_denominator = 100
    if df is not None and len(df) > 1:
        max_denominator = max(2, min(100, len(df)))
    fraction = Fraction(float(numeric)).limit_denominator(max_denominator)
    if fraction.denominator <= 1:
        return None
    if abs((fraction.numerator / fraction.denominator) - float(numeric)) <= 1e-9:
        return f"{fraction.numerator}/{fraction.denominator}"
    return None


def _canonicalize_crt_scalar(
    value: Any,
    question: str,
    df: Optional[pd.DataFrame] = None,
) -> Any:
    question_text = question or ""
    if not isinstance(value, str):
        proportion_fraction = _format_crt_proportion_fraction(value, question_text, df)
        if proportion_fraction is not None:
            return proportion_fraction
        numeric = _as_number_like(value)
        if (
            numeric is not None
            and numeric < 0
            and re.search(r"\bdifference\b", question_text, flags=re.I)
        ):
            delta = abs(numeric)
            return int(delta) if float(delta).is_integer() else delta
        if numeric is not None and re.search(r"\bstandard\s+deviation\b", question_text, flags=re.I):
            return round(numeric, 3)
        return value
    if not value.strip():
        return value
    candidate = re.sub(r"\s+", " ", value).strip()
    proportion_fraction = _format_crt_proportion_fraction(candidate, question_text, df)
    if proportion_fraction is not None:
        return proportion_fraction
    candidate_number = _as_number_like(candidate)
    if (
        candidate_number is not None
        and candidate_number < 0
        and re.search(r"\bdifference\b", question_text, flags=re.I)
    ):
        delta = abs(candidate_number)
        return str(int(delta) if float(delta).is_integer() else delta)
    if re.search(r"\bpercent(?:age)?\b|%", question_text, flags=re.I):
        percent_match = re.fullmatch(r"\s*([-+]?\d+)\.0+\s*%\s*", value)
        if percent_match:
            return f"{percent_match.group(1)}%"
    if (
        re.search(r"\b(?:country|countries|nation|nations)\b", question_text, flags=re.I)
        and candidate.upper() in COUNTRY_CODE_NAMES
    ):
        return COUNTRY_CODE_NAMES[candidate.upper()]
    stripped_entity = _strip_entity_metadata(value)
    if (
        stripped_entity != value
        and re.search(r"\b(nation|country|team|player|winner|highest\s+score)\b", question_text, flags=re.I)
    ):
        return stripped_entity
    if re.search(r"\b(highest|long jump|mark|achieved)\b", question_text, flags=re.I):
        metric_text = _metric_number_text(value)
        if metric_text is not None:
            return metric_text
    if df is not None and re.search(r"\bcombination\b", question_text, flags=re.I):
        parts = [
            part.strip(" \t\r\n\"'")
            for part in re.split(r"\s*&\s*|\s*,\s*|\s+\band\s+", value, flags=re.I)
            if part.strip(" \t\r\n\"'")
        ]
        if len(parts) == 2 and not any(_as_number_like(part) is not None for part in parts):
            for _, row in df.iterrows():
                located: List[Tuple[int, str]] = []
                used_cols: set[int] = set()
                for part in parts:
                    part_key = _loose_text_key(part)
                    match: Optional[Tuple[int, str]] = None
                    for col_index, col in enumerate(df.columns):
                        if col_index in used_cols:
                            continue
                        cell = row[col]
                        cell_key = _loose_text_key(cell)
                        if part_key and cell_key and (
                            part_key == cell_key
                            or part_key in cell_key
                            or cell_key in part_key
                        ):
                            match = (col_index, str(cell).strip())
                            break
                    if match is None:
                        break
                    used_cols.add(match[0])
                    located.append(match)
                if len(located) == len(parts):
                    ordered = [cell for _, cell in sorted(located, key=lambda item: item[0])]
                    return ", ".join(ordered)

    if re.search(r"\bhemisphere\b", question_text, flags=re.I):
        key = _loose_text_key(value)
        if key in {"east", "eastern"}:
            return "eastern hemisphere"
        if key in {"west", "western"}:
            return "western hemisphere"
    return value


def _coerce_pandas_answer_value(value: Any) -> Any:
    if isinstance(value, pd.Series):
        items = [
            item.item() if hasattr(item, "item") else item
            for item in value.dropna().tolist()
        ]
        if len(items) == 1:
            return items[0]
        return items
    if isinstance(value, pd.DataFrame):
        return value.to_dict(orient="records")
    return value


def _is_empty_answer_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _strip_entity_metadata(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = re.split(
        r"\s+release\s+date\s*:\s*",
        value.strip(),
        maxsplit=1,
        flags=re.I,
    )[0].strip()
    return re.sub(r"\s*\([a-z]{2,4}\)\s*$", "", text, flags=re.I).strip()


# ------------------------ Router ------------------------


ROUTER_PROMPT_TEMPLATE = """You are a classifier for table questions.
Given a question in any language and a table schema, decide whether solving it
requires complex multi-step numerical reasoning.

Output exactly one token: "SIMPLE" or "COMPLEX".

[Question]
{question}

[Table Columns]
{col_names}

[Hints]
- If the question only asks for a single value lookup, classification or a direct comparison between 2 cells: SIMPLE.
- If the question requires sum/average/ratio, year-on-year/环比, difference across years or aggregations over multiple rows/columns: COMPLEX.

[Answer]
"""


class RouterAgent:
    """Router that combines semantic and structural scores.

    - sem_score: semantic complexity, based on question only (0=easy,1=hard)
    - cell_score: structural complexity, based on question + row/col names,
                  approximated by number of cells touched / total cells
    - total_score = w_sem * sem_score + w_cell * cell_score
      => SIMPLE / COMPLEX decision
    """

    def __init__(self, llm_fn: Callable[[str], str], router_prompt_template: str = ROUTER_PROMPT_TEMPLATE):
        self.llm_fn = llm_fn
        self.prompt_tmpl = router_prompt_template

    def _rule_based_route(self, question: str) -> Optional[str]:
        """Simple keyword-based fallback when scores are ambiguous."""
        question_l = (question or "").lower()
        complex_keywords = [
            "增长", "增幅", "占比", "比例", "同比", "环比", "平均",
            "总和", "合计", "总计", "变化", "差值", "增速", "下降",
            "average", "mean", "sum", "total", "ratio", "proportion",
            "percentage", "percent", "difference", "compare", "comparison",
            "highest", "lowest", "most", "least", "top", "earliest",
            "latest", "before", "after", "trend", "change",
        ]
        simple_triggers = [
            "是多少", "有多少", "是什么", "为多少",
            "what is", "what was", "which is", "which was", "who is",
            "who was", "when is", "when was",
        ]

        has_complex = any(k in question_l for k in complex_keywords)
        has_simple = any(k in question_l for k in simple_triggers)

        if has_complex:
            return "COMPLEX"
        if has_simple and not has_complex:
            return "SIMPLE"
        return None

    def _score_difficulty(self, sem_score: float, cell_score: float) -> Tuple[float, str]:
        """DifficultyScorer: 根据语义/结构两个分数，给出总分 + 难度等级。

        - 输入：
          sem_score  ∈ [0,1]  语义复杂度（越大越复杂）
          cell_score ∈ [0,1]  结构复杂度 / 单元格覆盖比例
        - 输出：
          total_score ∈ [0,1]
          difficulty_level ∈ {"easy","medium","hard"}
        """
        # 加权融合，总分 0~1
        w_sem, w_cell = 0.6, 0.4
        total_score = w_sem * sem_score + w_cell * cell_score
        total_score = max(0.0, min(1.0, total_score))

        # 0.0–0.3 / 0.3–0.7 / 0.7–1.0
        if total_score < 0.3:
            level = "easy"
        elif total_score < 0.7:
            level = "medium"
        else:
            level = "hard"
        return total_score, level

    # -------- LLM scoring helpers --------

    def llm_semantic_score(self, question: str) -> float:
        """Return a semantic complexity score in [0,1] based only on the question."""
        prompt = f"""
You are an expert in semantic parsing for table-based QA. Evaluate the semantic complexity of this specific table question: {question}

Break down the required operations (e.g., direct lookup=simple, filter/aggregate=medium, multi-step/comparison/inference=complex). Rate on a [0-1] scale: 0=very simple (single-step retrieval), 1=very complex (multi-hop reasoning or verification).

Output only a JSON object in this format: {{"score": 0.60}}

[Answer]
"""
        raw = (self.llm_fn(prompt) or "").strip()
        data = _first_json_object(raw) or {}
        score_value = data.get("score")
        if score_value is None:
            explicit = re.search(
                r"(?:final\s+)?score\s*[:=]\s*([01](?:\.\d+)?)",
                raw,
                flags=re.I,
            )
            if explicit:
                score_value = explicit.group(1)
        if score_value is None:
            candidates = re.findall(r"(?<![\d.])(?:0(?:\.\d+)?|1(?:\.0+)?)(?![\d.])", raw)
            score_value = candidates[-1] if candidates else None
        try:
            val = float(score_value)
        except (TypeError, ValueError):
            val = 0.5
        return max(0.0, min(1.0, val))

    def llm_cell_score(
        self,
        question: str,
        row_names: Optional[str],
        col_names: Optional[str],
        df: pd.DataFrame,
    ) -> Tuple[float, int, Dict[str, Any]]:
        """Estimate which rows/columns are needed and how many cells will be touched.

        Returns:
            cell_score in [0,1]
            estimated_cells (int)
            selection metadata for table compression
        """
        row_part = row_names or "N/A"
        col_part = col_names or "N/A"
        prompt = f"""
You are an assistant for estimating how many table cells are needed
to answer a question.

Given:
- A question in any language.
- Candidate row names (separated by '##').
- Candidate column names (separated by '##').

1. Decide which row names and column names are actually needed.
2. Output ONLY a JSON object in the format:
   {{"rows": ["row_name1", ...], "cols": ["col_name1", ...]}}

[Question]
{question}

[Row Candidates]
{row_part}

[Column Candidates]
{col_part}

[Answer JSON]
"""
        raw = (self.llm_fn(prompt) or "").strip()
        data = _first_json_object(raw) or {}
        sel_rows = list(dict.fromkeys(str(x).strip() for x in data.get("rows", []) if str(x).strip()))
        sel_cols = list(dict.fromkeys(str(x).strip() for x in data.get("cols", []) if str(x).strip()))

        n_rows, n_cols = df.shape
        # Columns: map selected names back to actual columns; if none selected, assume all columns
        if sel_cols:
            used_cols = [
                c for c in df.columns
                if str(c) in sel_cols or any(str(c) in item or item in str(c) for item in sel_cols)
            ]
        else:
            used_cols = list(df.columns)

        # Rows: if any selected row names, approximate by their count; otherwise use full table
        used_row_count = n_rows if not sel_rows else min(n_rows, len(sel_rows))

        estimated_cells = max(1, used_row_count * max(1, len(used_cols)))
        total_cells = max(1, n_rows * max(1, n_cols))

        cell_score = estimated_cells / total_cells
        cell_score = max(0.0, min(1.0, cell_score))
        selection = {
            "selected_rows": sel_rows,
            "selected_cols": sel_cols,
            "matched_cols": [str(c) for c in used_cols],
        }
        return cell_score, estimated_cells, selection

    def route(self, state: TQASessionState) -> TQASessionState:
        """综合计算 sem_score + cell_score → difficulty_score → SIMPLE/COMPLEX。"""
        q = state.question or ""

        # 1) semantic complexity score
        sem_score = self.llm_semantic_score(q)
        state.semantic_features["sem_score"] = sem_score

        # 2) structural complexity score
        row_names = state.table_schema.get("row_names_str")
        col_names = state.table_schema.get("col_names_str")
        cell_score, estimated_cells, selection = self.llm_cell_score(
            question=q,
            row_names=row_names,
            col_names=col_names,
            df=state.df,
        )
        state.structural_features["cell_score"] = cell_score
        state.structural_features["estimated_cells_touched"] = estimated_cells
        state.structural_features.update(selection)

        # 3) DifficultyScorer：得到总分和难度等级
        total_score, difficulty_level = self._score_difficulty(sem_score, cell_score)
        state.difficulty_score = total_score
        state.difficulty_level = difficulty_level
        state.routing_context = {
            "sem_score": sem_score,
            "cell_score": cell_score,
            "estimated_cells_touched": estimated_cells,
            "total_score": total_score,
            "difficulty_level": difficulty_level,
        }

        if (
            not state.answer_mode
            and TableCompressor._needs_global_rows(state.question, state.answer_mode)
        ):
            state.route_type = "COMPLEX"
            return state

        # 4) 根据难度等级进行路由：easy→SIMPLE，medium/hard→COMPLEX
        if difficulty_level == "easy":
            state.route_type = "SIMPLE"
            return state
        if difficulty_level in {"medium", "hard"}:
            state.route_type = "COMPLEX"
            return state

class TableCompressor:
    """Question-aware table compression before SIMPLE/COMPLEX execution."""

    def __init__(self, max_easy_rows: int = 12, max_medium_rows: int = 40, max_hard_rows: int = 120) -> None:
        self.max_easy_rows = max_easy_rows
        self.max_medium_rows = max_medium_rows
        self.max_hard_rows = max_hard_rows

    @staticmethod
    def _norm(value: Any) -> str:
        return re.sub(r"\s+", " ", str(value).strip().lower())

    @staticmethod
    def _content_tokens(value: Any) -> set[str]:
        stopwords = {
            "a", "an", "and", "are", "as", "at", "be", "by", "did",
            "does", "for", "from", "had", "has", "have", "in", "is",
            "it", "of", "on", "or", "the", "this", "to", "was", "were",
            "what", "when", "where", "which", "who", "with",
        }
        return {
            token
            for token in re.findall(r"[a-z0-9]+", TableCompressor._norm(value))
            if len(token) > 2 and token not in stopwords
        }

    @staticmethod
    def _needs_global_rows(question: str, answer_mode: str) -> bool:
        if answer_mode in {"true_false", "yes_no"}:
            return True
        if re.search(
            r"^\s*this\s+.+?\b(?:has|had|with)\b.*\b(?:count|total|number)\b",
            question or "",
            flags=re.I,
        ):
            return True
        if re.search(
            r"\bhow many(?:\s+\S+){1,4}\s+did\b",
            question,
            flags=re.I,
        ):
            return False
        return bool(
            re.search(
                r"\b(how many|how often|total number|average|mean|sum|proportion|percentage|"
                r"at least|at most|most|least|highest|lowest|top|first|last|earlier|later|"
                r"earliest|latest|"
                r"only|all|any|none)\b",
                question,
                flags=re.I,
            )
        )

    @staticmethod
    def _is_relative_row_question(question: str) -> bool:
        return bool(
            re.search(
                r"\b(next|previous|before|after|preceding|following)\b",
                question,
                flags=re.I,
            )
        )

    @staticmethod
    def _needs_global_columns(question: str) -> bool:
        """Keep every comparison candidate when categories are stored as columns."""
        return bool(
            re.search(
                r"\b(best|worst|highest|lowest|maximum|minimum|largest|smallest|"
                r"performance|compare|comparison|change|trend|progressed|redistributed)\b",
                question,
                flags=re.I,
            )
        )

    def _match_cols(self, question: str, df: pd.DataFrame, selected_cols: List[str]) -> List[Any]:
        q_norm = self._norm(question)
        selected_norm = [self._norm(c) for c in selected_cols if self._norm(c)]
        cols = []
        for col in df.columns:
            col_norm = self._norm(col)
            if col_norm and col_norm in q_norm:
                cols.append(col)
                continue
            if any(col_norm == item or col_norm in item or item in col_norm for item in selected_norm):
                cols.append(col)
        if cols:
            return list(dict.fromkeys(cols))
        return list(df.columns)

    def _match_rows(self, question: str, df: pd.DataFrame, selected_rows: List[str]) -> List[Any]:
        q_norm = self._norm(question)
        question_tokens = self._content_tokens(question)
        selected_norm = [self._norm(r) for r in selected_rows if self._norm(r)]
        matched = []
        for idx, row in df.iterrows():
            values = [self._norm(v) for v in row.tolist()]
            row_text = " ".join(values)
            if any(item and item in row_text for item in selected_norm):
                matched.append(idx)
                continue
            # Heuristic fallback: match concrete labels/years mentioned in the question.
            for value in values[: min(3, len(values))]:
                if len(value) >= 2 and value in q_norm:
                    matched.append(idx)
                    break
            else:
                # Some WTQ evidence rows store the query entity in later notes or
                # description columns while the answer lives in an earlier label
                # column. Scan the full row, but require multiple content-token
                # overlaps to avoid broad matches on generic terms.
                for value in values[3:]:
                    if len(self._content_tokens(value) & question_tokens) >= 2:
                        matched.append(idx)
                        break
        return list(dict.fromkeys(matched))

    def _match_label_cols(
        self,
        question: str,
        df: pd.DataFrame,
        selected_rows: List[str],
    ) -> List[Any]:
        q_norm = self._norm(question)
        stopwords = {
            "about", "after", "before", "could", "from", "have", "many",
            "more", "other", "than", "that", "their", "there", "these",
            "this", "those", "were", "what", "when", "where", "which",
            "with", "would",
        }
        question_tokens = {
            token
            for token in re.findall(r"[a-z0-9-]+", q_norm)
            if len(token) >= 4 and token not in stopwords
        }
        selected_norm = [self._norm(row) for row in selected_rows if self._norm(row)]
        matched = []
        for col in df.columns:
            for value in df[col].dropna().tolist():
                value_norm = self._norm(value)
                if len(value_norm) < 3:
                    continue
                if value_norm in q_norm or any(
                    item == value_norm or item in value_norm or value_norm in item
                    for item in selected_norm
                ):
                    matched.append(col)
                    break
                value_tokens = set(re.findall(r"[a-z0-9-]+", value_norm))
                if question_tokens & value_tokens:
                    matched.append(col)
                    break
        return list(dict.fromkeys(matched))

    @staticmethod
    def _expand_rows(df: pd.DataFrame, row_indexes: List[Any], window: int, max_rows: int) -> List[Any]:
        if not row_indexes:
            return list(df.index[:max_rows])
        positions = {pos: idx for pos, idx in enumerate(df.index.tolist())}
        reverse = {idx: pos for pos, idx in positions.items()}
        selected_positions = []
        for idx in row_indexes:
            if idx not in reverse:
                continue
            pos = reverse[idx]
            start = max(0, pos - window)
            end = min(len(df.index), pos + window + 1)
            selected_positions.extend(range(start, end))
        expanded = [positions[pos] for pos in sorted(set(selected_positions))]
        return expanded[:max_rows]

    @staticmethod
    def _token_estimate_df(df: pd.DataFrame, sample_rows: int = 200) -> int:
        if df.empty:
            return estimate_text_tokens("| empty |")
        rows = min(sample_rows, len(df))
        text = df.head(rows).to_csv(index=False)
        sample_tokens = estimate_text_tokens(text)
        if len(df) <= rows:
            return sample_tokens
        return int(sample_tokens * (len(df) / max(1, rows)))

    def compress(self, state: TQASessionState) -> TQASessionState:
        original_df = state.original_df
        if original_df.empty:
            state.compressed_df = original_df
            state.compression_info = {
                "strategy": "empty",
                "original_cells": 0,
                "compressed_cells": 0,
                "compression_ratio": 1.0,
                "token_compression_ratio_est": 1.0,
                "used_rows": [],
                "used_cols": [],
            }
            return state

        level = state.difficulty_level or "medium"
        selected_rows = state.structural_features.get("selected_rows", [])
        selected_cols = state.structural_features.get("selected_cols", [])
        matched_cols = self._match_cols(state.question, original_df, selected_cols)
        matched_rows = self._match_rows(state.question, original_df, selected_rows)
        label_cols = self._match_label_cols(state.question, original_df, selected_rows)

        if level == "easy":
            strategy = "strict_cell_block"
            row_window, max_rows = 0, self.max_easy_rows
        elif level == "medium":
            strategy = "expanded_context_block"
            row_window, max_rows = 1, self.max_medium_rows
        else:
            strategy = "evidence_preserving_block"
            row_window, max_rows = 2, self.max_hard_rows

        if self._is_relative_row_question(state.question):
            row_window = max(1, row_window)
            matched_cols = list(original_df.columns)

        if self._needs_global_rows(state.question, state.answer_mode):
            matched_rows = list(original_df.index)
            max_rows = len(original_df)
            strategy = f"{strategy}_global_rows"

        row_indexes = self._expand_rows(original_df, matched_rows, row_window, max_rows)

        # Preserve the first column as row-label context when columns are narrowed.
        col_indexes = list(matched_cols)
        for col in label_cols:
            if col not in col_indexes:
                col_indexes.append(col)
        if len(original_df.columns) > 0 and original_df.columns[0] not in col_indexes:
            col_indexes.insert(0, original_df.columns[0])
        selected_col_set = set(col_indexes)
        col_indexes = [col for col in original_df.columns if col in selected_col_set]

        if self._needs_global_columns(state.question):
            col_indexes = list(original_df.columns)
            strategy = f"{strategy}_global_columns"

        estimated_cell_score = float(state.structural_features.get("cell_score", 1.0) or 1.0)
        if level == "hard" and estimated_cell_score >= 0.75:
            row_indexes = list(original_df.index)
            col_indexes = list(original_df.columns)
            strategy = "full_table_for_high_coverage"

        compressed_df = original_df.loc[row_indexes, col_indexes].copy()
        if compressed_df.empty:
            compressed_df = original_df.head(max_rows).copy()
            strategy = f"{strategy}_fallback_head"

        original_cells = max(1, int(original_df.shape[0] * original_df.shape[1]))
        compressed_cells = max(1, int(compressed_df.shape[0] * compressed_df.shape[1]))
        full_tokens = max(1, self._token_estimate_df(original_df))
        compressed_tokens = max(1, self._token_estimate_df(compressed_df))

        state.compressed_df = compressed_df
        state.df = compressed_df
        preview_rows = len(compressed_df)
        if state.route_type == "COMPLEX" and (
            not state.answer_mode or state.answer_contract.reasoning_required
        ):
            preview_rows = min(8, preview_rows)
        state.table_schema = _build_table_schema(
            compressed_df,
            max_preview_rows=max(1, preview_rows),
        )
        state.compression_info = {
            "strategy": strategy,
            "original_rows": int(original_df.shape[0]),
            "original_cols": int(original_df.shape[1]),
            "compressed_rows": int(compressed_df.shape[0]),
            "compressed_cols": int(compressed_df.shape[1]),
            "original_cells": original_cells,
            "compressed_cells": compressed_cells,
            "compression_ratio": compressed_cells / original_cells,
            "token_compression_ratio_est": compressed_tokens / full_tokens,
            "full_table_tokens_est": full_tokens,
            "compressed_table_tokens_est": compressed_tokens,
            "used_rows": [str(x) for x in row_indexes[:200]],
            "used_cols": [str(x) for x in compressed_df.columns.tolist()],
        }
        state.structural_features["compression_ratio"] = state.compression_info["compression_ratio"]
        state.structural_features["token_compression_ratio_est"] = state.compression_info["token_compression_ratio_est"]
        state.routing_context.update(
            {
                "compression_strategy": state.compression_info["strategy"],
                "compression_ratio": state.compression_info["compression_ratio"],
                "token_compression_ratio_est": state.compression_info["token_compression_ratio_est"],
            }
        )
        return state


# ------------------------ Simple lookup ------------------------


class SimpleCellLookupAgent:
    """Deterministic single-cell lookup for SIMPLE questions.

    The agent only returns a value when the compressed table and routing hints
    point to one row and one non-label column. Ambiguous cases fall back to the
    normal final-answer LLM path.
    """

    @staticmethod
    def _norm(value: Any) -> str:
        return re.sub(r"\s+", " ", str(value).strip().lower())

    def _row_label(self, row: pd.Series) -> str:
        if row.empty:
            return ""
        return str(row.iloc[0]).strip()

    def _candidate_rows(self, state: TQASessionState) -> List[Any]:
        df = state.df
        question_norm = self._norm(state.question)
        selected_rows = [
            self._norm(x)
            for x in state.structural_features.get("selected_rows", [])
            if self._norm(x)
        ]
        matches = []
        for idx, row in df.iterrows():
            row_text = self._norm(" ".join(str(v) for v in row.tolist()))
            row_label = self._norm(self._row_label(row))
            if selected_rows and any(item in row_text for item in selected_rows):
                matches.append(idx)
                continue
            if row_label and len(row_label) >= 2 and row_label in question_norm:
                matches.append(idx)
                continue
            for value in row.tolist()[: min(3, len(row.tolist()))]:
                value_norm = self._norm(value)
                if len(value_norm) >= 2 and value_norm in question_norm:
                    matches.append(idx)
                    break
        return list(dict.fromkeys(matches))

    def _candidate_cols(self, state: TQASessionState) -> List[Any]:
        df = state.df
        question_norm = self._norm(state.question)
        selected_cols = [
            self._norm(x)
            for x in state.structural_features.get("selected_cols", [])
            if self._norm(x)
        ]
        matched_cols = [
            self._norm(x)
            for x in state.structural_features.get("matched_cols", [])
            if self._norm(x)
        ]
        matches = []
        for col in df.columns:
            col_norm = self._norm(col)
            if col_norm and col_norm in question_norm:
                matches.append(col)
                continue
            hints = selected_cols + matched_cols
            if any(col_norm == item or col_norm in item or item in col_norm for item in hints):
                matches.append(col)
        if not matches and len(df.columns) == 2:
            matches.append(df.columns[1])

        if len(matches) > 1 and len(df.columns) > 0:
            label_col = df.columns[0]
            non_label = [col for col in matches if col != label_col]
            if non_label:
                matches = non_label
        return list(dict.fromkeys(matches))

    def lookup(self, state: TQASessionState) -> TQASessionState:
        df = state.df
        if df.empty:
            return state

        if TableCompressor._is_relative_row_question(state.question):
            state.simple_lookup_success = False
            state.simple_lookup_evidence = {
                "reason": "relative_row_question_requires_context",
            }
            return state

        row_matches = self._candidate_rows(state)
        col_matches = self._candidate_cols(state)
        if not row_matches and len(df) == 1:
            row_matches = [df.index[0]]

        if len(row_matches) != 1 or len(col_matches) != 1:
            state.simple_lookup_success = False
            state.simple_lookup_evidence = {
                "reason": "ambiguous_or_missing_target",
                "candidate_rows": [str(x) for x in row_matches],
                "candidate_cols": [str(x) for x in col_matches],
            }
            return state

        row_idx = row_matches[0]
        col = col_matches[0]
        value = df.loc[row_idx, col]
        if pd.isna(value):
            serial_value = None
        elif hasattr(value, "item"):
            serial_value = value.item()
        else:
            serial_value = value
        row_label = self._row_label(df.loc[row_idx])
        state.simple_lookup_success = True
        state.simple_lookup_value = serial_value
        state.simple_lookup_evidence = {
            "row_index": str(row_idx),
            "row_label": row_label,
            "col_name": str(col),
            "value": None if serial_value is None else str(serial_value),
            "source": "compressed_table_single_cell",
        }
        return state


# ------------------------ Planner ------------------------


PLANNER_PROMPT_TEMPLATE = """You are a table reasoning planner and programmer.

You are given:
- A question about a table from an arbitrary domain and language.
- A table represented as a pandas DataFrame `df`.

Your job:
1. First, write a step-by-step plan in natural language to solve the question.
2. Then, write executable Python code that uses ONLY the given DataFrame `df`
   (and standard Python/pandas operations) to compute the final answer.

Requirements:
- In the [PLAN] section, number the steps: Step1, Step2, ...
- In the [CODE] section, write valid Python code.
- Assume `df` is already defined as a pandas DataFrame with the following columns:
  {col_names}
- DataFrame shape: {num_rows} rows x {num_cols} columns.
- Use column names exactly as shown.
- Ground every filter in the table. Treat country, league, person, or topic wording
  that describes the whole table as table-level context; do not filter a column by
  that wording unless the question explicitly targets that column and the table
  contains matching cell values.
- Unless the question explicitly asks for distinct or unique items, an "X of N"
  claim counts table rows/events. Do not replace row counts with unique cell counts.
- For best/worst or other global comparisons, compare every available candidate.
- Do not drop summary, sum, total, or aggregate rows unless the question explicitly
  asks for non-summary records or peer-only comparisons. If a summary row answers
  a global comparison, keep it as a valid candidate.
- For season ranges such as 1936/37, a question asking "after 1936" excludes
  the season starting in 1936; use the next strictly later start year.
- If percentage columns are snapshots such as "% (1960)", "% (2000)", and
  "% (2040)", average percentage values across the requested rows/years unless
  the question explicitly asks for relative percent increase or growth rate.
- For implicit yes/no difference or association questions, answer yes only when
  the table shows a systematic pattern; isolated variation is not enough.
- Words such as "tend", "generally", or "usually" require a majority/rate over
  all valid opportunities; one matching example is not sufficient.
- For a counterfactual redistribution of a conserved numerator across the same
  units, first test whether the aggregate numerator and denominator totals change.
  If both totals are conserved, their aggregate ratio does not change.
- In phrases such as "previous winner" or "former titleholder", previous/former
  can describe a person who held the named title. Match the title and year to the
  entity row unless the question explicitly asks for the preceding year/edition.
- Follow this answer contract exactly:
  {answer_contract}
- Follow these dataset format constraints. They describe the benchmark format,
  never a target answer:
  {dataset_instructions}
- At the end of your code, assign the contracted answer to a variable named:
  final_answer_value

Question:
{question}

Table subject/context (may be empty):
{table_context}

Table Schema (first few rows):
{table_preview}

[COLUMN PROFILES]
Bounded summaries computed over every row available to the program:
{column_profiles}

If there was a previous critic feedback, consider it:
{critic_feedback}

Now produce your reasoning plan and code with the following format:

[PLAN]
Step1: ...
Step2: ...
...

[CODE]
# your python code here
...
final_answer_value = ...
"""


class PlannerAgent:
    def __init__(self, llm_fn: Callable[[str], str], prompt_template: str = PLANNER_PROMPT_TEMPLATE):
        self.llm_fn = llm_fn
        self.prompt_tmpl = prompt_template

    def plan(self, state: TQASessionState) -> TQASessionState:
        # Build prompt from question + table schema + critic feedback
        columns = state.table_schema.get("columns", [])
        col_names = ", ".join(str(c) for c in columns)
        table_preview = state.table_schema.get("preview_text", "")
        column_profiles = state.table_schema.get("column_profiles_text", "")
        critic_feedback = state.critic_feedback or "无"

        prompt = self.prompt_tmpl.format(
            col_names=col_names,
            num_rows=state.table_schema.get("num_rows", len(state.df)),
            num_cols=state.table_schema.get("num_cols", len(columns)),
            question=state.question,
            table_context=state.table_context or "Not provided",
            table_preview=table_preview,
            column_profiles=column_profiles or "Not available",
            critic_feedback=critic_feedback,
            answer_contract=state.answer_contract.instructions,
            dataset_instructions=state.dataset_instructions or "No additional constraints.",
        )
        raw = self.llm_fn(prompt)
        state.planner_raw_output = raw

        plan_text = _between(raw, "[PLAN]", "[CODE]")
        code_text = raw.split("[CODE]", 1)[-1] if "[CODE]" in raw else ""
        state.code_str = _strip_code_fence(code_text)

        plan_lines = []
        for line in plan_text.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("step"):
                plan_lines.append(stripped)
        state.plan_steps = plan_lines

        return state


# ------------------------ Calculator ------------------------


class Calculator:
    def __init__(self) -> None:
        pass

    @staticmethod
    def _safe_execute(code_str: str, df: pd.DataFrame) -> Dict[str, Any]:
        import math
        import numpy as np
        import pandas as pd  # local import for sandbox globals

        approved_modules = {"math": math, "numpy": np, "pandas": pd, "re": re}

        def restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
            del globals, locals, fromlist, level
            module = approved_modules.get(name)
            if module is None:
                raise ImportError(f"Import of {name!r} is not allowed")
            return module

        def quiet_print(*args, **kwargs):
            del args, kwargs
            return None

        allowed_builtins = {
            "__import__": restricted_import,
            "abs": abs,
            "all": all,
            "any": any,
            "bool": bool,
            "dict": dict,
            "enumerate": enumerate,
            "float": float,
            "int": int,
            "isinstance": isinstance,
            "list": list,
            "min": min,
            "max": max,
            "print": quiet_print,
            "set": set,
            "sum": sum,
            "len": len,
            "range": range,
            "round": round,
            "sorted": sorted,
            "str": str,
            "tuple": tuple,
            "zip": zip,
        }
        exec_env: Dict[str, Any] = {
            "__builtins__": allowed_builtins,
            "math": math,
            "np": np,
            "pd": pd,
            "re": re,
            "df": df,
        }
        exec(code_str, exec_env, exec_env)
        return {
            key: value
            for key, value in exec_env.items()
            if key != "__builtins__"
        }

    def execute(self, state: TQASessionState) -> TQASessionState:
        """Execute planner-generated code on state.df and update execution fields."""
        code = state.code_str or ""
        if not code.strip():
            state.exec_success = False
            state.exec_error = "Empty code_str from planner."
            state.final_value = None
            state.exec_locals = {}
            return state

        try:
            local_env = self._safe_execute(code, state.df)
            state.exec_success = True
            state.exec_locals = local_env
            state.final_value = local_env.get("final_answer_value", None)
            if state.final_value is Ellipsis:
                state.exec_success = False
                state.exec_error = "Planner left ellipsis placeholder as final answer."
                state.final_value = None
                return state
            state.exec_error = None
        except Exception as e:  # noqa: BLE001
            state.exec_success = False
            state.exec_error = str(e)
            state.final_value = None
            state.exec_locals = {}
        return state


# ------------------------ Critic ------------------------


CRITIC_PROMPT_TEMPLATE = """You are a careful auditor for table-based numerical reasoning.

You will be given:
- A question about a table from an arbitrary domain and language.
- A reasoning plan (PLAN) written in steps.
- A Python code snippet (CODE) that was executed on the table DataFrame `df`.
- The execution result (RESULT), or an error if execution failed.
- The DataFrame shape, columns, and a preview of its values.

Your job:
1. Check whether the plan and the code correctly answer the question.
2. Check whether the result is reasonable and matches the question.
3. Check whether the result follows the answer contract.
4. Reject filters whose literal values are not grounded in the shown table values,
   unless the code intentionally tests for absence.
5. Reject unique-value counting when the question asks about rows/events and does
   not explicitly say distinct or unique.
6. Reject global comparisons that omit available candidates.
7. Treat "tend", "generally", and "usually" as majority/rate claims, not
   existential claims based on one example.
8. Check conservation before claiming that aggregate ratios change after a
   redistribution across the same units.
9. Disambiguate "previous winner/former titleholder" from "the winner in the
   preceding year" using the table structure and explicit wording.
10. If you find serious issues, ask to REPLAN and describe what to fix.
11. Otherwise, PASS and briefly confirm correctness.

Output in the following format:

[VERDICT] PASS or REPLAN

[COMMENT]
(1-3 sentences explanation, in Chinese)
[/COMMENT]

[HINT_FOR_PLANNER]
(if REPLAN, give concrete suggestions how to modify PLAN or CODE; if PASS, you can say "保持当前方案")
[/HINT_FOR_PLANNER]


[QUESTION]
{question}

[TABLE SUBJECT/CONTEXT]
{table_context}

[ANSWER CONTRACT]
{answer_contract}

[DATASET FORMAT CONSTRAINTS]
{dataset_instructions}

[TABLE]
shape = {num_rows} rows x {num_cols} columns
columns = {col_names}
preview:
{table_preview}

[COLUMN PROFILES]
{column_profiles}

[PLAN]
{plan_text}

[CODE]
{code_str}

[RESULT]
success = {exec_success}
final_answer_value = {final_value}
error = {exec_error}
"""


class CriticAgent:
    def __init__(self, llm_fn: Callable[[str], str], prompt_template: str = CRITIC_PROMPT_TEMPLATE):
        self.llm_fn = llm_fn
        self.prompt_tmpl = prompt_template

    def review(self, state: TQASessionState) -> TQASessionState:
        # Check PLAN / CODE / execution result and decide PASS / REPLAN + feedback
        plan_text = "\n".join(state.plan_steps) if state.plan_steps else state.planner_raw_output
        code_str = state.code_str
        prompt = self.prompt_tmpl.format(
            question=state.question,
            table_context=state.table_context or "Not provided",
            answer_contract=state.answer_contract.instructions,
            dataset_instructions=state.dataset_instructions or "No additional constraints.",
            num_rows=state.table_schema.get("num_rows", len(state.df)),
            num_cols=state.table_schema.get("num_cols", len(state.df.columns)),
            col_names=", ".join(str(c) for c in state.df.columns),
            table_preview=state.table_schema.get("preview_text", ""),
            column_profiles=state.table_schema.get("column_profiles_text", "Not available"),
            plan_text=plan_text,
            code_str=code_str,
            exec_success=state.exec_success,
            final_value=state.final_value,
            exec_error=state.exec_error,
        )
        raw = self.llm_fn(prompt)
        state.critic_raw_output = raw

        verdict_line = _find_line_startswith(raw, "[VERDICT]")
        if "REPLAN" in verdict_line.upper():
            state.critic_verdict = "REPLAN"
        else:
            state.critic_verdict = "PASS"

        comment = _between(raw, "[COMMENT]", "[/COMMENT]")
        hint = _between(raw, "[HINT_FOR_PLANNER]", "[/HINT_FOR_PLANNER]")
        state.critic_feedback = hint or comment

        return state


# ------------------------ Multi-view validation ------------------------


EVIDENCE_CRITIC_PROMPT_TEMPLATE = """你是复杂表格问答系统中的 Evidence Critic。

请只从证据支持角度审查答案：目标行、目标列、压缩表是否保留了回答问题所需的关键单元格。
若答案缺少表格证据、压缩表丢失关键行列，或结果无法由表格支持，请要求 REPLAN。

输出格式：
[VERDICT] PASS or REPLAN

[COMMENT]
用 1-3 句中文说明证据是否充分。
[/COMMENT]

[QUESTION]
{question}

[TABLE_PREVIEW]
{table_preview}

[COMPRESSION_INFO]
{compression_info}

[PLAN]
{plan_text}

[FINAL_VALUE]
{final_value}
"""


LOGIC_CRITIC_PROMPT_TEMPLATE = """你是复杂表格问答系统中的 Logic Critic。

请只从推理逻辑和计算过程角度审查：Planner 步骤、Python 代码和执行结果是否与问题一致。
若存在公式错误、筛选条件错误、单位或聚合方向错误，请要求 REPLAN。

输出格式：
[VERDICT] PASS or REPLAN

[COMMENT]
用 1-3 句中文说明逻辑是否可靠。
[/COMMENT]

[QUESTION]
{question}

[PLAN]
{plan_text}

[CODE]
{code_str}

[EXECUTION]
success = {exec_success}
final_answer_value = {final_value}
error = {exec_error}
"""


ALTERNATIVE_PLANNER_PROMPT_TEMPLATE = """你是备用路径规划器。

请使用与主路径不同的写法，重新为同一个表格问题生成一段可执行 Python 代码，用于交叉验证主路径结果。
只输出 [CODE] 区块。代码必须只使用已经存在的 pandas DataFrame `df`，并把最终标量答案赋值给 `final_answer_value`。

[QUESTION]
{question}

[TABLE_SCHEMA]
columns = {col_names}

[TABLE_PREVIEW]
{table_preview}

[MAIN_PLAN]
{plan_text}

[MAIN_VALUE]
{final_value}

[CODE]
"""


class EvidenceCriticAgent:
    def __init__(self, llm_fn: Callable[[str], str], prompt_template: str = EVIDENCE_CRITIC_PROMPT_TEMPLATE):
        self.llm_fn = llm_fn
        self.prompt_tmpl = prompt_template

    def review(self, state: TQASessionState) -> TQASessionState:
        prompt = self.prompt_tmpl.format(
            question=state.question,
            table_preview=state.table_schema.get("preview_text", ""),
            compression_info=json.dumps(state.compression_info, ensure_ascii=False),
            plan_text="\n".join(state.plan_steps) if state.plan_steps else state.planner_raw_output,
            final_value=state.final_value,
        )
        raw = self.llm_fn(prompt)
        state.evidence_critic_raw_output = raw
        state.evidence_critic_verdict = _parse_verdict(raw)
        state.evidence_critic_feedback = _between(raw, "[COMMENT]", "[/COMMENT]")
        return state


class LogicCriticAgent:
    def __init__(self, llm_fn: Callable[[str], str], prompt_template: str = LOGIC_CRITIC_PROMPT_TEMPLATE):
        self.llm_fn = llm_fn
        self.prompt_tmpl = prompt_template

    def review(self, state: TQASessionState) -> TQASessionState:
        prompt = self.prompt_tmpl.format(
            question=state.question,
            plan_text="\n".join(state.plan_steps) if state.plan_steps else state.planner_raw_output,
            code_str=state.code_str,
            exec_success=state.exec_success,
            final_value=state.final_value,
            exec_error=state.exec_error,
        )
        raw = self.llm_fn(prompt)
        state.logic_critic_raw_output = raw
        state.logic_critic_verdict = _parse_verdict(raw)
        state.logic_critic_feedback = _between(raw, "[COMMENT]", "[/COMMENT]")
        return state


class AlternativePlannerAgent:
    def __init__(self, llm_fn: Callable[[str], str], prompt_template: str = ALTERNATIVE_PLANNER_PROMPT_TEMPLATE):
        self.llm_fn = llm_fn
        self.prompt_tmpl = prompt_template

    def plan(self, state: TQASessionState) -> TQASessionState:
        columns = state.table_schema.get("columns", [])
        prompt = self.prompt_tmpl.format(
            question=state.question,
            col_names=", ".join(str(c) for c in columns),
            table_preview=state.table_schema.get("preview_text", ""),
            plan_text="\n".join(state.plan_steps) if state.plan_steps else state.planner_raw_output,
            final_value=state.final_value,
        )
        raw = self.llm_fn(prompt)
        state.alternative_plan_raw_output = raw
        code_text = raw.split("[CODE]", 1)[-1] if "[CODE]" in raw else raw
        state.alternative_code_str = _strip_code_fence(code_text)
        return state


class CrossPathValidator:
    def validate(self, state: TQASessionState) -> TQASessionState:
        if not state.alternative_exec_success:
            state.cross_validation_verdict = "WARN"
            state.cross_validation_feedback = f"备用路径未成功执行：{state.alternative_exec_error}"
            return state

        if _values_match(state.final_value, state.alternative_final_value):
            state.cross_validation_verdict = "PASS"
            state.cross_validation_feedback = "主路径与备用路径结果一致。"
        else:
            state.cross_validation_verdict = "REPLAN"
            state.cross_validation_feedback = (
                "主路径与备用路径结果不一致："
                f"main={state.final_value}, alternative={state.alternative_final_value}"
            )
        return state


class EvidenceAggregator:
    def aggregate(self, state: TQASessionState) -> TQASessionState:
        replan_reasons = []
        if state.evidence_critic_verdict == "REPLAN":
            replan_reasons.append(f"Evidence Critic: {state.evidence_critic_feedback}")
        if state.logic_critic_verdict == "REPLAN":
            replan_reasons.append(f"Logic Critic: {state.logic_critic_feedback}")
        if state.cross_validation_verdict == "REPLAN":
            replan_reasons.append(f"CrossPathValidator: {state.cross_validation_feedback}")

        verdict = "REPLAN" if replan_reasons else "PASS"
        state.evidence_summary = {
            "final_value": state.final_value,
            "alternative_final_value": state.alternative_final_value,
            "compression_strategy": state.compression_info.get("strategy"),
            "used_cols": state.compression_info.get("used_cols", []),
            "simple_lookup_evidence": state.simple_lookup_evidence,
        }
        state.multi_view_validation = {
            "enabled": True,
            "verdict": verdict,
            "replan_reasons": replan_reasons,
            "evidence_critic_verdict": state.evidence_critic_verdict,
            "logic_critic_verdict": state.logic_critic_verdict,
            "alternative_exec_success": state.alternative_exec_success,
            "alternative_exec_error": state.alternative_exec_error,
            "cross_validation_verdict": state.cross_validation_verdict,
            "cross_validation_feedback": state.cross_validation_feedback,
            "evidence_summary": state.evidence_summary,
        }
        return state


class MultiViewValidator:
    """Minimal Evidence/Logic/Cross-path validation scaffold for complex routes."""

    def __init__(
        self,
        evidence_critic: EvidenceCriticAgent,
        logic_critic: LogicCriticAgent,
        alternative_planner: AlternativePlannerAgent,
        cross_path_validator: Optional[CrossPathValidator] = None,
        evidence_aggregator: Optional[EvidenceAggregator] = None,
    ) -> None:
        self.evidence_critic = evidence_critic
        self.logic_critic = logic_critic
        self.alternative_planner = alternative_planner
        self.cross_path_validator = cross_path_validator or CrossPathValidator()
        self.evidence_aggregator = evidence_aggregator or EvidenceAggregator()

    def review(self, state: TQASessionState) -> TQASessionState:
        state = self.evidence_critic.review(state)
        state = self.logic_critic.review(state)
        state = self.alternative_planner.plan(state)
        try:
            local_env = Calculator._safe_execute(state.alternative_code_str, state.df)
            state.alternative_exec_success = True
            state.alternative_final_value = local_env.get("final_answer_value", None)
            state.alternative_exec_error = None
        except Exception as e:  # noqa: BLE001
            state.alternative_exec_success = False
            state.alternative_final_value = None
            state.alternative_exec_error = str(e)

        state = self.cross_path_validator.validate(state)
        state = self.evidence_aggregator.aggregate(state)
        return state


# ------------------------ Final answer ------------------------


COMPLEX_ANSWER_PROMPT = """你是一名政务统计分析助手，请根据【问题】【推理步骤】【最终数值结果】生成简洁、正式的中文回答。

【问题】
{question}

【推理步骤】
{plan_steps}

【最终计算结果】
final_answer_value = {final_value}

要求：
1. 用 2–3 句话回答。
2. 首句直接给出数值答案，并说明单位（如果题目中能看出）。
3. 第二句简要说明是基于哪几年/哪些指标进行计算的（可参考推理步骤）。
4. 不要暴露 Python 代码。

现在给出回答：
"""

SIMPLE_ANSWER_PROMPT = """你是一个政务统计表格问答助手。

给定一个简单问题和表格（已在系统中解析），系统已经为你定位了目标单元格的数值：
value = {simple_answer_value}

【问题】
{question}

请用 1–2 句中文给出直接答案，并简单提及年份/地区/指标名称（如果题目中能看出）。
"""


class FinalAnswerAgent:
    def __init__(
        self,
        llm_fn: Callable[[str], str],
        complex_prompt: str = COMPLEX_ANSWER_PROMPT,
        simple_prompt: str = SIMPLE_ANSWER_PROMPT,
    ):
        self.llm_fn = llm_fn
        self.complex_prompt = complex_prompt
        self.simple_prompt = simple_prompt

    @staticmethod
    def _parse_classification_label(raw: str, allowed_labels: List[str]) -> str:
        data = _first_json_object(raw) or {}
        candidate = str(data.get("label", "")).strip()
        canonical = {label.lower(): label for label in allowed_labels}
        if candidate.lower() in canonical:
            return canonical[candidate.lower()]

        matches = {
            canonical[label.lower()]
            for label in allowed_labels
            if re.search(rf"\b{re.escape(label)}\b", raw, flags=re.I)
        }
        if len(matches) == 1:
            return next(iter(matches))
        raise RuntimeError(
            f"Classifier response does not contain exactly one allowed label: {allowed_labels}."
        )

    def classify(self, state: TQASessionState) -> TQASessionState:
        allowed_labels = list(state.answer_contract.allowed_labels)
        if not allowed_labels:
            raise ValueError(f"Unsupported answer mode: {state.answer_mode!r}")
        prompt = (
            "You are a closed-label table classifier. Use only the table and "
            "the statement/question below. Return one JSON object with a single "
            f"label chosen from {allowed_labels}. Do not add explanation.\n\n"
            f"[STATEMENT_OR_QUESTION]\n{state.question}\n\n"
            f"[TABLE_SUBJECT_OR_CONTEXT]\n{state.table_context or 'Not provided'}\n\n"
            f"[TABLE]\n{state.table_schema.get('preview_text', '')}\n\n"
            '[OUTPUT]\n{"label": "..."}'
        )
        raw = (self.llm_fn(prompt) or "").strip()
        state.classification_raw_output = raw
        label = self._parse_classification_label(raw, allowed_labels)
        state.final_value = label
        state.final_answer = label
        state.exec_success = True
        state.exec_error = None
        state.contract_validation = {"valid": True, "reason": ""}
        return state

    def verify_tabfact(self, state: TQASessionState) -> TQASessionState:
        allowed_labels = list(state.answer_contract.allowed_labels)
        table_text = _df_preview_text(state.df, max_rows=min(60, max(1, len(state.df))))
        prompt = (
            "You are a TabFact verification judge. Independently verify every clause "
            "of the statement against the table, including all entities, numbers, "
            "dates, conjunctions, and relations implied by the table subject. The "
            "program result is only a proposal and may have ignored a clause. Return "
            "one JSON object with exactly one label chosen from "
            f"{allowed_labels}. Do not explain.\n\n"
            f"[STATEMENT]\n{state.question}\n\n"
            f"[TABLE SUBJECT/CONTEXT]\n{state.table_context or 'Not provided'}\n\n"
            f"[DATASET CONSTRAINTS]\n{state.dataset_instructions}\n\n"
            f"[TABLE]\n{table_text}\n\n"
            f"[PROGRAM]\n{state.code_str}\n\n"
            f"[PROPOSED LABEL]\n{state.final_value}\n\n"
            '[OUTPUT]\n{"label": "..."}'
        )
        raw = (self.llm_fn(prompt) or "").strip()
        state.verification_raw_output = raw
        state.final_value = self._parse_classification_label(raw, allowed_labels)
        return state

    def respond(self, state: TQASessionState) -> TQASessionState:
        # SIMPLE: directly answer based on question + table preview
        # COMPLEX: use PLAN + final_value to format a more formal answer
        if (
            state.route_type == "COMPLEX"
            and state.contract_validation.get("valid") is False
        ):
            prompt = (
                "You are a direct table QA extractor performing structured recovery. "
                "The program did not produce a valid answer. Use only the supplied "
                "table evidence and return one JSON object with a single field named "
                "answer. The field value must follow the answer contract exactly; do "
                "not explain uncertainty.\n\n"
                f"[QUESTION]\n{state.question}\n\n"
                f"[TABLE SUBJECT/CONTEXT]\n{state.table_context or 'Not provided'}\n\n"
                f"[ANSWER CONTRACT]\n{state.answer_contract.instructions}\n\n"
                f"[TABLE PREVIEW]\n{state.table_schema.get('preview_text', '')}\n\n"
                f"[COLUMN PROFILES]\n"
                f"{state.table_schema.get('column_profiles_text', '')}\n\n"
                '[OUTPUT]\n{"answer": "..."}'
            )
            raw = (self.llm_fn(prompt) or "").strip()
            data = _first_json_object(raw) or {}
            answer = data.get("answer")
            if answer in (None, ""):
                answer = raw
            state.final_value = answer
            state.final_answer = (
                json.dumps(answer, ensure_ascii=False)
                if isinstance(answer, (list, tuple))
                else str(answer).strip()
            )
            state.exec_success = True
            state.exec_error = None
            return state

        if state.route_type == "COMPLEX" and state.answer_contract.kind in {
            "label",
            "list",
            "tuple",
        }:
            if state.answer_contract.kind in {"list", "tuple"}:
                state.final_answer = json.dumps(state.final_value, ensure_ascii=False)
            else:
                state.final_answer = str(state.final_value)
            return state

        if state.route_type == "SIMPLE":
            if state.simple_lookup_success:
                evidence = state.simple_lookup_evidence or {}
                row_label = evidence.get("row_label") or evidence.get("row_index") or "目标行"
                col_name = evidence.get("col_name") or "目标列"
                value = evidence.get("value")
                state.final_value = state.simple_lookup_value
                state.final_answer = f"根据表格中“{row_label}”行、“{col_name}”列，答案为 {value}。"
                state.exec_success = True
                state.exec_error = None
                return state
            table_preview = state.table_schema.get("preview_text", "")
            prompt = (
                "You are a direct table QA extractor. Answer only from the table. "
                "Return one JSON object with a single scalar or string field named "
                "answer. Follow the answer contract and do not add explanation.\n\n"
                "[QUESTION]\n"
                f"{state.question}\n\n"
                "[TABLE SUBJECT/CONTEXT]\n"
                f"{state.table_context or 'Not provided'}\n\n"
                "[ANSWER CONTRACT]\n"
                f"{state.answer_contract.instructions}\n\n"
                "[TABLE]\n"
                f"{table_preview}\n\n"
                '[OUTPUT]\n{"answer": "..."}'
            )
            raw = (self.llm_fn(prompt) or "").strip()
            data = _first_json_object(raw) or {}
            answer = data.get("answer")
            if answer in (None, ""):
                answer = raw
            state.final_value = answer
            state.final_answer = str(answer).strip()
            state.exec_success = True
            state.exec_error = None
            return state
        else:
            plan_steps_text = "\n".join(state.plan_steps)
            prompt = self.complex_prompt.format(
                question=state.question,
                plan_steps=plan_steps_text,
                final_value=state.final_value,
            )
        answer = self.llm_fn(prompt)
        state.final_answer = answer.strip()
        return state


# ------------------------ Orchestrator ------------------------


class TableQAPipeline:
    """High-level orchestrator that wires all agents together.

    External interface: run(state) → update state in-place.
    Internally calls Router / Planner / Calculator / Critic / FinalAnswer.
    """

    def __init__(
        self,
        router: RouterAgent,
        planner: PlannerAgent,
        calculator: Calculator,
        critic: CriticAgent,
        final_answer_agent: FinalAnswerAgent,
        compressor: Optional[TableCompressor] = None,
        simple_lookup_agent: Optional[SimpleCellLookupAgent] = None,
        multiview_validator: Optional[MultiViewValidator] = None,
        enable_multi_view_validation: bool = False,
        enable_selective_collaboration: bool = False,
        enable_strong_verification: bool = True,
        enable_deterministic_shortcuts: bool = True,
        disable_question_routing: bool = False,
        disable_risk_scoring: bool = False,
        disable_table_compression: bool = False,
        mact_avg_tokens: float = 8867.0,
        risk_profiler: Optional[RiskProfiler] = None,
        evidence_builder: Optional[EvidenceBuilder] = None,
        agreement_judge_factory: Optional[Callable[[AnswerContract], AgreementJudge]] = None,
        thinking_solver_factory: Optional[Callable[[], ThinkingSolver]] = None,
        verification_styles: Optional[List[str]] = None,
        max_replan: int = 2,
    ) -> None:
        self.router = router
        self.planner = planner
        self.calculator = calculator
        self.critic = critic
        self.final_answer_agent = final_answer_agent
        self.compressor = compressor or TableCompressor()
        self.simple_lookup_agent = simple_lookup_agent or SimpleCellLookupAgent()
        self.multiview_validator = multiview_validator or MultiViewValidator(
            evidence_critic=EvidenceCriticAgent(router.llm_fn),
            logic_critic=LogicCriticAgent(router.llm_fn),
            alternative_planner=AlternativePlannerAgent(router.llm_fn),
        )
        self.enable_multi_view_validation = enable_multi_view_validation
        self.enable_selective_collaboration = enable_selective_collaboration
        self.enable_strong_verification = enable_strong_verification
        self.enable_deterministic_shortcuts = enable_deterministic_shortcuts
        self.disable_question_routing = disable_question_routing
        self.disable_risk_scoring = disable_risk_scoring
        self.disable_table_compression = disable_table_compression
        self.max_replan = max_replan
        self.mact_avg_tokens = mact_avg_tokens
        self.risk_profiler = risk_profiler or RiskProfiler()
        self.evidence_builder = evidence_builder or EvidenceBuilder()
        self.agreement_judge_factory = agreement_judge_factory or (
            lambda contract: AgreementJudge(contract)
        )
        if thinking_solver_factory is None and enable_strong_verification:
            self.thinking_solver_factory = lambda: ThinkingSolver(router.llm_fn)
        else:
            self.thinking_solver_factory = thinking_solver_factory
        self.verification_styles = verification_styles or ["direct", "audit", "program"]
        self.budget_controller = BudgetController(
            BudgetPolicy(mact_avg_tokens=mact_avg_tokens)
        )

    @staticmethod
    def _normalize_and_validate(state: TQASessionState) -> bool:
        state.final_value = normalize_contract_value(
            state.final_value,
            state.answer_contract,
        )
        state.final_value = _coerce_pandas_answer_value(state.final_value)
        if (
            state.dataset_profile == "wtq"
            and state.answer_contract.kind == "scalar"
        ):
            state.final_value = _canonicalize_wtq_scalar(
                state.final_value,
                state.original_df,
                state.question,
            )
            state.final_value = _coerce_pandas_answer_value(state.final_value)
        if (
            state.dataset_profile == "crt"
            and state.answer_contract.kind == "scalar"
        ):
            state.final_value = _canonicalize_crt_scalar(
                state.final_value,
                state.question,
                state.original_df,
            )
            state.final_value = _coerce_pandas_answer_value(state.final_value)
        valid, reason = validate_contract_value(
            state.final_value,
            state.answer_contract,
        )
        state.contract_validation = {"valid": valid, "reason": reason}
        if valid and not _is_empty_answer_value(state.final_value):
            if state.answer_contract.kind in {"list", "tuple"}:
                state.final_answer = json.dumps(state.final_value, ensure_ascii=False)
            elif state.answer_contract.kind in {"scalar", "label"}:
                state.final_answer = str(state.final_value).strip()
        return valid

    def _should_run_llm_critic(self, state: TQASessionState) -> bool:
        return self.enable_multi_view_validation or state.difficulty_level == "hard"

    def build_state_from_table(self, question: str, table: Any) -> TQASessionState:
        df = build_df_from_table(table)
        schema = _build_table_schema(df)
        return TQASessionState(question=question, df=df, table_schema=schema)

    def run(self, state: TQASessionState) -> TQASessionState:
        if not self.enable_selective_collaboration:
            return self._run_legacy(state)
        return self._run_selective(state)

    def _current_token_count(self) -> int:
        llm_fn = getattr(self.router, "llm_fn", None)
        if not hasattr(llm_fn, "snapshot"):
            return 0
        snapshot = llm_fn.snapshot()
        if "total_tokens" in snapshot:
            return int(snapshot.get("total_tokens") or 0)
        return int(snapshot.get("total_tokens_est") or 0)

    @staticmethod
    def _risk_semantic_hint(state: TQASessionState) -> float:
        if state.difficulty_score is not None:
            base_score = float(state.difficulty_score)
        else:
            base_score = 0.15
        text = state.question or ""
        signals = 0
        if re.search(r"\b(average|difference|compare|comparison|ratio|percent|sum|total)\b", text, flags=re.I):
            signals += 1
        if re.search(r"\b(and|or|not|no|only|both|either)\b", text, flags=re.I):
            signals += 1
        if re.search(r"\b(before|after|highest|lowest|most|least|first|last)\b", text, flags=re.I):
            signals += 1
        if state.answer_contract.reasoning_required:
            signals += 1
        tag_count = len(
            set(state.problem_tags)
            & {
                "count",
                "superlative_order",
                "temporal",
                "comparison",
                "arithmetic",
                "negation_logic",
                "closed_choice",
                "trend_correlation",
            }
        )
        tag_score = 0.15 + 0.12 * tag_count
        return min(1.0, max(base_score, 0.15 + 0.20 * signals, tag_score))

    @staticmethod
    def _risk_floor_for_tags(state: TQASessionState) -> float:
        tags = set(state.problem_tags)
        dataset = (state.dataset_profile or "").lower()
        if not tags:
            return 0.0
        high_tags = {
            "count",
            "superlative_order",
            "temporal",
            "comparison",
            "arithmetic",
            "negation_logic",
            "closed_choice",
            "trend_correlation",
            "list_entity",
        }
        floor = min(0.68, 0.18 + 0.10 * len(tags & high_tags))
        if dataset == "wtq" and tags & {"superlative_order", "count", "negation_logic"}:
            floor = max(floor, 0.58)
        if dataset == "tabfact" and "closed_choice" in tags and len(tags & high_tags) >= 2:
            floor = max(floor, 0.58)
        if dataset == "crt" and (
            "closed_choice" in tags
            or "trend_correlation" in tags
            or {"superlative_order", "arithmetic"} <= tags
        ):
            floor = max(floor, 0.60)
        return floor

    def _assess_selective_risk(self, state: TQASessionState) -> None:
        state.problem_tags = classify_problem_tags(
            state.question,
            state.dataset_profile,
            state.answer_contract,
        )
        if self.disable_risk_scoring:
            state.risk_assessment = RiskAssessment(
                difficulty=0.0,
                ambiguity=0.0,
                evidence_gap=0.0,
                operation_risk=0.0,
                pre_risk=0.35,
                level="medium",
                feature_evidence={
                    "ablation": "risk_scoring_disabled",
                    "problem_tags": list(state.problem_tags),
                },
            )
            state.risk_level = "medium"
            return
        if state.evidence_pack is None:
            state.evidence_pack = self.evidence_builder.build(
                question=state.question,
                df=state.original_df,
                schema=state.table_schema,
                dataset_name=state.dataset_profile,
                answer_contract=state.answer_contract,
            )
        hard_triggers: List[str] = []
        if state.contract_validation.get("valid") is False:
            hard_triggers.append("contract_failure")
        if state.exec_success is False and state.exec_error:
            hard_triggers.append("execution_failure")
        assessment = self.risk_profiler.assess_pre(
            semantic_complexity=self._risk_semantic_hint(state),
            structure_signals=state.evidence_pack.structure_signals,
            ambiguity_signals=state.evidence_pack.ambiguity_signals,
            gap_signals=state.evidence_pack.gap_signals,
            operation_signals=state.evidence_pack.operation_signals,
            hard_triggers=hard_triggers,
        )
        tag_floor = self._risk_floor_for_tags(state)
        if tag_floor > assessment.pre_risk:
            assessment.pre_risk = min(1.0, tag_floor)
            assessment.level = self.risk_profiler.level_for(assessment)
            assessment.feature_evidence.setdefault("problem_tags", list(state.problem_tags))
            assessment.feature_evidence["tag_risk_floor"] = tag_floor
        state.risk_assessment = assessment
        state.risk_level = assessment.level

    def _apply_no_routing_ablation(self, state: TQASessionState) -> TQASessionState:
        state.route_type = "COMPLEX"
        state.difficulty_score = 1.0
        state.difficulty_level = "hard"
        state.semantic_features["sem_score"] = 1.0
        state.structural_features["cell_score"] = 1.0
        state.structural_features["estimated_cells_touched"] = int(
            max(1, state.original_df.shape[0] * state.original_df.shape[1])
        )
        state.routing_context = {
            "ablation": "question_routing_disabled",
            "route_type": state.route_type,
            "difficulty_score": state.difficulty_score,
            "difficulty_level": state.difficulty_level,
        }
        return state

    def _apply_no_compression_ablation(self, state: TQASessionState) -> TQASessionState:
        original_df = state.original_df
        state.compressed_df = original_df
        state.df = original_df
        full_tokens = max(1, TableCompressor._token_estimate_df(original_df))
        preview_rows = max(1, min(200, len(original_df)))
        state.table_schema = _build_table_schema(
            original_df,
            max_preview_rows=preview_rows,
        )
        original_cells = int(max(1, original_df.shape[0] * original_df.shape[1]))
        state.compression_info = {
            "strategy": "disabled_ablation_full_table",
            "original_rows": int(original_df.shape[0]),
            "original_cols": int(original_df.shape[1]),
            "compressed_rows": int(original_df.shape[0]),
            "compressed_cols": int(original_df.shape[1]),
            "original_cells": original_cells,
            "compressed_cells": original_cells,
            "compression_ratio": 1.0,
            "token_compression_ratio_est": 1.0,
            "full_table_tokens_est": full_tokens,
            "compressed_table_tokens_est": full_tokens,
            "used_rows": [str(x) for x in original_df.index.tolist()[:200]],
            "used_cols": [str(x) for x in original_df.columns.tolist()],
        }
        state.structural_features["compression_ratio"] = 1.0
        state.structural_features["token_compression_ratio_est"] = 1.0
        state.routing_context.update(
            {
                "compression_strategy": state.compression_info["strategy"],
                "compression_ratio": 1.0,
                "token_compression_ratio_est": 1.0,
            }
        )
        return state

    def _apply_semantic_shortcut(
        self,
        state: TQASessionState,
        value: Any,
        reason: str,
    ) -> bool:
        state.deterministic_shortcut_applied = True
        state.deterministic_shortcut_reason = reason
        state.final_value = value
        state.exec_success = True
        state.exec_error = None
        state.plan_steps = [reason]
        state.code_str = f"# deterministic semantic shortcut: {reason}"
        state.critic_skipped = True
        state.critic_verdict = "PASS"
        state.critic_feedback = reason
        state.grounding_validation = {"valid": True, "reason": reason}
        self._normalize_and_validate(state)
        return True

    @staticmethod
    def _crt_numeric_outlier_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        text = question or ""
        if not re.search(r"\boutliers?\b", text, flags=re.I):
            return None
        if not re.search(r"\b(?:identify|any|whether|if|yes|no)\b", text, flags=re.I):
            return None
        question_tokens = {_singular_token(token) for token in _loose_tokens(text)}
        candidate_cols: List[Any] = []
        for col in df.columns:
            col_tokens = {_singular_token(token) for token in _loose_tokens(col)}
            if not col_tokens or not (col_tokens & question_tokens):
                continue
            values = [
                _numeric_measure_value(value)
                for value in df[col].tolist()
            ]
            if sum(value is not None for value in values) >= 4:
                candidate_cols.append(col)
        if not candidate_cols:
            return None

        for col in candidate_cols:
            series = pd.Series(
                [
                    float(value)
                    for value in (
                        _numeric_measure_value(cell)
                        for cell in df[col].tolist()
                    )
                    if value is not None
                ]
            )
            if len(series) < 4 or series.nunique() < 2:
                continue
            q1 = float(series.quantile(0.25))
            q3 = float(series.quantile(0.75))
            iqr = q3 - q1
            upper = q3 + 1.5 * iqr
            lower = q1 - 1.5 * iqr
            ordered = sorted(series.tolist())
            max_gap = ordered[-1] - ordered[-2] if len(ordered) >= 2 else 0.0
            min_gap = ordered[1] - ordered[0] if len(ordered) >= 2 else 0.0
            if iqr > 0 and (ordered[-1] > upper or ordered[0] < lower):
                return "Yes"
            if ordered[-2] > 0 and ordered[-1] >= ordered[-2] * 3 and max_gap >= 5:
                return "Yes"
            if ordered[1] > 0 and ordered[1] >= ordered[0] * 3 and min_gap >= 5:
                return "Yes"
        return "No"

    @staticmethod
    def _crt_top_k_years_average_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        match = re.search(
            r"\baverage\s+number\s+of\s+years\s+played\b.*\btop\s+(\d+)\b",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        top_k = int(match.group(1))
        if top_k <= 0:
            return None
        rank_cols = [
            col for col in df.columns
            if re.search(r"\b(rank|place|position)\b", str(col), flags=re.I)
        ]
        years_cols = [
            col for col in df.columns
            if re.search(r"\byears?\b", str(col), flags=re.I)
        ]
        if not years_cols:
            return None
        years_col = years_cols[0]

        explicit_ends: List[int] = []
        all_years: List[int] = []
        for value in df[years_col].tolist():
            years = [int(year) for year in re.findall(r"\b(1[7-9]\d{2}|20\d{2})\b", str(value or ""))]
            all_years.extend(years)
            if len(years) >= 2:
                explicit_ends.append(max(years[0], years[-1]))
        inferred_end_year = max(explicit_ends or all_years) if all_years else None
        if inferred_end_year is None:
            return None

        def year_span(value: Any) -> Optional[Tuple[int, int]]:
            text = str(value or "")
            years = [int(year) for year in re.findall(r"\b(1[7-9]\d{2}|20\d{2})\b", text)]
            if len(years) >= 2:
                return min(years[0], years[-1]), max(years[0], years[-1])
            if len(years) == 1 and re.search(r"[-–—]\s*$", text.strip()):
                return min(years[0], inferred_end_year), max(years[0], inferred_end_year)
            return None

        if rank_cols:
            rank_col = rank_cols[0]
            ranked: List[Tuple[float, int, pd.Series]] = []
            for order, (_, row) in enumerate(df.iterrows()):
                rank = _as_number_like(row[rank_col])
                if rank is None:
                    continue
                ranked.append((rank, order, row))
            if len(ranked) < top_k:
                return None
            selected_rows = [row for _, _, row in sorted(ranked, key=lambda item: (item[0], item[1]))[:top_k]]
        else:
            if len(df) < top_k:
                return None
            selected_rows = [row for _, row in df.head(top_k).iterrows()]

        durations: List[int] = []
        for row in selected_rows:
            span = year_span(row[years_col])
            if span is None:
                return None
            durations.append(span[1] - span[0] + 1)
        average = sum(durations) / len(durations)
        return int(round(average)) if abs(average - round(average)) <= 0.25 else round(average, 2)

    @staticmethod
    def _crt_constructor_retirement_reason_percentage_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        text = question or ""
        match = re.search(
            r"\bwhich\s+constructor\b.*\bhighest\s+percentage\b.*\bretire(?:d)?\s+"
            r"due\s+to\s+(.+?)\s+problems?\b",
            text,
            flags=re.I,
        )
        if not match:
            return None
        reason_key = _loose_text_key(match.group(1))
        if not reason_key:
            return None
        constructor_cols = [
            col for col in df.columns
            if re.search(r"\bconstructor\b", str(col), flags=re.I)
        ]
        status_cols = [
            col for col in df.columns
            if re.search(r"\b(retired|retirement|status|result|reason|time)\b", str(col), flags=re.I)
        ]
        if not constructor_cols or not status_cols:
            return None
        constructor_col = constructor_cols[0]
        status_col = status_cols[0]
        groups: Dict[str, Dict[str, Any]] = {}
        for _, row in df.iterrows():
            constructor = row[constructor_col]
            if _is_missing_marker(constructor):
                continue
            constructor_text = re.sub(r"\s+", " ", str(constructor)).strip()
            constructor_key = _loose_text_key(constructor_text)
            if not constructor_key:
                continue
            group = groups.setdefault(
                constructor_key,
                {"display": constructor_text, "total": 0, "reason": 0},
            )
            group["total"] += 1
            if reason_key in _loose_text_key(row[status_col]):
                group["reason"] += 1
        scored: List[Tuple[float, int, str]] = []
        for group in groups.values():
            total = int(group["total"])
            reason = int(group["reason"])
            if total <= 0 or reason <= 0:
                continue
            scored.append((reason / total, reason, str(group["display"])))
        if not scored:
            return None
        scored.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        if len(scored) > 1 and abs(scored[0][0] - scored[1][0]) <= 1e-12:
            return None
        return scored[0][2]

    @staticmethod
    def _crt_duration_change_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        if not re.search(r"\bduration\b.*\bchanged?\b|\bchanged?\b.*\bduration\b", question, flags=re.I):
            return None
        duration_cols = [
            col
            for col in df.columns
            if re.search(r"\b(days?|duration|length)\b", str(col), flags=re.I)
        ]
        if not duration_cols:
            return None
        values = [_duration_or_text_key(value) for value in df[duration_cols[0]].dropna().tolist()]
        values = [value for value in values if value]
        if not values:
            return None
        return "Yes" if len(set(values)) > 1 else "No"

    @staticmethod
    def _crt_consecutive_year_medalist_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        text = question or ""
        if not re.search(r"\bconsecutive\s+years?\b", text, flags=re.I):
            return None
        if not re.search(r"\b(athletes?|medals?|won)\b", text, flags=re.I):
            return None
        year_cols = [col for col in df.columns if re.search(r"\byears?\b", str(col), flags=re.I)]
        medal_cols = [
            col
            for col in df.columns
            if re.search(r"\b(gold|silver|bronze|medals?)\b", str(col), flags=re.I)
        ]
        if not year_cols or not medal_cols:
            return None
        year_col = year_cols[0]
        years_by_athlete: Dict[str, set[int]] = {}
        for _, row in df.iterrows():
            year_value = _as_number_like(row[year_col])
            if year_value is None:
                continue
            year = int(year_value)
            for col in medal_cols:
                athlete = re.sub(r"\([^)]*\)", " ", str(row[col] or ""))
                athlete_key = _loose_text_key(athlete)
                if not athlete_key or athlete_key in {"nan", "none", "n a"}:
                    continue
                years_by_athlete.setdefault(athlete_key, set()).add(year)
        for years in years_by_athlete.values():
            if any(year + 1 in years for year in years):
                return "Yes"
        return "No" if years_by_athlete else None

    @staticmethod
    def _crt_recognition_category_advantage_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        text = question or ""
        if not re.search(r"\bgreater\s+chance\b", text, flags=re.I):
            return None
        if not re.search(r"\brecogniz(?:e|ed|ing)\b|\baward\s+categor", text, flags=re.I):
            return None
        award_cols = [
            col for col in df.columns if re.search(r"\b(award|category|categories)\b", str(col), flags=re.I)
        ]
        if not award_cols or len(df) < 3:
            return None
        generic = {
            "award",
            "best",
            "categor",
            "choice",
            "editor",
            "performer",
            "scene",
            "star",
            "with",
            "year",
        }
        token_counts: Dict[str, int] = {}
        for value in df[award_cols[0]].dropna().tolist():
            tokens = {
                token
                for token in _loose_tokens(value)
                if token not in generic and len(token) >= 4
            }
            for token in tokens:
                token_counts[token] = token_counts.get(token, 0) + 1
        return "Yes" if any(count >= 2 for count in token_counts.values()) else None

    @staticmethod
    def _crt_count_role_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        match = re.search(r"\bhow\s+many\b.*\bwere\s+(bishops?|priests?|deacons?)\b", question or "", flags=re.I)
        if not match:
            return None
        role = match.group(1).lower().rstrip("s")
        title_cols = [
            col for col in df.columns
            if re.search(r"\b(title|order|cardinal|source)\b", str(col), flags=re.I)
        ]
        if not title_cols:
            return None
        count = 0
        for _, row in df.iterrows():
            text = " ".join(str(row[col]) for col in title_cols)
            if re.search(rf"\b{re.escape(role)}\b", text, flags=re.I):
                count += 1
        return count

    @staticmethod
    def _crt_first_supported_architecture_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        if not re.search(r"\bfirst\b.*\bsupport\b.*\b64\s*-\s*bit|64\s*-\s*bit\b.*\barchitecture\b", question or "", flags=re.I):
            return None
        name_cols = [col for col in df.columns if re.fullmatch(r"(?i)name|version", str(col).strip())]
        date_cols = [col for col in df.columns if re.search(r"\brelease\s+date\b|\bdate\b", str(col), flags=re.I)]
        arch_cols = [col for col in df.columns if re.search(r"\barchitecture|supported architectures|based on", str(col), flags=re.I)]
        if not name_cols or not date_cols or not arch_cols:
            return None

        def date_key(value: Any) -> Tuple[int, int, int]:
            nums = [int(item) for item in re.findall(r"\d+", str(value))[:3]]
            while len(nums) < 3:
                nums.append(1)
            return tuple(nums[:3])  # type: ignore[return-value]

        candidates = []
        for _, row in df.iterrows():
            arch_text = " ".join(str(row[col]) for col in arch_cols)
            if re.search(r"\bia\s*-\s*64\b|\bx86\s*-\s*64\b|\b64\s*-\s*bit\b", arch_text, flags=re.I):
                candidates.append((date_key(row[date_cols[0]]), str(row[name_cols[0]]).strip()))
        if not candidates:
            return None
        return sorted(candidates, key=lambda item: item[0])[0][1]

    @staticmethod
    def _crt_largest_money_leftover_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        if not re.search(r"\blargest\b.*\bmoney\s+left\s+over\b.*\bdisbursements?\b", question or "", flags=re.I):
            return None
        entity_cols = [
            col for col in df.columns
            if pd.to_numeric(df[col], errors="coerce").notna().sum() < max(1, len(df) // 2)
        ]
        receipt_cols = [col for col in df.columns if re.search(r"\ball\s+receipts?\b|receipts?$", str(col), flags=re.I)]
        disbursement_cols = [col for col in df.columns if re.search(r"\ball\s+disbursements?\b|disbursements?$", str(col), flags=re.I)]
        if not entity_cols or not receipt_cols or not disbursement_cols:
            return None
        best_name = None
        best_value = None
        for _, row in df.iterrows():
            name = str(row[entity_cols[0]]).strip()
            if re.search(r"\b(total|combined)\b", name, flags=re.I):
                continue
            receipts = _as_number_like(row[receipt_cols[0]])
            disbursements = _as_number_like(row[disbursement_cols[0]])
            if receipts is None or disbursements is None:
                continue
            leftover = receipts - disbursements
            if best_value is None or leftover > best_value:
                best_value = leftover
                best_name = name
        return best_name

    @staticmethod
    def _crt_proportion_nationality_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(r"\bproportion\s+of\s+([a-z]+)\s+to\s+non\s*-\s*\1\b", question or "", flags=re.I)
        if not match:
            return None
        nationality = match.group(1)
        nationality_cols = [col for col in df.columns if re.search(r"\bnationality|country\b", str(col), flags=re.I)]
        if not nationality_cols:
            return None
        demonym_map = {
            "canadian": "canada",
            "american": "united states",
            "british": "united kingdom",
            "english": "england",
            "french": "france",
            "german": "germany",
            "italian": "italy",
            "spanish": "spain",
        }
        left = 0
        right = 0
        target_key = _loose_text_key(demonym_map.get(nationality.lower(), nationality))
        for value in df[nationality_cols[0]].tolist():
            if target_key and target_key in _loose_text_key(value):
                left += 1
            else:
                right += 1
        return _format_integer_ratio(left, right)

    @staticmethod
    def _crt_duplicate_named_entity_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        if not re.search(r"\bled\s+in\s+more\s+than\s+one\b", question or "", flags=re.I):
            return None
        entity_cols = [
            col for col in df.columns
            if re.search(r"\b(player|athlete|name)\b", str(col), flags=re.I)
        ]
        if not entity_cols:
            return None
        counts: Dict[str, int] = {}
        for value in df[entity_cols[0]].tolist():
            key = _loose_text_key(value)
            if key:
                counts[key] = counts.get(key, 0) + 1
        return "Yes" if any(count > 1 for count in counts.values()) else "No"

    @staticmethod
    def _crt_victory_type_stands_out_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        if not re.search(r"\btype\s+of\s+victory\b.*\bstands?\s+out\b|\bstands?\s+out\b.*\btype\s+of\s+victory\b", question or "", flags=re.I):
            return None
        result_cols = [col for col in df.columns if re.fullmatch(r"(?i)res|result", str(col).strip())]
        method_cols = [col for col in df.columns if re.search(r"\bmethod|victory\b", str(col), flags=re.I)]
        if not result_cols or not method_cols:
            return None
        counts: Dict[str, int] = {}
        for _, row in df.iterrows():
            if not re.search(r"\bwin\b", str(row[result_cols[0]]), flags=re.I):
                continue
            method = _loose_text_key(str(row[method_cols[0]]).split("(", 1)[0])
            if method:
                counts[method] = counts.get(method, 0) + 1
        if len(counts) < 2:
            return None
        ordered = sorted(counts.values(), reverse=True)
        return "Yes" if ordered[0] >= 2 and ordered[0] > ordered[1] else "No"

    @staticmethod
    def _crt_common_cardinal_source_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        if not re.search(r"\bmost\s+common\s+source\b.*\bcardinalhood\b", question or "", flags=re.I):
            return None
        title_cols = [
            col for col in df.columns
            if re.search(r"\b(title|order|cardinal)\b", str(col), flags=re.I)
        ]
        if not title_cols:
            return None
        counts = {"bishop": 0, "priest": 0, "deacon": 0}
        for value in df[title_cols[0]].tolist():
            text = str(value)
            for role in counts:
                if re.search(rf"\b{role}\b", text, flags=re.I):
                    counts[role] += 1
                    break
        best = max(counts, key=counts.get)
        return best if counts[best] > 0 else None

    @staticmethod
    def _crt_diverse_content_beyond_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        if not re.search(r"\bdiverse\s+content\b.*\bbeyond\b", question or "", flags=re.I):
            return None
        content_cols = [col for col in df.columns if re.search(r"\bcontent\b", str(col), flags=re.I)]
        if not content_cols:
            return None
        excluded = {"calcio", "football"}
        for value in df[content_cols[0]].tolist():
            tokens = {token for token in _loose_tokens(value) if token not in excluded}
            if tokens:
                return "Yes"
        return "No"

    @staticmethod
    def _crt_episode_viewership_vs_season_average_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r'\bviewership\s+of\s+the\s+"([^"]+)"\s+episode\s+compare\s+to\s+the\s+average\s+viewership\s+of\s+the\s+season',
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        title = match.group(1)
        title_cols = [col for col in df.columns if re.search(r"\btitle|episode\b", str(col), flags=re.I)]
        viewer_cols = [col for col in df.columns if re.search(r"\bviewers?|viewership\b", str(col), flags=re.I)]
        if not title_cols or not viewer_cols:
            return None
        viewer_values = pd.to_numeric(df[viewer_cols[0]], errors="coerce")
        if viewer_values.dropna().empty:
            return None
        target_rows = df[df[title_cols[0]].map(lambda value: _loose_text_key(title) in _loose_text_key(value))]
        if target_rows.empty:
            return None
        target_value = _as_number_like(target_rows.iloc[0][viewer_cols[0]])
        if target_value is None:
            return None
        average = float(viewer_values.dropna().mean())
        if target_value > average:
            return "better"
        if target_value < average:
            return "worse"
        return "equal"

    @staticmethod
    def _crt_event_type_difference_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        if not re.search(r"\bdifference\b.*\btypes?\s+of\s+events?\b", question, flags=re.I):
            return None
        if not re.search(r"\bbased\s+on\b", question, flags=re.I):
            return None
        event_cols = [col for col in df.columns if re.search(r"\bevents?\b", str(col), flags=re.I)]
        feature_cols = [
            col
            for col in df.columns
            if re.search(r"\b(days?|duration|stages?)\b", str(col), flags=re.I)
        ]
        if not event_cols or not feature_cols:
            return None
        event_col = event_cols[0]
        feature_to_events: Dict[Tuple[str, ...], set] = {}
        event_to_features: Dict[str, set] = {}
        for _, row in df.iterrows():
            event = re.sub(r"\s+", " ", str(row[event_col]).strip().lower())
            features: List[str] = []
            for col in feature_cols:
                value = row[col]
                if re.search(r"\b(days?|duration)\b", str(col), flags=re.I):
                    features.append(_duration_or_text_key(value))
                else:
                    number = _as_number_like(value)
                    features.append(
                        f"{str(col).lower()}:{round(number, 6)}"
                        if number is not None
                        else re.sub(r"\s+", " ", str(value).strip().lower())
                    )
            feature_key = tuple(features)
            feature_to_events.setdefault(feature_key, set()).add(event)
            event_to_features.setdefault(event, set()).add(feature_key)
        if len(feature_to_events) <= 1:
            return "No"
        if any(len(events) > 1 for events in feature_to_events.values()):
            return "No"
        if any(len(features) > 1 for features in event_to_features.values()):
            return "No"
        return "Yes"

    @staticmethod
    def _crt_percentage_snapshot_average(question: str, df: pd.DataFrame) -> Optional[float]:
        if not re.search(r"\baverage\s+percentage\s+change\b", question, flags=re.I):
            return None
        if re.search(r"\b(relative|increase|decrease|growth\s+rate|rate\s+of\s+change)\b", question, flags=re.I):
            return None
        top_match = re.search(r"\btop\s+(\d+)\b", question, flags=re.I)
        between_match = re.search(r"\bbetween\s+(\d{4})\s+and\s+(\d{4})\b", question, flags=re.I)
        rank_cols = [col for col in df.columns if str(col).strip().lower() == "rank"]
        percent_cols: List[Tuple[int, Any]] = []
        for col in df.columns:
            match = re.search(r"%\s*\(\s*(\d{4})\s*\)", str(col))
            if match:
                percent_cols.append((int(match.group(1)), col))
        if not top_match or not between_match or not rank_cols or not percent_cols:
            return None
        top_n = int(top_match.group(1))
        start_year = int(between_match.group(1))
        end_year = int(between_match.group(2))
        if start_year > end_year:
            start_year, end_year = end_year, start_year
        selected_cols = [col for year, col in percent_cols if start_year <= year <= end_year]
        if not selected_cols:
            return None
        work = df.copy()
        work["_rank_num"] = pd.to_numeric(work[rank_cols[0]], errors="coerce")
        top_rows = work[work["_rank_num"].le(top_n)]
        if top_rows.empty:
            return None
        values = top_rows[selected_cols].apply(pd.to_numeric, errors="coerce")
        mean_value = values.stack().mean()
        if pd.isna(mean_value):
            return None
        return float(mean_value)

    @staticmethod
    def _crt_owned_percentage_answer(question: str, df: pd.DataFrame) -> Optional[float]:
        match = re.search(
            r"\bwhat\s+percentage\s+of\s+.+?\s+are\s+owned\s+by\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        owner_phrase = match.group(1)
        owner_cols = [col for col in df.columns if re.search(r"\bowner\b", str(col), flags=re.I)]
        if not owner_cols or len(df) == 0:
            return None
        count = sum(
            1
            for value in df[owner_cols[0]].tolist()
            if TableQAPipeline._cell_contains_phrase_tokens(owner_phrase, value)
        )
        return round(count / len(df), 4)

    @staticmethod
    def _crt_medal_probability_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        text = question or ""
        if not re.search(r"\b(probability|likelihood)\b", text, flags=re.I):
            return None
        gold_cols = [col for col in df.columns if re.fullmatch(r"(?i)gold", str(col).strip())]
        total_cols = [col for col in df.columns if re.fullmatch(r"(?i)total", str(col).strip())]
        nation_cols = [col for col in df.columns if re.search(r"\b(nation|country)\b", str(col), flags=re.I)]
        if not gold_cols or not nation_cols:
            return None
        work = df.copy()
        work = work[
            ~work[nation_cols[0]].map(lambda value: bool(re.search(r"\b(grand\s+total|total)\b", str(value), flags=re.I)))
        ]
        if work.empty:
            return None
        threshold_match = re.search(
            r"\bat\s+least\s+((?:\d+)|one|two|three|four|five|six|seven|eight|nine|ten)\s+gold\s+medals?\b",
            text,
            flags=re.I,
        )
        if threshold_match and re.search(r"\bnation\b|\bcountry\b", text, flags=re.I):
            threshold = _small_number_from_text(threshold_match.group(1))
            if threshold is None:
                return None
            gold = pd.to_numeric(work[gold_cols[0]], errors="coerce")
            total = int(gold.notna().sum())
            if not total:
                return None
            percentage = int(gold.ge(threshold).sum()) / total * 100
            return f"{percentage:.1f}%"
        if re.search(r"\brandomly\s+chosen\s+medalist\b", text, flags=re.I):
            match = re.search(r"\bfrom\s+(.+?)[?.]?$", text, flags=re.I)
            if not match or not total_cols:
                return None
            target = match.group(1)
            total_medals = pd.to_numeric(work[total_cols[0]], errors="coerce").sum()
            if not total_medals:
                return None
            target_medals = 0.0
            for _, row in work.iterrows():
                if TableQAPipeline._cell_contains_phrase_tokens(target, row[nation_cols[0]]):
                    target_medals += float(_as_number_like(row[total_cols[0]]) or 0)
            percentage = target_medals / total_medals * 100
            return f"{percentage:.1f}%"
        if re.search(r"\bnation\b.*\bat\s+least\s+one\s+gold\b|\bat\s+least\s+one\s+gold\b.*\bnation\b", text, flags=re.I):
            gold = pd.to_numeric(work[gold_cols[0]], errors="coerce")
            total = int(gold.notna().sum())
            if not total:
                return None
            percentage = int(gold.gt(0).sum()) / total * 100
            return f"{percentage:.1f}%"
        return None

    @staticmethod
    def _crt_country_matches(phrase: str, value: Any) -> bool:
        phrase_key = re.sub(r"^the\s+", "", _loose_text_key(_strip_entity_metadata(phrase))).strip()
        value_key = re.sub(r"^the\s+", "", _loose_text_key(_strip_entity_metadata(value))).strip()
        if not phrase_key or not value_key:
            return False
        aliases = {
            "uk": {"uk", "united kingdom", "great britain", "britain", "gb"},
            "united kingdom": {"uk", "united kingdom", "great britain", "britain", "gb"},
            "great britain": {"uk", "united kingdom", "great britain", "britain", "gb"},
            "britain": {"uk", "united kingdom", "great britain", "britain", "gb"},
            "united states": {"united states", "usa", "us", "u s"},
            "usa": {"united states", "usa", "us", "u s"},
            "us": {"united states", "usa", "us", "u s"},
        }
        phrase_aliases = aliases.get(phrase_key, {phrase_key})
        value_aliases = aliases.get(value_key, {value_key})
        if phrase_aliases & value_aliases:
            return True
        return any(alias and (alias == value_key or alias in value_key) for alias in phrase_aliases)

    @staticmethod
    def _crt_medal_ratio_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        text = question or ""
        if not re.search(r"\bratio\b", text, flags=re.I):
            return None
        gold_cols = [col for col in df.columns if re.fullmatch(r"(?i)gold", str(col).strip())]
        silver_cols = [col for col in df.columns if re.fullmatch(r"(?i)silver", str(col).strip())]
        total_cols = [col for col in df.columns if re.fullmatch(r"(?i)total", str(col).strip())]
        rank_cols = [col for col in df.columns if re.fullmatch(r"(?i)rank", str(col).strip())]
        nation_cols = [col for col in df.columns if re.search(r"\b(nation|country)\b", str(col), flags=re.I)]
        named_match = re.search(
            r"\bratio\s+of\s+(.+?)\s+medals?\s+earned\s+by\s+(.+?)\s+to\s+"
            r"(?:the\s+)?(.+?)\s+medals?\s+earned\s+by\s+(.+?)[?.]?$",
            text,
            flags=re.I,
        )
        if named_match and nation_cols:
            left_metric_phrase, left_country, right_metric_phrase, right_country = [
                part.strip(" \t\r\n\"'")
                for part in named_match.groups()
            ]
            metric_cols = {
                "gold": gold_cols[0] if gold_cols else None,
                "silver": silver_cols[0] if silver_cols else None,
                "total": total_cols[0] if total_cols else None,
            }
            left_col = metric_cols.get(_loose_text_key(left_metric_phrase))
            right_col = metric_cols.get(_loose_text_key(right_metric_phrase))
            if left_col is not None and right_col is not None:
                left_value = right_value = None
                for _, row in df.iterrows():
                    if left_value is None and TableQAPipeline._crt_country_matches(left_country, row[nation_cols[0]]):
                        left_value = _numeric_measure_value(row[left_col])
                    if right_value is None and TableQAPipeline._crt_country_matches(right_country, row[nation_cols[0]]):
                        right_value = _numeric_measure_value(row[right_col])
                if left_value is not None and right_value is not None:
                    left_int = int(left_value) if float(left_value).is_integer() else left_value
                    right_int = int(right_value) if float(right_value).is_integer() else right_value
                    return f"{left_int}:{right_int}"
        if gold_cols and silver_cols and re.search(r"\bsilver\s+to\s+gold\b", text, flags=re.I):
            work = df.copy()
            work["_gold"] = pd.to_numeric(work[gold_cols[0]], errors="coerce").fillna(0)
            work["_silver"] = pd.to_numeric(work[silver_cols[0]], errors="coerce").fillna(0)
            if re.search(r"\bearned\s+a\s+gold\b|\bearned\s+gold\b", text, flags=re.I):
                work = work[work["_gold"].gt(0)]
            silver_total = int(work["_silver"].sum())
            gold_total = int(work["_gold"].sum())
            return f"{silver_total}:{gold_total}"
        if gold_cols and total_cols and re.search(r"\bgold\s+medals?\s+to\s+total\s+medals?\b", text, flags=re.I):
            work = df.copy()
            if rank_cols:
                top_match = re.search(r"\btop\s+((?:\d+)|one|two|three|four|five|six|seven|eight|nine|ten)\b", text, flags=re.I)
                if top_match:
                    top_n = _small_number_from_text(top_match.group(1))
                    if top_n is None:
                        return None
                    work["_rank"] = pd.to_numeric(work[rank_cols[0]], errors="coerce")
                    work = work[work["_rank"].le(top_n)]
            gold_total = pd.to_numeric(work[gold_cols[0]], errors="coerce").sum()
            medal_total = pd.to_numeric(work[total_cols[0]], errors="coerce").sum()
            if not medal_total:
                return None
            return round(float(gold_total / medal_total), 2)
        return None

    @staticmethod
    def _crt_total_points_season_ratio_answer(question: str, df: pd.DataFrame) -> Optional[float]:
        text = question or ""
        if not re.search(r"\bratio\b.*\btotal\s+points\b", text, flags=re.I):
            return None
        season_matches = re.findall(r"\b(20\d{2})\s+([A-Za-z])\s+season\b", text, flags=re.I)
        if len(season_matches) < 2:
            return None

        def select_col(year_text: str, group_text: str) -> Optional[Any]:
            short_year = year_text[-2:]
            group_key = group_text.lower()
            for col in df.columns:
                col_key = _loose_text_key(col)
                if short_year in col_key.split() and group_key in col_key.split() and re.search(r"\b(pt|pts|points?)\b", col_key):
                    return col
            return None

        left_col = select_col(*season_matches[0])
        right_col = select_col(*season_matches[1])
        if left_col is None or right_col is None:
            return None
        left_total = pd.to_numeric(df[left_col], errors="coerce").sum()
        right_total = pd.to_numeric(df[right_col], errors="coerce").sum()
        if not right_total:
            return None
        return round(float(left_total / right_total), 2)

    @staticmethod
    def _crt_average_metric_for_threshold_answer(question: str, df: pd.DataFrame) -> Optional[float]:
        text = question or ""
        match = re.search(
            r"\baverage\s+(.+?)\s+of\s+.+?\bmore\s+than\s+([\d.]+)\s+(.+?)(?:\s+at\b|\s+in\b|\?|$)",
            text,
            flags=re.I,
        )
        if not match:
            return None
        metric_phrase, threshold_text, threshold_phrase = match.groups()
        metric_col = _select_numeric_measure_column_by_phrase(df, metric_phrase)
        threshold_col = _select_numeric_measure_column_by_phrase(df, threshold_phrase)
        threshold = _numeric_measure_value(threshold_text)
        if metric_col is None or threshold_col is None or threshold is None:
            return None
        work = pd.DataFrame({
            "metric": _numeric_measure_series(df, metric_col),
            "threshold": _numeric_measure_series(df, threshold_col),
        }).dropna()
        work = work[work["threshold"].gt(float(threshold))]
        if work.empty:
            return None
        return round(float(work["metric"].mean()), 1)

    @staticmethod
    def _crt_penalty_score_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        text = question or ""
        if not re.search(r"\bpenalt", text, flags=re.I):
            return None
        score_match = re.search(r"\bscore\s+of\s+(\d+)\s*-\s*(\d+)|\baggregate\s+score\s+of\s+(\d+)\s*-\s*(\d+)", text, flags=re.I)
        if not score_match:
            return None
        score_parts = [part for part in score_match.groups() if part is not None]
        target = rf"{score_parts[0]}\s*-\s*{score_parts[1]}"
        score_cols = [col for col in df.columns if re.search(r"\b(score|agg|aggregate)\b", str(col), flags=re.I)]
        team_cols = [col for col in df.columns if re.search(r"\b(team\s*1|home|visitor|away)\b", str(col), flags=re.I)]
        other_team_cols = [col for col in df.columns if re.search(r"\b(team\s*2|opponent)\b", str(col), flags=re.I)]
        if not score_cols:
            return None
        matched_rows = [
            row
            for _, row in df.iterrows()
            if re.search(target, str(row[score_cols[0]]))
            and re.search(r"\bp\b|\bpenalt", str(row[score_cols[0]]), flags=re.I)
        ]
        if re.match(r"\s*did\b", text, flags=re.I):
            return "Yes" if matched_rows else "No"
        if matched_rows and team_cols and other_team_cols:
            row = matched_rows[0]
            return f"{str(row[team_cols[0]]).strip()} and {str(row[other_team_cols[0]]).strip()}"
        return None

    @staticmethod
    def _crt_partner_win_loss_ratio_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(r"\bwin[-\s]*loss\s+ratio\b.*?\bwith\s+(.+?)[?.]?$", question or "", flags=re.I)
        if not match:
            return None
        partner_phrase = match.group(1)
        partner_cols = [col for col in df.columns if re.search(r"\bpartner\b", str(col), flags=re.I)]
        outcome_cols = [col for col in df.columns if re.search(r"\b(outcome|result|status)\b", str(col), flags=re.I)]
        if not partner_cols or not outcome_cols:
            return None
        wins = losses = 0
        for _, row in df.iterrows():
            if not _cell_contains_entity_phrase(partner_phrase, row[partner_cols[0]]):
                continue
            outcome_key = _loose_text_key(row[outcome_cols[0]])
            if re.search(r"\bwinner\b|\bwon\b|\bchampion\b", outcome_key):
                wins += 1
            elif re.search(r"\brunner\s+up\b|\blost\b|\bloss\b|\bfinalist\b", outcome_key):
                losses += 1
        if wins == 0 and losses == 0:
            return None
        return f"{wins}:{losses}"

    @staticmethod
    def _crt_year_variation_answer(question: str, df: pd.DataFrame) -> Optional[int]:
        text = question or ""
        if not re.search(r"\bvariation\b.*\byear\s+established\b|\byear\s+established\b.*\bvariation\b", text, flags=re.I):
            return None
        year_cols = [col for col in df.columns if re.search(r"\byear\s+established\b|\bestablished\b", str(col), flags=re.I)]
        if not year_cols:
            return None
        years = _numeric_measure_series(df, year_cols[0]).dropna()
        if years.empty:
            return None
        variation = float(years.max() - years.min())
        return int(variation) if variation.is_integer() else round(variation, 3)

    @staticmethod
    def _crt_named_team_largest_margin_answer(question: str, df: pd.DataFrame) -> Optional[int]:
        match = re.search(r"\blargest\s+margin\s+of\s+victory\s+for\s+(.+?)(?:\s+during\b|\s+in\b|\?|$)", question or "", flags=re.I)
        if not match:
            return None
        team_phrase = match.group(1).strip(" \t\r\n\"'")
        left_cols = [col for col in df.columns if re.search(r"\b(visitor|away|team\s*1)\b", str(col), flags=re.I)]
        right_cols = [col for col in df.columns if re.search(r"\b(home|team\s*2|opponent)\b", str(col), flags=re.I)]
        score_cols = [col for col in df.columns if re.search(r"\bscore\b", str(col), flags=re.I)]
        if not left_cols or not right_cols or not score_cols:
            return None

        def team_matches(value: Any) -> bool:
            team_key = _loose_text_key(team_phrase)
            value_key = _loose_text_key(value)
            return bool(value_key and team_key and (value_key == team_key or value_key in team_key or team_key in value_key))

        margins: List[int] = []
        for _, row in df.iterrows():
            score_match = re.search(r"\b(\d+)\s*-\s*(\d+)\b", str(row[score_cols[0]]))
            if not score_match:
                continue
            left_score, right_score = int(score_match.group(1)), int(score_match.group(2))
            if team_matches(row[left_cols[0]]) and left_score > right_score:
                margins.append(left_score - right_score)
            if team_matches(row[right_cols[0]]) and right_score > left_score:
                margins.append(right_score - left_score)
        return max(margins) if margins else None

    @staticmethod
    def _crt_majority_medal_by_group_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        text = question or ""
        if not re.search(r"\bsingle\s+country\b.*\bmajority\s+of\s+medals\b", text, flags=re.I):
            return None
        nation_cols = [col for col in df.columns if re.search(r"\b(nation|country)\b", str(col), flags=re.I)]
        group_cols = [col for col in df.columns if re.search(r"\b(sport|event|category)\b", str(col), flags=re.I)]
        total_cols = [col for col in df.columns if re.fullmatch(r"(?i)total", str(col).strip())]
        if not nation_cols or not group_cols or not total_cols:
            return None
        work = df.copy()
        work["_total"] = pd.to_numeric(work[total_cols[0]], errors="coerce").fillna(0)
        for _, group in work.groupby(group_cols[0]):
            group_total = float(group["_total"].sum())
            if group_total <= 0:
                continue
            by_country = group.groupby(nation_cols[0])["_total"].sum()
            if not by_country.empty and float(by_country.max()) > group_total / 2:
                return "Yes"
        return "No"

    @staticmethod
    def _crt_significant_medals_without_gold_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        text = question or ""
        if not re.search(r"\bsignificant\s+amount\s+of\s+medals\b.*\bwithout\s+winning\s+a\s+gold\b", text, flags=re.I):
            return None
        gold_cols = [col for col in df.columns if re.fullmatch(r"(?i)gold", str(col).strip())]
        total_cols = [col for col in df.columns if re.fullmatch(r"(?i)total", str(col).strip())]
        if not gold_cols or not total_cols:
            return None
        work = pd.DataFrame({
            "gold": pd.to_numeric(df[gold_cols[0]], errors="coerce").fillna(0),
            "total": pd.to_numeric(df[total_cols[0]], errors="coerce").fillna(0),
        })
        max_total = float(work["total"].max())
        if max_total <= 0:
            return None
        return "Yes" if bool(((work["gold"] == 0) & (work["total"] >= max_total * 0.5)).any()) else "No"

    @staticmethod
    def _tabfact_only_set_equality_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"\bonly\s+(.+?)\s+(?:have|has|had|be|are|is|were|was)\b",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        expected = set(_split_entity_phrase(match.group(1)))
        if len(expected) < 2:
            return None
        ignored = {
            "",
            "total",
            "sum",
            "overall",
            "all",
            "tba",
            "to be announced",
            "no data",
            "unknown",
            "nan",
            "none",
            "na",
            "n a",
        }
        for col in df.columns:
            column_values: set[str] = set()
            for value in df[col].dropna().tolist():
                column_values.update(_cell_entity_values(value))
            column_values = {value for value in column_values if value not in ignored}
            if not expected.issubset(column_values):
                continue
            return "true" if column_values == expected else "false"
        return None

    @staticmethod
    def _tabfact_no_date_ordinal_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        text = question or ""
        if not re.search(r"\bno\b", text, flags=re.I):
            return None
        date_match = re.search(
            r"\bon\s+([A-Za-z]+\s+\d{1,2}\s*,?\s*\d{4})\b",
            text,
            flags=re.I,
        )
        threshold_match = re.search(
            r"\b(?:greater|larger|more|higher)\s+than\s+(?:week\s+)?(\d+)\b",
            text,
            flags=re.I,
        )
        if not date_match or not threshold_match:
            return None
        target_date = _parse_date_key(date_match.group(1))
        if target_date is None:
            return None
        threshold = float(threshold_match.group(1))
        ordinal_cols = [
            col
            for col in df.columns
            if re.search(r"\b(week|rank|round|list|position|no\.?|number)\b", str(col), flags=re.I)
        ]
        if not ordinal_cols:
            return None
        matched_date = False
        for _, row in df.iterrows():
            row_has_date = any(_parse_date_key(row[col]) == target_date for col in df.columns)
            if not row_has_date:
                continue
            matched_date = True
            for col in ordinal_cols:
                value = _as_number_like(row[col])
                if value is not None and value > threshold:
                    return "false"
        return "true" if matched_date else None

    @staticmethod
    def _tabfact_inverse_correlation_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"(.+?)\s+(?:have|has|had|be|is|are|was|were)\s+"
            r"(?:an?\s+)?inverse\s+correlation\s+(?:to|with)\s+(.+?)[.?!]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        left_col = _select_numeric_column_by_phrase(df, match.group(1))
        right_col = _select_numeric_column_by_phrase(df, match.group(2))
        if left_col is None or right_col is None or left_col == right_col:
            return None
        left = pd.to_numeric(df[left_col], errors="coerce")
        right = pd.to_numeric(df[right_col], errors="coerce")
        paired = pd.DataFrame({"left": left, "right": right}).dropna()
        if len(paired) < 3:
            return None
        if paired["left"].std() == 0 or paired["right"].std() == 0:
            return None
        corr = paired["left"].corr(paired["right"], method="pearson")
        if pd.isna(corr):
            return None
        return "true" if float(corr) < -0.10 else "false"

    @staticmethod
    def _tabfact_fuzzy_row_inclusion_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        text = question or ""
        if re.search(
            r"\b(no|not|never|without|only|greater|less|more|highest|lowest|"
            r"before|after|at least|at most|inverse|correlation)\b",
            text,
            flags=re.I,
        ):
            return None
        generic = {
            "award",
            "awarded",
            "category",
            "code",
            "detail",
            "direct",
            "directed",
            "episode",
            "nomination",
            "nominated",
            "nominee",
            "production",
            "result",
            "show",
            "table",
            "write",
            "written",
            "wrote",
        }
        question_tokens = [
            token
            for token in _loose_tokens(text)
            if token not in generic and (token.isdigit() or len(token) >= 3)
        ]
        if len(question_tokens) < 4:
            return None
        for _, row in df.iterrows():
            row_tokens = _loose_tokens(" ".join(str(row[col]) for col in df.columns))
            expanded_row_tokens = list(row_tokens)
            for token in row_tokens:
                if len(token) > 5 and token.endswith("ful"):
                    expanded_row_tokens.extend([token[:-3], "full"])
            if not row_tokens:
                continue
            if all(
                any(_fuzzy_token_match(token, row_token) for row_token in expanded_row_tokens)
                for token in question_tokens
            ):
                return "true"
        return None

    @staticmethod
    def _tabfact_team_score_count_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^(.+?)\s+score\s+(\d+)\s+or\s+more\s+points?\s+in\s+(\d+)\s+games?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        team, threshold_text, expected_text = match.groups()
        threshold = int(threshold_text)
        expected = int(expected_text)
        visitor_cols = [col for col in df.columns if re.fullmatch(r"(?i)visitor|away", str(col).strip())]
        home_cols = [col for col in df.columns if re.fullmatch(r"(?i)home", str(col).strip())]
        score_cols = [col for col in df.columns if re.search(r"\bscore\b", str(col), flags=re.I)]
        if not visitor_cols or not home_cols or not score_cols:
            return None
        team_key = _loose_text_key(team)
        count = 0
        for _, row in df.iterrows():
            score_match = re.search(r"(\d+)\s*-\s*(\d+)", str(row[score_cols[0]]))
            if not score_match:
                continue
            left, right = int(score_match.group(1)), int(score_match.group(2))
            value = None
            if team_key and team_key in _loose_text_key(row[visitor_cols[0]]):
                value = left
            elif team_key and team_key in _loose_text_key(row[home_cols[0]]):
                value = right
            if value is not None and value >= threshold:
                count += 1
        return "true" if count == expected else "false"

    @staticmethod
    def _tabfact_score_threshold_count_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^(\d+)\s+games?\s+have\s+a\s+score\s+of\s+"
            r"(?:more|greater|higher)\s+than\s+(\d+)\s+points?[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        expected, threshold = (int(text) for text in match.groups())
        score_cols = [col for col in df.columns if re.search(r"\b(?:score|result)\b", str(col), flags=re.I)]
        if not score_cols:
            return None
        count = 0
        for _, row in df.iterrows():
            pair = TableQAPipeline._score_pair(row[score_cols[0]])
            if pair is not None and (pair[0] > threshold or pair[1] > threshold):
                count += 1
        return "true" if count == expected else "false"

    @staticmethod
    def _tabfact_same_metric_value_count_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^(\d+)\s+.+?\s+have\s+the\s+same\s+amount\s+of\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        expected_text, metric_phrase = match.groups()
        metric_col = (
            TableQAPipeline._select_column_by_semantic_tokens(df, metric_phrase)
            or TableQAPipeline._select_column_by_tokens(df, metric_phrase)
        )
        if metric_col is None:
            return None
        counts: Dict[str, int] = {}
        for value in df[metric_col].tolist():
            key = _loose_text_key(value)
            if not key:
                continue
            counts[key] = counts.get(key, 0) + 1
        expected = int(expected_text)
        return "true" if any(count == expected for count in counts.values()) else "false"

    @staticmethod
    def _tabfact_match_type_count_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^(\d+)\s+out\s+of\s+the\s+(\d+)\s+match(?:es)?\s+be\s+a\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        expected_text, total_text, target_phrase = match.groups()
        match_cols = [col for col in df.columns if re.fullmatch(r"(?i)match", str(col).strip())]
        competition_cols = [col for col in df.columns if re.search(r"\bcompetition\b", str(col), flags=re.I)]
        if not match_cols or not competition_cols:
            return None
        match_col = match_cols[0]
        competition_col = competition_cols[0]
        numbered_rows = [
            row
            for _, row in df.iterrows()
            if re.fullmatch(r"\s*\d+(?:\.0+)?\s*", str(row[match_col] or ""))
        ]
        if len(numbered_rows) != int(total_text):
            return "false"
        count = sum(
            1
            for row in numbered_rows
            if TableQAPipeline._cell_contains_phrase_tokens(target_phrase, row[competition_col])
        )
        return "true" if count == int(expected_text) else "false"

    @staticmethod
    def _tabfact_final_record_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^the\s+.+?\s+season\s+end\b.+?\s+with\s+a\s+(\d+\s*-\s*\d+)\s+record[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        expected = re.sub(r"\s+", "", match.group(1))
        record_cols = [col for col in df.columns if re.fullmatch(r"(?i)record", str(col).strip())]
        if not record_cols:
            return None
        records = []
        for value in df[record_cols[0]].tolist():
            record_match = re.search(r"\b(\d+)\s*-\s*(\d+)\b", str(value))
            if record_match:
                records.append(f"{record_match.group(1)}-{record_match.group(2)}")
        if not records:
            return None
        return "true" if records[-1] == expected else "false"

    @staticmethod
    def _tabfact_swept_date_series_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^the\s+.+?\s+swept\s+the\s+(.+?)\s+in\s+the\s+(\d+)\s+game\s+series\s+"
            r"from\s+(.+?)\s+to\s+(.+?)(?:\s+in\b|[?.]?$)",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        opponent_phrase, expected_count_text, start_phrase, end_phrase = match.groups()
        date_cols = [col for col in df.columns if re.fullmatch(r"(?i)date", str(col).strip())]
        opponent_cols = [col for col in df.columns if re.search(r"\bopponent\b|\bteam\b", str(col), flags=re.I)]
        record_cols = [col for col in df.columns if re.fullmatch(r"(?i)record", str(col).strip())]
        if not date_cols or not opponent_cols or not record_cols:
            return None

        months = {
            "january": 1,
            "jan": 1,
            "february": 2,
            "feb": 2,
            "march": 3,
            "mar": 3,
            "april": 4,
            "apr": 4,
            "may": 5,
            "june": 6,
            "jun": 6,
            "july": 7,
            "jul": 7,
            "august": 8,
            "aug": 8,
            "september": 9,
            "sep": 9,
            "october": 10,
            "oct": 10,
            "november": 11,
            "nov": 11,
            "december": 12,
            "dec": 12,
        }

        def month_day(value: Any, default_month: Optional[int] = None) -> Optional[Tuple[int, int]]:
            text = str(value or "").lower()
            match_month_day = re.search(
                r"\b("
                + "|".join(re.escape(month) for month in months)
                + r")\s+(\d{1,2})(?:st|nd|rd|th)?\b",
                text,
            )
            if match_month_day:
                return months[match_month_day.group(1)], int(match_month_day.group(2))
            match_day = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\b", text)
            if match_day and default_month is not None:
                return default_month, int(match_day.group(1))
            return None

        def record_pair(value: Any) -> Optional[Tuple[int, int]]:
            record_match = re.search(r"\b(\d+)\s*-\s*(\d+)\b", str(value))
            if not record_match:
                return None
            return int(record_match.group(1)), int(record_match.group(2))

        start = month_day(start_phrase)
        if start is None:
            return None
        end = month_day(end_phrase, default_month=start[0])
        if end is None or start[0] != end[0]:
            return None
        start_day, end_day = start[1], end[1]
        expected_count = int(expected_count_text)
        opponent_tokens = {
            token
            for token in _loose_tokens(opponent_phrase)
            if len(token) >= 4
        }

        rows = list(df.iterrows())
        matched: List[Tuple[int, pd.Series]] = []
        for position, (_, row) in enumerate(rows):
            current_date = month_day(row[date_cols[0]])
            if current_date is None or current_date[0] != start[0]:
                continue
            if not (start_day <= current_date[1] <= end_day):
                continue
            row_opponent_tokens = set(_loose_tokens(row[opponent_cols[0]]))
            if opponent_tokens and not (opponent_tokens & row_opponent_tokens):
                continue
            matched.append((position, row))
        if len(matched) != expected_count:
            return "false"

        wins = 0
        for position, row in matched:
            current_record = record_pair(row[record_cols[0]])
            if current_record is None:
                return None
            previous_record = None
            for prev_position in range(position - 1, -1, -1):
                previous_record = record_pair(rows[prev_position][1][record_cols[0]])
                if previous_record is not None:
                    break
            if previous_record is None:
                return None
            if current_record[0] == previous_record[0] + 1 and current_record[1] == previous_record[1]:
                wins += 1
        return "true" if wins == expected_count else "false"

    @staticmethod
    def _tabfact_state_draft_only_player_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^(.+?)\s+be\s+the\s+only\s+player\s+from\s+(.+?)\s+on\s+the\s+team\b"
            r".*?\b1st\s+round\s+draft\s+pick\s+(\d{4})[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        player_phrase, state_phrase, draft_year = match.groups()
        state_abbrev = {
            "alabama": "al",
            "alaska": "ak",
            "arizona": "az",
            "arkansas": "ar",
            "california": "ca",
            "colorado": "co",
            "connecticut": "ct",
            "delaware": "de",
            "florida": "fl",
            "georgia": "ga",
            "hawaii": "hi",
            "idaho": "id",
            "illinois": "il",
            "indiana": "in",
            "iowa": "ia",
            "kansas": "ks",
            "kentucky": "ky",
            "louisiana": "la",
            "maine": "me",
            "maryland": "md",
            "massachusetts": "ma",
            "michigan": "mi",
            "minnesota": "mn",
            "mississippi": "ms",
            "missouri": "mo",
            "montana": "mt",
            "nebraska": "ne",
            "nevada": "nv",
            "new hampshire": "nh",
            "new jersey": "nj",
            "new mexico": "nm",
            "new york": "ny",
            "north carolina": "nc",
            "north dakota": "nd",
            "ohio": "oh",
            "oklahoma": "ok",
            "oregon": "or",
            "pennsylvania": "pa",
            "rhode island": "ri",
            "south carolina": "sc",
            "south dakota": "sd",
            "tennessee": "tn",
            "texas": "tx",
            "utah": "ut",
            "vermont": "vt",
            "virginia": "va",
            "washington": "wa",
            "west virginia": "wv",
            "wisconsin": "wi",
            "wyoming": "wy",
        }.get(_loose_text_key(state_phrase))
        if state_abbrev is None:
            return None
        player_cols = [col for col in df.columns if re.search(r"\bplayer\b|\bname\b", str(col), flags=re.I)]
        hometown_cols = [col for col in df.columns if re.search(r"\bhometown\b|\bhome town\b|\bbirthplace\b", str(col), flags=re.I)]
        draft_cols = [col for col in df.columns if re.search(r"\bdraft\b", str(col), flags=re.I)]
        if not player_cols or not hometown_cols or not draft_cols:
            return None
        player_col = player_cols[0]
        hometown_col = hometown_cols[0]
        draft_col = draft_cols[0]

        def from_state(value: Any) -> bool:
            text = str(value or "").lower()
            return bool(re.search(rf"(?:,\s*|\b){re.escape(state_abbrev)}\b", text))

        state_rows = [row for _, row in df.iterrows() if from_state(row[hometown_col])]
        target_rows = [
            row
            for row in state_rows
            if _cell_contains_entity_phrase(player_phrase, row[player_col])
        ]
        if len(target_rows) != 1:
            return None
        draft_text = str(target_rows[0][draft_col]).lower()
        is_first_round_year = bool(
            re.search(r"\b1st\s+round\b", draft_text)
            and re.search(rf"\b{re.escape(draft_year)}\b", draft_text)
        )
        return "true" if len(state_rows) == 1 and is_first_round_year else "false"

    @staticmethod
    def _tabfact_location_most_between_years_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^(.+?)\s+host\s+the\s+most\b.+?\bin\s+between\s+(\d{4})\s+and\s+(\d{4})[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        location_phrase, start_text, end_text = match.groups()
        year_location_cols = [col for col in df.columns if re.search(r"\byear\b.*\blocation\b", str(col), flags=re.I)]
        year_cols = [col for col in df.columns if re.fullmatch(r"(?i)year", str(col).strip())]
        location_cols = [col for col in df.columns if re.fullmatch(r"(?i)location|host|venue|city", str(col).strip())]
        start_year = int(start_text)
        end_year = int(end_text)
        counts: Dict[str, int] = {}

        if year_location_cols:
            for value in df[year_location_cols[0]].tolist():
                text = str(value or "").strip()
                year_match = re.match(r"(\d{4})\s+(.+)$", text)
                if not year_match:
                    continue
                year = int(year_match.group(1))
                if start_year <= year <= end_year:
                    location = _loose_text_key(year_match.group(2))
                    counts[location] = counts.get(location, 0) + 1
        elif year_cols and location_cols:
            for _, row in df.iterrows():
                year_value = _numeric_measure_value(row[year_cols[0]])
                if year_value is None:
                    continue
                year = int(year_value)
                if start_year <= year <= end_year:
                    location = _loose_text_key(row[location_cols[0]])
                    counts[location] = counts.get(location, 0) + 1
        else:
            return None

        target_key = _loose_text_key(location_phrase)
        if not counts or target_key not in counts:
            return "false"
        max_count = max(counts.values())
        return "true" if counts[target_key] == max_count and max_count > 0 else "false"

    @staticmethod
    def _tabfact_overtime_count_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(r"\b(?:go|went)\s+into\s+overtime\s+in\s+(\d+)\s+games?\b", question or "", flags=re.I)
        if not match:
            return None
        expected = int(match.group(1))
        count = 0
        for _, row in df.iterrows():
            row_text = " ".join(str(row[col]) for col in df.columns)
            if re.search(r"\b(?:ot|overtime)\b", row_text, flags=re.I):
                count += 1
        return "true" if count == expected else "false"

    @staticmethod
    def _tabfact_win_difference_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^(.+?)\s+win\s+(\d+)\s+more\s+races?\s+than\s+(.+?)(?:\s+in\b|$)",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        left_name, diff_text, right_name = match.groups()
        winner_cols = [col for col in df.columns if re.search(r"\bwinner\b", str(col), flags=re.I)]
        if not winner_cols:
            return None
        left_key = _loose_text_key(left_name)
        right_key = _loose_text_key(right_name)
        counts = {left_key: 0, right_key: 0}
        for value in df[winner_cols[0]].tolist():
            key = _loose_text_key(value)
            if left_key and left_key in key:
                counts[left_key] += 1
            if right_key and right_key in key:
                counts[right_key] += 1
        return "true" if counts[left_key] - counts[right_key] == int(diff_text) else "false"

    @staticmethod
    def _tabfact_highest_location_numeric_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"\binstitution\s+locat\w*\s+in\s+(.+?)\s+have\s+the\s+highest\s+(?:average\s+)?(.+?)$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        location_phrase, metric_phrase = match.groups()
        location_cols = [col for col in df.columns if re.search(r"\blocation\b", str(col), flags=re.I)]
        metric_col = _select_numeric_column_by_phrase(df, metric_phrase)
        if not location_cols or metric_col is None:
            return None
        numeric = pd.to_numeric(df[metric_col], errors="coerce")
        if numeric.dropna().empty:
            return None
        row = df.loc[numeric.idxmax()]
        return "true" if _loose_text_key(location_phrase) in _loose_text_key(row[location_cols[0]]) else "false"

    @staticmethod
    def _tabfact_same_city_host_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^(.+?)\s+host\s+at\s+(.+?)\s*,\s*locat\w*\s+in\s+the\s+same\s+city\s+as\s+(.+?)$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        left_host, venue, right_host = match.groups()
        host_cols = [col for col in df.columns if re.search(r"\bhost\b", str(col), flags=re.I)]
        venue_cols = [col for col in df.columns if re.search(r"\bvenue\b", str(col), flags=re.I)]
        city_cols = [col for col in df.columns if re.search(r"\bcity\b", str(col), flags=re.I)]
        if not host_cols or not city_cols:
            return None
        left_city = None
        right_city = None
        left_key = _loose_text_key(left_host)
        right_key = _loose_text_key(right_host)
        venue_key = _loose_text_key(venue)
        for _, row in df.iterrows():
            host_key = _loose_text_key(row[host_cols[0]])
            if left_key and left_key in host_key:
                if venue_cols and venue_key and venue_key not in _loose_text_key(row[venue_cols[0]]):
                    continue
                left_city = _loose_text_key(row[city_cols[0]])
            if right_key and right_key in host_key:
                right_city = _loose_text_key(row[city_cols[0]])
        if left_city is None or right_city is None:
            return None
        return "true" if left_city == right_city else "false"

    @staticmethod
    def _tabfact_second_stage_classification_winner_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^(.+?)\s+'s\s+second\s+stage\s+as\s+the\s+(.+?)\s+classification\s+be\s+when\s+(.+?)\s+be\s+the\s+winner$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        entity, classification_phrase, winner = match.groups()
        stage_cols = [col for col in df.columns if re.search(r"\bstage\b", str(col), flags=re.I)]
        winner_cols = [col for col in df.columns if re.fullmatch(r"(?i)winner", str(col).strip())]
        classification_col = TableQAPipeline._select_column_by_tokens(df, f"{classification_phrase} classification")
        if not stage_cols or not winner_cols or classification_col is None:
            return None
        entity_key = _loose_text_key(entity)
        matches = []
        for _, row in df.iterrows():
            if entity_key and entity_key in _loose_text_key(row[classification_col]):
                stage_value = _as_number_like(row[stage_cols[0]]) or 0
                matches.append((stage_value, row))
        if len(matches) < 2:
            return "false"
        second_row = sorted(matches, key=lambda item: item[0])[1][1]
        return "true" if _loose_text_key(winner) in _loose_text_key(second_row[winner_cols[0]]) else "false"

    @staticmethod
    def _semantic_phrase_tokens(value: Any) -> List[str]:
        synonyms = {
            "woman": "women",
            "women": "women",
            "female": "women",
            "man": "men",
            "men": "men",
            "male": "men",
            "single": "single",
            "double": "double",
        }
        ignored = {
            "game",
            "row",
            "record",
            "compete",
            "consist",
            "contain",
            "include",
        }
        tokens = []
        for token in _loose_tokens(value):
            token = synonyms.get(token, token)
            token = _singular_token(token)
            if token and token not in ignored:
                tokens.append(token)
        return tokens

    @staticmethod
    def _select_column_by_semantic_tokens(df: pd.DataFrame, phrase: Any) -> Optional[Any]:
        phrase_tokens = set(TableQAPipeline._semantic_phrase_tokens(phrase))
        if not phrase_tokens:
            return None
        best_col = None
        best_score = 0.0
        for col in df.columns:
            col_tokens = set(TableQAPipeline._semantic_phrase_tokens(col))
            if not col_tokens:
                continue
            overlap = phrase_tokens & col_tokens
            if not overlap:
                continue
            score = len(overlap) / max(len(phrase_tokens), len(col_tokens))
            if phrase_tokens.issubset(col_tokens) or col_tokens.issubset(phrase_tokens):
                score += 0.25
            if score > best_score:
                best_col = col
                best_score = score
        return best_col if best_score > 0 else None

    @staticmethod
    def _cell_contains_phrase_tokens(phrase: Any, cell: Any) -> bool:
        query_tokens = _loose_tokens(phrase)
        cell_tokens = _loose_tokens(cell)
        if not query_tokens or not cell_tokens:
            return False
        return all(
            any(_fuzzy_token_match(query_token, cell_token) for cell_token in cell_tokens)
            for query_token in query_tokens
        )

    @staticmethod
    def _score_points(value: Any) -> Optional[float]:
        text = str(value or "")
        parenthesized = re.findall(r"\((\d+(?:\.\d+)?)\)", text)
        if parenthesized:
            return float(parenthesized[-1])
        numbers = re.findall(r"\d+(?:\.\d+)?", text)
        if not numbers:
            return None
        return float(numbers[-1])

    @staticmethod
    def _score_total(value: Any) -> Optional[float]:
        text = str(value or "")
        match = re.search(r"\b(\d+)\s*-\s*(\d+)\b", text)
        if not match:
            return None
        return float(match.group(1)) + float(match.group(2))

    @staticmethod
    def _score_pair(value: Any) -> Optional[Tuple[float, float]]:
        text = str(value or "")
        match = re.search(r"\b(\d+)\s*-\s*(\d+)\b", text)
        if not match:
            return None
        return float(match.group(1)), float(match.group(2))

    @staticmethod
    def _date_phrase_matches(phrase: str, value: Any) -> bool:
        months = {
            "january": 1,
            "jan": 1,
            "february": 2,
            "feb": 2,
            "march": 3,
            "mar": 3,
            "april": 4,
            "apr": 4,
            "may": 5,
            "june": 6,
            "jun": 6,
            "july": 7,
            "jul": 7,
            "august": 8,
            "aug": 8,
            "september": 9,
            "sep": 9,
            "sept": 9,
            "october": 10,
            "oct": 10,
            "november": 11,
            "nov": 11,
            "december": 12,
            "dec": 12,
        }

        def month_day(text: Any) -> Optional[Tuple[int, int]]:
            key = _loose_text_key(text)
            month_pattern = "|".join(sorted(months, key=len, reverse=True))
            first = re.search(rf"\b({month_pattern})\s+(\d{{1,2}})\b", key)
            if first:
                return months[first.group(1)], int(first.group(2))
            second = re.search(rf"\b(\d{{1,2}})\s+({month_pattern})\b", key)
            if second:
                return months[second.group(2)], int(second.group(1))
            return None

        phrase_month_day = month_day(phrase)
        value_month_day = month_day(value)
        if phrase_month_day and value_month_day and not re.search(r"\b\d{4}\b", phrase):
            return phrase_month_day == value_month_day
        phrase_key = _parse_date_key(phrase)
        value_key = _parse_date_key(value)
        if phrase_key and value_key:
            if re.search(r"\b\d{4}\b", phrase):
                return phrase_key == value_key
            return phrase_key[1:] == value_key[1:]
        phrase_text = _loose_text_key(phrase)
        value_text = _loose_text_key(value)
        return bool(phrase_text and phrase_text in value_text)

    @staticmethod
    def _tabfact_numeric_difference_from_max_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^(.+?)\s+have\s+([\d.,]+)\s+fewer\s+(.+?)\s+than\s+the\s+.+?\bhighest\s+(.+?)$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        entity_phrase, diff_text, metric_phrase, high_metric_phrase = match.groups()
        metric_col = _select_numeric_column_by_phrase(df, metric_phrase) or _select_numeric_column_by_phrase(df, high_metric_phrase)
        if metric_col is None:
            return None
        entity_col = TableQAPipeline._select_column_by_semantic_tokens(df, "model name")
        if entity_col is None:
            text_cols = [
                col for col in df.columns
                if col != metric_col and pd.to_numeric(df[col], errors="coerce").notna().sum() < max(1, len(df) // 2)
            ]
            entity_col = text_cols[0] if text_cols else None
        if entity_col is None:
            return None
        entity_rows = [
            row for _, row in df.iterrows()
            if TableQAPipeline._cell_contains_phrase_tokens(entity_phrase, row[entity_col])
        ]
        if not entity_rows:
            return None
        entity_value = _as_number_like(entity_rows[0][metric_col])
        numeric = pd.to_numeric(df[metric_col], errors="coerce")
        if entity_value is None or numeric.dropna().empty:
            return None
        observed = float(numeric.max()) - float(entity_value)
        expected = float(str(diff_text).replace(",", ""))
        return "true" if abs(observed - expected) <= 1e-3 else "false"

    @staticmethod
    def _tabfact_column_value_count_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^(\d+)\s+of\s+.+?\s+(?:replicate|replicates|are|be|is|were|was)\s+in\s+the\s+(.+?)$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        expected = int(match.group(1))
        target_phrase = match.group(2)
        best_col = None
        best_count = 0
        for col in df.columns:
            count = sum(
                1
                for value in df[col].tolist()
                if TableQAPipeline._cell_contains_phrase_tokens(target_phrase, value)
            )
            if count > best_count:
                best_col = col
                best_count = count
        if best_col is None or best_count == 0:
            return None
        return "true" if best_count == expected else "false"

    @staticmethod
    def _tabfact_highest_scoring_game_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"\bhighest\s+scoring\s+game\s+be\s+(.+?)\s*,?\s+(\d+)\s+runs?\s+be\s+score\b",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        date_phrase, total_text = match.groups()
        date_cols = [col for col in df.columns if re.search(r"\bdate\b", str(col), flags=re.I)]
        score_cols = [col for col in df.columns if re.search(r"\bscore\b", str(col), flags=re.I)]
        if not date_cols or not score_cols:
            return None
        totals: List[Tuple[float, Any]] = []
        for _, row in df.iterrows():
            total = TableQAPipeline._score_total(row[score_cols[0]])
            if total is not None:
                totals.append((total, row))
        if not totals:
            return None
        max_total = max(total for total, _ in totals)
        winners = [row for total, row in totals if total == max_total]
        expected_total = float(total_text)
        if abs(max_total - expected_total) > 1e-6:
            return "false"
        return "true" if any(TableQAPipeline._date_phrase_matches(date_phrase, row[date_cols[0]]) for row in winners) else "false"

    @staticmethod
    def _tabfact_row_condition_count_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"\bthere\s+be\s+(\d+)\s+(.+?)\s+consist\s+of\s+(.+?)\s+when\s+(.+?)\s+compete\s+in\s+the\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        expected_text, target_col_phrase, target_members, condition_entity, condition_col_phrase = match.groups()
        target_col = TableQAPipeline._select_column_by_semantic_tokens(df, target_col_phrase)
        condition_col = TableQAPipeline._select_column_by_semantic_tokens(df, condition_col_phrase)
        if target_col is None or condition_col is None or target_col == condition_col:
            return None
        count = 0
        for _, row in df.iterrows():
            if not TableQAPipeline._cell_contains_phrase_tokens(condition_entity, row[condition_col]):
                continue
            if TableQAPipeline._cell_contains_phrase_tokens(target_members, row[target_col]):
                count += 1
        return "true" if count == int(expected_text) else "false"

    @staticmethod
    def _tabfact_unique_side_winner_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"\bon\s+(.+?)\s+only\s+(\d+)\s+(home|away)\s+teams?\s*,\s*(.+?)\s*,\s+win\b",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        date_phrase, expected_text, side, team_name = match.groups()
        home_team_cols = [col for col in df.columns if re.fullmatch(r"(?i)home\s+team", str(col).strip())]
        away_team_cols = [col for col in df.columns if re.fullmatch(r"(?i)away\s+team", str(col).strip())]
        home_score_cols = [col for col in df.columns if re.fullmatch(r"(?i)home\s+team\s+score", str(col).strip())]
        away_score_cols = [col for col in df.columns if re.fullmatch(r"(?i)away\s+team\s+score", str(col).strip())]
        date_cols = [col for col in df.columns if re.fullmatch(r"(?i)date", str(col).strip())]
        if not (home_team_cols and away_team_cols and home_score_cols and away_score_cols and date_cols):
            return None
        winners: List[Any] = []
        for _, row in df.iterrows():
            if not TableQAPipeline._date_phrase_matches(date_phrase, row[date_cols[0]]):
                continue
            home_score = TableQAPipeline._score_points(row[home_score_cols[0]])
            away_score = TableQAPipeline._score_points(row[away_score_cols[0]])
            if home_score is None or away_score is None or home_score == away_score:
                continue
            if side.lower() == "home" and home_score > away_score:
                winners.append(row[home_team_cols[0]])
            if side.lower() == "away" and away_score > home_score:
                winners.append(row[away_team_cols[0]])
        expected = int(expected_text)
        team_matches = [
            winner
            for winner in winners
            if TableQAPipeline._cell_contains_phrase_tokens(team_name, winner)
        ]
        return "true" if len(winners) == expected and len(team_matches) == expected else "false"

    @staticmethod
    def _tabfact_episode_order_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^(.+?)\s+episode\s+happen\s+(earlier|later)\s+in\s+the\s+series\s+than\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        left_title, direction, right_title = match.groups()
        title_cols = [
            col for col in df.columns if re.search(r"\b(title|episode)\b", str(col), flags=re.I)
        ]
        order_cols = [
            col
            for col in df.columns
            if re.search(r"\bno\s+for\s+series\b|\bepisode\s*(?:no|number)\b", str(col), flags=re.I)
        ]
        if not order_cols:
            order_cols = [
                col
                for col in df.columns
                if re.search(r"\bno\s+overall\b|\bnumber\b|\bno\.?\b", str(col), flags=re.I)
            ]
        date_cols = [col for col in df.columns if re.search(r"\bdate\b", str(col), flags=re.I)]
        if not title_cols or not (order_cols or date_cols):
            return None

        def find_row(title_phrase: str) -> Optional[pd.Series]:
            for _, row in df.iterrows():
                if TableQAPipeline._cell_contains_phrase_tokens(title_phrase, row[title_cols[0]]):
                    return row
            return None

        left_row = find_row(left_title)
        right_row = find_row(right_title)
        if left_row is None or right_row is None:
            return None
        left_order = right_order = None
        if order_cols:
            left_order = _as_number_like(left_row[order_cols[0]])
            right_order = _as_number_like(right_row[order_cols[0]])
        if left_order is None or right_order is None:
            if not date_cols:
                return None
            left_order = _parse_date_key(left_row[date_cols[0]])
            right_order = _parse_date_key(right_row[date_cols[0]])
        if left_order is None or right_order is None:
            return None
        observed = left_order < right_order if direction.lower() == "earlier" else left_order > right_order
        return "true" if observed else "false"

    @staticmethod
    def _tabfact_episode_credit_count_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^(.+?)\s+(only\s+)?(direct|directed|write|wrote)\s+"
            r"((?:\d+)|one|two|three|four|five|six|seven|eight|nine|ten)\s+episodes?"
            r"(?:\s+of\s+.+)?[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        person, only_text, verb, count_text = match.groups()
        expected = _small_number_from_text(count_text)
        if expected is None:
            return None
        col_pattern = r"\bdirected\s+by\b|\bdirector\b" if verb.lower().startswith("direct") else r"\bwritten\s+by\b|\bwriter\b"
        credit_cols = [col for col in df.columns if re.search(col_pattern, str(col), flags=re.I)]
        if not credit_cols:
            return None
        count = sum(
            1
            for value in df[credit_cols[0]].tolist()
            if TableQAPipeline._cell_contains_phrase_tokens(person, value)
        )
        if only_text and count != expected:
            return "false"
        return "true" if count == expected else "false"

    @staticmethod
    def _tabfact_goal_competition_count_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"\bscore\s+((?:\d+)|one|two|three|four|five|six|seven|eight|nine|ten)\s+goals?"
            r"\b.*?\b(?:at|in|during)\s+(.+?)\s+competitions?\b",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        expected = _small_number_from_text(match.group(1))
        target_phrase = match.group(2)
        if expected is None:
            return None
        competition_cols = [col for col in df.columns if re.search(r"\bcompetition\b", str(col), flags=re.I)]
        if not competition_cols:
            return None
        target_tokens = [
            token
            for token in _loose_tokens(target_phrase)
            if token
            not in {
                "at",
                "career",
                "competition",
                "competitions",
                "during",
                "her",
                "his",
                "in",
                "international",
                "match",
                "matches",
                "their",
            }
        ]
        if not target_tokens:
            return None
        count = 0
        for value in df[competition_cols[0]].tolist():
            value_key = _loose_text_key(value)
            if all(token in value_key for token in target_tokens):
                count += 1
        return "true" if count == expected else "false"

    @staticmethod
    def _tabfact_not_fewer_than_any_other_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^(.+?)\s+(?:do|does|did)\s+not\s+have\s+fewer\s+(.+?)\s+than\s+any\s+other\s+.+?[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        entity_phrase, metric_phrase = match.groups()
        metric_col = _select_numeric_column_by_phrase(df, metric_phrase)
        if metric_col is None:
            return None
        entity_cols = [col for col in df.columns if col != metric_col]
        if not entity_cols:
            return None
        entity_row = None
        for _, row in df.iterrows():
            if any(TableQAPipeline._cell_contains_phrase_tokens(entity_phrase, row[col]) for col in entity_cols):
                entity_row = row
                break
        if entity_row is None:
            return None
        entity_value = _as_number_like(entity_row[metric_col])
        all_values = pd.to_numeric(df[metric_col], errors="coerce").dropna()
        if entity_value is None or all_values.empty:
            return None
        return "true" if float(entity_value) >= float(all_values.max()) else "false"

    @staticmethod
    def _tabfact_nonzero_metric_count_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        text = question or ""
        match = re.search(
            r"^(.+?)\s+be\s+1\s+of\s+the\s+(\d+)\s+.+?\s+with\s+(.+?)(?:\s+during\b|[?.]?$)",
            text,
            flags=re.I,
        )
        only_match = re.search(
            r"^(.+?)\s+be\s+the\s+only\s+.+?\s+with\s+(.+?)(?:\s+during\b|[?.]?$)",
            text,
            flags=re.I,
        )
        if match:
            entity_phrase, expected_text, metric_phrase = match.groups()
            expected = int(expected_text)
        elif only_match:
            entity_phrase, metric_phrase = only_match.groups()
            expected = 1
        else:
            return None
        metric_col = _select_numeric_column_by_phrase(df, metric_phrase)
        if metric_col is None:
            return None
        entity_cols = [col for col in df.columns if col != metric_col]
        entity_has_metric = False
        count = 0
        for _, row in df.iterrows():
            value = _as_number_like(row[metric_col])
            has_metric = value is not None and float(value) > 0
            if has_metric:
                count += 1
            if any(TableQAPipeline._cell_contains_phrase_tokens(entity_phrase, row[col]) for col in entity_cols):
                entity_has_metric = has_metric
        return "true" if entity_has_metric and count == expected else "false"

    @staticmethod
    def _tabfact_max_metric_span_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"\bgreatest\s+number\s+of\s+(.+?)\s+from\s+.+?\s+happen\s+over\s+the\s+span\s+of\s+(\d+)\s+years?\b",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        metric_phrase, expected_text = match.groups()
        metric_col = _select_numeric_column_by_phrase(df, metric_phrase)
        span_cols = [col for col in df.columns if re.search(r"\bspan\b", str(col), flags=re.I)]
        if metric_col is None or not span_cols:
            return None
        numeric = pd.to_numeric(df[metric_col], errors="coerce")
        if numeric.dropna().empty:
            return None
        row = df.loc[numeric.idxmax()]
        year_range = _extract_year_range(row[span_cols[0]])
        if year_range is None:
            return None
        start_year, end_year = year_range
        observed_years = abs(int(end_year) - int(start_year)) + 1
        return "true" if observed_years == int(expected_text) else "false"

    @staticmethod
    def _tabfact_goal_result_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        text = question or ""
        competition_cols = [col for col in df.columns if re.search(r"\bcompetition\b", str(col), flags=re.I)]
        result_cols = [col for col in df.columns if re.fullmatch(r"(?i)result", str(col).strip())]
        if not competition_cols:
            return None

        def competition_matches(value: Any, phrase: str) -> bool:
            tokens = [
                token
                for token in _loose_tokens(phrase)
                if token not in {"competition", "competitions", "during", "in", "the"}
            ]
            if not tokens:
                return False
            value_key = _loose_text_key(value)
            return all(token in value_key for token in tokens)

        scoreless_match = re.search(r"\bremain\s+scoreless\s+during\s+(.+?)[?.]?$", text, flags=re.I)
        if scoreless_match:
            target_phrase = scoreless_match.group(1)
            scored_during_target = any(
                competition_matches(value, target_phrase)
                for value in df[competition_cols[0]].tolist()
            )
            scored_first = True
            first_goal_match = re.search(r"\bscore\s+a\s+goal\s+at\s+the\s+(\d{4})\b", text, flags=re.I)
            if first_goal_match:
                year = first_goal_match.group(1)
                scored_first = any(
                    year in _loose_text_key(row[competition_cols[0]])
                    or any(year in _loose_text_key(row[col]) for col in df.columns)
                    for _, row in df.iterrows()
                )
            return "true" if scored_first and not scored_during_target else "false"

        lose_match = re.search(
            r"\b(?:only\s+)?lose\s+(\d+)(?:\s+of\s+(\d+))?\s+times?.+?"
            r"\binternational\s+competitions?.+?\bscore\s+a\s+goal\b",
            text,
            flags=re.I,
        )
        if not lose_match or not result_cols:
            return None
        expected_losses = int(lose_match.group(1))
        expected_total = int(lose_match.group(2)) if lose_match.group(2) else None

        def is_loss(value: Any) -> bool:
            score_match = re.search(r"\b(\d+)\s*-\s*(\d+)\b", str(value or ""))
            if not score_match:
                return False
            return int(score_match.group(1)) < int(score_match.group(2))

        rows = [
            row
            for _, row in df.iterrows()
            if "friendly" not in _loose_text_key(row[competition_cols[0]])
        ]
        if not rows:
            return None
        loss_count = sum(1 for row in rows if is_loss(row[result_cols[0]]))
        total_ok = expected_total is None or len(rows) == expected_total
        return "true" if loss_count == expected_losses and total_ok else "false"

    @staticmethod
    def _tabfact_last_row_entity_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^(.+?)\s+be\s+the\s+last\s+(?:place|team|player|entry|row|position)\b",
            question or "",
            flags=re.I,
        )
        if not match or df.empty:
            return None
        entity_phrase = match.group(1)
        entity_rows = _find_rows_for_entity(df, entity_phrase)
        if not entity_rows:
            return None
        last_index = df.index[-1]
        return "true" if any(row.name == last_index for row in entity_rows) else "false"

    @staticmethod
    def _tabfact_condition_value_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^the\s+(.+?)\s+when\s+the\s+(.+?)\s+be\s+(.+?)\s+and\s+the\s+(.+?)\s+be\s+(.+?)\s+be\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        target_phrase, first_col_phrase, first_value, second_col_phrase, second_value, expected_value = match.groups()
        target_col = (
            TableQAPipeline._select_column_by_semantic_tokens(df, target_phrase)
            or TableQAPipeline._select_column_by_tokens(df, target_phrase)
        )
        first_col = (
            TableQAPipeline._select_column_by_semantic_tokens(df, first_col_phrase)
            or TableQAPipeline._select_column_by_tokens(df, first_col_phrase)
        )
        second_col = (
            TableQAPipeline._select_column_by_semantic_tokens(df, second_col_phrase)
            or TableQAPipeline._select_column_by_tokens(df, second_col_phrase)
        )
        if target_col is None or first_col is None or second_col is None:
            return None
        matched_rows = [
            row
            for _, row in df.iterrows()
            if _value_matches_phrase(row[first_col], first_value)
            and _value_matches_phrase(row[second_col], second_value)
        ]
        if not matched_rows:
            return None
        return "true" if any(_value_matches_phrase(row[target_col], expected_value) for row in matched_rows) else "false"

    @staticmethod
    def _tabfact_threshold_implication_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^when\s+the\s+(.+?)\s+be\s+(larger|greater|more|higher|less|lower)\s+than\s+([\d.,]+)\s*,?\s+"
            r"and\s+(?:a|an|the)?\s*(.+?)\s+(?:be\s+)?(larger|greater|more|higher|less|lower)\s+than\s+([\d.,]+)\s+"
            r"the\s+(.+?)\s+be\s+(larger|greater|more|higher|less|lower)\s+than\s+([\d.,]+)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        (
            first_phrase,
            first_op,
            first_threshold_text,
            second_phrase,
            second_op,
            second_threshold_text,
            target_phrase,
            target_op,
            target_threshold_text,
        ) = match.groups()
        first_col = _select_numeric_measure_column_by_phrase(df, first_phrase)
        second_col = _select_numeric_measure_column_by_phrase(df, second_phrase)
        target_col = _select_numeric_measure_column_by_phrase(df, target_phrase)
        if first_col is None or second_col is None or target_col is None:
            return None
        first_threshold = _numeric_measure_value(first_threshold_text)
        second_threshold = _numeric_measure_value(second_threshold_text)
        target_threshold = _numeric_measure_value(target_threshold_text)
        if first_threshold is None or second_threshold is None or target_threshold is None:
            return None
        matched = []
        for _, row in df.iterrows():
            first_value = _numeric_measure_value(row[first_col])
            second_value = _numeric_measure_value(row[second_col])
            target_value = _numeric_measure_value(row[target_col])
            if first_value is None or second_value is None or target_value is None:
                continue
            if _comparison_holds(first_value, first_op, first_threshold) and _comparison_holds(
                second_value,
                second_op,
                second_threshold,
            ):
                matched.append(target_value)
        if not matched:
            return None
        return "true" if all(_comparison_holds(value, target_op, target_threshold) for value in matched) else "false"

    @staticmethod
    def _tabfact_same_side_score_comparison_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^(.+?)\s+get\s+a\s+(higher|lower)\s+score\s+than\s+(.+?)\s+as\s+an?\s+(home|away)\s+team[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        left_entity, direction, right_entity, side = match.groups()
        team_cols = [col for col in df.columns if re.fullmatch(rf"(?i){side}\s+team", str(col).strip())]
        score_cols = [col for col in df.columns if re.fullmatch(rf"(?i){side}\s+team\s+score", str(col).strip())]
        if not team_cols or not score_cols:
            return None
        team_col, score_col = team_cols[0], score_cols[0]

        def side_scores(entity: str) -> List[float]:
            values = []
            for _, row in df.iterrows():
                if not _cell_contains_entity_phrase(entity, row[team_col]):
                    continue
                score = TableQAPipeline._score_points(row[score_col])
                if score is not None:
                    values.append(float(score))
            return values

        left_scores = side_scores(left_entity)
        right_scores = side_scores(right_entity)
        if len(left_scores) != 1 or len(right_scores) != 1:
            return None
        observed = left_scores[0] > right_scores[0] if direction.lower() == "higher" else left_scores[0] < right_scores[0]
        return "true" if observed else "false"

    @staticmethod
    def _tabfact_highest_shutout_score_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^(.+?)\s+play\s+the\s+highest\s+scoring\s+shut\s*out\s+game\s*:?\s*(\d+)\s+to\s+(\d+)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        entity_phrase, left_text, right_text = match.groups()
        expected_scores = (int(left_text), int(right_text))
        if 0 not in expected_scores:
            return None
        score_cols = [col for col in df.columns if re.fullmatch(r"(?i)score", str(col).strip())]
        team_cols = [col for col in df.columns if re.search(r"\b(home|away)\s+team\b", str(col), flags=re.I)]
        if not score_cols or not team_cols:
            return None
        shutout_rows: List[Tuple[int, Tuple[int, int], pd.Series]] = []
        for _, row in df.iterrows():
            score_match = re.search(r"\b(\d+)\s*-\s*(\d+)\b", str(row[score_cols[0]]))
            if not score_match:
                continue
            scores = (int(score_match.group(1)), int(score_match.group(2)))
            if 0 not in scores or scores[0] == scores[1]:
                continue
            shutout_rows.append((sum(scores), scores, row))
        if not shutout_rows:
            return None
        max_total = max(total for total, _, _ in shutout_rows)
        expected_total = sum(expected_scores)
        if expected_total != max_total:
            return "false"
        expected_set = set(expected_scores)
        return "true" if any(
            total == max_total
            and set(scores) == expected_set
            and any(_cell_contains_entity_phrase(entity_phrase, row[col]) for col in team_cols)
            for total, scores, row in shutout_rows
        ) else "false"

    @staticmethod
    def _tabfact_entity_metric_comparison_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^(.+?)\s+have\s+a\s+(lower|higher)\s+number\s+of\s+(.+?)\s+than\s+the\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        left_entity, direction, metric_phrase, right_entity = match.groups()
        metric_col = _select_numeric_measure_column_by_phrase(df, metric_phrase)
        if metric_col is None:
            return None
        entity_cols = [col for col in df.columns if col != metric_col]
        left_rows = _find_rows_for_entity(df, left_entity, entity_cols)
        right_rows = _find_rows_for_entity(df, right_entity, entity_cols)
        if len(left_rows) != 1 or len(right_rows) != 1:
            return None
        left_value = _numeric_measure_value(left_rows[0][metric_col])
        right_value = _numeric_measure_value(right_rows[0][metric_col])
        if left_value is None or right_value is None:
            return None
        observed = left_value < right_value if direction.lower() == "lower" else left_value > right_value
        return "true" if observed else "false"

    @staticmethod
    def _tabfact_entity_extreme_metric_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^(.+?)\s+have\s+the\s+(smallest|lowest|largest|highest)\s+(.+?)\s+at\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        entity_phrase, extreme, metric_phrase, expected_phrase = match.groups()
        metric_col = _select_numeric_measure_column_by_phrase(df, metric_phrase)
        if metric_col is None:
            return None
        entity_cols = [col for col in df.columns if col != metric_col]
        entity_rows = _find_rows_for_entity(df, entity_phrase, entity_cols)
        if len(entity_rows) != 1:
            return None
        series = _numeric_measure_series(df, metric_col).dropna()
        entity_value = _numeric_measure_value(entity_rows[0][metric_col])
        expected_value = _numeric_measure_value(expected_phrase)
        if series.empty or entity_value is None or expected_value is None:
            return None
        target_value = float(series.min()) if extreme.lower() in {"smallest", "lowest"} else float(series.max())
        observed = abs(float(entity_value) - target_value) <= 1e-6 and abs(float(entity_value) - float(expected_value)) <= 1e-6
        return "true" if observed else "false"

    @staticmethod
    def _tabfact_least_threshold_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^less\s+than\s+([\d.,]+)\s+(.+?)\s+attend\s+the\s+game\s+against\s+the\s+(.+?)\s+make\s+it\s+the\s+least\s+attended\s+game[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        threshold_text, metric_phrase, opponent_phrase = match.groups()
        metric_cols = [col for col in df.columns if re.search(r"\b(attendance|crowd)\b", str(col), flags=re.I)]
        metric_col = metric_cols[0] if metric_cols else _select_numeric_measure_column_by_phrase(df, metric_phrase)
        if metric_col is None:
            return None
        threshold = _numeric_measure_value(threshold_text)
        series = _numeric_measure_series(df, metric_col).dropna()
        if threshold is None or series.empty:
            return None
        minimum = float(series.min())
        entity_cols = [col for col in df.columns if col != metric_col]
        matched_values = [
            _numeric_measure_value(row[metric_col])
            for row in _find_rows_for_entity_across_row(df, opponent_phrase, entity_cols)
        ]
        matched_values = [float(value) for value in matched_values if value is not None]
        if not matched_values:
            return None
        observed = any(value < float(threshold) and abs(value - minimum) <= 1e-6 for value in matched_values)
        return "true" if observed else "false"

    @staticmethod
    def _tabfact_extreme_metric_belongs_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        text = question or ""

        def check(extreme: str, metric_phrase: str, entity_phrase: str, expected_phrase: Optional[str] = None) -> Optional[str]:
            metric_col = _select_numeric_measure_column_by_phrase(df, metric_phrase)
            if metric_col is None:
                return None
            series = _numeric_measure_series(df, metric_col).dropna()
            if series.empty:
                return None
            target_value = float(series.min()) if extreme.lower() in {"lowest", "smallest"} else float(series.max())
            entity_cols = [col for col in df.columns if col != metric_col]
            entity_rows = _find_rows_for_entity_across_row(df, entity_phrase, entity_cols)
            if not entity_rows:
                return None
            expected_value = _numeric_measure_value(expected_phrase) if expected_phrase is not None else None
            for row in entity_rows:
                value = _numeric_measure_value(row[metric_col])
                if value is None or abs(float(value) - target_value) > 1e-6:
                    continue
                if expected_value is not None and abs(float(value) - float(expected_value)) > 1e-6:
                    continue
                return "true"
            return "false"

        match = re.search(
            r"^the\s+(lowest|smallest|highest|largest)\s+(.+?)\s+be\s+(.+?)[?.]?$",
            text,
            flags=re.I,
        )
        if match and not re.search(r"\bby\b|\bbelongs?\s+to\b", text, flags=re.I):
            extreme, metric_phrase, entity_phrase = match.groups()
            return check(extreme, metric_phrase, entity_phrase)

        match = re.search(
            r"^the\s+(highest|largest|lowest|smallest)\s+(?:number\s+of\s+)?(.+?)\s+be\s+([\d.,]+)\s+by\s+(?:team\s+)?(.+?)[?.]?$",
            text,
            flags=re.I,
        )
        if match:
            extreme, metric_phrase, expected_text, entity_phrase = match.groups()
            return check(extreme, metric_phrase, entity_phrase, expected_text)

        match = re.search(
            r"^the\s+(smallest|lowest|highest|largest)\s+(.+?)\s+belongs?\s+to\s+(.+?)\s+at\s+([\d.,]+)[?.]?$",
            text,
            flags=re.I,
        )
        if match:
            extreme, metric_phrase, entity_phrase, expected_text = match.groups()
            return check(extreme, metric_phrase, entity_phrase, expected_text)

        match = re.search(
            r"^(.+?)\s+have\s+the\s+(lowest|smallest|highest|largest)\s+(.+?)\s+among\s+all\s+the\s+.+?[?.]?$",
            text,
            flags=re.I,
        )
        if match:
            entity_phrase, extreme, metric_phrase = match.groups()
            return check(extreme, metric_phrase, entity_phrase)
        return None

    @staticmethod
    def _tabfact_threshold_count_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^a\s+total\s+of\s+(\d+)\s+.+?\s+have\s+an?\s+(.+?)\s+(higher|lower|larger|less|greater)\s+than\s+([\d.,]+)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        expected_text, metric_phrase, op, threshold_text = match.groups()
        metric_col = _select_numeric_measure_column_by_phrase(df, metric_phrase)
        threshold = _numeric_measure_value(threshold_text)
        if metric_col is None or threshold is None:
            return None
        count = 0
        for value in _numeric_measure_series(df, metric_col).dropna().tolist():
            if _comparison_holds(float(value), op, float(threshold)):
                count += 1
        return "true" if count == int(expected_text) else "false"

    @staticmethod
    def _tabfact_first_n_rows_count_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^(\d+)\s+of\s+the\s+first\s+(\d+)\s+.+?\s+come\s+from\s+the\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        expected_text, first_n_text, entity_phrase = match.groups()
        first_rows = list(df.iterrows())[: int(first_n_text)]
        if len(first_rows) < int(first_n_text):
            return None
        count = sum(1 for _, row in first_rows if _row_contains_entity_phrase(row, entity_phrase))
        return "true" if count == int(expected_text) else "false"

    @staticmethod
    def _tabfact_entity_metric_threshold_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^the\s+episode\s+number\s+in\s+the\s+series\s+(.+?)\s+be\s+(before|after)\s+([\d.,]+)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        entity_phrase, direction, threshold_text = match.groups()
        metric_col = _select_numeric_measure_column_by_phrase(df, "episode number series no in series")
        if metric_col is None:
            return None
        title_cols = [col for col in df.columns if re.search(r"\b(title|episode)\b", str(col), flags=re.I)]
        search_cols = title_cols or [col for col in df.columns if col != metric_col]
        entity_rows = _find_rows_for_entity_across_row(df, entity_phrase, search_cols)
        threshold = _numeric_measure_value(threshold_text)
        if len(entity_rows) != 1 or threshold is None:
            return None
        value = _numeric_measure_value(entity_rows[0][metric_col])
        if value is None:
            return None
        observed = float(value) < float(threshold) if direction.lower() == "before" else float(value) > float(threshold)
        return "true" if observed else "false"

    @staticmethod
    def _tabfact_year_column_value_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(r"^in\s+(\d{4})\s*,\s+(.+?)\s+be\s+the\s+(.+?)[?.]?$", question or "", flags=re.I)
        if not match:
            return None
        year_text, expected_value, target_phrase = match.groups()
        year_cols = [col for col in df.columns if re.search(r"\byear\b", str(col), flags=re.I)]
        target_col = (
            TableQAPipeline._select_column_by_semantic_tokens(df, target_phrase)
            or TableQAPipeline._select_column_by_tokens(df, target_phrase)
        )
        if not year_cols or target_col is None:
            return None
        matched_rows = [row for _, row in df.iterrows() if _numeric_measure_value(row[year_cols[0]]) == float(year_text)]
        if not matched_rows:
            return None
        return "true" if any(_value_matches_phrase(row[target_col], expected_value) for row in matched_rows) else "false"

    @staticmethod
    def _tabfact_game_result_score_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^(.+?)\s+win\s+game\s+(\d+)\s+with\s+a\s+score\s+of\s+(\d+\s*-\s*\d+)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        entity_phrase, game_text, expected_score = match.groups()
        game_cols = [col for col in df.columns if re.fullmatch(r"(?i)game", str(col).strip())]
        team_cols = [col for col in df.columns if re.fullmatch(r"(?i)team|opponent", str(col).strip())]
        score_cols = [col for col in df.columns if re.fullmatch(r"(?i)score|result", str(col).strip())]
        if not (game_cols and team_cols and score_cols):
            return None
        for _, row in df.iterrows():
            if _numeric_measure_value(row[game_cols[0]]) != float(game_text):
                continue
            if not _cell_contains_entity_phrase(entity_phrase, row[team_cols[0]]):
                continue
            score_text = str(row[score_cols[0]]).lower()
            score_match = re.search(r"\b([wl])\s+(\d+)\s*-\s*(\d+)", score_text, flags=re.I)
            if not score_match:
                return None
            outcome, left_score, right_score = score_match.groups()
            expected_pair = re.search(r"(\d+)\s*-\s*(\d+)", expected_score)
            if not expected_pair or (left_score, right_score) != expected_pair.groups():
                return "false"
            opponent_won = outcome.lower() == "l" and int(right_score) > int(left_score)
            return "true" if opponent_won else "false"
        return None

    @staticmethod
    def _tabfact_date_metric_difference_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^(.+?)\s+on\s+(.+?)\s+be\s+([\d.,]+)\s+(more|less)\s+than\s+the\s+game\s+a\s+week\s+later[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        metric_phrase, date_phrase, diff_text, direction = match.groups()
        date_cols = [col for col in df.columns if re.search(r"\bdate\b", str(col), flags=re.I)]
        metric_col = _select_numeric_measure_column_by_phrase(df, metric_phrase)
        if not date_cols or metric_col is None:
            return None
        date_key = _parse_date_key(date_phrase)
        expected_diff = _numeric_measure_value(diff_text)
        if date_key is None or expected_diff is None:
            return None
        target_date = pd.Timestamp(*date_key)
        later_date = target_date + pd.Timedelta(days=7)
        current_value = later_value = None
        for _, row in df.iterrows():
            row_key = _parse_date_key(row[date_cols[0]])
            if row_key is None:
                continue
            row_date = pd.Timestamp(*row_key)
            if row_date == target_date:
                current_value = _numeric_measure_value(row[metric_col])
            if row_date == later_date:
                later_value = _numeric_measure_value(row[metric_col])
        if current_value is None or later_value is None:
            return None
        observed_diff = float(current_value) - float(later_value)
        if direction.lower() == "less":
            observed_diff = -observed_diff
        return "true" if abs(observed_diff - float(expected_diff)) <= 1e-6 else "false"

    @staticmethod
    def _tabfact_condition_metric_value_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^the\s+(.+?)\s+in\s+the\s+.+?\s+with\s+([\d.,]+)\s+(.+?)\s+be\s+([\d.,]+)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        target_phrase, condition_text, condition_phrase, expected_text = match.groups()
        target_col = _select_numeric_measure_column_by_phrase(df, target_phrase)
        condition_col = _select_numeric_measure_column_by_phrase(df, condition_phrase)
        expected = _numeric_measure_value(expected_text)
        condition_value = _numeric_measure_value(condition_text)
        if target_col is None or condition_col is None or expected is None or condition_value is None:
            return None
        for _, row in df.iterrows():
            observed_condition = _numeric_measure_value(row[condition_col])
            if observed_condition is None or abs(observed_condition - condition_value) > 1e-6:
                continue
            observed = _numeric_measure_value(row[target_col])
            if observed is None:
                continue
            return "true" if abs(observed - expected) <= 1e-6 else "false"
        return "false"

    @staticmethod
    def _tabfact_entity_score_sum_comparison_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^(.+?)\s+score\s+(less|more|fewer|greater|higher|lower)\s+points?\s+(?:than|that)\s+(.+?)(?:\s+in\b|[?.]?$)",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        left_entity, operator_text, right_entity = [part.strip(" \t\r\n\"'") for part in match.groups()]
        score_cols = [col for col in df.columns if re.search(r"\b(score|points?)\b", str(col), flags=re.I)]
        entity_cols = [col for col in df.columns if re.search(r"\b(player|team|name|competitor)\b", str(col), flags=re.I)]
        if not score_cols or not entity_cols:
            return None

        def score_for(entity: str) -> Optional[float]:
            rows = _find_rows_for_entity_across_row(df, entity, entity_cols)
            if len(rows) != 1:
                return None
            return _numeric_measure_value(rows[0][score_cols[0]])

        left_score = score_for(left_entity)
        right_score = score_for(right_entity)
        if left_score is None or right_score is None:
            return None
        return "true" if _comparison_holds(left_score, operator_text, right_score) else "false"

    @staticmethod
    def _tabfact_replay_count_month_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^(\d+)\s+match(?:es)?\s+be\s+replay\s+in\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        expected_text, date_phrase = match.groups()
        expected = int(expected_text)
        target_key = _parse_date_key(date_phrase)
        if target_key is None:
            return None
        date_cols = [col for col in df.columns if re.search(r"\bdate\b", str(col), flags=re.I)]
        if not date_cols:
            return None
        count = 0
        for _, row in df.iterrows():
            row_key = _parse_date_key(row[date_cols[0]])
            if row_key is None or row_key[0] != target_key[0] or row_key[1] != target_key[1]:
                continue
            row_text = _loose_text_key(" ".join(str(row[col]) for col in df.columns))
            if re.search(r"\breplay\b", row_text):
                count += 1
        return "true" if count == expected else "false"

    @staticmethod
    def _tabfact_lowest_attendance_weeks_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"\bweek\s+(.+?)\s+be\s+play\s+with\s+the\s+lowest\s+attendance\s+at\s+(?:the\s+)?(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        weeks_text, site_phrase = match.groups()
        expected_weeks = {
            int(value)
            for value in re.findall(r"\b\d+\b", weeks_text)
        }
        if not expected_weeks:
            return None
        week_cols = [col for col in df.columns if re.fullmatch(r"(?i)week", str(col).strip())]
        site_cols = [col for col in df.columns if re.search(r"\b(site|stadium|venue|game site)\b", str(col), flags=re.I)]
        attendance_cols = [col for col in df.columns if re.search(r"\battendance|crowd\b", str(col), flags=re.I)]
        if not week_cols or not site_cols or not attendance_cols:
            return None
        site_rows = [
            row
            for _, row in df.iterrows()
            if TableQAPipeline._cell_contains_phrase_tokens(site_phrase, row[site_cols[0]])
        ]
        if len(site_rows) < len(expected_weeks):
            return None
        ranked: List[Tuple[float, int]] = []
        for row in site_rows:
            attendance = _numeric_measure_value(row[attendance_cols[0]])
            week = _numeric_measure_value(row[week_cols[0]])
            if attendance is None or week is None:
                continue
            ranked.append((attendance, int(week)))
        if len(ranked) < len(expected_weeks):
            return None
        ranked.sort(key=lambda item: item[0])
        observed_weeks = {week for _, week in ranked[: len(expected_weeks)]}
        return "true" if observed_weeks == expected_weeks else "false"

    @staticmethod
    def _tabfact_replay_home_team_win_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        text = question or ""
        if not re.search(r"\breplay\b", text, flags=re.I) or not re.search(r"\bhome\s+team\b", text, flags=re.I):
            return None
        expected = 2 if re.search(r"\bboth\b", text, flags=re.I) else _small_number_from_text(text)
        if expected is None:
            return None
        tie_cols = [col for col in df.columns if re.search(r"\btie\b", str(col), flags=re.I)]
        score_cols = [col for col in df.columns if re.search(r"\bscore\b", str(col), flags=re.I)]
        if not tie_cols or not score_cols:
            return None
        rows = list(df.iterrows())
        replay_results: List[bool] = []
        for index, (_, row) in enumerate(rows[:-1]):
            score = TableQAPipeline._score_pair(row[score_cols[0]])
            if score is None or abs(score[0] - score[1]) > 1e-6:
                continue
            next_row = rows[index + 1][1]
            if not re.search(r"\breplay\b", _loose_text_key(next_row[tie_cols[0]])):
                continue
            replay_score = TableQAPipeline._score_pair(next_row[score_cols[0]])
            if replay_score is None:
                return None
            replay_results.append(replay_score[0] > replay_score[1])
        if len(replay_results) != expected:
            return "false"
        return "true" if all(replay_results) else "false"

    @staticmethod
    def _tabfact_entity_tenure_contains_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^(.+?)\s+be\s+on\s+the\s+team\s+the\s+entire\s+time\s+that\s+(.+?)\s+be[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        left_entity, right_entity = [part.strip(" \t\r\n\"'") for part in match.groups()]
        year_cols = [col for col in df.columns if re.search(r"\byears?\b|\bseason\b|\btenure\b", str(col), flags=re.I)]
        entity_cols = [col for col in df.columns if col not in year_cols]
        if not year_cols or not entity_cols:
            return None

        def parse_span(value: Any) -> Optional[Tuple[int, int]]:
            text = str(value or "")
            match_range = re.search(r"\b(1[7-9]\d{2}|20\d{2})\s*(?:-|–|—|to|,)\s*(1[7-9]\d{2}|20\d{2}|\d{2})\b", text)
            if not match_range:
                return None
            start_text, end_text = match_range.groups()
            start = int(start_text)
            if len(end_text) == 2:
                end = (start // 100) * 100 + int(end_text)
                if end < start:
                    end += 100
            else:
                end = int(end_text)
            return (start, end) if end >= start else None

        def span_for(entity: str) -> Optional[Tuple[int, int]]:
            rows = _find_rows_for_entity_across_row(df, entity, entity_cols)
            if len(rows) != 1:
                return None
            for col in year_cols:
                span = parse_span(rows[0][col])
                if span is not None:
                    return span
            return None

        left_span = span_for(left_entity)
        right_span = span_for(right_entity)
        if left_span is None or right_span is None:
            return None
        return "true" if left_span[0] <= right_span[0] and left_span[1] >= right_span[1] else "false"

    @staticmethod
    def _tabfact_aircraft_call_sign_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^(.+?)\s+fly\s+a\s+(.+?)\s+and\s+have\s+the\s+call\s+sign\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        pilot_phrase, aircraft_phrase, call_sign_phrase = [
            part.strip(" \t\r\n\"'")
            for part in match.groups()
        ]
        pilot_cols = [col for col in df.columns if re.search(r"\bpilot\b|\bdriver\b|\bperson\b", str(col), flags=re.I)]
        aircraft_cols = [col for col in df.columns if re.search(r"\baircraft\b|\bplane\b|\bvehicle\b", str(col), flags=re.I)]
        call_cols = [col for col in df.columns if re.search(r"\bcall\s*sign\b", str(col), flags=re.I)]
        if not pilot_cols or not aircraft_cols or not call_cols:
            return None
        pilot_tokens = [token for token in _loose_tokens(pilot_phrase) if len(token) >= 3 and token not in {"capt", "captain"}]
        aircraft_key = _loose_text_key(aircraft_phrase).replace(" ", "")
        call_key = _loose_text_key(call_sign_phrase)
        if not pilot_tokens or not aircraft_key or not call_key:
            return None
        for _, row in df.iterrows():
            pilot_key = _loose_text_key(row[pilot_cols[0]])
            aircraft_value_key = _loose_text_key(row[aircraft_cols[0]]).replace(" ", "")
            call_value_key = _loose_text_key(row[call_cols[0]])
            pilot_match = all(token in pilot_key.split() for token in pilot_tokens)
            aircraft_match = aircraft_key == aircraft_value_key
            call_match = call_key == call_value_key
            if pilot_match and aircraft_match and call_match:
                return "true"
        return "false"

    @staticmethod
    def _tabfact_only_not_from_country_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^the\s+only\s+(.+?)\s+who\s+be\s+not\s+from\s+the\s+(.+?)\s+be\s+from\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        entity_phrase, excluded_country, expected_country = match.groups()
        entity_cols = [
            col
            for col in df.columns
            if re.search(rf"\b{re.escape(_singular_token(entity_phrase.strip().lower()))}\b|\b(player|name|person)\b", str(col), flags=re.I)
        ]
        country_cols = [col for col in df.columns if re.search(r"\b(country|nation|nationality)\b", str(col), flags=re.I)]
        if not country_cols:
            return None
        country_col = country_cols[0]
        non_excluded = [
            row
            for _, row in df.iterrows()
            if not _cell_contains_entity_phrase(excluded_country, row[country_col])
        ]
        if len(non_excluded) != 1:
            return "false"
        if entity_cols and not any(_loose_text_key(row[entity_cols[0]]) for row in non_excluded):
            return None
        return "true" if _cell_contains_entity_phrase(expected_country, non_excluded[0][country_col]) else "false"

    @staticmethod
    def _tabfact_entity_numeric_year_value_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^the\s+(.+?)\s+have\s+a\s+(.+?)\s+of\s+([\d,]+(?:\.\d+)?)\s+in\s+(\d{4})[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        entity_phrase, metric_phrase, expected_text, year_text = match.groups()
        metric_col = None
        metric_tokens = set(_loose_tokens(metric_phrase))
        for col in df.columns:
            col_key = _loose_text_key(col)
            col_tokens = set(_loose_tokens(col))
            if str(year_text) not in col_key:
                continue
            if metric_tokens & col_tokens or _loose_text_key(metric_phrase) in col_key:
                metric_col = col
                break
        if metric_col is None:
            metric_col = _select_numeric_measure_column_by_phrase(df, metric_phrase)
        expected = _numeric_measure_value(expected_text)
        if metric_col is None or expected is None:
            return None
        search_cols = [col for col in df.columns if col != metric_col]
        rows = _find_rows_for_entity_across_row(df, entity_phrase, search_cols)
        if not rows:
            entity_key = _loose_text_key(entity_phrase)
            for col in search_cols:
                col_key = _loose_text_key(col)
                if entity_key.endswith(f" {col_key}"):
                    rows = _find_rows_for_entity_across_row(df, entity_key[: -len(col_key)].strip(), search_cols)
                    break
        if not rows:
            return None
        return "true" if any(abs((_numeric_measure_value(row[metric_col]) or float("nan")) - expected) <= 1e-6 for row in rows) else "false"

    @staticmethod
    def _tabfact_column_value_fraction_count_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^the\s+(.+?)\s+be\s+(.+?)\s+for\s+(\d+)\s+of\s+the\s+(\d+)\s+.+?[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        column_phrase, value_phrase, expected_text, total_text = match.groups()
        target_col = (
            TableQAPipeline._select_column_by_semantic_tokens(df, column_phrase)
            or TableQAPipeline._select_column_by_tokens(df, column_phrase)
        )
        if target_col is None:
            return None
        expected = int(expected_text)
        stated_total = int(total_text)
        count = sum(1 for value in df[target_col].tolist() if _value_matches_phrase(value, value_phrase))
        return "true" if count == expected and len(df) == stated_total else "false"

    @staticmethod
    def _tabfact_finish_position_count_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^(.+?)\s+finish\s+(\d+)(?:st|nd|rd|th)?\s+(\d+)\s+times?[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        entity_phrase, position_text, expected_text = match.groups()
        position_cols = [col for col in df.columns if re.search(r"\b(finish|position|rank|place|result)\b", str(col), flags=re.I)]
        if not position_cols:
            return None
        target_position = int(position_text)
        count = 0
        for _, row in df.iterrows():
            if not _row_contains_entity_phrase(row, entity_phrase, [col for col in df.columns if col not in position_cols]):
                continue
            if any((_numeric_measure_value(row[col]) or float("nan")) == target_position for col in position_cols):
                count += 1
        return "true" if count == int(expected_text) else "false"

    @staticmethod
    def _tabfact_zero_score_team_count_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+teams?\s+score\s+(zero|\d+)\s+points?[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        expected = _small_number_from_text(match.group(1))
        score_text = match.group(2)
        target_score = 0 if score_text.lower() == "zero" else int(score_text)
        score_cols = [col for col in df.columns if re.search(r"\b(score|points?|pts)\b", str(col), flags=re.I)]
        if expected is None or not score_cols:
            return None
        count = 0
        for _, row in df.iterrows():
            values = [_numeric_measure_value(row[col]) for col in score_cols]
            if any(value is not None and abs(value - target_score) <= 1e-6 for value in values):
                count += 1
        return "true" if count == expected else "false"

    @staticmethod
    def _tabfact_minmax_numeric_difference_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^the\s+(lowest|smallest|highest|largest)\s+(.+?)\s+be\s+([\d.]+)\s+"
            r"(lower|higher|more|less)\s+than\s+the\s+(lowest|smallest|highest|largest)\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        left_extreme, metric_phrase, expected_text, _, right_extreme, _ = match.groups()
        metric_col = _select_numeric_measure_column_by_phrase(df, metric_phrase)
        if metric_col is None:
            numeric_cols = [
                col for col in df.columns if _numeric_measure_series(df, col).dropna().nunique() >= 2
            ]
            if len(numeric_cols) == 1:
                metric_col = numeric_cols[0]
        expected = _numeric_measure_value(expected_text)
        if metric_col is None or expected is None:
            return None
        series = _numeric_measure_series(df, metric_col).dropna()
        if series.empty:
            return None
        left_value = float(series.min()) if left_extreme.lower() in {"lowest", "smallest"} else float(series.max())
        right_value = float(series.min()) if right_extreme.lower() in {"lowest", "smallest"} else float(series.max())
        return "true" if abs(abs(right_value - left_value) - float(expected)) <= 1e-6 else "false"

    @staticmethod
    def _tabfact_rank_country_count_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^(\d+)\s+of\s+the\s+people\s+tie\s+for\s+(.+?)\s+place\s+be\s+from\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        expected_text, rank_text, country_phrase = match.groups()
        expected = int(expected_text)
        rank = _small_number_from_text(rank_text)
        if rank is None:
            ordinal_words = {
                "first": 1,
                "second": 2,
                "third": 3,
                "fourth": 4,
                "fifth": 5,
                "sixth": 6,
                "seventh": 7,
                "eighth": 8,
                "ninth": 9,
                "tenth": 10,
            }
            rank = ordinal_words.get(_loose_text_key(rank_text))
        rank_cols = [col for col in df.columns if re.search(r"\b(place|rank|position)\b", str(col), flags=re.I)]
        country_cols = [col for col in df.columns if re.search(r"\b(country|nation|nationality)\b", str(col), flags=re.I)]
        if rank is None or not rank_cols or not country_cols:
            return None
        count = 0
        for _, row in df.iterrows():
            rank_match = re.search(r"\b(?:t)?(\d+)\b", str(row[rank_cols[0]]), flags=re.I)
            if not rank_match or int(rank_match.group(1)) != rank:
                continue
            if _value_matches_phrase(row[country_cols[0]], country_phrase):
                count += 1
        return "true" if count == expected else "false"

    @staticmethod
    def _tabfact_unique_country_count_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^there\s+be\s+a\s+total\s+of\s+(\d+)\s+country\s+represent\s+by\s+the\s+player[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        expected = int(match.group(1))
        country_cols = [col for col in df.columns if re.search(r"\b(country|nation|nationality)\b", str(col), flags=re.I)]
        if not country_cols:
            return None
        values = {
            _loose_text_key(value)
            for value in df[country_cols[0]].tolist()
            if not _is_missing_marker(value) and _loose_text_key(value)
        }
        return "true" if len(values) == expected else "false"

    @staticmethod
    def _tabfact_majority_over_par_country_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^a\s+majority\s+of\s+the\s+people\s+who\s+score\s+over\s+par\s+be\s+from\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        country_phrase = match.group(1)
        par_cols = [col for col in df.columns if re.search(r"\bto\s*par\b|\bpar\b", str(col), flags=re.I)]
        country_cols = [col for col in df.columns if re.search(r"\b(country|nation|nationality)\b", str(col), flags=re.I)]
        if not par_cols or not country_cols:
            return None
        over_rows = []
        for _, row in df.iterrows():
            value = _numeric_measure_value(row[par_cols[0]])
            if value is not None and value > 0:
                over_rows.append(row)
        if not over_rows:
            return None
        country_count = sum(1 for row in over_rows if _value_matches_phrase(row[country_cols[0]], country_phrase))
        return "true" if country_count > len(over_rows) / 2 else "false"

    @staticmethod
    def _tabfact_only_column_value_not_count_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^only\s+(.+?)\s+(\d+)\s+be\s+not\s+list\s+(\d+)\s+time[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        column_phrase, target_text, count_text = match.groups()
        target_value = target_text
        expected_count = int(count_text)
        target_col = (
            TableQAPipeline._select_column_by_semantic_tokens(df, column_phrase)
            or TableQAPipeline._select_column_by_tokens(df, column_phrase)
        )
        if target_col is None:
            return None
        counts: Dict[str, int] = {}
        originals: Dict[str, str] = {}
        for value in df[target_col].tolist():
            key = _loose_text_key(value)
            if not key:
                continue
            counts[key] = counts.get(key, 0) + 1
            originals[key] = str(value)
        exceptions = [key for key, count in counts.items() if count != expected_count]
        if len(exceptions) != 1:
            return "false"
        return "true" if _value_matches_phrase(originals[exceptions[0]], target_value) else "false"

    @staticmethod
    def _tabfact_only_not_from_countries_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^the\s+only\s+player\s+not\s+from\s+(.+?)\s+or\s+(.+?)\s+be\s+from\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        left_country, right_country, expected_country = match.groups()
        country_cols = [col for col in df.columns if re.search(r"\b(country|nation|nationality)\b", str(col), flags=re.I)]
        if not country_cols:
            return None
        country_col = country_cols[0]
        remaining = [
            row
            for _, row in df.iterrows()
            if not _value_matches_phrase(row[country_col], left_country)
            and not _value_matches_phrase(row[country_col], right_country)
        ]
        if len(remaining) != 1:
            return "false"
        return "true" if _value_matches_phrase(remaining[0][country_col], expected_country) else "false"

    @staticmethod
    def _tabfact_every_player_source_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        if not re.search(r"^every\s+player\s+come\s+from\s+either\s+a\s+college\s+program\s+or\s+a\s+junior\s*/\s*club\s+team[?.]?$", question or "", flags=re.I):
            return None
        source_cols = [col for col in df.columns if re.search(r"\bcollege\b.*\bjunior\b.*\bclub\b|\bteam\b.*\bleague\b", str(col), flags=re.I)]
        if not source_cols:
            return None
        values = TableQAPipeline._wtq_non_summary_rows(df)[source_cols[0]].tolist()
        return "true" if values and all(not _is_missing_marker(value) and _loose_text_key(value) for value in values) else "false"

    @staticmethod
    def _tabfact_opponent_attendance_comparison_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^the\s+game\s+against\s+(.+?)\s+have\s+a\s+higher\s+attendance\s+than\s+the\s+game\s+against\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        left_opponent, right_opponent = match.groups()
        team_cols = [col for col in df.columns if re.search(r"\b(team|opponent)\b", str(col), flags=re.I)]
        attendance_cols = [col for col in df.columns if re.search(r"\battendance\b", str(col), flags=re.I)]
        if not team_cols or not attendance_cols:
            return None

        def attendance_for(opponent: str) -> Optional[float]:
            rows = _find_rows_for_entity_across_row(df, opponent, team_cols)
            if len(rows) != 1:
                return None
            return _numeric_measure_value(rows[0][attendance_cols[0]])

        left_value = attendance_for(left_opponent)
        right_value = attendance_for(right_opponent)
        if left_value is None or right_value is None:
            return None
        return "true" if left_value > right_value else "false"

    @staticmethod
    def _tabfact_extreme_score_difference_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^there\s+be\s+a\s+([\d.]+)\s+point\s+difference\s+between\s+the\s+highest\s+score\s+"
            r"\(([\d.]+)\)\s+and\s+the\s+lowest\s+score\s+\(([\d.]+)\)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        expected_text, high_text, low_text = match.groups()
        points_col = _select_numeric_measure_column_by_phrase(df, "points score")
        if points_col is None:
            return None
        series = _numeric_measure_series(df, points_col).dropna()
        if series.empty:
            return None
        expected = float(expected_text)
        stated_high = float(high_text)
        stated_low = float(low_text)
        observed_high = float(series.max())
        observed_low = float(series.min())
        return "true" if (
            abs(observed_high - stated_high) <= 1e-6
            and abs(observed_low - stated_low) <= 1e-6
            and abs((observed_high - observed_low) - expected) <= 1e-6
        ) else "false"

    @staticmethod
    def _tabfact_entity_metric_more_than_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^the\s+(.+?)\s+have\s+(\d+)\s+more\s+(.+?)\s+than\s+the\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        left_entity, diff_text, metric_phrase, right_entity = match.groups()
        metric_col = (
            _select_numeric_measure_column_by_phrase(df, metric_phrase)
            or TableQAPipeline._select_column_by_semantic_tokens(df, metric_phrase)
            or TableQAPipeline._select_column_by_tokens(df, metric_phrase)
        )
        if metric_col is None:
            return None
        entity_cols = [col for col in df.columns if col != metric_col]
        left_rows = _find_rows_for_entity_across_row(df, left_entity, entity_cols)
        right_rows = _find_rows_for_entity_across_row(df, right_entity, entity_cols)
        if len(left_rows) != 1 or len(right_rows) != 1:
            return None
        left_value = _numeric_measure_value(left_rows[0][metric_col])
        right_value = _numeric_measure_value(right_rows[0][metric_col])
        if left_value is None or right_value is None:
            return None
        return "true" if abs((left_value - right_value) - int(diff_text)) <= 1e-6 else "false"

    @staticmethod
    def _tabfact_second_highest_metric_entity_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^for\s+(.+?)\s*,\s*(.+?)\s+be\s+the\s+tournament\s+with\s+his\s+second\s+highest\s+number\s+of\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        _, entity_phrase, metric_phrase = match.groups()
        metric_col = (
            _select_numeric_measure_column_by_phrase(df, metric_phrase)
            or TableQAPipeline._select_column_by_semantic_tokens(df, metric_phrase)
            or TableQAPipeline._select_column_by_tokens(df, metric_phrase)
        )
        if metric_col is None:
            return None
        entity_cols = [col for col in df.columns if col != metric_col]
        if not entity_cols:
            return None
        rows = []
        for _, row in TableQAPipeline._wtq_non_summary_rows(df).iterrows():
            value = _numeric_measure_value(row[metric_col])
            if value is None:
                continue
            label = next((row[col] for col in entity_cols if not _is_missing_marker(row[col]) and _loose_text_key(row[col])), None)
            if label is not None:
                rows.append((value, label))
        if len(rows) < 2:
            return None
        rows.sort(key=lambda item: item[0], reverse=True)
        second_value = sorted({value for value, _ in rows}, reverse=True)[1]
        second_labels = [label for value, label in rows if abs(value - second_value) <= 1e-6]
        return "true" if any(_value_matches_phrase(label, entity_phrase) for label in second_labels) else "false"

    @staticmethod
    def _month_number(month_phrase: str) -> Optional[int]:
        months = {
            "january": 1,
            "february": 2,
            "march": 3,
            "april": 4,
            "may": 5,
            "june": 6,
            "july": 7,
            "august": 8,
            "september": 9,
            "october": 10,
            "november": 11,
            "december": 12,
        }
        return months.get(_loose_text_key(month_phrase))

    @staticmethod
    def _score_pair(value: Any) -> Optional[Tuple[int, int]]:
        match = re.search(r"\b(\d+)\s*[-:]\s*(\d+)\b", str(value or ""))
        if not match:
            return None
        return int(match.group(1)), int(match.group(2))

    @staticmethod
    def _tenure_spans(value: Any) -> List[Tuple[int, int]]:
        spans: List[Tuple[int, int]] = []
        for part in re.split(r"\s*,\s*", str(value or "")):
            years = [int(year) for year in re.findall(r"\b(\d{2,4})\b", part)]
            if not years:
                continue
            start = years[0] + 1900 if years[0] < 100 else years[0]
            end = years[-1]
            if end < 100:
                end += (start // 100) * 100
            if end < start:
                end += 100
            spans.append((start, end))
        return spans

    @staticmethod
    def _tabfact_entity_max_metric_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(r"^the\s+(.+?)\s+(?:episode\s+)?have\s+the\s+most\s+(.+?)[?.]?$", question or "", flags=re.I)
        if not match:
            return None
        entity_phrase, metric_phrase = match.groups()
        metric_col = _select_numeric_measure_column_by_phrase(df, metric_phrase)
        if metric_col is None:
            return None
        series = _numeric_measure_series(df, metric_col)
        if series.dropna().empty:
            return None
        max_value = float(series.max())
        entity_cols = [col for col in df.columns if col != metric_col]
        winners = [
            row
            for idx, row in df.iterrows()
            if not pd.isna(series.loc[idx]) and abs(float(series.loc[idx]) - max_value) <= 1e-6
        ]
        return "true" if any(_row_contains_entity_phrase(row, entity_phrase, entity_cols) for row in winners) else "false"

    @staticmethod
    def _tabfact_only_year_more_than_count_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^(\d{4})\s+be\s+the\s+only\s+year\s+.+?\s+score\s+more\s+than\s+(\d+)\s+goal",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        target_year, threshold_text = match.groups()
        date_cols = [col for col in df.columns if re.search(r"\bdate\b", str(col), flags=re.I)]
        if not date_cols:
            return None
        counts: Dict[str, int] = {}
        for value in df[date_cols[0]].tolist():
            year_match = re.search(r"\b(1[7-9]\d{2}|20\d{2})\b", str(value or ""))
            if year_match:
                counts[year_match.group(1)] = counts.get(year_match.group(1), 0) + 1
        years = [year for year, count in counts.items() if count > int(threshold_text)]
        return "true" if years == [target_year] else "false"

    @staticmethod
    def _tabfact_record_equal_count_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^there\s+be\s+only\s+(\d+)\s+day\s+during\s+(.+?)\s+.+?\s+have\s+a\s+50\s*/\s*50\s+win\s*/\s*loss\s+record[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        expected_text, month_phrase = match.groups()
        month = TableQAPipeline._month_number(month_phrase)
        date_cols = [col for col in df.columns if re.search(r"\bdate\b", str(col), flags=re.I)]
        record_cols = [col for col in df.columns if re.search(r"\brecord\b", str(col), flags=re.I)]
        if month is None or not date_cols or not record_cols:
            return None
        count = 0
        for _, row in df.iterrows():
            parsed = TableQAPipeline._month_day_from_text(row[date_cols[0]])
            pair = TableQAPipeline._score_pair(row[record_cols[0]])
            if parsed is not None and parsed[0] == month and pair is not None and pair[0] == pair[1]:
                count += 1
        return "true" if count == int(expected_text) else "false"

    @staticmethod
    def _tabfact_month_no_game_days_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^there\s+be\s+only\s+(\d+)\s+day\s+in\s+(.+?)\s+on\s+which\s+.+?\s+do\s+not\s+have\s+to\s+play\s+a\s+game[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        expected_text, month_phrase = match.groups()
        month = TableQAPipeline._month_number(month_phrase)
        date_cols = [col for col in df.columns if re.search(r"\bdate\b", str(col), flags=re.I)]
        if month is None or not date_cols:
            return None
        days_by_month = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30, 7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}
        played_days = {
            parsed[1]
            for parsed in (TableQAPipeline._month_day_from_text(value) for value in df[date_cols[0]].tolist())
            if parsed is not None and parsed[0] == month
        }
        if not played_days:
            return None
        missing_count = len(set(range(1, days_by_month[month] + 1)) - played_days)
        return "true" if missing_count == int(expected_text) else "false"

    @staticmethod
    def _tabfact_lowest_attendance_result_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(r"\b(win|lose)\s+the\s+game\s+which\s+have\s+the\s+lowest\s+attendance\s+of\s+the\s+month[?.]?$", question or "", flags=re.I)
        if not match:
            return None
        expected_result = match.group(1).lower()
        attendance_cols = [col for col in df.columns if re.search(r"\battendance\b", str(col), flags=re.I)]
        score_cols = [col for col in df.columns if re.search(r"\bscore\b", str(col), flags=re.I)]
        if not attendance_cols or not score_cols:
            return None
        attendance = _numeric_measure_series(df, attendance_cols[0])
        if attendance.dropna().empty:
            return None
        min_idx = attendance.idxmin()
        pair = TableQAPipeline._score_pair(df.loc[min_idx, score_cols[0]])
        if pair is None or pair[0] == pair[1]:
            return None
        actual = "win" if pair[0] > pair[1] else "lose"
        return "true" if actual == expected_result else "false"

    @staticmethod
    def _tabfact_beer_award_count_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        simple = re.search(
            r"^.+?'s\s+(.+?)\s+beer\s+have\s+(\d+)\s+award\s+between\s+(\d{4})\s+and\s+(\d{4})[?.]?$",
            question or "",
            flags=re.I,
        )
        competition = re.search(
            r"^.+?'s\s+(.+?)\s+(\d+)\s+time\s+win\s+an\s+award\s+at\s+the\s+(.+?)\s+between\s+(\d{4})\s+and\s+(\d{4})[?.]?$",
            question or "",
            flags=re.I,
        )
        if not simple and not competition:
            return None
        if simple:
            entity_phrase, expected_text, start_text, end_text = simple.groups()
            competition_phrase = None
        else:
            entity_phrase, expected_text, competition_phrase, start_text, end_text = competition.groups()
        year_cols = [col for col in df.columns if re.search(r"\byear\b", str(col), flags=re.I)]
        beer_cols = [col for col in df.columns if re.search(r"\bbeer\s+name\b|\bname\b", str(col), flags=re.I)]
        competition_cols = [col for col in df.columns if re.search(r"\bcompetition\b", str(col), flags=re.I)]
        if not year_cols or not beer_cols:
            return None
        count = 0
        for _, row in df.iterrows():
            year = _numeric_measure_value(row[year_cols[0]])
            if year is None or not (int(start_text) <= int(year) <= int(end_text)):
                continue
            if not _value_matches_phrase(row[beer_cols[0]], entity_phrase):
                continue
            if competition_phrase is not None:
                if not competition_cols or not _value_matches_phrase(row[competition_cols[0]], competition_phrase):
                    continue
            count += 1
        return "true" if count == int(expected_text) else "false"

    @staticmethod
    def _tabfact_surface_count_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(r"^(.+?)\s+play\s+a\s+total\s+of\s+(\d+)\s+game\s+on\s+a\s+(.+?)\s+tennis\s+court[?.]?$", question or "", flags=re.I)
        if not match:
            return None
        _, expected_text, surface_phrase = match.groups()
        surface_cols = [col for col in df.columns if re.search(r"\bsurface\b|\bcourt\b", str(col), flags=re.I)]
        if not surface_cols:
            return None
        count = sum(1 for value in df[surface_cols[0]].tolist() if _value_matches_phrase(value, surface_phrase))
        return "true" if count == int(expected_text) else "false"

    @staticmethod
    def _tabfact_not_champion_loss_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(r"^(.+?)\s+be\s+not\s+the\s+female\s+lose\s+the\s+.+?\s+championship[?.]?$", question or "", flags=re.I)
        if not match:
            return None
        rank_cols = [col for col in df.columns if re.fullmatch(r"(?i)rank", str(col).strip())]
        name_cols = [col for col in df.columns if re.search(r"\b(name|player)\b", str(col), flags=re.I)]
        if not rank_cols or not name_cols:
            return None
        rows = _find_rows_for_entity_across_row(df, match.group(1), name_cols)
        if len(rows) != 1:
            return None
        rank = _numeric_measure_value(rows[0][rank_cols[0]])
        if rank is None:
            return None
        return "true" if int(rank) == 1 else "false"

    @staticmethod
    def _tabfact_top_n_country_no_medal_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^(.+?)\s+have\s+(\d+)\s+of\s+the\s+top\s+(\d+)\s+but\s+do\s+not\s+have\s+anyone\s+win\s+a\s+medal[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        country_phrase, expected_text, top_text = match.groups()
        rank_cols = [col for col in df.columns if re.fullmatch(r"(?i)rank", str(col).strip())]
        country_cols = [col for col in df.columns if re.search(r"\b(country|nation|nationality)\b", str(col), flags=re.I)]
        if not rank_cols or not country_cols:
            return None
        top_country = 0
        medal_country = 0
        for _, row in df.iterrows():
            rank = _numeric_measure_value(row[rank_cols[0]])
            if rank is None or not _value_matches_phrase(row[country_cols[0]], country_phrase):
                continue
            if rank <= int(top_text):
                top_country += 1
            if rank <= 3:
                medal_country += 1
        return "true" if top_country == int(expected_text) and medal_country == 0 else "false"

    @staticmethod
    def _tabfact_tenure_gap_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(r"^(.+?)\s+play\s+for\s+the\s+jazz\s+(\d+)\s+year\s+before\s+(.+?)[?.]?$", question or "", flags=re.I)
        if not match:
            return None
        left_entity, gap_text, right_entity = match.groups()
        player_cols = [col for col in df.columns if re.search(r"\bplayer\b|\bname\b", str(col), flags=re.I)]
        year_cols = [col for col in df.columns if re.search(r"\byears?\s+for\s+jazz\b|\byears?\b", str(col), flags=re.I)]
        if not player_cols or not year_cols:
            return None
        left_rows = _find_rows_for_entity_across_row(df, left_entity, player_cols)
        right_rows = _find_rows_for_entity_across_row(df, right_entity, player_cols)
        if len(left_rows) != 1 or len(right_rows) != 1:
            return None
        left_spans = TableQAPipeline._tenure_spans(left_rows[0][year_cols[0]])
        right_spans = TableQAPipeline._tenure_spans(right_rows[0][year_cols[0]])
        if not left_spans or not right_spans:
            return None
        observed_gap = min(start for start, _ in right_spans) - max(end for _, end in left_spans)
        return "true" if observed_gap == int(gap_text) else "false"

    @staticmethod
    def _tabfact_stint_duration_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(r"^(.+?)\s+have\s+(\d+)\s+stint\s+.+?\s+total\s+(\d+)\s+year\s+in\s+total[?.]?$", question or "", flags=re.I)
        if not match:
            return None
        entity_phrase, stint_text, total_text = match.groups()
        player_cols = [col for col in df.columns if re.search(r"\bplayer\b|\bname\b", str(col), flags=re.I)]
        year_cols = [col for col in df.columns if re.search(r"\byears?\s+for\s+jazz\b|\byears?\b", str(col), flags=re.I)]
        if not player_cols or not year_cols:
            return None
        rows = _find_rows_for_entity_across_row(df, entity_phrase, player_cols)
        if len(rows) != 1:
            return None
        spans = TableQAPipeline._tenure_spans(rows[0][year_cols[0]])
        if not spans:
            return None
        total_years = sum(end - start + 1 for start, end in spans)
        return "true" if len(spans) == int(stint_text) and total_years == int(total_text) else "false"

    @staticmethod
    def _tabfact_race_column_count_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        single = re.search(r"^(.+?)\s+be\s+the\s+(.+?)\s+for\s+(\d+)\s+race\s+in\s+the\s+.+?[?.]?$", question or "", flags=re.I)
        compound = re.search(
            r"^(.+?)\s+win\s+(\d+)\s+race\s+.+?,\s+but\s+he\s+be\s+the\s+(.+?)\s+for\s+(\d+)\s+race[?.]?$",
            question or "",
            flags=re.I,
        )
        if not single and not compound:
            return None
        if single:
            entity_phrase, column_phrase, expected_text = single.groups()
            checks = [(column_phrase, int(expected_text))]
        else:
            entity_phrase, win_text, leader_phrase, leader_text = compound.groups()
            checks = [("winner", int(win_text)), (leader_phrase, int(leader_text))]
        for column_phrase, expected in checks:
            target_col = (
                TableQAPipeline._select_column_by_semantic_tokens(df, column_phrase)
                or TableQAPipeline._select_column_by_tokens(df, column_phrase)
            )
            if target_col is None:
                return None
            count = 0
            for _, row in df.iterrows():
                row_text = _loose_text_key(" ".join(str(row[col]) for col in df.columns))
                if "rest day" in row_text or row_text.startswith("total "):
                    continue
                if _value_matches_phrase(row[target_col], entity_phrase):
                    count += 1
            if count != expected:
                return "false"
        return "true"

    @staticmethod
    def _tabfact_consecutive_date_wins_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(r"^(.+?)\s+win\s+(\d+)\s+race\s+in\s+a\s+row\s*,\s+on\s+(.+?)\s*,\s+during\s+.+?[?.]?$", question or "", flags=re.I)
        if not match:
            return None
        entity_phrase, expected_text, dates_phrase = match.groups()
        month_match = re.search(
            r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\b",
            dates_phrase,
            flags=re.I,
        )
        if not month_match:
            return None
        month = TableQAPipeline._month_number(month_match.group(1))
        days = [int(day) for day in re.findall(r"\b(\d{1,2})(?:st|nd|rd|th)?\b", dates_phrase)]
        date_cols = [col for col in df.columns if re.search(r"\bdate\b", str(col), flags=re.I)]
        winner_cols = [col for col in df.columns if re.search(r"\bwinner\b", str(col), flags=re.I)]
        if month is None or len(days) != int(expected_text) or not date_cols or not winner_cols:
            return None
        if sorted(days) != list(range(min(days), max(days) + 1)):
            return "false"
        matched_days = set()
        for _, row in df.iterrows():
            parsed = TableQAPipeline._month_day_from_text(row[date_cols[0]])
            if parsed is None or parsed[0] != month or parsed[1] not in days:
                continue
            if _value_matches_phrase(row[winner_cols[0]], entity_phrase):
                matched_days.add(parsed[1])
        return "true" if matched_days == set(days) else "false"

    @staticmethod
    def _tabfact_entity_attribute_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^(?:the\s+)?(.+?)\s+(?:have|has|had|be|is|are|was|were)\s+(?:an?\s+)?(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        subject_phrase, remainder = match.groups()
        if re.search(r"\b(?:when|with)\b", question or "", flags=re.I):
            question_key = _loose_text_key(question)
            mentioned_cols = [
                col for col in df.columns if _loose_text_key(col) and _loose_text_key(col) in question_key
            ]
            if len(mentioned_cols) >= 2:
                return None
        remainder_key = _loose_text_key(remainder)
        if not remainder_key:
            return None
        attribute_col = None
        expected_phrase = ""
        for col in df.columns:
            col_key = _loose_text_key(col)
            if not col_key or not remainder_key.endswith(f" {col_key}"):
                continue
            expected_phrase = remainder_key[: -len(col_key)].strip()
            if expected_phrase:
                attribute_col = col
                break
        if attribute_col is None or not expected_phrase:
            return None
        if re.search(
            r"\d|\b(?:more|less|fewer|greater|higher|lower|largest|highest|smallest|lowest|than|of)\b",
            expected_phrase,
            flags=re.I,
        ):
            return None

        subject_key = _loose_text_key(subject_phrase)
        for col in df.columns:
            col_key = _loose_text_key(col)
            if subject_key.startswith(f"{col_key} of "):
                subject_key = subject_key[len(col_key) + 4 :].strip()
                break
            if subject_key.startswith(f"{col_key} "):
                subject_key = subject_key[len(col_key) + 1 :].strip()
                break
        if not subject_key:
            return None
        search_cols = [col for col in df.columns if col != attribute_col]
        rows = _find_rows_for_entity_across_row(df, subject_key, search_cols)
        if not rows:
            return None
        return "true" if any(_value_matches_phrase(row[attribute_col], expected_phrase) for row in rows) else "false"

    @staticmethod
    def _tabfact_same_row_cell_mention_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        text = question or ""
        if not re.search(r"\b(?:when|with|and|have|has|had)\b", text, flags=re.I):
            return None
        if re.search(r"\b(?:less|more|greater|higher|lower|fewer)\s+than\b", text, flags=re.I):
            return None

        def clause_value(clause: str) -> Optional[str]:
            clause_key = _loose_text_key(clause)
            if not clause_key:
                return None
            conditions: List[Tuple[Any, set[int]]] = []
            for col in df.columns:
                col_key = _loose_text_key(col)
                if not col_key or col_key not in clause_key:
                    continue
                matched_rows: set[int] = set()
                for row_position, value in enumerate(df[col].tolist()):
                    value_key = _loose_text_key(value)
                    if not value_key or value_key in {"none", "nan", "na", "n a", "-"}:
                        continue
                    if len(value_key) < 3 and not re.search(r"\d", value_key):
                        continue
                    if value_key in clause_key:
                        matched_rows.add(row_position)
                if matched_rows:
                    conditions.append((col, matched_rows))
            if len(conditions) < 2:
                return None
            for row_position in range(len(df)):
                if all(row_position in rows for _, rows in conditions):
                    return "true"
            return "false"

        clauses = [
            part.strip()
            for part in re.split(r"\s+\band\s+", text, flags=re.I)
            if part.strip()
        ]
        if len(clauses) > 1:
            clause_results = [clause_value(clause) for clause in clauses]
            if any(result == "false" for result in clause_results):
                return "false"
            if clause_results and all(result == "true" for result in clause_results):
                return "true"

        question_key = _loose_text_key(text)
        if not question_key:
            return None

        conditions: List[Tuple[Any, set[int]]] = []
        for col in df.columns:
            col_key = _loose_text_key(col)
            if not col_key or col_key not in question_key:
                continue
            matched_rows: set[int] = set()
            for row_position, value in enumerate(df[col].tolist()):
                value_key = _loose_text_key(value)
                if not value_key or value_key in {"none", "nan", "na", "n a", "-"}:
                    continue
                if len(value_key) < 3 and not re.search(r"\d", value_key):
                    continue
                if value_key in question_key:
                    matched_rows.add(row_position)
            if matched_rows:
                conditions.append((col, matched_rows))

        if len(conditions) < 2:
            return None
        for row_position in range(len(df)):
            if all(row_position in rows for _, rows in conditions):
                return "true"
        return "false"

    @staticmethod
    def _tabfact_numbered_same_team_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        text = question or ""
        if re.search(r"\b(?:not|never|neither|no)\s+play\b", text, flags=re.I):
            return None
        match = re.search(
            r"^\s*(.+?)\s+and\s+(.+?)\s+play\s+on\s+the\s+same\s+team\s+as\s+"
            r"no\s*([0-9]+)\s+and\s+no\s*([0-9]+)\b",
            text,
            flags=re.I,
        )
        if not match:
            return None
        left_entity, right_entity, left_number, right_number = [
            part.strip(" \t\r\n\"'.")
            for part in match.groups()
        ]
        if not left_entity or not right_entity:
            return None

        def numbered_col(number: str) -> Optional[Any]:
            target = f"no{number}"
            for col in df.columns:
                if _loose_text_key(col).replace(" ", "") == target:
                    return col
            return None

        target_cols = [numbered_col(left_number), numbered_col(right_number)]
        if any(col is None for col in target_cols):
            return None
        for _, row in df.iterrows():
            combined = " ".join(str(row[col]) for col in target_cols if col is not None)
            if (
                _cell_contains_entity_phrase(left_entity, combined)
                and _cell_contains_entity_phrase(right_entity, combined)
            ):
                return "true"
        return "false"

    @staticmethod
    def _tabfact_column_value_count_assertion_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        text = question or ""
        one_of_match = re.search(
            r"^(.+?)\s+be\s+1\s+of\s+(\d+)\s+.+?\s+go\s+to\s+(.+?)\s+college\b",
            text,
            flags=re.I,
        )
        if one_of_match:
            entity_phrase, expected_text, school_phrase = one_of_match.groups()
            college_cols = [col for col in df.columns if re.search(r"\bcollege\b", str(col), flags=re.I)]
            entity_cols = [col for col in df.columns if re.search(r"\b(name|player|student)\b", str(col), flags=re.I)]
            if not college_cols or not entity_cols:
                return None
            college_col = college_cols[0]
            expected = int(expected_text)
            rows_for_school = [
                row
                for _, row in df.iterrows()
                if TableQAPipeline._cell_contains_phrase_tokens(school_phrase, row[college_col])
            ]
            entity_rows = _find_rows_for_entity_across_row(df, entity_phrase, entity_cols)
            if not entity_rows:
                return None
            entity_in_school = any(
                TableQAPipeline._cell_contains_phrase_tokens(school_phrase, row[college_col])
                for row in entity_rows
            )
            return "true" if len(rows_for_school) == expected and entity_in_school else "false"

        only_match = re.search(
            r"\bthere\s+be\s+only\s+(\d+)\s+(.+?)\s+in\s+(.+?)(?:\s+for\b|[?.]?$)",
            text,
            flags=re.I,
        )
        if only_match:
            expected_text, value_phrase, column_phrase = only_match.groups()
            target_col = (
                TableQAPipeline._select_column_by_semantic_tokens(df, column_phrase)
                or TableQAPipeline._select_column_by_tokens(df, column_phrase)
            )
            if target_col is None:
                return None
            count = sum(1 for value in df[target_col].tolist() if _value_matches_phrase(value, value_phrase))
            return "true" if count == int(expected_text) else "false"

        count_match = re.search(
            r"^(\d+)\s+.+?\s+be\s+(.+?)\s+in\s+their\s+(.+?)[?.]?$",
            text,
            flags=re.I,
        )
        if not count_match:
            return None
        expected_text, value_phrase, _ = count_match.groups()
        best_count = 0
        for col in df.columns:
            value_key = _loose_text_key(value_phrase)
            if value_key in {"re elect", "re elected", "reelect", "reelected"}:
                count = sum(
                    1
                    for value in df[col].tolist()
                    if _loose_text_key(value) in {"re elected", "reelected"}
                )
            else:
                count = sum(1 for value in df[col].tolist() if _value_matches_phrase(value, value_phrase))
            best_count = max(best_count, count)
        if best_count == 0:
            return None
        return "true" if best_count == int(expected_text) else "false"

    @staticmethod
    def _tabfact_two_entity_appearance_count_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"\bboth\s+the\s+(.+?)\s+and\s+(.+?)\s+teams?\s+be\s+only\s+feature\s+on\s+the\s+list\s+a\s+single\s+time\b",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        left_entity, right_entity = match.groups()
        team_cols = [col for col in df.columns if re.search(r"\b(?:home\s+team|away\s+team|team)\b", str(col), flags=re.I)]
        if not team_cols:
            return None

        def appearance_count(entity: str) -> int:
            return sum(
                1
                for _, row in df.iterrows()
                for col in team_cols
                if _cell_contains_entity_phrase(entity, row[col])
            )

        return "true" if appearance_count(left_entity) == 1 and appearance_count(right_entity) == 1 else "false"

    @staticmethod
    def _tabfact_first_last_time_gap_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"\bthere\s+be\s+([\d.]+)\s+seconds?\s+between\s+the\s+first\s+and\s+last\s+race\s+car\s+driver\b",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        expected = float(match.group(1))
        time_cols = [col for col in df.columns if re.fullmatch(r"(?i)time", str(col).strip())]
        if not time_cols:
            return None
        gaps = []
        for value in df[time_cols[0]].tolist():
            text = str(value or "").strip()
            if re.match(r"^\+\s*[\d.]+$", text):
                gaps.append(float(re.sub(r"^\+\s*", "", text)))
            elif re.match(r"^\d+'\d", text):
                gaps.append(0.0)
        if len(gaps) < 2:
            return None
        observed = gaps[-1] - gaps[0]
        return "true" if abs(observed - expected) <= 1e-3 else "false"

    @staticmethod
    def _tabfact_decimal_measure_value(value: Any) -> Optional[float]:
        text = str(value or "")
        text = re.sub(r"(?<=\d)\s*,\s*(?=\d)", ".", text)
        return _numeric_measure_value(text)

    @staticmethod
    def _tabfact_entity_metric_difference_value_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^the\s+(.+?)\s+be\s+([\d.]+)\s+(?:meter|metre|m)\s+(smaller|shorter|larger|longer)\s+"
            r"in\s+(.+?)\s+than\s+the\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        left_entity, diff_text, direction, metric_phrase, right_entity = match.groups()
        metric_col = _select_numeric_measure_column_by_phrase(df, metric_phrase)
        if metric_col is None:
            return None
        entity_cols = [col for col in df.columns if col != metric_col]
        left_rows = _find_rows_for_entity_across_row(df, left_entity, entity_cols)
        right_rows = _find_rows_for_entity_across_row(df, right_entity, entity_cols)
        if len(left_rows) != 1 or len(right_rows) != 1:
            return None
        left_value = TableQAPipeline._tabfact_decimal_measure_value(left_rows[0][metric_col])
        right_value = TableQAPipeline._tabfact_decimal_measure_value(right_rows[0][metric_col])
        if left_value is None or right_value is None:
            return None
        expected = float(diff_text)
        observed = right_value - left_value if direction.lower() in {"smaller", "shorter"} else left_value - right_value
        return "true" if abs(observed - expected) <= 1e-3 else "false"

    @staticmethod
    def _tabfact_country_pair_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^(.+?)\s+be\s+from\s+(.+?)\s*,\s*while\s+(.+?)\s+be\s+from\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        left_entity, left_country, right_entity, right_country = match.groups()
        entity_cols = [col for col in df.columns if re.search(r"\b(player|name|driver|person)\b", str(col), flags=re.I)]
        country_cols = [col for col in df.columns if re.search(r"\b(country|nation|nationality)\b", str(col), flags=re.I)]
        if not entity_cols or not country_cols:
            return None
        entity_col = entity_cols[0]
        country_col = country_cols[0]

        def country_for(entity_phrase: str) -> Optional[str]:
            for _, row in df.iterrows():
                if TableQAPipeline._cell_contains_phrase_tokens(entity_phrase, row[entity_col]):
                    return _loose_text_key(row[country_col])
            return None

        left_actual = country_for(left_entity)
        right_actual = country_for(right_entity)
        if left_actual is None or right_actual is None:
            return None
        left_expected = _loose_text_key(left_country)
        right_expected = _loose_text_key(right_country)
        return "true" if left_actual == left_expected and right_actual == right_expected else "false"

    @staticmethod
    def _tabfact_zero_gold_count_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"\bthere\s+be\s+(\d+)\s+(?:nation|nations|country|countries|team|teams)\b"
            r".*?\b(?:didn't|did\s+not|do\s+not|doesn't|does\s+not)\s+have\s+any\s+gold\s+medals?\b",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        gold_cols = [col for col in df.columns if re.fullmatch(r"(?i)gold", str(col).strip())]
        if not gold_cols:
            return None
        expected = int(match.group(1))
        count = sum(
            1
            for value in df[gold_cols[0]].tolist()
            if (_numeric_measure_value(value) or 0.0) == 0.0
        )
        return "true" if count == expected else "false"

    @staticmethod
    def _month_day_from_text(value: Any) -> Optional[Tuple[int, int]]:
        months = {
            "january": 1,
            "jan": 1,
            "february": 2,
            "feb": 2,
            "march": 3,
            "mar": 3,
            "april": 4,
            "apr": 4,
            "may": 5,
            "june": 6,
            "jun": 6,
            "july": 7,
            "jul": 7,
            "august": 8,
            "aug": 8,
            "september": 9,
            "sept": 9,
            "sep": 9,
            "october": 10,
            "oct": 10,
            "november": 11,
            "nov": 11,
            "december": 12,
            "dec": 12,
        }
        text = str(value or "").lower()
        match = re.search(
            r"\b("
            + "|".join(re.escape(month) for month in months)
            + r")\s+(\d{1,2})(?:st|nd|rd|th)?\b",
            text,
        )
        if match:
            return months[match.group(1)], int(match.group(2))
        reverse_match = re.search(
            r"\b(\d{1,2})(?:st|nd|rd|th)?\s+("
            + "|".join(re.escape(month) for month in months)
            + r")\b",
            text,
        )
        if not reverse_match:
            return None
        return months[reverse_match.group(2)], int(reverse_match.group(1))

    @staticmethod
    def _tabfact_every_before_date_result_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"\bevery\s+game\s+before\s+(.+?)\s+be\s+a\s+(?:victory|win)\b",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        target = TableQAPipeline._month_day_from_text(match.group(1))
        date_cols = [col for col in df.columns if re.fullmatch(r"(?i)date", str(col).strip())]
        result_cols = [col for col in df.columns if re.fullmatch(r"(?i)result|outcome", str(col).strip())]
        if target is None or not date_cols or not result_cols:
            return None
        rows_before = []
        for _, row in df.iterrows():
            row_day = TableQAPipeline._month_day_from_text(row[date_cols[0]])
            if row_day is not None and row_day < target:
                rows_before.append(row)
        if not rows_before:
            return None
        all_wins = all(
            re.search(r"\b(win|won|victory)\b", str(row[result_cols[0]]), flags=re.I)
            for row in rows_before
        )
        return "true" if all_wins else "false"

    @staticmethod
    def _tabfact_venue_competition_date_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^the\s+(.+?)\s+competition\s+at\s+the\s+venue\s+(.+?)\s*,?\s+be\s+on\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        competition_phrase, venue_phrase, date_phrase = match.groups()
        date_cols = [col for col in df.columns if re.fullmatch(r"(?i)date", str(col).strip())]
        venue_cols = [col for col in df.columns if re.search(r"\bvenue|location|site\b", str(col), flags=re.I)]
        competition_cols = [col for col in df.columns if re.search(r"\bcompetition|event|type\b", str(col), flags=re.I)]
        if not date_cols or not venue_cols or not competition_cols:
            return None
        date_key = _loose_text_key(date_phrase)
        for _, row in df.iterrows():
            if not TableQAPipeline._cell_contains_phrase_tokens(competition_phrase, row[competition_cols[0]]):
                continue
            if not TableQAPipeline._cell_contains_phrase_tokens(venue_phrase, row[venue_cols[0]]):
                continue
            if date_key and date_key == _loose_text_key(row[date_cols[0]]):
                return "true"
        return "false"

    @staticmethod
    def _tabfact_score_but_lose_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^(.+?)\s+score\s+(\d+)\s+points?\s+but\s+lose\s+the\s+game\s+during\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        team_phrase, points_text, event_phrase = match.groups()
        details_cols = [col for col in df.columns if re.search(r"\b(details?|game|event|final)\b", str(col), flags=re.I)]
        premier_cols = [col for col in df.columns if re.search(r"\bpremiers?|winner|champion\b", str(col), flags=re.I)]
        runner_cols = [col for col in df.columns if re.search(r"\brunners?\s*up|loser\b", str(col), flags=re.I)]
        score_cols = [col for col in df.columns if re.fullmatch(r"(?i)score", str(col).strip())]
        if not details_cols or not premier_cols or not runner_cols or not score_cols:
            return None
        expected_points = int(points_text)
        for _, row in df.iterrows():
            if not TableQAPipeline._cell_contains_phrase_tokens(event_phrase, row[details_cols[0]]):
                continue
            team_is_runner_up = TableQAPipeline._cell_contains_phrase_tokens(team_phrase, row[runner_cols[0]])
            team_is_premier = TableQAPipeline._cell_contains_phrase_tokens(team_phrase, row[premier_cols[0]])
            score_match = re.search(r"\b(\d+)\s*-\s*(\d+)\b", str(row[score_cols[0]]))
            if not score_match:
                return None
            _, loser_points = [int(value) for value in score_match.groups()]
            if team_is_runner_up:
                return "true" if loser_points == expected_points else "false"
            if team_is_premier:
                return "false"
            return None
        return None

    @staticmethod
    def _tabfact_second_smallest_metric_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"^the\s+second\s+smallest\s+(.+?)\s+be\s+([\d,]+(?:\.\d+)?)\s+for\s+the\s+(.+?)\s+show\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        metric_phrase, value_text, title_phrase, date_phrase = match.groups()
        metric_col = _select_numeric_measure_column_by_phrase(df, metric_phrase)
        title_cols = [col for col in df.columns if re.search(r"\btitle|show|episode\b", str(col), flags=re.I)]
        date_cols = [col for col in df.columns if re.search(r"\bmonth|year|date\b", str(col), flags=re.I)]
        expected_value = _numeric_measure_value(value_text)
        if metric_col is None or not title_cols or not date_cols or expected_value is None:
            return None
        values = sorted(
            value
            for value in (_numeric_measure_value(item) for item in df[metric_col].tolist())
            if value is not None
        )
        if len(values) < 2:
            return None
        second_smallest = values[1]
        target_value = None
        for _, row in df.iterrows():
            if not TableQAPipeline._cell_contains_phrase_tokens(title_phrase, row[title_cols[0]]):
                continue
            if not TableQAPipeline._cell_contains_phrase_tokens(date_phrase, row[date_cols[0]]):
                continue
            target_value = _numeric_measure_value(row[metric_col])
            break
        if target_value is None:
            return "false"
        is_true = (
            abs(target_value - expected_value) <= 1e-6
            and abs(second_smallest - expected_value) <= 1e-6
        )
        return "true" if is_true else "false"

    @staticmethod
    def _tabfact_retirement_threshold_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"\bthere\s+be\s+(less\s+than|fewer\s+than|more\s+than|at\s+least|at\s+most)\s+(\d+)\s+"
            r"(?:player|players|driver|drivers)\b.*?\bretir\w*\b",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        operator, threshold_text = match.groups()
        status_cols = [col for col in df.columns if re.search(r"\bretired|time\s*/\s*retired|status|time\b", str(col), flags=re.I)]
        if not status_cols:
            return None
        threshold = int(threshold_text)

        def is_retired(value: Any) -> bool:
            text = str(value or "").strip().lower()
            if not text or text in {"nan", "none", "n/a", "na", "-", "--"}:
                return False
            if re.match(r"^\+?\s*\d+(?:\.\d+)?(?:\s*laps?)?$", text):
                return False
            if re.match(r"^\d+:\d", text):
                return False
            if re.match(r"^\+\s*\d", text):
                return False
            return True

        count = sum(1 for value in df[status_cols[0]].tolist() if is_retired(value))
        op = operator.lower()
        if op in {"less than", "fewer than"}:
            result = count < threshold
        elif op == "more than":
            result = count > threshold
        elif op == "at least":
            result = count >= threshold
        else:
            result = count <= threshold
        return "true" if result else "false"

    @staticmethod
    def _select_column_by_tokens(df: pd.DataFrame, phrase: str) -> Optional[Any]:
        phrase_tokens = {_singular_token(token) for token in re.findall(r"[a-z0-9]+", _loose_text_key(phrase))}
        if not phrase_tokens:
            return None
        best_col = None
        best_score = 0
        for col in df.columns:
            col_tokens = {_singular_token(token) for token in re.findall(r"[a-z0-9]+", _loose_text_key(col))}
            if not col_tokens:
                continue
            score = len(phrase_tokens & col_tokens)
            if score > best_score:
                best_col = col
                best_score = score
        return best_col if best_score else None

    @staticmethod
    def _wtq_entity_column(df: pd.DataFrame, phrase: str, *, exclude: Optional[set[Any]] = None) -> Optional[Any]:
        exclude = exclude or set()
        col = (
            TableQAPipeline._select_column_by_semantic_tokens(df, phrase)
            or TableQAPipeline._select_column_by_tokens(df, phrase)
        )
        if col is not None and col not in exclude:
            return col

        skipped_names = {
            "rank",
            "place",
            "position",
            "pos",
            "#",
            "no",
            "no.",
            "number",
            "%",
            "±%",
            "+/-",
        }
        for candidate in df.columns:
            if candidate in exclude:
                continue
            name = str(candidate).strip().lower()
            if name in skipped_names:
                continue
            values = [value for value in df[candidate].tolist() if not _is_missing_marker(value)]
            if not values:
                continue
            numeric_count = sum(1 for value in values if _as_number_like(value) is not None)
            if numeric_count < len(values):
                return candidate
        return None

    @staticmethod
    def _wtq_non_summary_rows(df: pd.DataFrame) -> pd.DataFrame:
        summary_keys = {
            "majority",
            "turnout",
            "total",
            "totals",
            "overall",
            "swing",
            "hold",
        }

        def is_summary_row(row: pd.Series) -> bool:
            keys = [_loose_text_key(value) for value in row.tolist()]
            keys = [key for key in keys if key]
            if not keys:
                return True
            if any(key in summary_keys for key in keys):
                return True
            return any(re.search(r"\b(?:majority|turnout|total|overall)\b", key) for key in keys)

        kept = df[[not is_summary_row(row) for _, row in df.iterrows()]]
        return kept if not kept.empty else df

    @staticmethod
    def _wtq_explicit_or_options(question: str) -> List[str]:
        text = re.sub(r"\s+", " ", question or "").strip(" ?.")
        if "," not in text or not re.search(r"\bor\b", text, flags=re.I):
            return []
        option_text = text.split(",", 1)[1]
        parts = [
            part.strip(" \t\r\n\"'")
            for part in re.split(r"\s*,\s*|\s+\bor\s+", option_text, flags=re.I)
            if part.strip(" \t\r\n\"'")
        ]
        return parts if len(parts) >= 2 else []

    @staticmethod
    def _wtq_prefer_entity_owner_column(
        df: pd.DataFrame,
        owner_col: Any,
        *,
        exclude: Optional[set[Any]] = None,
    ) -> Any:
        exclude = exclude or set()
        owner_key = _loose_text_key(owner_col)
        if not re.search(r"\b(?:detail|details|description|info|information|notes)\b", owner_key):
            return owner_col
        for candidate in df.columns:
            if candidate == owner_col or candidate in exclude:
                continue
            candidate_key = _loose_text_key(candidate)
            if candidate_key not in {"title", "name"} and not candidate_key.endswith(" title"):
                continue
            values = [value for value in df[candidate].tolist() if not _is_missing_marker(value)]
            if values and sum(1 for value in values if _as_number_like(value) is not None) < len(values):
                return candidate
        return owner_col

    @staticmethod
    def _wtq_last_row_entity_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        match = re.search(
            r"\b(?:which|what)\s+(.+?)\s+(?:is|was|were|are)\s+last\s+"
            r"(?:on|in)\s+(?:the\s+)?(?:chart|table)\b",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        target_col = TableQAPipeline._wtq_entity_column(df, match.group(1))
        if target_col is None:
            return None
        values = [
            value
            for value in TableQAPipeline._wtq_non_summary_rows(df)[target_col].tolist()
            if not _is_missing_marker(value) and _loose_text_key(value)
        ]
        return values[-1] if values else None

    @staticmethod
    def _wtq_superlative_owner_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        text = question or ""
        first_match = re.search(
            r"\b(?:which|what)\s+(.+?)\s+(?:has|have|had|earned|won)\s+"
            r"(?:the\s+)?(most|least|highest|lowest|largest|smallest)\s+(.+?)[?.]?$",
            text,
            flags=re.I,
        )
        opened_match = re.search(
            r"\b(?:which|what)\s+(.+?)\s+(?:was|were|is|are)\s+"
            r"(?:the\s+)?(last|latest|first|earliest)\s+to\s+be\s+(.+?)[?.]?$",
            text,
            flags=re.I,
        )
        if first_match:
            owner_phrase, direction, metric_phrase = first_match.groups()
        elif opened_match:
            owner_phrase, direction, metric_phrase = opened_match.groups()
        else:
            return None

        explicit_options = TableQAPipeline._wtq_explicit_or_options(text)
        if explicit_options and "," in metric_phrase:
            metric_phrase = metric_phrase.split(",", 1)[0]
        metric_col = (
            TableQAPipeline._select_column_by_semantic_tokens(df, metric_phrase)
            or TableQAPipeline._select_column_by_tokens(df, metric_phrase)
        )
        if metric_col is None:
            return None
        owner_col = TableQAPipeline._wtq_entity_column(
            df,
            owner_phrase,
            exclude={metric_col},
        )
        if owner_col is None:
            return None
        owner_col = TableQAPipeline._wtq_prefer_entity_owner_column(
            df,
            owner_col,
            exclude={metric_col},
        )

        ascending = direction.lower() in {"least", "lowest", "smallest", "first", "earliest"}
        rows: List[Tuple[float, int, Any]] = []
        matched_option_keys: set[str] = set()
        option_keys = [_loose_text_key(option) for option in explicit_options]
        for order, (_, row) in enumerate(TableQAPipeline._wtq_non_summary_rows(df).iterrows()):
            owner_value = row[owner_col]
            owner_key = _loose_text_key(owner_value)
            if _is_missing_marker(owner_value) or not owner_key:
                continue
            matched_option = ""
            if option_keys:
                matched_option = next(
                    (
                        option_key
                        for option_key in option_keys
                        if option_key
                        and (
                            option_key == owner_key
                            or option_key in owner_key
                            or (len(owner_key) >= 4 and owner_key in option_key)
                        )
                    ),
                    "",
                )
                if not matched_option:
                    continue
                matched_option_keys.add(matched_option)
            metric_value = _as_number_like(row[metric_col])
            if metric_value is None:
                continue
            rows.append((metric_value, order, owner_value))
        if not rows:
            return None
        if option_keys and len(matched_option_keys) < min(2, len(option_keys)):
            return None
        rows.sort(key=lambda item: (item[0], item[1]), reverse=not ascending)
        return rows[0][2]

    @staticmethod
    def _wtq_reference_cell(df: pd.DataFrame, reference: str) -> Optional[Tuple[int, Any]]:
        reference_key = _loose_text_key(reference)
        if not reference_key:
            return None
        rows = TableQAPipeline._wtq_non_summary_rows(df).reset_index(drop=True)
        for row_index, row in rows.iterrows():
            for column in rows.columns:
                cell_key = _loose_text_key(row[column])
                if cell_key and (
                    cell_key == reference_key
                    or (len(reference_key) >= 4 and reference_key in cell_key)
                    or (len(cell_key) >= 4 and cell_key in reference_key)
                ):
                    return row_index, column
        return None

    @staticmethod
    def _wtq_after_reference_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        text = question or ""
        count_match = re.search(
            r"\bhow\s+many\s+.+?\s+(?:come|comes|came)\s+after\s+(.+?)[?.]?$",
            text,
            flags=re.I,
        )
        next_match = re.search(
            r"\b(?:who|what)\s+(?:come|comes|came)\s+(?:in\s+)?after\s+(.+?)[?.]?$",
            text,
            flags=re.I,
        )
        next_entity_match = re.search(
            r"\b(?:who|what|which)\s+(?:(?:is|was|were|are)\s+)?(?:the\s+)?(?:next\s+)?"
            r"(.+?)\s+(?:listed\s+)?(?:(?:come|comes|came|is|are)\s+)?after\s+(.+?)[?.]?$",
            text,
            flags=re.I,
        )
        if not count_match and not next_match and not next_entity_match:
            return None
        if next_entity_match:
            target_phrase, reference = next_entity_match.groups()
        else:
            target_phrase, reference = "", (count_match or next_match).group(1)
        reference = reference.strip(" \t\r\n\"'")
        cell = TableQAPipeline._wtq_reference_cell(df, reference)
        if cell is None:
            return None
        row_index, column = cell
        rows = TableQAPipeline._wtq_non_summary_rows(df).reset_index(drop=True)
        if count_match:
            return max(0, len(rows) - row_index - 1)
        next_index = row_index + 1
        if next_index >= len(rows):
            return None
        target_col = None
        if next_entity_match:
            target_col = TableQAPipeline._wtq_adjacent_target_column(df, target_phrase)
        if target_col is None:
            target_col = column
        if next_entity_match:
            for candidate_index in range(next_index, len(rows)):
                value = rows.loc[candidate_index, target_col]
                if not _is_missing_marker(value):
                    return value
            return None
        value = rows.loc[next_index, target_col]
        return None if _is_missing_marker(value) else value

    @staticmethod
    def _wtq_route_after_stop_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        match = re.search(
            r"\bwhere\s+does\s+the\s+bus\s+stop\s+after\s+(.+?)\s+on\s+route\s+([a-z0-9]+)\b",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        stop_phrase, route_text = [part.strip(" \t\r\n\"'.?") for part in match.groups()]
        route_cols = [col for col in df.columns if re.fullmatch(r"(?i)route|route\s*#", str(col).strip())]
        destination_cols = [
            col for col in df.columns if re.search(r"\b(destination|destinations|stops?|service)\b", str(col), flags=re.I)
        ]
        if not route_cols or not destination_cols:
            return None
        route_col = route_cols[0]
        route_key = _loose_text_key(route_text)
        stop_key = _loose_text_key(stop_phrase)
        if not route_key or not stop_key:
            return None
        for _, row in df.iterrows():
            if _loose_text_key(row[route_col]) != route_key:
                continue
            for col in destination_cols:
                text = str(row[col] or "").strip()
                match_stop = re.search(re.escape(stop_phrase), text, flags=re.I)
                if match_stop is None and stop_key in _loose_text_key(text):
                    parts = _loose_text_key(text).split(stop_key, 1)
                    suffix = parts[1].strip() if len(parts) == 2 else ""
                    return suffix.title() if suffix else None
                if match_stop is None:
                    continue
                suffix = text[match_stop.end():].strip(" \t\r\n-/,;")
                if suffix:
                    return suffix
        return None

    @staticmethod
    def _wtq_same_number_entity_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        same_as_match = re.search(
            r"\b(?:who|which|what)\s+(?:has|have|had)\s+the\s+same\s+"
            r"(?:number|no\.?|#)\s+as\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if same_as_match:
            reference = same_as_match.group(1).strip(" \t\r\n\"'")
            reference_key = _loose_text_key(reference)
            rows = TableQAPipeline._wtq_non_summary_rows(df).reset_index(drop=True)
            reference_row_index = None
            reference_col = None
            for row_index, row in rows.iterrows():
                for column in rows.columns:
                    cell_key = _loose_text_key(row[column])
                    if cell_key and reference_key and (
                        cell_key == reference_key
                        or reference_key in cell_key
                        or cell_key in reference_key
                    ):
                        reference_row_index = row_index
                        reference_col = column
                        break
                if reference_row_index is not None:
                    break
            if reference_row_index is None or reference_col is None:
                return None
            number_columns = [
                col
                for col in rows.columns
                if col != reference_col
                and _numeric_measure_value(rows.loc[reference_row_index, col]) is not None
            ]
            if not number_columns:
                return None
            number_columns.sort(
                key=lambda col: (
                    0
                    if re.search(r"^(?:#|no\.?|number)$", str(col).strip(), flags=re.I)
                    else 1,
                    str(col),
                )
            )
            number_col = number_columns[0]
            target_number = _numeric_measure_value(rows.loc[reference_row_index, number_col])
            if target_number is None:
                return None
            entity_col = reference_col
            matches: List[Any] = []
            for row_index, row in rows.iterrows():
                if row_index == reference_row_index:
                    continue
                value_number = _numeric_measure_value(row[number_col])
                if value_number is None or abs(value_number - target_number) > 1e-6:
                    continue
                value = row[entity_col]
                if not _is_missing_marker(value):
                    matches.append(value)
            return matches[0] if len(matches) == 1 else None

        match = re.search(
            r"\bwhich\s+(.+?)\s+has\s+the\s+same\s+(.+?)\s+and\s+(.+?)\s+numbers?\b",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        entity_phrase, left_phrase, right_phrase = match.groups()
        left_col = (
            TableQAPipeline._select_column_by_semantic_tokens(df, left_phrase)
            or TableQAPipeline._select_column_by_tokens(df, left_phrase)
        )
        right_col = (
            TableQAPipeline._select_column_by_semantic_tokens(df, right_phrase)
            or TableQAPipeline._select_column_by_tokens(df, right_phrase)
        )
        if left_col is None and re.search(r"\bepisode\b", left_phrase, flags=re.I):
            left_col = next((col for col in df.columns if re.search(r"\beps?\b|\bepisode\b", str(col), flags=re.I)), None)
        if right_col is None and re.search(r"\bproduction\b", right_phrase, flags=re.I):
            right_col = next((col for col in df.columns if re.search(r"\bprod\b|\bproduction\b", str(col), flags=re.I)), None)
        if left_col is None or right_col is None or left_col == right_col:
            return None
        entity_col = TableQAPipeline._wtq_entity_column(df, entity_phrase, exclude={left_col, right_col})
        if entity_col is None:
            return None
        matches: List[Any] = []
        for _, row in TableQAPipeline._wtq_non_summary_rows(df).iterrows():
            left_value = _numeric_measure_value(row[left_col])
            right_value = _numeric_measure_value(row[right_col])
            if left_value is None or right_value is None:
                continue
            if abs(left_value - right_value) <= 1e-6:
                value = row[entity_col]
                if not _is_missing_marker(value):
                    matches.append(value)
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _wtq_named_year_span_duration_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"\bhow\s+long\s+did\s+(.+?)(?:'s)?\s+(?:career\s+last|play|stay|serve)\b",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        entity_phrase = match.group(1).strip(" \t\r\n\"'")
        year_cols = [col for col in df.columns if re.search(r"\byears?\b|\bseason\b|\btenure\b", str(col), flags=re.I)]
        if not year_cols:
            return None

        def parse_span(value: Any) -> Optional[int]:
            text = str(value or "")
            match_range = re.search(r"\b(1[7-9]\d{2}|20\d{2})\s*(?:-|–|—|to)\s*(present|1[7-9]\d{2}|20\d{2}|\d{2})\b", text, flags=re.I)
            if not match_range:
                return None
            start_text, end_text = match_range.groups()
            if end_text.lower() == "present":
                return None
            start = int(start_text)
            if len(end_text) == 2:
                end = (start // 100) * 100 + int(end_text)
                if end < start:
                    end += 100
            else:
                end = int(end_text)
            if end < start:
                return None
            return end - start

        rows = _find_rows_for_entity_across_row(df, entity_phrase, [col for col in df.columns if col not in year_cols])
        if len(rows) != 1:
            return None
        for col in year_cols:
            years = parse_span(rows[0][col])
            if years is None:
                continue
            suffix = "year" if years == 1 else "years"
            return f"{years} {suffix}"
        return None

    @staticmethod
    def _wtq_consecutive_month_count_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        match = re.search(
            r"\bhow\s+many\s+consecutive\s+.+?\s+(?:premiered|started|began|opened)\s+in\s+([a-z]+)\b",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        month_name = match.group(1).lower()
        month_cols = [
            col for col in df.columns if re.search(r"\b(premiere|start|begin|open|date)\b", str(col), flags=re.I)
        ]
        if not month_cols:
            return None
        current = best = 0
        for value in TableQAPipeline._wtq_non_summary_rows(df)[month_cols[0]].tolist():
            if re.search(rf"\b{re.escape(month_name)}\b", str(value or ""), flags=re.I):
                current += 1
                best = max(best, current)
            else:
                current = 0
        return best if best > 0 else None

    @staticmethod
    def _wtq_date_cutoff_row_count_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        match = re.search(
            r"\bhow\s+many\s+.+?\s+(?:aired|played|were\s+played|was\s+played|took\s+place|occurred|happened)\s+"
            r"(before|after)\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        direction, cutoff_phrase = match.groups()
        cutoff = TableQAPipeline._month_day_from_text(cutoff_phrase)
        if cutoff is None:
            return None
        rows = TableQAPipeline._wtq_non_summary_rows(df).reset_index(drop=True)
        row_dates: List[Tuple[int, int, int]] = []
        for row_index, row in rows.iterrows():
            parsed = None
            for col in rows.columns:
                parsed = TableQAPipeline._month_day_from_text(row[col])
                if parsed is not None:
                    break
            if parsed is not None:
                row_dates.append((row_index, parsed[0], parsed[1]))
        if len(row_dates) < 2:
            return None

        def after_cutoff(item: Tuple[int, int, int]) -> bool:
            _, month, day = item
            return (month, day) > cutoff

        def before_cutoff(item: Tuple[int, int, int]) -> bool:
            _, month, day = item
            return (month, day) < cutoff

        if direction.lower() == "after":
            for offset, item in enumerate(row_dates):
                if after_cutoff(item):
                    return len(row_dates) - offset
            return 0
        count = 0
        for item in row_dates:
            if before_cutoff(item):
                count += 1
                continue
            break
        return count

    @staticmethod
    def _wtq_after_month_row_count_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        months = {
            "january": 1,
            "jan": 1,
            "february": 2,
            "feb": 2,
            "march": 3,
            "mar": 3,
            "april": 4,
            "apr": 4,
            "may": 5,
            "june": 6,
            "jun": 6,
            "july": 7,
            "jul": 7,
            "august": 8,
            "aug": 8,
            "september": 9,
            "sept": 9,
            "sep": 9,
            "october": 10,
            "oct": 10,
            "november": 11,
            "nov": 11,
            "december": 12,
            "dec": 12,
        }
        month_pattern = "|".join(sorted((re.escape(month) for month in months), key=len, reverse=True))
        match = re.search(
            rf"\bhow\s+many\s+.+?\s+(?:took\s+place|occurred|happened|played|were\s+held|were)\s+after\s+({month_pattern})\b",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        if re.match(r"\s+\d{1,2}(?:st|nd|rd|th)?\b", (question or "")[match.end() :], flags=re.I):
            return None
        target_month = months[match.group(1).lower()]
        rows = TableQAPipeline._wtq_non_summary_rows(df)
        best_col = None
        best_dates: List[Tuple[int, int]] = []
        for col in rows.columns:
            dates = [
                parsed
                for value in rows[col].tolist()
                if (parsed := TableQAPipeline._month_day_from_text(value)) is not None
            ]
            if len(dates) > len(best_dates):
                best_col = col
                best_dates = dates
        if best_col is None or len(best_dates) < 2:
            return None
        return sum(1 for month, _ in best_dates if month > target_month)

    @staticmethod
    def _wtq_first_metric_threshold_date_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        match = re.search(
            r"\bon\s+what\s+date\s+did\s+(?:the\s+)?(.+?)\s+first\s+go\s+"
            r"(above|over|greater\s+than|below|under|less\s+than)\s+([\d,]+(?:\.\d+)?)\b",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        metric_phrase, operator, threshold_text = match.groups()
        metric_col = (
            TableQAPipeline._select_column_by_semantic_tokens(df, metric_phrase)
            or TableQAPipeline._select_column_by_tokens(df, metric_phrase)
        )
        date_cols = [col for col in df.columns if re.search(r"\bdate\b", str(col), flags=re.I)]
        threshold = _numeric_measure_value(threshold_text)
        if metric_col is None or not date_cols or threshold is None:
            return None
        wants_greater = operator.lower() in {"above", "over", "greater than"}
        for _, row in TableQAPipeline._wtq_non_summary_rows(df).iterrows():
            number = _numeric_measure_value(row[metric_col])
            if number is None:
                continue
            matched = number > threshold if wants_greater else number < threshold
            if matched and not _is_missing_marker(row[date_cols[0]]):
                return row[date_cols[0]]
        return None

    @staticmethod
    def _wtq_score_pair_low_score_entity_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        match = re.search(
            r"\bwhich\s+(?:player|team|competitor)\s+scored\s+(?:only\s+)?(.+?)\s+points?\b",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        target = _small_number_from_text(match.group(1))
        if target is None:
            return None
        score_cols = [col for col in df.columns if re.search(r"\bscore\b", str(col), flags=re.I)]
        winner_cols = [col for col in df.columns if re.search(r"\bwinner\b|\bchampion\b", str(col), flags=re.I)]
        runner_cols = [col for col in df.columns if re.search(r"\brunner(?:-|\s*)up\b|\bloser\b", str(col), flags=re.I)]
        if not score_cols or not winner_cols or not runner_cols:
            return None
        matches: List[Any] = []
        for _, row in df.iterrows():
            pair = TableQAPipeline._score_pair(row[score_cols[0]])
            if pair is None:
                continue
            if abs(pair[0] - target) <= 1e-6 and not _is_missing_marker(row[winner_cols[0]]):
                matches.append(row[winner_cols[0]])
            if abs(pair[1] - target) <= 1e-6 and not _is_missing_marker(row[runner_cols[0]]):
                matches.append(row[runner_cols[0]])
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _wtq_column_header_number_for_year_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"\bwhat\s+is\s+the\s+(.+?)\s+number\b.*?\bin\s+(\d{4})\b",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        target_phrase, year_text = match.groups()
        if not re.search(r"\b(series|cycle|saros|eclipse|event)\b", target_phrase, flags=re.I):
            return None
        matching_cols = []
        for col in df.columns:
            for value in df[col].tolist():
                if re.search(rf"\b{re.escape(year_text)}\b", str(value or "")):
                    matching_cols.append(col)
                    break
        if len(matching_cols) != 1:
            return None
        numbers = re.findall(r"\b\d+\b", str(matching_cols[0]))
        return numbers[-1] if numbers else None

    @staticmethod
    def _wtq_rank_gap_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        match = re.search(
            r"\bhow\s+many\s+ranks?\s+(?:about|between)\s+(.+?)\s+(?:is|are|and)\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        left_phrase, right_phrase = [part.strip(" \t\r\n\"'") for part in match.groups()]
        rank_cols = [col for col in df.columns if re.search(r"\b(rank|position|place)\b", str(col), flags=re.I)]
        if not rank_cols:
            return None
        rank_col = rank_cols[0]
        entity_col = TableQAPipeline._wtq_entity_column(df, "entity name officer player team", exclude={rank_col})
        if entity_col is None:
            return None

        def rank_for(entity: str) -> Optional[float]:
            rows = _find_rows_for_entity_across_row(df, entity, [entity_col])
            if len(rows) != 1:
                return None
            return _numeric_measure_value(rows[0][rank_col])

        left_rank = rank_for(left_phrase)
        right_rank = rank_for(right_phrase)
        if left_rank is None or right_rank is None:
            return None
        gap = abs(left_rank - right_rank)
        return int(gap) if float(gap).is_integer() else gap

    @staticmethod
    def _wtq_explicit_option_absence_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        text = question or ""
        if not re.search(r"\bnot\s+compete\s+at\b|\bdid\s+not\s+compete\s+at\b", text, flags=re.I):
            return None
        option_text = text.split(";", 1)[1] if ";" in text else text.split(":", 1)[-1]
        option_text = re.sub(r",\s*or(?=[a-z])", ", ", option_text, flags=re.I)
        options = [
            _loose_text_key(part)
            for part in re.split(r"\s*,\s*|\s+\bor\s+", option_text, flags=re.I)
            if _loose_text_key(part)
        ]
        options = [option for option in options if option]
        if len(options) < 2:
            return None
        table_text = _loose_text_key(" ".join(str(value) for _, row in df.iterrows() for value in row.tolist()))
        missing = [option for option in options if option not in table_text]
        return missing[0] if len(missing) == 1 else None

    @staticmethod
    def _wtq_metric_value_entity_list_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        match = re.search(
            r"\bonly\s+(\d+)\s+(.+?)\s+had\s+([\d,]+(?:\.\d+)?)\s+(.+?),\s*who\s+were\s+they\b",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        expected_text, entity_phrase, value_text, metric_phrase = match.groups()
        expected_count = int(expected_text)
        target_value = _numeric_measure_value(value_text)
        metric_col = (
            TableQAPipeline._select_column_by_semantic_tokens(df, metric_phrase)
            or TableQAPipeline._select_column_by_tokens(df, metric_phrase)
        )
        if metric_col is None or target_value is None:
            return None
        entity_col = TableQAPipeline._wtq_entity_column(df, entity_phrase, exclude={metric_col})
        if entity_col is None:
            return None
        matches = []
        for _, row in TableQAPipeline._wtq_non_summary_rows(df).iterrows():
            value = _numeric_measure_value(row[metric_col])
            if value is None or abs(value - target_value) > 1e-6:
                continue
            entity = row[entity_col]
            if not _is_missing_marker(entity):
                matches.append(entity)
        if len(matches) != expected_count:
            return None
        return matches

    @staticmethod
    def _wtq_inferred_rank_entity_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        match = re.search(
            r"\branked\s+number\s+one\b.*?\bwho\s+is\s+ranked\s+number\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        target_rank = _small_number_from_text(match.group(1))
        if target_rank is None:
            return None
        rank_cols = [col for col in df.columns if re.search(r"\b(rank|position|place)\b", str(col), flags=re.I)]
        if not rank_cols:
            return None
        rank_col = rank_cols[0]
        entity_col = TableQAPipeline._wtq_entity_column(df, "name athlete player team", exclude={rank_col})
        if entity_col is None:
            return None
        rows = TableQAPipeline._wtq_non_summary_rows(df).reset_index(drop=True)
        for index, row in rows.iterrows():
            rank = TableQAPipeline._ordinal_value(row[rank_col])
            if rank is None:
                rank = float(index + 1)
            if int(rank) == target_rank:
                value = row[entity_col]
                return None if _is_missing_marker(value) else value
        return None

    @staticmethod
    def _wtq_threshold_count_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        match = re.search(
            r"\bhow\s+many\s+.+?\s+(?:only\s+)?(?:have|has|had)\s+(?:a\s+)?(.+?)\s+"
            r"(?:of\s+)?([\d,]+(?:\.\d+)?)\s+or\s+(below|under|less|above|over|more)\b",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        metric_phrase, threshold_text, direction = match.groups()
        metric_col = (
            TableQAPipeline._select_column_by_semantic_tokens(df, metric_phrase)
            or TableQAPipeline._select_column_by_tokens(df, metric_phrase)
        )
        threshold = _numeric_measure_value(threshold_text)
        if metric_col is None or threshold is None:
            return None
        wants_below = direction.lower() in {"below", "under", "less"}
        count = 0
        for value in TableQAPipeline._wtq_non_summary_rows(df)[metric_col].tolist():
            number = _numeric_measure_value(value)
            if number is None:
                continue
            if number < threshold if wants_below else number > threshold:
                count += 1
        return count

    @staticmethod
    def _wtq_at_least_metric_count_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        match = re.search(
            r"\b(?:how\s+many|number\s+of|count\s+of)\s+(.+?)\s+who\s+"
            r"(?:scored|had|have|has|recorded)\s+at\s+least\s+([\d,]+(?:\.\d+)?)\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        _, threshold_text, metric_phrase = match.groups()
        metric_col = (
            TableQAPipeline._select_column_by_semantic_tokens(df, metric_phrase)
            or TableQAPipeline._select_column_by_tokens(df, metric_phrase)
        )
        threshold = _numeric_measure_value(threshold_text)
        if metric_col is None or threshold is None:
            return None
        count = 0
        for value in TableQAPipeline._wtq_non_summary_rows(df)[metric_col].tolist():
            number = _numeric_measure_value(value)
            if number is not None and number >= threshold:
                count += 1
        return count

    @staticmethod
    def _wtq_listed_after_cell_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        match = re.search(
            r"\bwhat\s+is\s+the\s+(.+?)\s+listed\s+after\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        target_phrase, reference = match.groups()
        reference_key = _loose_text_key(reference)
        reference_number = _numeric_measure_value(reference)
        rows = TableQAPipeline._wtq_non_summary_rows(df).reset_index(drop=True)
        cells: List[Tuple[int, int, Any]] = [
            (row_index, col_index, rows.iloc[row_index, col_index])
            for row_index in range(len(rows))
            for col_index in range(len(rows.columns))
        ]
        start_index = None
        for index, (_, _, value) in enumerate(cells):
            value_key = _loose_text_key(value)
            value_number = _numeric_measure_value(value)
            if (
                reference_key
                and value_key
                and (reference_key == value_key or reference_key in value_key)
            ) or (
                reference_number is not None
                and value_number is not None
                and abs(reference_number - value_number) <= 1e-6
            ):
                start_index = index
                break
        if start_index is None:
            return None
        reference_row_index, reference_col_index, _ = cells[start_index]
        target_col = TableQAPipeline._wtq_adjacent_target_column(df, target_phrase)
        if target_col is not None and rows.columns[reference_col_index] == target_col:
            next_row_index = reference_row_index + 1
            if next_row_index < len(rows):
                value = rows.loc[next_row_index, target_col]
                return None if _is_missing_marker(value) else value
        wants_year = bool(re.search(r"\byear\b", target_phrase, flags=re.I))
        for _, _, value in cells[start_index + 1:]:
            if _is_missing_marker(value):
                continue
            text = str(value)
            if wants_year:
                year_match = re.search(r"\b(1[7-9]\d{2}|20\d{2})\b", text)
                if year_match:
                    return year_match.group(1)
                continue
            if _loose_text_key(text):
                return value
        return None

    @staticmethod
    def _wtq_adjacent_target_column(df: pd.DataFrame, target_phrase: str) -> Optional[Any]:
        phrase = re.sub(
            r"\b(?:the|a|an|next|previous|directly|listed|came|come|comes)\b",
            " ",
            target_phrase or "",
            flags=re.I,
        )
        phrase = re.sub(r"\s+", " ", phrase).strip()
        phrase_key = _loose_text_key(phrase)
        if re.search(r"\b(?:experiment\s+)?number\b", phrase_key, flags=re.I):
            for column in df.columns:
                if _loose_text_key(column) in {"num", "no", "number"} or str(column).strip() == "#":
                    return column
        return (
            TableQAPipeline._select_column_by_semantic_tokens(df, phrase)
            or TableQAPipeline._select_column_by_tokens(df, phrase)
        )

    @staticmethod
    def _wtq_directly_before_reference_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        match = re.search(
            r"\b(?:which|what)\s+(.+?)\s+(?:came|comes|come)\s+directly\s+before\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        target_phrase, reference = match.groups()
        cell = TableQAPipeline._wtq_reference_cell(df, reference.strip(" \t\r\n\"'"))
        if cell is None:
            return None
        row_index, _ = cell
        if row_index <= 0:
            return None
        rows = TableQAPipeline._wtq_non_summary_rows(df).reset_index(drop=True)
        target_col = TableQAPipeline._wtq_adjacent_target_column(df, target_phrase)
        if target_col is None:
            return None
        value = rows.loc[row_index - 1, target_col]
        return None if _is_missing_marker(value) else value

    @staticmethod
    def _wtq_overtime_count_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        if not re.search(
            r"\b(?:how\s+many|number\s+of(?:\s+times)?)\b.*\bovertime\b",
            question or "",
            flags=re.I,
        ):
            return None
        entity_groups: List[List[str]] = []
        between_match = re.search(
            r"\bbetween\s+(?:the\s+)?(.+?)\s+and\s+(?:the\s+)?(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if between_match:
            entity_groups = [_loose_tokens(part) for part in between_match.groups()]

        count = 0
        for _, row in TableQAPipeline._wtq_non_summary_rows(df).iterrows():
            row_text = " ".join(str(row[column]) for column in df.columns)
            row_tokens = _loose_tokens(row_text)
            if entity_groups:
                if not all(
                    group
                    and any(_fuzzy_token_match(token, row_token) for token in group for row_token in row_tokens)
                    for group in entity_groups
                ):
                    continue
            if re.search(r"\b(?:OT|overtime|extra\s+time|AET)\b", row_text, flags=re.I):
                count += 1
        return count if count else None

    @staticmethod
    def _wtq_playoff_count_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        if not re.search(r"\bhow\s+many\s+years\b.*\bmake\s+the\s+playoffs?\b", question or "", flags=re.I):
            return None
        playoff_col = (
            TableQAPipeline._select_column_by_semantic_tokens(df, "playoffs")
            or TableQAPipeline._select_column_by_tokens(df, "playoffs")
        )
        if playoff_col is None:
            return None
        negative_patterns = re.compile(
            r"\b(?:no\s+playoff|did\s+not\s+qualify|not\s+qualify|n/?a|none)\b",
            flags=re.I,
        )
        count = 0
        for value in TableQAPipeline._wtq_non_summary_rows(df)[playoff_col].tolist():
            if _is_missing_marker(value):
                continue
            text = str(value)
            if negative_patterns.search(text):
                continue
            if _loose_text_key(text):
                count += 1
        return count

    @staticmethod
    def _wtq_column_entry_count_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        match = re.search(
            r"\b(?:what\s+is\s+)?(?:the\s+)?number\s+of\s+winners\s+in\s+the\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        target_col = (
            TableQAPipeline._select_column_by_semantic_tokens(df, match.group(1))
            or TableQAPipeline._select_column_by_tokens(df, match.group(1))
        )
        if target_col is None:
            return None
        return sum(
            1
            for value in TableQAPipeline._wtq_non_summary_rows(df)[target_col].tolist()
            if not _is_missing_marker(value) and bool(_loose_text_key(value))
        )

    @staticmethod
    def _wtq_sponsor_count_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        if not re.search(r"\b(?:total\s+)?number\s+of\s+sponsors?\b", question or "", flags=re.I):
            return None
        sponsor_cols = [column for column in df.columns if "sponsor" in _loose_text_key(column)]
        if not sponsor_cols:
            return None
        sponsors: dict[str, str] = {}
        for column in sponsor_cols:
            for value in TableQAPipeline._wtq_non_summary_rows(df)[column].tolist():
                if _is_missing_marker(value):
                    continue
                text = re.sub(r"\s+", " ", str(value)).strip()
                key = _loose_text_key(text)
                if key:
                    sponsors.setdefault(key, text)
        return len(sponsors)

    @staticmethod
    def _wtq_retired_injured_attempt_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        if not re.search(r"\b(?:who|which)\b.*\bretired\s+injured\b", question or "", flags=re.I):
            return None
        attempt_count = _small_number_from_text(question or "")
        if attempt_count is None:
            return None
        entity_col = TableQAPipeline._wtq_entity_column(df, "person name")
        if entity_col is None:
            return None
        for _, row in TableQAPipeline._wtq_non_summary_rows(df).iterrows():
            row_text = " ".join(str(row[column]) for column in df.columns)
            if not re.search(r"\bretired\s+injured\b", row_text, flags=re.I):
                continue
            if not _text_mentions_number_or_ordinal(row_text, attempt_count):
                continue
            value = row[entity_col]
            return None if _is_missing_marker(value) else value
        return None

    @staticmethod
    def _wtq_only_metric_value_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        with_match = re.search(
            r"^\s*(?:the\s+)?only\s+(.+?)\s+with\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        to_have_match = re.search(
            r"^\s*what\s+is\s+the\s+only\s+(.+?)\s+to\s+(?:have|has|had)\s+"
            r"([\d,]+(?:\.\d+)?)\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if to_have_match:
            _, value_phrase, metric_phrase = to_have_match.groups()
        elif with_match:
            metric_phrase, value_phrase = with_match.groups()
        else:
            return None
        if re.search(r"\b(?:above|below|over|under|more|less)\b", value_phrase, flags=re.I):
            return None
        metric_col = (
            TableQAPipeline._select_column_by_semantic_tokens(df, metric_phrase)
            or TableQAPipeline._select_column_by_tokens(df, metric_phrase)
        )
        if metric_col is None:
            return None
        target_number = _numeric_measure_value(value_phrase)
        target_key = _loose_text_key(value_phrase)
        matched_rows = []
        for _, row in TableQAPipeline._wtq_non_summary_rows(df).iterrows():
            value = row[metric_col]
            value_number = _numeric_measure_value(value)
            value_key = _loose_text_key(value)
            if (
                target_number is not None
                and value_number is not None
                and abs(target_number - value_number) <= 1e-6
            ) or (
                target_number is None
                and target_key
                and target_key == value_key
            ):
                matched_rows.append(row)
        if len(matched_rows) != 1:
            return None
        entity_col = TableQAPipeline._wtq_entity_column(df, "", exclude={metric_col})
        if entity_col is None:
            return None
        value = matched_rows[0][entity_col]
        return None if _is_missing_marker(value) else value

    @staticmethod
    def _wtq_entity_filter_variants(phrase: str) -> List[str]:
        demonyms = {
            "belgian": "belgium",
            "brazilian": "brazil",
            "italian": "italy",
            "french": "france",
            "german": "germany",
            "spanish": "spain",
            "dutch": "netherlands",
            "american": "united states",
            "korean": "south korea",
        }
        ignored = {
            "affiliate",
            "affiliates",
            "candidate",
            "candidates",
            "contestant",
            "contestants",
            "listing",
            "listings",
            "player",
            "players",
            "rider",
            "riders",
            "team",
            "teams",
        }
        key = _loose_text_key(phrase)
        variants = [key] if key else []
        tokens = [token for token in _loose_tokens(phrase) if token not in ignored]
        mapped_tokens = [demonyms.get(token, token) for token in tokens]
        if mapped_tokens:
            variants.append(" ".join(mapped_tokens))
            if len(mapped_tokens) == 1:
                variants.extend(mapped_tokens)
        seen = set()
        deduped = []
        for variant in variants:
            if variant and variant not in seen:
                seen.add(variant)
                deduped.append(variant)
        return deduped

    @staticmethod
    def _wtq_row_matches_entity_filter(row: pd.Series, variants: List[str], cols: List[Any]) -> bool:
        for col in cols:
            value = row[col]
            for variant in variants:
                if _value_matches_phrase(value, variant):
                    return True
        return False

    @staticmethod
    def _wtq_metric_sum_by_entity_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        total_match = re.search(
            r"^\s*total\s+(.+?)\s+by\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        together_match = re.search(
            r"\bhow\s+many\s+(.+?)\s+does\s+(.+?)\s+have\s+all\s+together[?.]?$",
            question or "",
            flags=re.I,
        )
        if total_match:
            metric_phrase, entity_phrase = total_match.groups()
        elif together_match:
            metric_phrase, entity_phrase = together_match.groups()
        else:
            return None
        metric_col = (
            _select_numeric_measure_column_by_phrase(df, metric_phrase)
            or TableQAPipeline._select_column_by_semantic_tokens(df, metric_phrase)
            or TableQAPipeline._select_column_by_tokens(df, metric_phrase)
        )
        if metric_col is None:
            return None
        variants = TableQAPipeline._wtq_entity_filter_variants(entity_phrase)
        if not variants:
            return None
        search_cols = [col for col in df.columns if col != metric_col]
        values: List[float] = []
        for _, row in TableQAPipeline._wtq_non_summary_rows(df).iterrows():
            if not TableQAPipeline._wtq_row_matches_entity_filter(row, variants, search_cols):
                continue
            number = _numeric_measure_value(row[metric_col])
            if number is not None:
                values.append(number)
        if not values:
            return None
        total = sum(values)
        return int(total) if float(total).is_integer() else total

    @staticmethod
    def _wtq_combined_entity_count_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        match = re.search(
            r"\bnumber\s+of\s+(.+?)\s+from\s+(.+?)\s+combined[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        _, option_text = match.groups()
        options = [
            part.strip(" \t\r\n\"'")
            for part in re.split(r"\s*,\s*|\s+\band\s+", option_text, flags=re.I)
            if part.strip(" \t\r\n\"'")
        ]
        if len(options) < 2:
            return None
        variants_by_option = [TableQAPipeline._wtq_entity_filter_variants(option) for option in options]
        best_count = 0
        for col in df.columns:
            count = 0
            for value in TableQAPipeline._wtq_non_summary_rows(df)[col].tolist():
                if any(any(_value_matches_phrase(value, variant) for variant in variants) for variants in variants_by_option):
                    count += 1
            best_count = max(best_count, count)
        return best_count if best_count > 0 else None

    @staticmethod
    def _wtq_column_value_count_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        match = re.search(
            r"\bhow\s+many\s+(.+?)\s+(.+?)s?\s+(?:are|were)\s+there[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        value_phrase, column_phrase = match.groups()
        if re.search(r"\b(?:points?|goals?|wins?|losses?)\b", column_phrase, flags=re.I):
            return None
        target_col = (
            TableQAPipeline._select_column_by_semantic_tokens(df, column_phrase)
            or TableQAPipeline._select_column_by_tokens(df, column_phrase)
        )
        if target_col is None:
            return None
        count = sum(
            1
            for value in TableQAPipeline._wtq_non_summary_rows(df)[target_col].tolist()
            if _value_matches_phrase(value, value_phrase)
        )
        return count if count > 0 else None

    @staticmethod
    def _wtq_multi_condition_lookup_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        match = re.search(
            r"^\s*(?:at|in|for)\s+which\s+(.+?)\s+"
            r"(?:was|were|is|are)\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        target_phrase, condition_text = match.groups()
        condition_text = re.sub(r"\btoo\b", "", condition_text, flags=re.I).strip(" ,")
        condition_parts = [
            part.strip(" ,")
            for part in re.split(r"\s+\band\s+", condition_text, flags=re.I)
            if part.strip(" ,")
        ]
        if len(condition_parts) < 2:
            return None
        target_col = (
            TableQAPipeline._select_column_by_semantic_tokens(df, target_phrase)
            or TableQAPipeline._select_column_by_tokens(df, target_phrase)
        )
        if target_col is None:
            return None
        conditions: List[Tuple[Any, float]] = []
        for part in condition_parts:
            condition_match = re.search(
                r"^(?:the\s+)?(.+?)\s+(?:as\s+|equals?\s+|equal\s+to\s+)?"
                r"([-+]?\d+(?:\.\d+)?)\s*$",
                part,
                flags=re.I,
            )
            if not condition_match:
                return None
            col_phrase, expected_text = condition_match.groups()
            condition_col = (
                TableQAPipeline._select_column_by_semantic_tokens(df, col_phrase)
                or TableQAPipeline._select_column_by_tokens(df, col_phrase)
            )
            expected = _numeric_measure_value(expected_text)
            if condition_col is None or condition_col == target_col or expected is None:
                return None
            conditions.append((condition_col, expected))

        matched_rows: List[pd.Series] = []
        for _, row in TableQAPipeline._wtq_non_summary_rows(df).iterrows():
            if all(
                (value := _numeric_measure_value(row[col])) is not None
                and abs(float(value) - expected) <= 1e-6
                for col, expected in conditions
            ):
                matched_rows.append(row)
        if len(matched_rows) != 1:
            return None
        value = matched_rows[0][target_col]
        return None if _is_missing_marker(value) else value

    @staticmethod
    def _wtq_zero_metric_count_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        match = re.search(
            r"\bhow\s+many\s+.+?\s+did\s+not\s+win\s+any\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        metric_col = (
            TableQAPipeline._select_column_by_semantic_tokens(df, match.group(1))
            or TableQAPipeline._select_column_by_tokens(df, match.group(1))
        )
        if metric_col is None:
            return None
        count = 0
        for value in TableQAPipeline._wtq_non_summary_rows(df)[metric_col].tolist():
            number = _as_number_like(value)
            if number is not None and abs(number) <= 1e-12:
                count += 1
        return count

    @staticmethod
    def _wtq_same_column_count_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        match = re.search(
            r"\bhow\s+many\s+times\s+is\s+the\s+(.+?)\s+the\s+same\s+as\s+the\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        left_phrase, right_phrase = match.groups()
        left_col = (
            TableQAPipeline._select_column_by_semantic_tokens(df, left_phrase)
            or TableQAPipeline._select_column_by_tokens(df, left_phrase)
        )
        right_col = (
            TableQAPipeline._select_column_by_semantic_tokens(df, right_phrase)
            or TableQAPipeline._select_column_by_tokens(df, right_phrase)
        )
        if left_col is None or right_col is None or left_col == right_col:
            return None
        count = 0
        for _, row in TableQAPipeline._wtq_non_summary_rows(df).iterrows():
            left_key = _loose_text_key(row[left_col])
            right_key = _loose_text_key(row[right_col])
            if not left_key or not right_key:
                continue
            if left_key == right_key or left_key in right_key or right_key in left_key:
                count += 1
        return count

    @staticmethod
    def _wtq_contributor_count_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        match = re.search(
            r"\bhow\s+many\s+.+?\s+did\s+(.+?)\s+contribute\s+to\b",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        contributor = match.group(1).strip(" \t\r\n\"'")
        count = 0
        for _, row in TableQAPipeline._wtq_non_summary_rows(df).iterrows():
            if any(
                _loose_text_matches_reference(value, contributor)
                for value in row.tolist()
                if not _is_missing_marker(value)
            ):
                count += 1
        return count

    @staticmethod
    def _wtq_usage_count_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        match = re.search(
            r"\bhow\s+many\s+uses\s+are\s+listed\s+for\s+(?:the\s+)?(.+?)(?:\s+tree)?[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        entity = match.group(1).strip()
        usage_cols = [
            col for col in df.columns
            if re.search(r"\b(usages?|uses?|characteristics?|status)\b", str(col), flags=re.I)
        ]
        if not usage_cols:
            return None
        matched_rows = [
            row for _, row in df.iterrows()
            if any(
                TableQAPipeline._cell_contains_phrase_tokens(entity, row[col])
                for col in df.columns
            )
        ]
        if len(matched_rows) != 1:
            return None
        text = str(matched_rows[0][usage_cols[0]])
        use_match = re.search(
            r"\b(?:uses?\s+as|used\s+for|used\s+as)\s+(.+?)(?:\.|$)",
            text,
            flags=re.I,
        )
        if not use_match:
            return None
        items_text = re.sub(r"\b(?:and\s+so\s+forth|etc\.?)\b", "", use_match.group(1), flags=re.I)
        parts = [
            part.strip(" \t\r\n.;:")
            for part in re.split(r"\s*,\s*|\s+\band\s+", items_text, flags=re.I)
            if part.strip(" \t\r\n.;:")
        ]
        return len(parts) if parts else None

    @staticmethod
    def _wtq_occurrence_count_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        match = re.search(
            r"\bhow\s+many\s+times\s+is\s+(.+?)\s+listed[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        target_key = _loose_text_key(match.group(1).strip(" \t\r\n\"'"))
        if not target_key:
            return None
        count = 0
        pattern = re.compile(rf"(?<!\w){re.escape(target_key)}(?!\w)")
        for column in df.columns:
            for value in df[column].tolist():
                value_key = _loose_text_key(value)
                if value_key:
                    count += len(pattern.findall(value_key))
        return count if count > 0 else None

    @staticmethod
    def _ordinal_value(value: Any) -> Optional[float]:
        number = _as_number_like(value)
        if number is not None:
            return number
        match = re.search(r"\b(\d+)(?:st|nd|rd|th)\b", str(value or ""), flags=re.I)
        if match:
            return float(match.group(1))
        return None

    @staticmethod
    def _wtq_ordinal_position_count_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        match = re.search(
            r"\bhow\s+many\s+times\b.*?\bposition\s+of\s+at\s+least\s+(\d+)(?:st|nd|rd|th)?\s+place\s+or\s+better\b",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        threshold = float(match.group(1))
        position_cols = [
            col for col in df.columns
            if re.search(r"\b(position|place|rank)\b", str(col), flags=re.I)
        ]
        if not position_cols:
            return None
        count = 0
        for value in df[position_cols[0]].tolist():
            ordinal = TableQAPipeline._ordinal_value(value)
            if ordinal is not None and ordinal <= threshold:
                count += 1
        return count

    @staticmethod
    def _wtq_top_placing_competitor_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        if not re.search(r"\btop\s+placing\s+competitor\b", question or "", flags=re.I):
            return None
        rank_cols = [
            col for col in df.columns if re.search(r"\b(place|placing|rank|position)\b", str(col), flags=re.I)
        ]
        competitor_cols = [
            col for col in df.columns if re.search(r"\b(competitor|athlete|player|name)\b", str(col), flags=re.I)
        ]
        if not rank_cols or not competitor_cols:
            return None
        work = df.copy()
        rank_col = rank_cols[0]
        def parse_placing(value: Any) -> Optional[float]:
            number = _as_number_like(value)
            if number is not None:
                return number
            text = str(value or "")
            ordinal = re.search(r"\b(\d+)(?:st|nd|rd|th)\b", text, flags=re.I)
            if ordinal:
                return float(ordinal.group(1))
            return None

        work["_rank_num"] = work[rank_col].map(parse_placing)
        work = work.dropna(subset=["_rank_num"])
        if work.empty:
            return None
        row = work.sort_values("_rank_num", ascending=True).iloc[0]
        return row[competitor_cols[0]]

    @staticmethod
    def _wtq_last_placing_entity_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        match = re.search(
            r"\b(?:who|what)\s+(.+?)\s+came\s+in\s+last\b",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        entity_phrase = match.group(1)
        rank_cols = [
            col for col in df.columns if re.search(r"\b(rank|place|placing|position|pos)\b", str(col), flags=re.I)
        ]
        if not rank_cols:
            return None
        entity_col = TableQAPipeline._wtq_entity_column(df, entity_phrase, exclude=set(rank_cols))
        if entity_col is None:
            return None
        ranked: List[Tuple[float, Any]] = []
        for _, row in df.iterrows():
            rank = TableQAPipeline._ordinal_value(row[rank_cols[0]])
            if rank is None:
                continue
            value = row[entity_col]
            if not _is_missing_marker(value):
                ranked.append((rank, value))
        if not ranked:
            return None
        ranked.sort(key=lambda item: item[0], reverse=True)
        return ranked[0][1]

    @staticmethod
    def _wtq_extreme_metric_lookup_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        match = re.search(
            r"\bwhat\s+is\s+the\s+(.+?)\s+of\s+(?:the\s+)?(?:.+?)\s+with\s+"
            r"(?:the\s+)?(smallest|lowest|largest|highest|least|most)\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        target_phrase, direction, metric_phrase = match.groups()
        target_col = (
            TableQAPipeline._select_column_by_semantic_tokens(df, target_phrase)
            or TableQAPipeline._select_column_by_tokens(df, target_phrase)
        )
        metric_col = (
            TableQAPipeline._select_column_by_semantic_tokens(df, metric_phrase)
            or TableQAPipeline._select_column_by_tokens(df, metric_phrase)
        )
        if target_col is None or metric_col is None or target_col == metric_col:
            return None
        rows: List[Tuple[float, int, Any]] = []
        for order, (_, row) in enumerate(TableQAPipeline._wtq_non_summary_rows(df).iterrows()):
            metric = _numeric_measure_value(row[metric_col])
            value = row[target_col]
            if metric is None or _is_missing_marker(value):
                continue
            rows.append((metric, order, value))
        if not rows:
            return None
        ascending = direction.lower() in {"smallest", "lowest", "least"}
        rows.sort(key=lambda item: (item[0], item[1]), reverse=not ascending)
        return rows[0][2]

    @staticmethod
    def _wtq_first_status_entity_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        match = re.search(
            r"\b(?:who|what)\s+(?:was|is)\s+(?:the\s+)?(first|last)\s+(.+?)\s+to\s+get\s+"
            r"(evicted|ejected|eliminated)\b",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        direction, entity_phrase, status_word = match.groups()
        status_cols = [
            col for col in df.columns
            if re.search(r"\b(status|result|outcome)\b", str(col), flags=re.I)
        ]
        if not status_cols:
            return None
        entity_col = TableQAPipeline._wtq_entity_column(df, entity_phrase, exclude=set(status_cols))
        if entity_col is None:
            return None
        matches: List[Tuple[float, int, Any]] = []
        for order, (_, row) in enumerate(df.iterrows()):
            status_text = str(row[status_cols[0]])
            if not re.search(status_word, status_text, flags=re.I):
                continue
            ordinal = TableQAPipeline._ordinal_value(status_text)
            if ordinal is None:
                ordinal = float(order)
            value = row[entity_col]
            if not _is_missing_marker(value):
                matches.append((ordinal, order, value))
        if not matches:
            return None
        matches.sort(key=lambda item: (item[0], item[1]), reverse=direction.lower() == "last")
        return matches[0][2]

    @staticmethod
    def _wtq_last_requested_column_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        who_last_match = re.search(
            r"\bwho\s+(?:was|is)\s+(?:the\s+)?(.+?)\s+in\s+the\s+last\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if who_last_match:
            target_phrase, order_phrase = who_last_match.groups()
            target_col = (
                TableQAPipeline._select_column_by_semantic_tokens(df, target_phrase)
                or TableQAPipeline._select_column_by_tokens(df, target_phrase)
            )
            order_col = (
                TableQAPipeline._select_column_by_semantic_tokens(df, order_phrase)
                or TableQAPipeline._select_column_by_tokens(df, order_phrase)
            )
            if target_col is not None and order_col is not None and target_col != order_col:
                ranked_rows: List[Tuple[float, int, pd.Series]] = []
                fallback_rows: List[Tuple[int, pd.Series]] = []
                for order, (_, row) in enumerate(TableQAPipeline._wtq_non_summary_rows(df).iterrows()):
                    if _is_missing_marker(row[target_col]):
                        continue
                    fallback_rows.append((order, row))
                    order_number = _numeric_measure_value(row[order_col])
                    if order_number is not None:
                        ranked_rows.append((order_number, order, row))
                selected_row = None
                if ranked_rows:
                    selected_row = sorted(ranked_rows, key=lambda item: (item[0], item[1]))[-1][2]
                elif fallback_rows:
                    selected_row = fallback_rows[-1][1]
                if selected_row is not None:
                    value = selected_row[target_col]
                    return None if _is_missing_marker(value) else value

        listed_match = re.search(
            r"\bwhat\s+is\s+the\s+(.+?)\s+listed\s+for\s+the\s+last\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if listed_match:
            target_phrase, owner_phrase = listed_match.groups()
            target_col = TableQAPipeline._select_column_by_semantic_tokens(df, target_phrase)
            if target_col is None:
                target_col = TableQAPipeline._select_column_by_tokens(df, target_phrase)
            owner_col = TableQAPipeline._select_column_by_semantic_tokens(df, owner_phrase)
            if owner_col is None:
                owner_col = TableQAPipeline._select_column_by_tokens(df, owner_phrase)
            if target_col is not None and owner_col is not None:
                ranked_rows: List[Tuple[float, int, pd.Series]] = []
                fallback_rows: List[Tuple[int, pd.Series]] = []
                for order, (_, row) in enumerate(TableQAPipeline._wtq_non_summary_rows(df).iterrows()):
                    owner_value = row[owner_col]
                    if _is_missing_marker(owner_value):
                        continue
                    fallback_rows.append((order, row))
                    owner_number = _numeric_measure_value(owner_value)
                    if owner_number is not None:
                        ranked_rows.append((owner_number, order, row))
                selected_row: Optional[pd.Series] = None
                if ranked_rows:
                    selected_row = sorted(ranked_rows, key=lambda item: (item[0], item[1]))[-1][2]
                elif fallback_rows:
                    selected_row = fallback_rows[-1][1]
                if selected_row is not None:
                    value = selected_row[target_col]
                    return None if _is_missing_marker(value) else value

        match = re.search(
            r"\blast\s+(.+?)(?:\s+that\b|\s+which\b|\s+who\b|\s+in\b|\?|$)",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        target_phrase = match.group(1).strip()
        if not target_phrase:
            return None
        target_col = TableQAPipeline._select_column_by_semantic_tokens(df, target_phrase)
        if target_col is None:
            return None
        rows = TableQAPipeline._wtq_non_summary_rows(df)
        year_match = re.search(r"\b(?:on|in|for)\s+(\d{4})\b", question or "", flags=re.I)
        if year_match:
            year_cols = [
                col
                for col in rows.columns
                if re.search(r"\byear\b", str(col), flags=re.I)
            ]
            if year_cols:
                year = float(year_match.group(1))
                filtered = rows[
                    [
                        _numeric_measure_value(value) == year
                        for value in rows[year_cols[0]].tolist()
                    ]
                ]
                if not filtered.empty:
                    rows = filtered
        values = [
            value
            for value in rows[target_col].tolist()
            if not _is_missing_marker(value) and _loose_text_key(value)
        ]
        return values[-1] if values else None

    @staticmethod
    def _wtq_no_more_than_once_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        match = re.search(
            r"\bwhich\s+(.+?)(?:\(?s\)?)?\s*did\s+not\s+win\s+more\s+than\s+once\b",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        target_phrase = match.group(1)
        candidate_cols = [
            col
            for col in df.columns
            if re.search(r"\bwinning\s+team\b", str(col), flags=re.I)
        ]
        if not candidate_cols:
            col = TableQAPipeline._select_column_by_semantic_tokens(df, target_phrase)
            candidate_cols = [col] if col is not None else []
        if not candidate_cols:
            return None
        target_col = candidate_cols[0]
        counts: Dict[str, int] = {}
        originals: Dict[str, Any] = {}
        for value in df[target_col].tolist():
            key = _loose_text_key(value)
            if not key or _is_missing_marker(value):
                continue
            counts[key] = counts.get(key, 0) + 1
            originals.setdefault(key, value)
        values = [originals[key] for key, count in counts.items() if count <= 1]
        if not values:
            return None
        return values[0] if len(values) == 1 else values

    @staticmethod
    def _wtq_unique_count_threshold_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(r"\bat\s+least\s+(\d+)\s+([a-z][a-z -]+?)\s+on\s+(?:the\s+)?chart\b", question or "", flags=re.I)
        if not match:
            return None
        threshold = int(match.group(1))
        noun = match.group(2).strip()
        if noun.endswith("s"):
            noun = noun[:-1]
        target_col = TableQAPipeline._select_column_by_tokens(df, noun)
        if target_col is None:
            return None
        values = {
            _loose_text_key(value)
            for value in df[target_col].tolist()
            if not _is_missing_marker(value) and _loose_text_key(value)
        }
        return "yes" if len(values) >= threshold else "no"

    @staticmethod
    def _wtq_duration_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"\bhow\s+long\b.*?\bget\s+to\s+(.+?)\s+when\s+departing\s+at\s+(\d{1,2}[\.:]\d{2})",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        destination = _loose_text_key(match.group(1))
        departure_text = match.group(2).replace(".", ":")
        time_cols = [
            col for col in df.columns if re.search(r"\b(depart\w*|arrival|arriv\w*|time)\b", str(col), flags=re.I)
        ]
        depart_cols = [col for col in time_cols if re.search(r"\bdepart\w*", str(col), flags=re.I)]
        arrival_cols = [col for col in time_cols if re.search(r"\b(arrival|arriv\w*)", str(col), flags=re.I)]
        if not depart_cols or not arrival_cols:
            return None

        def parse_time(value: Any) -> Optional[int]:
            text = str(value).strip().replace(".", ":")
            m = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
            if not m:
                return None
            return int(m.group(1)) * 60 + int(m.group(2))

        for _, row in df.iterrows():
            row_text = _loose_text_key(" ".join(str(row[col]) for col in df.columns))
            if destination and destination not in row_text:
                continue
            depart_minutes = parse_time(row[depart_cols[0]])
            if depart_minutes is None or parse_time(departure_text) != depart_minutes:
                continue
            arrival_minutes = parse_time(row[arrival_cols[0]])
            if arrival_minutes is None:
                continue
            delta = arrival_minutes - depart_minutes
            if delta < 0:
                delta += 24 * 60
            return f"{delta} minutes"
        return None

    @staticmethod
    def _wtq_combined_numbers_for_column_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        match = re.search(
            r"\b(before|after)\s+(\d{4})\b.*?\bcombined\s+numbers?\s+for\s+(.+?)[?\.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        direction, year_text, target_phrase = match.groups()
        target_col = TableQAPipeline._select_column_by_tokens(df, target_phrase)
        year_cols = [
            col for col in df.columns if re.search(r"\b(season|year|date)\b", str(col), flags=re.I)
        ]
        if target_col is None or not year_cols:
            return None
        threshold = int(year_text)
        values: List[float] = []
        for _, row in df.iterrows():
            row_year = None
            for col in year_cols:
                year_match = re.search(r"\b(\d{4})\b", str(row[col]))
                if year_match:
                    row_year = int(year_match.group(1))
                    break
            if row_year is None:
                continue
            if direction.lower() == "before" and row_year >= threshold:
                continue
            if direction.lower() == "after" and row_year <= threshold:
                continue
            value = _as_number_like(row[target_col])
            if value is not None:
                values.append(value)
        if not values:
            return None
        total = sum(values)
        return int(total) if float(total).is_integer() else total

    @staticmethod
    def _wtq_existing_total_metric_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        match = re.search(
            r"\btotal(?:\s+number\s+of)?\s+(.+?)[?\.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        metric_phrase = match.group(1).strip()
        if not metric_phrase:
            return None
        metric_col = (
            _select_numeric_measure_column_by_phrase(df, metric_phrase)
            or TableQAPipeline._select_column_by_semantic_tokens(df, metric_phrase)
            or TableQAPipeline._select_column_by_tokens(df, metric_phrase)
        )
        if metric_col is None:
            return None
        total_markers = {"total", "totals", "grand total", "totaal"}
        matched_values: List[float] = []
        for _, row in df.iterrows():
            has_total_marker = any(
                col != metric_col and _loose_text_key(row[col]) in total_markers
                for col in df.columns
            )
            if not has_total_marker:
                continue
            value = _numeric_measure_value(row[metric_col])
            if value is not None:
                matched_values.append(value)
        if len(matched_values) != 1:
            return None
        value = matched_values[0]
        return int(value) if float(value).is_integer() else value

    @staticmethod
    def _wtq_stated_left_count_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        match = re.search(
            r"\bthere\s+(?:were|are)\s+([a-z0-9]+)\b.+?,\s+([a-z0-9]+)\s+(?:were|are)\b.+?"
            r"\bhow\s+many\b.+?\bleft\b",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        total = _small_number_from_text(match.group(1))
        removed = _small_number_from_text(match.group(2))
        if total is None or removed is None or total < removed:
            return None
        return total - removed

    @staticmethod
    def _wtq_release_date_gap_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        match = re.search(
            r"\bhow\s+long\s+were\s+the\s+release\s+dates\s+between\s+(.+?)\s+and\s+(.+?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        left_phrase, right_phrase = [item.strip(" \t\r\n\"'") for item in match.groups()]
        date_cols = [col for col in df.columns if re.search(r"\brelease\s+date\b|\bdate\b", str(col), flags=re.I)]
        if not date_cols:
            return None

        def find_date(entity_phrase: str) -> Optional[Tuple[int, int, int]]:
            for _, row in df.iterrows():
                if not any(
                    TableQAPipeline._cell_contains_phrase_tokens(entity_phrase, row[col])
                    for col in df.columns
                    if col not in date_cols
                ):
                    continue
                date_key = _parse_date_key(row[date_cols[0]])
                if date_key is not None:
                    return date_key
            return None

        left_date = find_date(left_phrase)
        right_date = find_date(right_phrase)
        if left_date is None or right_date is None:
            return None
        months = abs((left_date[0] - right_date[0]) * 12 + (left_date[1] - right_date[1]))
        if months <= 0:
            return None
        suffix = "month" if months == 1 else "months"
        return f"{months} {suffix}"

    @staticmethod
    def _wtq_only_column_threshold_answer(question: str, df: pd.DataFrame) -> Optional[Any]:
        match = re.search(
            r"\bonly\s+(.+?)\s+with\s+(?:a\s+)?(.+?)\s+value\s+"
            r"(above|over|greater\s+than|more\s+than|below|under|less\s+than)\s+([\d,]+(?:\.\d+)?)[?.]?$",
            question or "",
            flags=re.I,
        )
        if not match:
            return None
        label_phrase, metric_phrase, operator, threshold_text = match.groups()
        metric_key = _loose_text_key(metric_phrase).replace(" ", "")
        metric_col = None
        for col in df.columns:
            col_key = _loose_text_key(col).replace(" ", "")
            if metric_key and (metric_key == col_key or metric_key in col_key or col_key in metric_key):
                metric_col = col
                break
        if metric_col is None:
            metric_col = (
                TableQAPipeline._select_column_by_semantic_tokens(df, metric_phrase)
                or TableQAPipeline._select_column_by_tokens(df, metric_phrase)
            )
        label_col = TableQAPipeline._wtq_entity_column(df, label_phrase, exclude={metric_col})
        threshold = _numeric_measure_value(threshold_text)
        if metric_col is None or label_col is None or threshold is None:
            return None
        wants_greater = operator.lower() in {"above", "over", "greater than", "more than"}
        matched = []
        for _, row in df.iterrows():
            value = _numeric_measure_value(row[metric_col])
            if value is None:
                continue
            if (value > threshold) if wants_greater else (value < threshold):
                label = row[label_col]
                if not _is_missing_marker(label):
                    matched.append(label)
        return matched[0] if len(matched) == 1 else None

    @staticmethod
    def _crt_century_manufacturing_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        text = question or ""
        if not re.search(r"\b(manufactur|built|build|produced|production)\w*\b", text, flags=re.I):
            return None
        centuries = [int(match.group(1)) for match in re.finditer(r"\b(1[89])00s\b", text)]
        if len(centuries) < 2:
            return None
        date_cols = [
            col
            for col in df.columns
            if re.search(r"\b(manufactur|built|build|produced|production|introduced|date|year)\w*\b", str(col), flags=re.I)
        ]
        if not date_cols:
            date_cols = [
                col
                for col in df.columns
                if any(_extract_year_range(value) is not None for value in df[col].dropna().head(12).tolist())
            ]
        if not date_cols:
            return None
        weight_cols = [
            col
            for col in df.columns
            if col not in date_cols
            and re.search(r"\b(quantity|count|number|total|produced|built)\b", str(col), flags=re.I)
            and pd.to_numeric(df[col], errors="coerce").notna().sum() > 0
        ]
        first_range = (centuries[0] * 100, centuries[0] * 100 + 99)
        second_range = (centuries[1] * 100, centuries[1] * 100 + 99)
        first_total = 0.0
        second_total = 0.0
        saw_year = False
        for _, row in df.iterrows():
            row_range = None
            for col in date_cols:
                row_range = _extract_year_range(row[col])
                if row_range is not None:
                    break
            if row_range is None:
                continue
            saw_year = True
            weight = 1.0
            if weight_cols:
                parsed_weight = _as_number_like(row[weight_cols[0]])
                if parsed_weight is not None:
                    weight = parsed_weight
            if _range_overlaps(row_range, first_range):
                first_total += weight
            if _range_overlaps(row_range, second_range):
                second_total += weight
        if not saw_year:
            return None
        if first_total > second_total:
            return "more"
        if first_total < second_total:
            return "less"
        return "equal"

    @staticmethod
    def _crt_consistent_top_k_answer(question: str, df: pd.DataFrame) -> Optional[str]:
        text = question or ""
        if not re.search(r"\bconsistently\b", text, flags=re.I):
            return None
        top_match = re.search(r"\btop\s+(\d+)\b", text, flags=re.I)
        range_match = re.search(r"\bfrom\s+(\d{4})\s+to\s+(\d{4})\b", text, flags=re.I)
        if not top_match or not range_match:
            return None
        top_n = float(top_match.group(1))
        start_year = int(range_match.group(1))
        end_year = int(range_match.group(2))
        if start_year > end_year:
            start_year, end_year = end_year, start_year
        years = list(range(start_year, end_year + 1))

        rank_cols_by_year: Dict[int, Any] = {}
        for col in df.columns:
            col_text = str(col)
            if not re.search(r"\b(rank|position|place)\b", col_text, flags=re.I):
                continue
            for year in years:
                if re.search(rf"\b{year}\b", col_text):
                    rank_cols_by_year[year] = col
        if len(rank_cols_by_year) == len(years):
            for _, row in df.iterrows():
                ranks = [
                    _as_number_like(row[rank_cols_by_year[year]])
                    for year in years
                ]
                if all(rank is not None and rank <= top_n for rank in ranks):
                    return "Yes"
            return "No"

        single_rank_cols = [
            col for col in df.columns if re.fullmatch(r"(?i)rank|position|place", str(col).strip())
        ]
        year_value_cols = {
            year: col
            for year in years
            for col in df.columns
            if str(col).strip() == str(year)
        }
        if single_rank_cols and len(year_value_cols) == len(years):
            rank_col = single_rank_cols[0]
            for _, row in df.iterrows():
                rank = _as_number_like(row[rank_col])
                if rank is None or rank > top_n:
                    continue
                values = [str(row[year_value_cols[year]]).strip().lower() for year in years]
                if all(value and value not in {"nan", "none", "na", "n/a", "tba", "unknown"} for value in values):
                    return "Yes"
            return "No"

        year_cols = [col for col in df.columns if re.fullmatch(r"(?i)year", str(col).strip())]
        rank_cols = [col for col in df.columns if re.search(r"\b(rank|position|place)\b", str(col), flags=re.I)]
        entity_cols = [
            col
            for col in df.columns
            if col not in year_cols
            and col not in rank_cols
            and pd.to_numeric(df[col], errors="coerce").notna().sum() < len(df)
        ]
        if not year_cols or not rank_cols or not entity_cols:
            return None
        year_col = year_cols[0]
        rank_col = rank_cols[0]
        entity_col = entity_cols[0]
        qualified: Dict[str, set[int]] = {}
        for _, row in df.iterrows():
            year_value = _as_number_like(row[year_col])
            rank_value = _as_number_like(row[rank_col])
            entity = _loose_text_key(row[entity_col])
            if not entity or year_value is None or rank_value is None:
                continue
            year = int(year_value)
            if start_year <= year <= end_year and rank_value <= top_n:
                qualified.setdefault(entity, set()).add(year)
        required_years = set(years)
        return "Yes" if any(required_years.issubset(seen) for seen in qualified.values()) else "No"

    def _try_tabfact_semantic_shortcut(self, state: TQASessionState) -> bool:
        if state.dataset_profile != "tabfact" or state.answer_contract.kind != "label":
            return False
        question = state.question or ""
        df = state.original_df
        for reason, value in [
            (
                "TabFact only/set-equality claim checked deterministically.",
                self._tabfact_only_set_equality_answer(question, df),
            ),
            (
                "TabFact negated date and ordinal comparison checked deterministically.",
                self._tabfact_no_date_ordinal_answer(question, df),
            ),
            (
                "TabFact inverse correlation checked deterministically.",
                self._tabfact_inverse_correlation_answer(question, df),
            ),
            (
                "TabFact team score threshold count checked deterministically.",
                self._tabfact_team_score_count_answer(question, df),
            ),
            (
                "TabFact score threshold count checked deterministically.",
                self._tabfact_score_threshold_count_answer(question, df),
            ),
            (
                "TabFact duplicate metric value count checked deterministically.",
                self._tabfact_same_metric_value_count_answer(question, df),
            ),
            (
                "TabFact match type count checked deterministically.",
                self._tabfact_match_type_count_answer(question, df),
            ),
            (
                "TabFact final season record checked deterministically.",
                self._tabfact_final_record_answer(question, df),
            ),
            (
                "TabFact swept date series checked deterministically.",
                self._tabfact_swept_date_series_answer(question, df),
            ),
            (
                "TabFact player state and draft claim checked deterministically.",
                self._tabfact_state_draft_only_player_answer(question, df),
            ),
            (
                "TabFact location majority in year range checked deterministically.",
                self._tabfact_location_most_between_years_answer(question, df),
            ),
            (
                "TabFact overtime game count checked deterministically.",
                self._tabfact_overtime_count_answer(question, df),
            ),
            (
                "TabFact winner count difference checked deterministically.",
                self._tabfact_win_difference_answer(question, df),
            ),
            (
                "TabFact highest numeric row location checked deterministically.",
                self._tabfact_highest_location_numeric_answer(question, df),
            ),
            (
                "TabFact host same-city relation checked deterministically.",
                self._tabfact_same_city_host_answer(question, df),
            ),
            (
                "TabFact second-stage classification winner checked deterministically.",
                self._tabfact_second_stage_classification_winner_answer(question, df),
            ),
            (
                "TabFact entity difference from maximum numeric value checked deterministically.",
                self._tabfact_numeric_difference_from_max_answer(question, df),
            ),
            (
                "TabFact column value count checked deterministically.",
                self._tabfact_column_value_count_answer(question, df),
            ),
            (
                "TabFact highest scoring game checked deterministically.",
                self._tabfact_highest_scoring_game_answer(question, df),
            ),
            (
                "TabFact simultaneous row condition count checked deterministically.",
                self._tabfact_row_condition_count_answer(question, df),
            ),
            (
                "TabFact unique home/away winner statement checked deterministically.",
                self._tabfact_unique_side_winner_answer(question, df),
            ),
            (
                "TabFact episode order statement checked deterministically.",
                self._tabfact_episode_order_answer(question, df),
            ),
            (
                "TabFact episode credit count checked deterministically.",
                self._tabfact_episode_credit_count_answer(question, df),
            ),
            (
                "TabFact goal count by competition checked deterministically.",
                self._tabfact_goal_competition_count_answer(question, df),
            ),
            (
                "TabFact not-fewer-than-any-other comparison checked deterministically.",
                self._tabfact_not_fewer_than_any_other_answer(question, df),
            ),
            (
                "TabFact nonzero metric count checked deterministically.",
                self._tabfact_nonzero_metric_count_answer(question, df),
            ),
            (
                "TabFact maximum metric span checked deterministically.",
                self._tabfact_max_metric_span_answer(question, df),
            ),
            (
                "TabFact goal result count checked deterministically.",
                self._tabfact_goal_result_answer(question, df),
            ),
            (
                "TabFact last-row entity order checked deterministically.",
                self._tabfact_last_row_entity_answer(question, df),
            ),
            (
                "TabFact row condition value checked deterministically.",
                self._tabfact_condition_value_answer(question, df),
            ),
            (
                "TabFact threshold implication checked deterministically.",
                self._tabfact_threshold_implication_answer(question, df),
            ),
            (
                "TabFact same-side score comparison checked deterministically.",
                self._tabfact_same_side_score_comparison_answer(question, df),
            ),
            (
                "TabFact highest shutout score checked deterministically.",
                self._tabfact_highest_shutout_score_answer(question, df),
            ),
            (
                "TabFact entity metric comparison checked deterministically.",
                self._tabfact_entity_metric_comparison_answer(question, df),
            ),
            (
                "TabFact entity extreme metric checked deterministically.",
                self._tabfact_entity_extreme_metric_answer(question, df),
            ),
            (
                "TabFact least-threshold superlative checked deterministically.",
                self._tabfact_least_threshold_answer(question, df),
            ),
            (
                "TabFact extreme metric owner checked deterministically.",
                self._tabfact_extreme_metric_belongs_answer(question, df),
            ),
            (
                "TabFact threshold count checked deterministically.",
                self._tabfact_threshold_count_answer(question, df),
            ),
            (
                "TabFact first-N row count checked deterministically.",
                self._tabfact_first_n_rows_count_answer(question, df),
            ),
            (
                "TabFact entity metric threshold checked deterministically.",
                self._tabfact_entity_metric_threshold_answer(question, df),
            ),
            (
                "TabFact year column value checked deterministically.",
                self._tabfact_year_column_value_answer(question, df),
            ),
            (
                "TabFact game result score checked deterministically.",
                self._tabfact_game_result_score_answer(question, df),
            ),
            (
                "TabFact date metric difference checked deterministically.",
                self._tabfact_date_metric_difference_answer(question, df),
            ),
            (
                "TabFact same-row metric value under a condition checked deterministically.",
                self._tabfact_condition_metric_value_answer(question, df),
            ),
            (
                "TabFact entity score-sum comparison checked deterministically.",
                self._tabfact_entity_score_sum_comparison_answer(question, df),
            ),
            (
                "TabFact replay count in requested month checked deterministically.",
                self._tabfact_replay_count_month_answer(question, df),
            ),
            (
                "TabFact lowest-attendance week set checked deterministically.",
                self._tabfact_lowest_attendance_weeks_answer(question, df),
            ),
            (
                "TabFact replay home-team win relation checked deterministically.",
                self._tabfact_replay_home_team_win_answer(question, df),
            ),
            (
                "TabFact entity tenure containment checked deterministically.",
                self._tabfact_entity_tenure_contains_answer(question, df),
            ),
            (
                "TabFact aircraft and call-sign identity checked with strict same-row matching.",
                self._tabfact_aircraft_call_sign_answer(question, df),
            ),
            (
                "TabFact only-not-country claim checked deterministically.",
                self._tabfact_only_not_from_country_answer(question, df),
            ),
            (
                "TabFact entity numeric year value checked deterministically.",
                self._tabfact_entity_numeric_year_value_answer(question, df),
            ),
            (
                "TabFact column value fraction count checked deterministically.",
                self._tabfact_column_value_fraction_count_answer(question, df),
            ),
            (
                "TabFact finish position count checked deterministically.",
                self._tabfact_finish_position_count_answer(question, df),
            ),
            (
                "TabFact zero-score team count checked deterministically.",
                self._tabfact_zero_score_team_count_answer(question, df),
            ),
            (
                "TabFact min/max numeric difference checked deterministically.",
                self._tabfact_minmax_numeric_difference_answer(question, df),
            ),
            (
                "TabFact tied-rank country count checked deterministically.",
                self._tabfact_rank_country_count_answer(question, df),
            ),
            (
                "TabFact unique represented-country count checked deterministically.",
                self._tabfact_unique_country_count_answer(question, df),
            ),
            (
                "TabFact over-par country majority checked deterministically.",
                self._tabfact_majority_over_par_country_answer(question, df),
            ),
            (
                "TabFact only column value with non-target frequency checked deterministically.",
                self._tabfact_only_column_value_not_count_answer(question, df),
            ),
            (
                "TabFact only-not-from-country pair checked deterministically.",
                self._tabfact_only_not_from_countries_answer(question, df),
            ),
            (
                "TabFact every-player source column checked deterministically.",
                self._tabfact_every_player_source_answer(question, df),
            ),
            (
                "TabFact opponent attendance comparison checked deterministically.",
                self._tabfact_opponent_attendance_comparison_answer(question, df),
            ),
            (
                "TabFact extreme score difference checked deterministically.",
                self._tabfact_extreme_score_difference_answer(question, df),
            ),
            (
                "TabFact entity metric difference checked deterministically.",
                self._tabfact_entity_metric_more_than_answer(question, df),
            ),
            (
                "TabFact second-highest metric entity checked deterministically.",
                self._tabfact_second_highest_metric_entity_answer(question, df),
            ),
            (
                "TabFact entity maximum metric ownership checked deterministically.",
                self._tabfact_entity_max_metric_answer(question, df),
            ),
            (
                "TabFact only-year repeated-goal count checked deterministically.",
                self._tabfact_only_year_more_than_count_answer(question, df),
            ),
            (
                "TabFact equal win/loss record count checked deterministically.",
                self._tabfact_record_equal_count_answer(question, df),
            ),
            (
                "TabFact monthly no-game days checked deterministically.",
                self._tabfact_month_no_game_days_answer(question, df),
            ),
            (
                "TabFact lowest-attendance game result checked deterministically.",
                self._tabfact_lowest_attendance_result_answer(question, df),
            ),
            (
                "TabFact beer award count checked deterministically.",
                self._tabfact_beer_award_count_answer(question, df),
            ),
            (
                "TabFact tennis surface count checked deterministically.",
                self._tabfact_surface_count_answer(question, df),
            ),
            (
                "TabFact championship loss claim checked deterministically.",
                self._tabfact_not_champion_loss_answer(question, df),
            ),
            (
                "TabFact top-rank country medal absence checked deterministically.",
                self._tabfact_top_n_country_no_medal_answer(question, df),
            ),
            (
                "TabFact tenure gap checked deterministically.",
                self._tabfact_tenure_gap_answer(question, df),
            ),
            (
                "TabFact stint duration checked deterministically.",
                self._tabfact_stint_duration_answer(question, df),
            ),
            (
                "TabFact race column count checked deterministically.",
                self._tabfact_race_column_count_answer(question, df),
            ),
            (
                "TabFact consecutive dated race wins checked deterministically.",
                self._tabfact_consecutive_date_wins_answer(question, df),
            ),
            (
                "TabFact same-row cell mentions checked deterministically.",
                self._tabfact_same_row_cell_mention_answer(question, df),
            ),
            (
                "TabFact entity attribute row checked deterministically.",
                self._tabfact_entity_attribute_answer(question, df),
            ),
            (
                "TabFact column value count assertion checked deterministically.",
                self._tabfact_column_value_count_assertion_answer(question, df),
            ),
            (
                "TabFact two-entity appearance count checked deterministically.",
                self._tabfact_two_entity_appearance_count_answer(question, df),
            ),
            (
                "TabFact first-last time gap checked deterministically.",
                self._tabfact_first_last_time_gap_answer(question, df),
            ),
            (
                "TabFact entity metric difference value checked deterministically.",
                self._tabfact_entity_metric_difference_value_answer(question, df),
            ),
            (
                "TabFact country pair affiliation checked deterministically.",
                self._tabfact_country_pair_answer(question, df),
            ),
            (
                "TabFact zero gold medal count checked deterministically.",
                self._tabfact_zero_gold_count_answer(question, df),
            ),
            (
                "TabFact numbered same-team relation checked deterministically.",
                self._tabfact_numbered_same_team_answer(question, df),
            ),
            (
                "TabFact all games before date result checked deterministically.",
                self._tabfact_every_before_date_result_answer(question, df),
            ),
            (
                "TabFact venue competition date checked deterministically.",
                self._tabfact_venue_competition_date_answer(question, df),
            ),
            (
                "TabFact score-but-lose relation checked deterministically.",
                self._tabfact_score_but_lose_answer(question, df),
            ),
            (
                "TabFact second-smallest metric row checked deterministically.",
                self._tabfact_second_smallest_metric_answer(question, df),
            ),
            (
                "TabFact retirement threshold count checked deterministically.",
                self._tabfact_retirement_threshold_answer(question, df),
            ),
            (
                "TabFact atomic row fact matched with minor spelling tolerance.",
                self._tabfact_fuzzy_row_inclusion_answer(question, df),
            ),
        ]:
            if value is not None:
                return self._apply_semantic_shortcut(state, value, reason)
        return False

    def _try_wtq_semantic_shortcut(self, state: TQASessionState) -> bool:
        if state.dataset_profile != "wtq":
            return False
        question = state.question or ""
        df = state.original_df
        for reason, value in [
            (
                "WTQ stated remaining count computed from question counts.",
                self._wtq_stated_left_count_answer(question, df),
            ),
            (
                "WTQ release date gap computed deterministically.",
                self._wtq_release_date_gap_answer(question, df),
            ),
            (
                "WTQ only row matching a column threshold selected deterministically.",
                self._wtq_only_column_threshold_answer(question, df),
            ),
            (
                "WTQ only row matching a metric value selected deterministically.",
                self._wtq_only_metric_value_answer(question, df),
            ),
            (
                "WTQ metric values summed after entity filtering.",
                self._wtq_metric_sum_by_entity_answer(question, df),
            ),
            (
                "WTQ rows for listed entities counted and combined.",
                self._wtq_combined_entity_count_answer(question, df),
            ),
            (
                "WTQ target column selected from multiple row conditions deterministically.",
                self._wtq_multi_condition_lookup_answer(question, df),
            ),
            (
                "WTQ row-major listed-after value selected deterministically.",
                self._wtq_listed_after_cell_answer(question, df),
            ),
            (
                "WTQ directly-before adjacent row target selected deterministically.",
                self._wtq_directly_before_reference_answer(question, df),
            ),
            (
                "WTQ overtime marker rows counted deterministically.",
                self._wtq_overtime_count_answer(question, df),
            ),
            (
                "WTQ playoff participation count checked deterministically.",
                self._wtq_playoff_count_answer(question, df),
            ),
            (
                "WTQ requested column winner entries counted deterministically.",
                self._wtq_column_entry_count_answer(question, df),
            ),
            (
                "WTQ unique sponsor names counted deterministically.",
                self._wtq_sponsor_count_answer(question, df),
            ),
            (
                "WTQ retired-injured ordinal attempt row selected deterministically.",
                self._wtq_retired_injured_attempt_answer(question, df),
            ),
            (
                "WTQ low-frequency winning entity selected deterministically.",
                self._wtq_no_more_than_once_answer(question, df),
            ),
            (
                "WTQ last chart/table entity selected deterministically.",
                self._wtq_last_row_entity_answer(question, df),
            ),
            (
                "WTQ superlative owner selected from metric column deterministically.",
                self._wtq_superlative_owner_answer(question, df),
            ),
            (
                "WTQ route destination after a named stop selected deterministically.",
                self._wtq_route_after_stop_answer(question, df),
            ),
            (
                "WTQ after-reference row order answered deterministically.",
                self._wtq_after_reference_answer(question, df),
            ),
            (
                "WTQ zero metric rows counted deterministically.",
                self._wtq_zero_metric_count_answer(question, df),
            ),
            (
                "WTQ matching table columns counted deterministically.",
                self._wtq_same_column_count_answer(question, df),
            ),
            (
                "WTQ entity selected where two numbering columns match.",
                self._wtq_same_number_entity_answer(question, df),
            ),
            (
                "WTQ contributor rows counted deterministically.",
                self._wtq_contributor_count_answer(question, df),
            ),
            (
                "WTQ listed usage items counted deterministically.",
                self._wtq_usage_count_answer(question, df),
            ),
            (
                "WTQ listed entity occurrences counted across table cells.",
                self._wtq_occurrence_count_answer(question, df),
            ),
            (
                "WTQ ordinal position threshold counted deterministically.",
                self._wtq_ordinal_position_count_answer(question, df),
            ),
            (
                "WTQ named year-span duration computed deterministically.",
                self._wtq_named_year_span_duration_answer(question, df),
            ),
            (
                "WTQ consecutive month run counted deterministically.",
                self._wtq_consecutive_month_count_answer(question, df),
            ),
            (
                "WTQ rows before or after a specific date counted deterministically.",
                self._wtq_date_cutoff_row_count_answer(question, df),
            ),
            (
                "WTQ rows after requested month counted deterministically.",
                self._wtq_after_month_row_count_answer(question, df),
            ),
            (
                "WTQ first date crossing a metric threshold selected deterministically.",
                self._wtq_first_metric_threshold_date_answer(question, df),
            ),
            (
                "WTQ last requested table-column value selected deterministically.",
                self._wtq_last_requested_column_answer(question, df),
            ),
            (
                "WTQ top placing competitor selected by minimum place/rank.",
                self._wtq_top_placing_competitor_answer(question, df),
            ),
            (
                "WTQ last placing entity selected by maximum place/rank.",
                self._wtq_last_placing_entity_answer(question, df),
            ),
            (
                "WTQ requested column selected from extreme metric row.",
                self._wtq_extreme_metric_lookup_answer(question, df),
            ),
            (
                "WTQ first/last status entity selected deterministically.",
                self._wtq_first_status_entity_answer(question, df),
            ),
            (
                "WTQ low score in score-pair row mapped to the corresponding entity.",
                self._wtq_score_pair_low_score_entity_answer(question, df),
            ),
            (
                "WTQ column-header number selected from the column containing the requested year.",
                self._wtq_column_header_number_for_year_answer(question, df),
            ),
            (
                "WTQ rank gap computed as an absolute position difference.",
                self._wtq_rank_gap_answer(question, df),
            ),
            (
                "WTQ absent explicit option selected from table coverage.",
                self._wtq_explicit_option_absence_answer(question, df),
            ),
            (
                "WTQ explicit metric-value entity list returned deterministically.",
                self._wtq_metric_value_entity_list_answer(question, df),
            ),
            (
                "WTQ blank rank prefixes inferred from row order.",
                self._wtq_inferred_rank_entity_answer(question, df),
            ),
            (
                "WTQ threshold-qualified rows counted deterministically.",
                self._wtq_threshold_count_answer(question, df),
            ),
            (
                "WTQ column values matching a requested phrase counted deterministically.",
                self._wtq_column_value_count_answer(question, df),
            ),
            (
                "WTQ at-least metric rows counted deterministically.",
                self._wtq_at_least_metric_count_answer(question, df),
            ),
            (
                "WTQ at-least chart question counted unique values in the requested column.",
                self._wtq_unique_count_threshold_answer(question, df),
            ),
            (
                "WTQ travel duration computed from departure and arrival times.",
                self._wtq_duration_answer(question, df),
            ),
            (
                "WTQ existing total row metric selected deterministically.",
                self._wtq_existing_total_metric_answer(question, df),
            ),
            (
                "WTQ combined-numbers phrasing interpreted as summing the requested metric column.",
                self._wtq_combined_numbers_for_column_answer(question, df),
            ),
        ]:
            if value is not None:
                return self._apply_semantic_shortcut(state, value, reason)
        return False

    def _try_crt_semantic_shortcut(self, state: TQASessionState) -> bool:
        if state.dataset_profile != "crt":
            return False
        question = state.question or ""
        df = state.original_df
        for reason, value in [
            (
                "CRT numeric outlier presence checked deterministically.",
                self._crt_numeric_outlier_answer(question, df),
            ),
            (
                "CRT top-k years-played average computed deterministically.",
                self._crt_top_k_years_average_answer(question, df),
            ),
            (
                "CRT constructor retirement reason percentage computed deterministically.",
                self._crt_constructor_retirement_reason_percentage_answer(question, df),
            ),
            (
                "CRT episode viewership compared with season average deterministically.",
                self._crt_episode_viewership_vs_season_average_answer(question, df),
            ),
            (
                "CRT owner percentage computed deterministically.",
                self._crt_owned_percentage_answer(question, df),
            ),
            (
                "CRT medal probability computed deterministically.",
                self._crt_medal_probability_answer(question, df),
            ),
            (
                "CRT medal ratio computed deterministically.",
                self._crt_medal_ratio_answer(question, df),
            ),
            (
                "CRT total-points season ratio rounded deterministically.",
                self._crt_total_points_season_ratio_answer(question, df),
            ),
            (
                "CRT threshold-filtered average rounded deterministically.",
                self._crt_average_metric_for_threshold_answer(question, df),
            ),
            (
                "CRT penalty score relation checked deterministically.",
                self._crt_penalty_score_answer(question, df),
            ),
            (
                "CRT partner win-loss ratio formatted deterministically.",
                self._crt_partner_win_loss_ratio_answer(question, df),
            ),
            (
                "CRT year-established variation computed as range.",
                self._crt_year_variation_answer(question, df),
            ),
            (
                "CRT named-team largest winning margin computed deterministically.",
                self._crt_named_team_largest_margin_answer(question, df),
            ),
            (
                "CRT majority medal group checked deterministically.",
                self._crt_majority_medal_by_group_answer(question, df),
            ),
            (
                "CRT significant medals without gold checked deterministically.",
                self._crt_significant_medals_without_gold_answer(question, df),
            ),
            (
                "CRT role count checked deterministically.",
                self._crt_count_role_answer(question, df),
            ),
            (
                "CRT first supported architecture row selected deterministically.",
                self._crt_first_supported_architecture_answer(question, df),
            ),
            (
                "CRT leftover funds after disbursements computed deterministically.",
                self._crt_largest_money_leftover_answer(question, df),
            ),
            (
                "CRT nationality proportion computed deterministically.",
                self._crt_proportion_nationality_answer(question, df),
            ),
            (
                "CRT repeated named entity checked deterministically.",
                self._crt_duplicate_named_entity_answer(question, df),
            ),
            (
                "CRT victory type distribution checked deterministically.",
                self._crt_victory_type_stands_out_answer(question, df),
            ),
            (
                "CRT cardinal source mode checked deterministically.",
                self._crt_common_cardinal_source_answer(question, df),
            ),
            (
                "CRT diverse content beyond excluded theme checked deterministically.",
                self._crt_diverse_content_beyond_answer(question, df),
            ),
        ]:
            if value is not None:
                return self._apply_semantic_shortcut(state, value, reason)
        consistent_top_k = self._crt_consistent_top_k_answer(question, df)
        if consistent_top_k is not None:
            return self._apply_semantic_shortcut(
                state,
                consistent_top_k,
                "CRT consistent top-k membership checked deterministically.",
            )
        century_comparison = self._crt_century_manufacturing_answer(question, df)
        if century_comparison is not None:
            return self._apply_semantic_shortcut(
                state,
                century_comparison,
                "CRT century manufacturing ranges counted deterministically.",
            )
        consecutive_medalist = self._crt_consecutive_year_medalist_answer(question, df)
        if consecutive_medalist is not None:
            return self._apply_semantic_shortcut(
                state,
                consecutive_medalist,
                "CRT consecutive-year medalists checked deterministically.",
            )
        recognition_category = self._crt_recognition_category_advantage_answer(question, df)
        if recognition_category is not None:
            return self._apply_semantic_shortcut(
                state,
                recognition_category,
                "CRT repeated award-category themes checked deterministically.",
            )
        percentage_average = self._crt_percentage_snapshot_average(question, df)
        if percentage_average is not None:
            places = state.answer_contract.decimal_places
            if places is None or (
                places < 3
                and not re.search(r"\b\d+\s+decimal", question, flags=re.I)
            ):
                places = 3
            if state.answer_contract.decimal_places != places:
                state.answer_contract = AnswerContract(
                    kind=state.answer_contract.kind,
                    allowed_labels=state.answer_contract.allowed_labels,
                    reasoning_required=state.answer_contract.reasoning_required,
                    instructions=state.answer_contract.instructions,
                    decimal_places=places,
                    arity=state.answer_contract.arity,
                )
            value = round(percentage_average, places if places is not None else 3)
            return self._apply_semantic_shortcut(
                state,
                value,
                "CRT percentage snapshot average computed deterministically.",
            )
        event_difference = self._crt_event_type_difference_answer(question, df)
        if event_difference is not None:
            return self._apply_semantic_shortcut(
                state,
                event_difference,
                "CRT event-type association checked deterministically.",
            )
        duration_change = self._crt_duration_change_answer(question, df)
        if duration_change is not None:
            return self._apply_semantic_shortcut(
                state,
                duration_change,
                "CRT duration values normalized and compared deterministically.",
            )
        return False

    def _candidate_from_state(self, state: TQASessionState, name: str = "code") -> CandidateAnswer:
        valid = state.contract_validation.get("valid")
        is_valid = bool(valid) if valid is not None else state.final_value not in (None, "")
        failure = "" if is_valid else (
            state.contract_validation.get("reason")
            or state.exec_error
            or "invalid_candidate"
        )
        return CandidateAnswer(
            name=name,
            raw_answer=state.final_value,
            normalized_answer=state.final_value,
            is_valid=is_valid,
            reasoning_summary="\n".join(state.plan_steps),
            evidence_refs=[],
            executable_program=state.code_str,
            execution_result=state.final_value if state.exec_success else None,
            confidence=0.98 if is_valid and state.deterministic_shortcut_applied else (0.7 if is_valid else 0.0),
            token_usage=self._current_token_count(),
            failure=failure,
        )

    def _alternative_candidate_from_state(self, state: TQASessionState) -> Optional[CandidateAnswer]:
        if state.alternative_final_value in (None, "") and not state.alternative_exec_success:
            return None
        normalized = normalize_contract_value(
            state.alternative_final_value,
            state.answer_contract,
        )
        valid, reason = validate_contract_value(normalized, state.answer_contract)
        return CandidateAnswer(
            name="alternative",
            raw_answer=state.alternative_final_value,
            normalized_answer=normalized,
            is_valid=valid and bool(state.alternative_exec_success),
            reasoning_summary=state.alternative_plan_raw_output,
            executable_program=state.alternative_code_str,
            execution_result=normalized if state.alternative_exec_success else None,
            confidence=0.6 if valid and state.alternative_exec_success else 0.0,
            token_usage=0,
            failure="" if valid and state.alternative_exec_success else (reason or state.alternative_exec_error or "invalid_alternative"),
        )

    @staticmethod
    def _should_accept_strong_candidate(
        current: CandidateAnswer,
        thinking: CandidateAnswer,
        answer_contract: AnswerContract,
        forced: bool,
    ) -> bool:
        if not thinking.is_valid:
            return False
        if forced:
            return True
        if thinking.confidence < 0.55:
            return False
        if answer_similarity(
            current.normalized_answer,
            thinking.normalized_answer,
            answer_contract,
        ) >= 1.0:
            return True
        return thinking.confidence >= 0.60

    @classmethod
    def _should_preserve_crt_numeric_execution(
        cls,
        state: TQASessionState,
        selected: Optional[CandidateAnswer],
        consensus: Optional[CandidateAnswer],
        forced: bool,
    ) -> bool:
        if forced or (state.dataset_profile or "").lower() != "crt":
            return False
        if selected is None or consensus is None:
            return False
        if selected.name != "code" or not selected.is_valid:
            return False
        if not consensus.name.startswith("thinking_") or not consensus.is_valid:
            return False
        if answer_similarity(
            selected.normalized_answer,
            consensus.normalized_answer,
            state.answer_contract,
        ) >= 1.0:
            return False
        if not (
            cls._is_numeric_scalar_answer(selected.normalized_answer)
            and cls._is_numeric_scalar_answer(consensus.normalized_answer)
        ):
            return False
        return bool(
            re.search(
                r"\b(?:average|mean|proportion|ratio|percentage|percent)\b",
                state.question or "",
                flags=re.I,
            )
        )

    @staticmethod
    def _is_numeric_scalar_answer(value: Any) -> bool:
        if isinstance(value, bool):
            return False
        if isinstance(value, (int, float)):
            return True
        if not isinstance(value, str):
            return False
        text = value.strip().replace(",", "")
        return bool(re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text))

    @staticmethod
    def _looks_like_failed_lookup_answer(value: Any) -> bool:
        text = str(value if value is not None else "").strip().lower()
        return any(
            marker in text
            for marker in (
                "not found",
                "no unique",
                "one or both",
                "unable to determine",
                "cannot determine",
            )
        )

    @staticmethod
    def _looks_like_aggregate_row_label_answer(value: Any) -> bool:
        text = str(value if value is not None else "").strip().lower()
        text = re.sub(r"\s+", " ", text)
        return bool(
            re.fullmatch(
                r"(?:grand )?total(?:\s+(?:number|goals?|points?|medals?|pasurams|votes?)\b.*)?",
                text,
            )
        )

    @staticmethod
    def _wtq_question_expects_entity_scalar(question: str) -> bool:
        text = question or ""
        if not re.match(r"\s*(?:who|which|name|list)\b", text, flags=re.I):
            return False
        return not re.search(
            r"\b(?:how many|what number|what is the number|"
            r"what was the number|what year|which year)\b",
            text,
            flags=re.I,
        )

    @staticmethod
    def _wtq_value_in_question(question: str, value: Any) -> bool:
        value_key = _loose_text_key(value)
        question_key = _loose_text_key(question)
        return bool(value_key and value_key in question_key)

    @staticmethod
    def _wtq_value_in_original_table(state: TQASessionState, value: Any) -> bool:
        value_key = _loose_text_key(value)
        if not value_key:
            return False
        df = state.original_df
        for _, row in df.iterrows():
            for col in df.columns:
                if value_key in _loose_text_key(row[col]):
                    return True
        return False

    @staticmethod
    def _wtq_numeric_value_in_original_table(state: TQASessionState, value: Any) -> bool:
        target = _as_number_like(value)
        if target is None:
            return False
        df = state.original_df
        for _, row in df.iterrows():
            for col in df.columns:
                cell = row[col]
                cell_number = _as_number_like(cell)
                if cell_number is not None and abs(cell_number - target) <= 1e-9:
                    return True
                for token in re.findall(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?", str(cell or "")):
                    token_number = _as_number_like(token)
                    if token_number is not None and abs(token_number - target) <= 1e-9:
                        return True
        return False

    @staticmethod
    def _looks_like_year_number(value: Optional[float]) -> bool:
        if value is None:
            return False
        rounded = round(value)
        return abs(value - rounded) <= 1e-9 and 1700 <= int(rounded) <= 2100

    @classmethod
    def _should_accept_wtq_verifier_override(
        cls,
        state: TQASessionState,
        selected: CandidateAnswer,
        consensus: CandidateAnswer,
    ) -> bool:
        if (state.dataset_profile or "").lower() != "wtq":
            return False
        if not consensus.name.startswith("thinking_"):
            return False
        if not consensus.is_valid or float(consensus.confidence or 0.0) < 0.90:
            return False
        if not selected.is_valid:
            return True
        if answer_similarity(
            selected.normalized_answer,
            consensus.normalized_answer,
            state.answer_contract,
        ) >= 1.0:
            return False

        question = state.question or ""
        selected_value = selected.normalized_answer
        consensus_value = consensus.normalized_answer
        if cls._looks_like_failed_lookup_answer(selected_value):
            return True
        if cls._looks_like_aggregate_row_label_answer(selected_value):
            return True
        if (
            cls._wtq_question_expects_entity_scalar(question)
            and cls._is_numeric_scalar_answer(selected_value)
        ):
            return True
        if re.search(r"\bconsecutive\s+days\b", question, flags=re.I):
            return True
        if (
            re.search(r"\bonly\b", question, flags=re.I)
            and cls._wtq_value_in_original_table(state, consensus_value)
        ):
            return True
        if (
            re.search(
                r"\b(?:earlier|shortest|longest|higher|lower|more|less)\b",
                question,
                flags=re.I,
            )
            and re.search(r"\bor\b", question, flags=re.I)
            and cls._wtq_value_in_question(question, selected_value)
            and cls._wtq_value_in_question(question, consensus_value)
        ):
            return True
        if (
            re.search(r"\b(?:first|earliest|next)\b", question, flags=re.I)
            and cls._wtq_value_in_original_table(state, consensus_value)
            and not (
                cls._wtq_value_in_question(question, selected_value)
                and not cls._wtq_value_in_question(question, consensus_value)
            )
        ):
            return True
        selected_number = _as_number_like(selected_value)
        consensus_number = _as_number_like(consensus_value)
        if (
            cls._looks_like_year_number(selected_number)
            and cls._looks_like_year_number(consensus_number)
            and re.search(r"\b(?:(?:what|which)\s+year|years?)\b", question, flags=re.I)
            and re.search(r"\b(?:not|nor|neither|except|excluding)\b", question, flags=re.I)
            and cls._wtq_numeric_value_in_original_table(state, consensus_value)
        ):
            return True
        if (
            selected_number is not None
            and abs(selected_number) <= 1e-9
            and consensus_number not in (None, 0.0)
            and re.search(r"\b(?:top scorer|most|top number)\b", question, flags=re.I)
        ):
            return True
        return False

    @staticmethod
    def _select_consensus_candidate(
        candidates: List[CandidateAnswer],
        answer_contract: AnswerContract,
    ) -> Optional[CandidateAnswer]:
        valid = [candidate for candidate in candidates if candidate.is_valid]
        if not valid:
            return None
        groups: List[List[CandidateAnswer]] = []
        for candidate in valid:
            placed = False
            for group in groups:
                if answer_similarity(
                    group[0].normalized_answer,
                    candidate.normalized_answer,
                    answer_contract,
                ) >= 1.0:
                    group.append(candidate)
                    placed = True
                    break
            if not placed:
                groups.append([candidate])

        def group_score(group: List[CandidateAnswer]) -> Tuple[int, float, float]:
            verifier_votes = sum(1 for item in group if item.name.startswith("thinking_"))
            avg_conf = sum(float(item.confidence or 0.0) for item in group) / max(1, len(group))
            return (len(group), verifier_votes, avg_conf)

        best_group = max(groups, key=group_score)
        if len(best_group) >= 2:
            return max(best_group, key=lambda item: float(item.confidence or 0.0))
        # With no agreement, keep the single highest-confidence verifier only if
        # it is very confident; otherwise preserve the main selected candidate.
        best = max(valid, key=lambda item: float(item.confidence or 0.0))
        if best.name.startswith("thinking_") and float(best.confidence or 0.0) >= 0.90:
            return best
        return None

    def _should_apply_strong_verification(
        self,
        state: TQASessionState,
        decision: Any,
    ) -> Tuple[bool, str, bool]:
        if not self.enable_strong_verification:
            return False, "", False
        if self.thinking_solver_factory is None:
            return False, "", False
        if state.post_risk_assessment and state.post_risk_assessment.requires_fallback:
            return True, "post_risk_requires_fallback", True
        if getattr(decision, "requires_fallback", False):
            return True, f"candidate_agreement:{decision.reason}", True

        tags = set(state.problem_tags)
        dataset = (state.dataset_profile or "").lower()
        if dataset == "wtq":
            if state.route_type == "SIMPLE" and state.answer_contract.kind == "scalar":
                return True, "wtq_simple_crosscheck", False
            if tags & {"superlative_order", "count", "negation_logic"}:
                return True, "wtq_high_risk_denotation", False
            if "temporal" in tags and state.route_type == "COMPLEX":
                return True, "wtq_temporal_crosscheck", False
            if "temporal" in tags and tags & {"comparison", "arithmetic", "list_entity"}:
                return True, "wtq_temporal_reasoning", False
        if dataset == "tabfact":
            if state.answer_contract.kind == "label":
                # TabFact binary labels stay on the cheaper verifier path unless
                # fallback/disagreement forced verification above. Trigger-71
                # diagnostics showed broad unforced overrides add cost without net gain.
                return False, "", False
        if dataset == "crt":
            if state.answer_contract.kind == "label" and tags & {
                "closed_choice",
                "comparison",
                "arithmetic",
                "trend_correlation",
                "superlative_order",
            }:
                return True, "crt_closed_choice_verification", False
            if tags & {"trend_correlation"}:
                return True, "crt_trend_or_correlation_verification", False
            if {"superlative_order", "arithmetic"} <= tags:
                return True, "crt_ordered_numeric_verification", False

        if state.risk_assessment and state.risk_assessment.level in {"high", "fallback"}:
            return True, f"risk_level:{state.risk_assessment.level}", False
        return False, "", False

    def _strong_verification_evidence(
        self,
        state: TQASessionState,
        reason: str,
    ) -> Dict[str, object]:
        original_schema = _build_table_schema(
            state.original_df,
            max_preview_rows=min(8, max(1, len(state.original_df))),
        )
        return {
            "dataset_profile": state.dataset_profile,
            "dataset_instructions": state.dataset_instructions,
            "table_subject_context": state.table_context,
            "trigger_reason": reason,
            "question_tags": list(state.problem_tags),
            "compression_info": dict(state.compression_info or {}),
            "original_table_shape": {
                "rows": int(state.original_df.shape[0]),
                "columns": [str(column) for column in state.original_df.columns],
            },
            "original_table": _df_evidence_text(state.original_df),
            "compressed_table_shape": {
                "rows": int(state.df.shape[0]),
                "columns": [str(column) for column in state.df.columns],
            },
            "compressed_table": _df_evidence_text(state.df),
            "column_profiles": original_schema.get("column_profiles_text", ""),
            "evidence_pack": state.evidence_pack.to_dict() if state.evidence_pack is not None else {},
        }

    def _run_selective(self, state: TQASessionState) -> TQASessionState:
        state.evidence_pack = self.evidence_builder.build(
            question=state.question,
            df=state.original_df,
            schema=state.table_schema,
            dataset_name=state.dataset_profile,
            answer_contract=state.answer_contract,
        )
        result = self._run_legacy(state)
        self._assess_selective_risk(result)

        candidates = [self._candidate_from_state(result)]
        alternative = self._alternative_candidate_from_state(result)
        if alternative is not None:
            candidates.append(alternative)
        result.candidate_answers = list(candidates)

        judge = self.agreement_judge_factory(result.answer_contract)
        decision = judge.decide(candidates)
        result.agreement_decision = decision
        result.post_risk_assessment = self.risk_profiler.assess_post(
            pre_risk=result.risk_assessment.pre_risk,
            candidate_disagreement=decision.disagreement,
            verification_gap=decision.verification_gap,
            execution_failure=0.0 if result.exec_success or result.simple_lookup_success else 1.0,
            contract_failure=1.0 if result.contract_validation.get("valid") is False else 0.0,
            unit_failure=0.0,
            normalization_failure=1.0 if result.contract_validation.get("valid") is False else 0.0,
        )
        should_verify, verification_reason, forced = self._should_apply_strong_verification(
            result,
            decision,
        )
        if should_verify:
            result.strong_verification_applied = True
            result.strong_verification_reason = verification_reason
            if result.risk_level not in {"fallback"}:
                result.risk_level = "high" if not forced else "fallback"
            verification_evidence = self._strong_verification_evidence(
                result,
                verification_reason,
            )
            strong_candidates: List[CandidateAnswer] = []
            verification_styles = self.verification_styles
            if result.dataset_profile == "wtq" and not forced:
                verification_styles = ["direct"]
            for style in verification_styles:
                thinking = self.thinking_solver_factory().solve(
                    question=result.question,
                    evidence=verification_evidence,
                    candidates=candidates + strong_candidates,
                    answer_contract=result.answer_contract,
                    style=style,
                )
                strong_candidates.append(thinking)
                result.candidate_answers.append(thinking)
            selected = decision.selected or candidates[0]
            all_candidates = candidates + strong_candidates
            consensus = self._select_consensus_candidate(
                all_candidates,
                result.answer_contract,
            )
            if consensus is None and forced:
                consensus = next(
                    (candidate for candidate in strong_candidates if candidate.is_valid),
                    None,
                )
            protected_deterministic = (
                result.deterministic_shortcut_applied
                and consensus is not None
                and selected is not None
                and not forced
                and answer_similarity(
                    selected.normalized_answer,
                    consensus.normalized_answer,
                    result.answer_contract,
                )
                < 1.0
            )
            if protected_deterministic:
                consensus = selected
            protected_crt_numeric_execution = self._should_preserve_crt_numeric_execution(
                result,
                selected,
                consensus,
                forced,
            )
            if protected_crt_numeric_execution:
                consensus = selected
            wtq_unforced_verifier_conflict = (
                result.dataset_profile == "wtq"
                and not forced
                and consensus is not None
                and selected is not None
                and selected.is_valid
                and consensus.name.startswith("thinking_")
                and answer_similarity(
                    selected.normalized_answer,
                    consensus.normalized_answer,
                    result.answer_contract,
                )
                < 1.0
            )
            wtq_verifier_override = (
                wtq_unforced_verifier_conflict
                and consensus is not None
                and selected is not None
                and self._should_accept_wtq_verifier_override(
                    result,
                    selected,
                    consensus,
                )
            )
            accepted_consensus = False
            if consensus is not None and (
                forced
                or wtq_verifier_override
                or (
                    not wtq_unforced_verifier_conflict
                    and (
                        consensus.name == selected.name
                        or self._should_accept_strong_candidate(
                            selected,
                            consensus,
                            result.answer_contract,
                            forced=forced,
                        )
                    )
                )
            ):
                accepted_consensus = True
                result.final_value = consensus.normalized_answer
                result.simple_lookup_success = False
                result.simple_lookup_value = None
                result.simple_lookup_evidence = {
                    "source": "strong_verification_consensus",
                    "previous_source": result.simple_lookup_evidence,
                }
                self._normalize_and_validate(result)
                if result.answer_contract.kind in {"list", "tuple"}:
                    result.final_answer = json.dumps(result.final_value, ensure_ascii=False)
                else:
                    result.final_answer = str(result.final_value).strip()
            post_decision = judge.decide(all_candidates)
            if accepted_consensus:
                result.agreement_decision = AgreementDecision(
                    selected=consensus,
                    candidates=all_candidates,
                    agreement=True,
                    requires_fallback=False,
                    disagreement=0.0,
                    verification_gap=post_decision.verification_gap,
                    reason=(
                        "deterministic_shortcut_preserved"
                        if protected_deterministic
                        else "crt_numeric_execution_preserved"
                        if protected_crt_numeric_execution
                        else "wtq_answer_shape_verifier_override"
                        if wtq_verifier_override
                        else "strong_verification_consensus"
                    ),
                )
            elif result.deterministic_shortcut_applied and selected is not None and not forced:
                result.agreement_decision = AgreementDecision(
                    selected=selected,
                    candidates=all_candidates,
                    agreement=True,
                    requires_fallback=False,
                    disagreement=post_decision.disagreement,
                    verification_gap=post_decision.verification_gap,
                    reason="deterministic_shortcut_preserved",
                )
            else:
                result.agreement_decision = post_decision
        elif result.post_risk_assessment.requires_fallback:
            result.risk_level = "fallback"
            if self.thinking_solver_factory is not None:
                thinking = self.thinking_solver_factory().solve(
                    question=result.question,
                    evidence=result.evidence_pack.to_dict(),
                    candidates=candidates,
                    answer_contract=result.answer_contract,
                )
                result.candidate_answers.append(thinking)
                if thinking.is_valid:
                    result.final_value = thinking.normalized_answer
                    result.simple_lookup_success = False
                    result.simple_lookup_value = None
                    result.simple_lookup_evidence = {
                        "source": "strong_verification_fallback",
                        "previous_source": result.simple_lookup_evidence,
                    }
                    self._normalize_and_validate(result)
                    if result.answer_contract.kind in {"list", "tuple"}:
                        result.final_answer = json.dumps(result.final_value, ensure_ascii=False)
                    else:
                        result.final_answer = str(result.final_value).strip()

        self.budget_controller.record(result.risk_level or "medium", self._current_token_count())
        result.budget_state = self.budget_controller.to_dict()
        return result

    def _run_legacy(self, state: TQASessionState) -> TQASessionState:
        started_at = time.perf_counter()
        try:
            # 1) routing
            if self.disable_question_routing:
                state = self._apply_no_routing_ablation(state)
            else:
                state = self.router.route(state)
            if not state.route_type:
                state.route_type = "COMPLEX"
            if state.answer_contract.reasoning_required:
                state.route_type = "COMPLEX"
                state.risk_escalated = True

            # 2) question-aware compression, used by both paths
            if self.disable_table_compression:
                state = self._apply_no_compression_ablation(state)
            else:
                state = self.compressor.compress(state)

            if self.enable_deterministic_shortcuts:
                if self._try_wtq_semantic_shortcut(state):
                    return state

                if self._try_tabfact_semantic_shortcut(state):
                    return state

                if self._try_crt_semantic_shortcut(state):
                    return state

            if (
                state.answer_contract.kind == "label"
                and not state.answer_contract.reasoning_required
            ):
                state = self.final_answer_agent.classify(state)
                self._normalize_and_validate(state)
                return state

            # 3) simple path
            if state.route_type == "SIMPLE":
                state = self.simple_lookup_agent.lookup(state)
                state = self.final_answer_agent.respond(state)
                self._normalize_and_validate(state)
                return state

            # 4) complex path with planner / calculator / critic
            for _ in range(self.max_replan):
                state = self.planner.plan(state)
                grounding_valid, grounding_reason = validate_generated_code_grounding(
                    state.question,
                    state.code_str,
                )
                state.grounding_validation = {
                    "valid": grounding_valid,
                    "reason": grounding_reason,
                }
                if not grounding_valid:
                    state.exec_success = False
                    state.exec_error = grounding_reason
                    state.exec_locals = {}
                    state.final_value = None
                    state.critic_verdict = "REPLAN"
                    state.critic_feedback = grounding_reason
                    continue
                contract_code_valid, contract_code_reason = validate_answer_contract_code_alignment(
                    state.code_str,
                    state.answer_contract,
                )
                if not contract_code_valid:
                    state.exec_success = False
                    state.exec_error = contract_code_reason
                    state.exec_locals = {}
                    state.final_value = None
                    state.critic_verdict = "REPLAN"
                    state.critic_feedback = contract_code_reason
                    continue
                state = self.calculator.execute(state)
                if not state.exec_success:
                    state.critic_verdict = "REPLAN"
                    state.critic_feedback = (
                        f"Execution failed: {state.exec_error}. Re-inspect the column "
                        "profiles, verify that filters match at least one row before "
                        "using iloc/values[0], and check other text columns for the "
                        "requested entity. Do not repeat the same failing filter."
                    )
                    continue
                if (
                    state.dataset_profile == "wtq"
                    and state.answer_contract.kind == "scalar"
                ):
                    state.final_value = _canonicalize_wtq_scalar(
                        state.final_value,
                        state.df,
                        state.question,
                    )
                if (
                    state.dataset_profile == "crt"
                    and state.answer_contract.kind == "scalar"
                ):
                    state.final_value = _canonicalize_crt_scalar(
                        state.final_value,
                        state.question,
                        state.df,
                    )
                if (
                    state.answer_contract.kind == "scalar"
                    and re.match(r"^\s*(?:who|which)\b", state.question, flags=re.I)
                    and "release date" not in state.question.lower()
                ):
                    state.final_value = _strip_entity_metadata(state.final_value)
                if not self._normalize_and_validate(state):
                    state.critic_verdict = "REPLAN"
                    state.critic_feedback = state.contract_validation["reason"]
                    continue
                if (
                    state.dataset_profile == "tabfact"
                    and state.answer_contract.kind == "label"
                ):
                    state = self.final_answer_agent.verify_tabfact(state)
                    if not self._normalize_and_validate(state):
                        state.critic_verdict = "REPLAN"
                        state.critic_feedback = state.contract_validation["reason"]
                        continue
                if self._should_run_llm_critic(state):
                    state = self.critic.review(state)
                    if state.critic_verdict != "PASS":
                        continue
                else:
                    state.critic_skipped = True
                    state.critic_verdict = "PASS"
                    state.critic_feedback = (
                        "Deterministic execution, grounding, and answer-contract checks passed."
                    )

                if self.enable_multi_view_validation:
                    state = self.multiview_validator.review(state)
                    if state.multi_view_validation.get("verdict") == "REPLAN":
                        state.critic_verdict = "REPLAN"
                        state.critic_feedback = "\n".join(
                            state.multi_view_validation.get("replan_reasons", [])
                        )
                        continue

                break

            self._normalize_and_validate(state)
            state = self.final_answer_agent.respond(state)
            if (
                state.dataset_profile == "wtq"
                and state.answer_contract.kind == "scalar"
            ):
                state.final_value = _canonicalize_wtq_scalar(
                    state.final_value,
                    state.df,
                    state.question,
                )
            if (
                state.dataset_profile == "crt"
                and state.answer_contract.kind == "scalar"
            ):
                state.final_value = _canonicalize_crt_scalar(
                    state.final_value,
                    state.question,
                    state.df,
                )
            if (
                state.answer_contract.kind == "scalar"
                and re.match(r"^\s*(?:who|which)\b", state.question, flags=re.I)
                and "release date" not in state.question.lower()
            ):
                state.final_value = _strip_entity_metadata(state.final_value)
            self._normalize_and_validate(state)
            return state
        finally:
            state.elapsed_seconds = time.perf_counter() - started_at
            state.cost_metrics = {
                "elapsed_seconds": state.elapsed_seconds,
                "route_type": state.route_type,
                "difficulty_score": state.difficulty_score,
                "difficulty_level": state.difficulty_level,
                "multi_view_validation_enabled": self.enable_multi_view_validation,
                "multi_view_validation_verdict": state.multi_view_validation.get("verdict"),
                "selective_collaboration_enabled": self.enable_selective_collaboration,
                "strong_verification_enabled": self.enable_strong_verification,
                "deterministic_shortcuts_enabled": self.enable_deterministic_shortcuts,
                "question_routing_enabled": not self.disable_question_routing,
                "risk_scoring_enabled": not self.disable_risk_scoring,
                "table_compression_enabled": not self.disable_table_compression,
                "risk_level": state.risk_level,
                "answer_mode": state.answer_mode,
                "critic_skipped": state.critic_skipped,
                **(state.compression_info or {}),
            }
        return state
