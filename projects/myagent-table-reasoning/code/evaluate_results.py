"""Aggregate MACT or myAgent JSONL outputs for experiment reporting."""

from __future__ import annotations

import argparse
import json
import re
import string
import unicodedata
from collections import Counter
from statistics import mean
from typing import Any, Dict, Iterable, List


def normalize_answer(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.lower()
    text = re.sub(r"\b(a|an|the)\b", " ", text)
    text = "".join(ch for ch in text if ch not in set(string.punctuation))
    return " ".join(text.split())


def _as_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple)) and len(value) == 1:
        value = value[0]
    if hasattr(value, "item"):
        try:
            value = value.item()
        except Exception:
            pass
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def exact_match(prediction: Any, gold: Any) -> bool:
    if isinstance(gold, (list, tuple, set)):
        return any(exact_match(prediction, candidate) for candidate in gold)
    if isinstance(prediction, (list, tuple, set)) and len(prediction) > 1:
        return any(exact_match(candidate, gold) for candidate in prediction)
    pred_num = _as_number(prediction)
    gold_num = _as_number(gold)
    if pred_num is not None and gold_num is not None:
        return abs(pred_num - gold_num) <= 1e-6
    return normalize_answer(prediction) == normalize_answer(gold)


def _as_items(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _wtq_normalize(value: Any) -> str:
    text = "" if value is None else str(value)
    text = "".join(
        char
        for char in unicodedata.normalize("NFKD", text)
        if unicodedata.category(char) != "Mn"
    )
    text = re.sub(r"[‘’´`]", "'", text)
    text = re.sub(r"[“”]", '"', text)
    text = re.sub(r"[‐‑‒–—−]", "-", text)
    text = text.replace('\\"', '"').replace("\\'", "'").strip("\\")
    while True:
        previous = text
        text = re.sub(r"((?<!^)\[[^\]]*\]|\[\d+\]|[•♦†‡*#+])*$", "", text.strip())
        text = re.sub(r"(?<!^)( \([^)]*\))*$", "", text.strip())
        text = re.sub(r'^"([^"]*)"$', r"\1", text.strip())
        if text == previous:
            break
    if text.endswith("."):
        text = text[:-1]
    text = text.strip(" \t\r\n\"'\\")
    return re.sub(r"\s+", " ", text).lower().strip()


def _wtq_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


def _wtq_item_key(value: Any) -> tuple[str, Any]:
    if isinstance(value, bool):
        return ("string", "yes" if value else "no")
    number = _wtq_number(value)
    if number is not None:
        return ("number", round(number, 9))
    return ("string", _wtq_normalize(value))


def _wtq_denotation_match(prediction: Any, row: Dict[str, Any]) -> bool:
    predicted_items = list(dict.fromkeys(_wtq_item_key(item) for item in _as_items(prediction)))
    raw_items = _as_items(gold_for_em(row))
    canonical_items = _as_items(row.get("answer_canonical") or [])
    if not raw_items:
        raw_items = canonical_items

    target_options = []
    for index, raw_item in enumerate(raw_items):
        options = {_wtq_item_key(raw_item)}
        if index < len(canonical_items):
            options.add(_wtq_item_key(canonical_items[index]))
        target_options.append(options)

    if len(predicted_items) != len(target_options):
        return False

    def match_target(index: int, used_predictions: set[int]) -> bool:
        if index == len(target_options):
            return True
        for pred_index, predicted in enumerate(predicted_items):
            if pred_index in used_predictions or predicted not in target_options[index]:
                continue
            if match_target(index + 1, used_predictions | {pred_index}):
                return True
        return False

    return match_target(0, set())


def _binary_label(value: Any) -> str:
    items = _as_items(value)
    if len(items) != 1:
        return ""
    normalized = normalize_answer(items[0])
    if normalized in {"1", "true", "yes"}:
        return "true"
    if normalized in {"0", "false", "no"}:
        return "false"
    return ""


def _crt_sequence(value: Any, split_delimited: bool) -> List[Any]:
    items = _as_items(value)
    if len(items) == 1 and split_delimited and isinstance(items[0], str):
        text = items[0].strip().strip("[]()")
        parts = [part.strip(" \t\r\n\"'") for part in re.split(r"\s*[,;|]\s*", text)]
        if len(parts) > 1:
            return parts
    return items


def _crt_answer_match(prediction: Any, row: Dict[str, Any]) -> bool:
    contract = row.get("answer_contract") or {}
    question = str(row.get("question") or row.get("utterance") or "")
    prediction_items = _as_items(prediction)
    if re.search(r"\bpercent(?:age)?\b", question, flags=re.I):
        pred_number = _as_number(prediction)
        gold_items = _as_items(gold_for_em(row))
        if len(gold_items) == 1:
            gold_number = _as_number(str(gold_items[0]).replace("%", ""))
            if pred_number is not None and gold_number is not None:
                return abs(pred_number - gold_number) <= 1e-6
    expects_multiple = (
        contract.get("kind") == "tuple"
        or len(re.findall(r"\bhow many\b", question, flags=re.I)) >= 2
        or len(prediction_items) > 1
    )
    if not expects_multiple:
        return exact_match(prediction, gold_for_em(row))

    predicted = _crt_sequence(prediction, split_delimited=True)
    target = _crt_sequence(gold_for_em(row), split_delimited=True)
    return len(predicted) == len(target) and all(
        exact_match(pred_item, target_item)
        for pred_item, target_item in zip(predicted, target)
    )


def dataset_accuracy(row: Dict[str, Any]) -> bool:
    prediction = prediction_for_em(row)
    source = str(row.get("source_dataset") or "").lower()
    if source == "wtq":
        return _wtq_denotation_match(prediction, row)
    if source == "tabfact":
        pred_label = _binary_label(prediction)
        gold_label = _binary_label(gold_for_em(row))
        return bool(pred_label and pred_label == gold_label)
    if source == "crt":
        return _crt_answer_match(prediction, row)
    return exact_match(prediction, gold_for_em(row))


def _accuracy_metric(rows: List[Dict[str, Any]]) -> str:
    sources = {str(row.get("source_dataset") or "").lower() for row in rows}
    sources.discard("")
    if sources == {"wtq"}:
        return "wtq_denotation_accuracy"
    if sources == {"tabfact"}:
        return "tabfact_binary_accuracy"
    if sources == {"crt"}:
        return "crt_normalized_accuracy"
    return "generic_exact_match" if not sources else "mixed_dataset_accuracy"


def prediction_for_em(row: Dict[str, Any]) -> Any:
    """Prefer structured values over natural-language answers for EM metrics."""
    if row.get("simple_lookup_success") and row.get("simple_lookup_value") is not None:
        return row.get("simple_lookup_value")
    if row.get("final_value") is not None:
        return row.get("final_value")
    evidence = row.get("simple_lookup_evidence") or {}
    if evidence.get("value") is not None:
        return evidence.get("value")
    if row.get("final_answer") not in (None, ""):
        return row.get("final_answer")
    return row.get("pred_answer")


def gold_for_em(row: Dict[str, Any]) -> Any:
    for key in ("gold_answer", "answer", "targetValue", "target_value"):
        if row.get(key) not in (None, ""):
            return row.get(key)
    return None


def load_jsonl(path: str) -> List[Dict[str, Any]]:
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def avg(values: Iterable[Any]) -> float:
    nums = [float(v) for v in values if v is not None]
    return mean(nums) if nums else 0.0


def _result_schema(rows: List[Dict[str, Any]]) -> str:
    return "mact" if any("pred_answer" in row for row in rows) else "myagent"


def _token_metrics(row: Dict[str, Any]) -> Dict[str, Any]:
    api = row.get("api_metrics") or {}
    if api:
        prompt = api.get("prompt_tokens", 0) or 0
        completion = api.get("completion_tokens", 0) or 0
        total = api.get("total_tokens")
        if total is None:
            total = prompt + completion
        return {
            "calls": api.get("request_count", 0),
            "prompt": prompt,
            "completion": completion,
            "total": total,
            "source": "api_usage",
        }

    llm = row.get("llm_metrics") or {}
    return {
        "calls": llm.get("llm_call_count", 0),
        "prompt": llm.get("prompt_tokens_est", 0),
        "completion": llm.get("completion_tokens_est", 0),
        "total": llm.get("total_tokens_est", 0),
        "source": "estimated",
    }


def _estimated_token_metric(row: Dict[str, Any], key: str) -> Any:
    api = row.get("api_metrics") or {}
    if key in api:
        return api.get(key)
    return (row.get("llm_metrics") or {}).get(key)


def _risk_level(row: Dict[str, Any]) -> str:
    observability = row.get("observability") or {}
    cost_metrics = row.get("cost_metrics") or {}
    risk_assessment = row.get("risk_assessment") or {}
    return str(
        row.get("risk_level")
        or observability.get("risk_level")
        or cost_metrics.get("risk_level")
        or risk_assessment.get("level")
        or "unknown"
    )


def _risk_strata_summary(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(_risk_level(row), []).append(row)

    result: Dict[str, Dict[str, Any]] = {}
    for level, level_rows in sorted(grouped.items()):
        with_gold = [row for row in level_rows if gold_for_em(row) not in (None, "")]
        correct = sum(1 for row in with_gold if dataset_accuracy(row))
        token_metrics = [_token_metrics(row) for row in level_rows]
        result[level] = {
            "count": len(level_rows),
            "num_with_gold": len(with_gold),
            "correct": correct,
            "accuracy": correct / len(with_gold) if with_gold else 0.0,
            "avg_total_tokens": avg(metric.get("total") for metric in token_metrics),
            "failure_count": sum(1 for row in level_rows if row.get("exec_error")),
        }
    return result


def summarize_rows(rows: List[Dict[str, Any]]) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    if not rows:
        return {"num_samples": 0}, []

    schema = _result_schema(rows)
    route_dist = Counter(
        row.get("route_type") for row in rows if row.get("route_type") is not None
    )
    level_dist = Counter(
        row.get("difficulty_level")
        for row in rows
        if row.get("difficulty_level") is not None
    )
    has_gold = [row for row in rows if gold_for_em(row) not in (None, "")]
    em_mismatch = [
        row
        for row in has_gold
        if not dataset_accuracy(row)
    ]
    failed_exec = [row for row in rows if row.get("exec_error")]
    missing_answer = [
        row for row in rows if prediction_for_em(row) in (None, "")
    ]
    multi_view_replan = [
        row
        for row in rows
        if row.get("multi_view_validation", {}).get("verdict") == "REPLAN"
    ]
    cross_path_mismatch = [
        row for row in rows if row.get("cross_validation_verdict") == "REPLAN"
    ]
    anomalies = list(
        {
            id(row): row
            for row in (
                failed_exec
                + missing_answer
                + em_mismatch
                + multi_view_replan
                + cross_path_mismatch
            )
        }.values()
    )

    token_metrics = [_token_metrics(row) for row in rows]
    token_sources = {metric["source"] for metric in token_metrics}
    token_measurement = (
        next(iter(token_sources)) if len(token_sources) == 1 else "mixed"
    )
    compression_ratios = []
    token_compression_ratios = []
    for row in rows:
        compression = row.get("compression_info") or {}
        ratio = compression.get("compression_ratio")
        if ratio is None and "pred_answer" in row:
            ratio = 1.0
        compression_ratios.append(ratio)
        token_compression_ratios.append(
            compression.get("token_compression_ratio_est")
        )

    summary = {
        "result_schema": schema,
        "num_samples": len(rows),
        "num_with_gold": len(has_gold),
        "exact_match": avg(
            exact_match(prediction_for_em(row), gold_for_em(row))
            for row in has_gold
        ),
        "primary_accuracy": avg(dataset_accuracy(row) for row in has_gold),
        "accuracy_metric": _accuracy_metric(has_gold),
        "num_failed_exec": len(failed_exec),
        "num_missing_answer": len(missing_answer),
        "num_em_mismatch": len(em_mismatch),
        "num_multi_view_replan": len(multi_view_replan),
        "num_cross_path_mismatch": len(cross_path_mismatch),
        "route_distribution": dict(route_dist),
        "difficulty_distribution": dict(level_dist),
        "risk_distribution": dict(Counter(_risk_level(row) for row in rows)),
        "risk_strata": _risk_strata_summary(rows),
        "simple_lookup_success_rate": avg(
            row.get("simple_lookup_success") for row in rows
        ),
        "multi_view_pass_rate": avg(
            row.get("multi_view_validation", {}).get("verdict") == "PASS"
            for row in rows
            if row.get("multi_view_validation", {}).get("enabled")
        ),
        "alternative_exec_success_rate": avg(
            row.get("alternative_exec_success")
            for row in rows
            if row.get("multi_view_validation", {}).get("enabled")
        ),
        "avg_llm_calls": avg(metric.get("calls") for metric in token_metrics),
        "avg_total_tokens": avg(metric.get("total") for metric in token_metrics),
        "avg_prompt_tokens": avg(metric.get("prompt") for metric in token_metrics),
        "avg_completion_tokens": avg(
            metric.get("completion") for metric in token_metrics
        ),
        "token_measurement": token_measurement,
        "avg_compression_ratio": avg(compression_ratios),
        "avg_token_compression_ratio_est": avg(token_compression_ratios),
        "avg_elapsed_seconds": avg(
            row.get("elapsed_seconds_total") for row in rows
        ),
    }
    # Preserve the previous myAgent report keys for downstream documents.
    summary["avg_total_tokens_est"] = avg(
        _estimated_token_metric(row, "total_tokens_est") for row in rows
    )
    summary["avg_prompt_tokens_est"] = avg(
        _estimated_token_metric(row, "prompt_tokens_est") for row in rows
    )
    summary["avg_completion_tokens_est"] = avg(
        _estimated_token_metric(row, "completion_tokens_est") for row in rows
    )
    return summary, anomalies


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("jsonl_path", help="Path to a MACT or myAgent JSONL output file.")
    parser.add_argument(
        "--error_output",
        default="",
        help="Optional jsonl path for failed execution, missing answer, or EM mismatch samples.",
    )
    args = parser.parse_args()

    rows = load_jsonl(args.jsonl_path)
    if not rows:
        print("No rows found.")
        return

    summary, anomalies = summarize_rows(rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.error_output:
        with open(args.error_output, "w", encoding="utf-8") as f:
            for row in anomalies:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
