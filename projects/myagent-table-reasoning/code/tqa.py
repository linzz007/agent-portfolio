""" MACT（NAACL 2025）相关的工具类与函数
版权 (c) 2025 Robert Bosch GmbH

本程序是自由软件：你可以按照自由软件基金会发布的 GNU Affero 通用公共许可证第 3 版或（你可以选择的）更高版本的条款重新发布和/或修改。
本程序的发布目的是希望它有用，但不提供任何保证；甚至不包含适销性或特定用途适用性的默示保证。详情参考 GNU Affero 通用公共许可证。
你应该已经收到一份 GNU Affero 通用公共许可证副本；如果没有，请访问 <https://www.gnu.org/licenses/>。"""

import argparse
import json
import os
import re
import time
import traceback

import pandas as pd

from answer_contracts import infer_answer_contract
from dataset_profiles import infer_dataset_hints
from model_backends import add_model_backend_args, build_llm_fn
from robust_outputs import apply_fallback_answer, attach_robust_fields
from my_agents import (
    RouterAgent,
    PlannerAgent,
    Calculator,
    CriticAgent,
    FinalAnswerAgent,
    LLMCallTracker,
    TableQAPipeline,
    TQASessionState,
    build_df_from_table,
    _build_table_schema,
    load_csv_row_col_names,
)
from utils import get_databench_table


def _json_default(value):
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if hasattr(value, "tolist"):
        try:
            return value.tolist()
        except (TypeError, ValueError):
            pass
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"Object of type {value.__class__.__name__} is not JSON serializable")


def _metric_delta(before, after):
    keys = ("request_count", "prompt_tokens", "completion_tokens", "total_tokens")
    return {
        key: int((after or {}).get(key, 0) or 0) - int((before or {}).get(key, 0) or 0)
        for key in keys
    }


def _append_jsonl_with_retry(path, item, attempts=5, delay_seconds=0.5):
    payload = json.dumps(item, ensure_ascii=False, default=_json_default) + "\n"
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    last_error = None
    for attempt in range(attempts):
        try:
            with open(path, "a", encoding="utf-8") as fout:
                fout.write(payload)
            return
        except PermissionError as exc:
            last_error = exc
            if attempt == attempts - 1:
                break
            time.sleep(delay_seconds * (attempt + 1))
    raise last_error


def _gold_answer_from_row(row):
    return row.get("answer") or row.get("targetValue") or row.get("target_value") or ""


def _source_dataset_for_task(task):
    task_name = (task or "").lower()
    return {
        "scitab": "tabfact",
    }.get(task_name, task_name)


def _failure_item_for_exception(
    row,
    args,
    exc,
    *,
    elapsed_seconds,
    llm_metrics,
    api_metrics=None,
):
    item = dict(row)
    item.setdefault("source_dataset", _source_dataset_for_task(getattr(args, "task", "")))
    item["route_type"] = item.get("route_type")
    item["difficulty_score"] = item.get("difficulty_score")
    item["difficulty_level"] = item.get("difficulty_level")
    item["routing_context"] = item.get("routing_context") or {}
    item["compression_info"] = item.get("compression_info") or {}
    item["cost_metrics"] = {
        "elapsed_seconds": elapsed_seconds,
        "failure_stage": "sample_exception",
    }
    item["llm_metrics"] = llm_metrics
    if api_metrics is not None:
        item["api_metrics"] = api_metrics
    item["elapsed_seconds_total"] = elapsed_seconds
    item["planner_plan_steps"] = []
    item["planner_code"] = ""
    item["exec_success"] = False
    item["exec_error"] = f"{exc.__class__.__name__}: {exc}"
    item["final_value"] = None
    item["simple_lookup_success"] = False
    item["simple_lookup_value"] = None
    item["simple_lookup_evidence"] = {}
    item["critic_verdict"] = {}
    item["critic_feedback"] = ""
    item["multi_view_validation"] = {}
    item["evidence_critic_verdict"] = {}
    item["logic_critic_verdict"] = {}
    item["alternative_code"] = ""
    item["alternative_exec_success"] = False
    item["alternative_exec_error"] = ""
    item["alternative_final_value"] = None
    item["cross_validation_verdict"] = ""
    item["evidence_summary"] = ""
    item["answer_mode"] = ""
    item["classification_raw_output"] = ""
    item["verification_raw_output"] = ""
    item["final_answer"] = ""
    item["pred_answer"] = ""
    item["gold_answer"] = _gold_answer_from_row(row)
    item["failure_traceback"] = traceback.format_exc()
    return apply_fallback_answer(
        item,
        task=getattr(args, "task", ""),
        error_message=item["exec_error"],
        retry_count=0,
        fallback_reason="myagent_sample_exception",
    )


def _to_serializable(value):
    if value is None:
        return None
    if hasattr(value, "to_dict"):
        return _to_serializable(value.to_dict())
    if hasattr(value, "as_dict"):
        return _to_serializable(value.as_dict())
    if isinstance(value, dict):
        return {str(key): _to_serializable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_serializable(item) for item in value]
    if hasattr(value, "tolist"):
        try:
            return _to_serializable(value.tolist())
        except (TypeError, ValueError):
            pass
    if hasattr(value, "item"):
        return value.item()
    return value


def _state_observability(state):
    return {
        "answer_contract": state.answer_contract.as_dict(),
        "risk_escalated": bool(state.risk_escalated),
        "contract_validation": dict(state.contract_validation),
        "grounding_validation": dict(state.grounding_validation),
        "table_context": state.table_context,
        "dataset_profile": getattr(state, "dataset_profile", ""),
        "dataset_instructions": getattr(state, "dataset_instructions", ""),
        "critic_skipped": bool(getattr(state, "critic_skipped", False)),
        "risk_level": getattr(state, "risk_level", ""),
        "risk_assessment": _to_serializable(getattr(state, "risk_assessment", None)),
        "post_risk_assessment": _to_serializable(getattr(state, "post_risk_assessment", None)),
        "problem_tags": list(getattr(state, "problem_tags", [])),
        "strong_verification_applied": bool(
            getattr(state, "strong_verification_applied", False)
        ),
        "strong_verification_reason": getattr(state, "strong_verification_reason", ""),
        "deterministic_shortcut_applied": bool(
            getattr(state, "deterministic_shortcut_applied", False)
        ),
        "deterministic_shortcut_reason": getattr(
            state,
            "deterministic_shortcut_reason",
            "",
        ),
        "evidence_pack": _to_serializable(getattr(state, "evidence_pack", None)),
        "candidate_answers": _to_serializable(getattr(state, "candidate_answers", [])),
        "agreement_decision": _to_serializable(getattr(state, "agreement_decision", None)),
        "budget_state": _to_serializable(getattr(state, "budget_state", {})),
    }


def table_context_for_row(row) -> str:
    """Collect non-gold metadata that describes what the table is about."""
    values = []
    for key in ("entity", "table_title", "title", "caption", "page_title"):
        value = str(row.get(key, "") or "").strip()
        if value and value not in values:
            values.append(value)
    return " | ".join(values)


def answer_mode_for_task(task: str) -> str:
    return {
        "scitab": "true_false",
        "tabfact": "true_false",
    }.get((task or "").lower(), "")


def answer_mode_for_sample(task: str, question: str) -> str:
    task_mode = answer_mode_for_task(task)
    if task_mode:
        return task_mode
    if (task or "").lower() != "crt":
        return ""

    text = question or ""
    normalized_choice_text = re.sub(r"\bmroe\b", "more", text, flags=re.I)
    modes = (
        ("yes_no", ("yes", "no")),
        ("better_worse_equal", ("better", "worse", "equal")),
        ("more_less_equal", ("more", "less", "equal")),
        (
            "increase_decrease_no_change",
            ("increase", "decrease", "no change"),
        ),
        ("better_worse", ("better", "worse")),
    )
    if re.search(r"answer\s+with\s+only\b", normalized_choice_text, flags=re.IGNORECASE):
        for mode, labels in modes:
            if all(re.search(rf"\b{re.escape(label)}\b", normalized_choice_text, flags=re.I) for label in labels):
                return mode
        return ""
    if re.search(
        r"\b(proportion|number|count)\b.*\bcompared\s+to\b",
        text,
        flags=re.I,
    ):
        return "more_less_equal"
    if re.match(
        r"^\s*(?:was|were|did|does|do|is|are|can|could|will|would|has|have)\b",
        text,
        flags=re.I,
    ) and not re.search(
        r"\b(?:answer\s+with\s+only|or)\b",
        text,
        flags=re.I,
    ):
        return "yes_no"
    return ""


def runtime_contract_for_sample(task: str, question: str, df: pd.DataFrame, row):
    """Build a gold-independent contract from task, question, and table shape."""
    source_dataset = str(row.get("source_dataset", "") or "").strip().lower()
    dataset = source_dataset or {
        "scitab": "tabfact",
        "tablefact": "tabfact",
    }.get((task or "").lower(), (task or "").lower())
    hints = infer_dataset_hints(dataset, question, df)
    answer_mode = hints.answer_mode or answer_mode_for_sample(task, question)
    contract = infer_answer_contract(
        question,
        answer_mode,
        allowed_labels=hints.allowed_labels,
        kind_override=hints.kind,
        arity=hints.arity,
        decimal_places=hints.decimal_places,
        reasoning_required=hints.reasoning_required,
        extra_instructions=hints.prompt_instructions,
    )
    return answer_mode, hints, contract


def main(args):
    # 输入：args(argparse.Namespace) 命令行参数集
    # 输出：无
    # 作用：根据参数加载模型与数据集，构建自定义表格问答流水线，并记录输出结果。

    # ---------- 构建统一 LLM 函数（llm_fn: prompt -> str） ----------
    raw_llm_fn = build_llm_fn(args)

    llm_tracker = LLMCallTracker(raw_llm_fn)
    llm_fn = llm_tracker

    # ---------- 组装各个子 Agent ----------
    router = RouterAgent(llm_fn=llm_fn)
    planner = PlannerAgent(llm_fn=llm_fn)
    calculator = Calculator()
    critic = CriticAgent(llm_fn=llm_fn)
    final_answer_agent = FinalAnswerAgent(llm_fn=llm_fn)
    pipeline = TableQAPipeline(
        router=router,
        planner=planner,
        calculator=calculator,
        critic=critic,
        final_answer_agent=final_answer_agent,
        enable_multi_view_validation=args.enable_multiview_validation,
        enable_selective_collaboration=getattr(args, "collaboration_mode", "selective") != "legacy",
        enable_strong_verification=not getattr(args, "disable_strong_verification", False),
        enable_deterministic_shortcuts=not getattr(args, "disable_deterministic_shortcuts", False),
        disable_question_routing=getattr(args, "disable_question_routing", False),
        disable_risk_scoring=getattr(args, "disable_risk_scoring", False),
        disable_table_compression=getattr(args, "disable_table_compression", False),
        mact_avg_tokens=getattr(args, "mact_avg_tokens", 8867.0),
        max_replan=args.max_replan,
    )

    # ---------- 读取数据集（jsonl 或 WikiTableQuestions tsv） ----------
    if args.dataset_path.endswith(".tsv"):
        table_dataset = []
        with open(args.dataset_path, "r", encoding="utf-8") as f:
            lines = [line.rstrip("\n") for line in f if line.strip()]
        # 假设首行为表头，字段至少包含：id, question, table_path, answer
        header = lines[0].split("\t")
        for line in lines[1:]:
            parts = line.split("\t")
            row = {k: v for k, v in zip(header, parts)}
            table_dataset.append(row)
    else:
        with open(args.dataset_path, "r", encoding="utf-8") as f:
            table_dataset = [json.loads(line) for line in f]
    if getattr(args, "limit", 0):
        table_dataset = table_dataset[: args.limit]

    # ---------- 输出文件路径 ----------
    plan_model_name = args.plan_model_name.split("/")[-1].strip()
    output_path = args.output_path or f"{args.task}_{plan_model_name}_myAgent.jsonl"
    if output_path and not args.append_output and os.path.exists(output_path):
        os.remove(output_path)

    # ---------- 遍历样本并运行流水线 ----------
    trial = 0
    for idx, row in enumerate(table_dataset):
        llm_tracker.reset()
        api_metrics_before = (
            raw_llm_fn.snapshot() if hasattr(raw_llm_fn, "snapshot") else None
        )
        sample_started_at = time.perf_counter()
        try:
            question = row.get("question") or row.get("utterance") or row.get("statement", "")
            table_context = table_context_for_row(row)

            if args.dataset_path.endswith(".tsv"):
                # TSV 模式（例如 WikiTableQuestions）：从相对路径读取 CSV 表格
                table_rel_path = row.get("table_path") or row.get("table") or row.get("context") or ""
                base_dir = os.path.dirname(args.dataset_path)
                csv_path = os.path.join(base_dir, table_rel_path)
                if not os.path.exists(csv_path):
                    csv_path = os.path.normpath(os.path.join(base_dir, "..", table_rel_path))

                # 读取完整 df（供复杂路径使用）
                df = pd.read_csv(csv_path)
                table_schema = _build_table_schema(df)

                # 额外抽取“行名/列名”两个字符串，供 Router 的语义评估使用
                row_names_str, col_names_str = load_csv_row_col_names(csv_path)
                table_schema["row_names_str"] = row_names_str
                table_schema["col_names_str"] = col_names_str

            elif args.task == "databench":
                # DataBench：直接从 parquet 构造完整 DataFrame
                _, _, df_path = get_databench_table(args.table_dir, row["dataset"])
                df = pd.read_parquet(df_path, engine="pyarrow")
                table_schema = _build_table_schema(df)
            else:
                # 原 MACT jsonl 模式：table_text 为 list-of-lists
                table = row["table_text"]
                df = build_df_from_table(table)
                table_schema = _build_table_schema(df)
            answer_mode, dataset_hints, answer_contract = runtime_contract_for_sample(
                args.task,
                question,
                df,
                row,
            )
            state = TQASessionState(
                question=question,
                df=df,
                table_schema=table_schema,
                answer_mode=answer_mode,
                table_context=table_context,
                answer_contract=answer_contract,
                dataset_profile=dataset_hints.dataset,
                dataset_instructions=dataset_hints.prompt_instructions,
                missing_markers=dataset_hints.missing_markers,
            )

            state = pipeline.run(state)

            item = dict(row)
            item["route_type"] = state.route_type
            item["difficulty_score"] = state.difficulty_score
            item["difficulty_level"] = state.difficulty_level
            item["routing_context"] = state.routing_context
            item["compression_info"] = state.compression_info
            item["cost_metrics"] = state.cost_metrics
            item["llm_metrics"] = llm_tracker.snapshot()
            item.update(_state_observability(state))
            if api_metrics_before is not None:
                item["api_metrics"] = _metric_delta(
                    api_metrics_before,
                    raw_llm_fn.snapshot(),
                )
            item["elapsed_seconds_total"] = time.perf_counter() - sample_started_at
            item["planner_plan_steps"] = state.plan_steps
            item["planner_code"] = state.code_str
            item["exec_success"] = state.exec_success
            item["exec_error"] = state.exec_error
            item["final_value"] = state.final_value
            item["simple_lookup_success"] = state.simple_lookup_success
            item["simple_lookup_value"] = state.simple_lookup_value
            item["simple_lookup_evidence"] = state.simple_lookup_evidence
            item["critic_verdict"] = state.critic_verdict
            item["critic_feedback"] = state.critic_feedback
            item["multi_view_validation"] = state.multi_view_validation
            item["evidence_critic_verdict"] = state.evidence_critic_verdict
            item["logic_critic_verdict"] = state.logic_critic_verdict
            item["alternative_code"] = state.alternative_code_str
            item["alternative_exec_success"] = state.alternative_exec_success
            item["alternative_exec_error"] = state.alternative_exec_error
            item["alternative_final_value"] = state.alternative_final_value
            item["cross_validation_verdict"] = state.cross_validation_verdict
            item["evidence_summary"] = state.evidence_summary
            item["answer_mode"] = state.answer_mode
            item["classification_raw_output"] = state.classification_raw_output
            item["verification_raw_output"] = state.verification_raw_output
            item["final_answer"] = state.final_answer
            item["gold_answer"] = _gold_answer_from_row(row)
            attach_robust_fields(item, fallback_used=False, retry_count=0)

            _append_jsonl_with_retry(output_path, item)

            trial += 1
            print(f"Finished sample {trial}/{len(table_dataset)}")
        except Exception as exc:
            print(traceback.format_exc())
            api_metrics = None
            if api_metrics_before is not None:
                api_metrics = _metric_delta(
                    api_metrics_before,
                    raw_llm_fn.snapshot(),
                )
            item = _failure_item_for_exception(
                row,
                args,
                exc,
                elapsed_seconds=time.perf_counter() - sample_started_at,
                llm_metrics=llm_tracker.snapshot(),
                api_metrics=api_metrics,
            )
            _append_jsonl_with_retry(output_path, item)
            trial += 1
            print(
                f"Failed sample {trial}/{len(table_dataset)}: "
                f"{row.get('id', idx)} ({exc.__class__.__name__})"
            )
            continue


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--plan_model_name",
        default="",
        help="name of the planning model.",
    )
    parser.add_argument(
        "--code_model_name",
        default="",
        help="(unused in myAgent) name of the coding model.",
    )
    parser.add_argument(
        "--cache_dir",
        default="",
        help="cache dir to load a model from.",
    )
    parser.add_argument(
        "--model_path",
        type=str,
        default="",
        help="model path to the planning model.",
    )
    parser.add_argument(
        "--dataset_path",
        type=str,
        default="../datasets/wtq.jsonl",
        help="dataset path.",
    )
    parser.add_argument(
        "--output_path",
        type=str,
        default="",
        help="output jsonl path; defaults to a task/model-derived file name.",
    )
    parser.add_argument(
        "--table_dir",
        type=str,
        default="../datasets/databench/data",
        help="databench table directory",
    )
    parser.add_argument(
        "--max_step",
        type=int,
        default=6,
        help="(unused in myAgent) maximum number for valid iterations.",
    )
    parser.add_argument(
        "--max_actual_step",
        type=int,
        default=6,
        help="(unused in myAgent) maximum number for all iterations.",
    )
    parser.add_argument(
        "--task",
        type=str,
        default="wtq",
        choices=["wtq", "crt", "tat", "scitab", "tabfact", "databench"],
    )
    parser.add_argument(
        "--as_reward",
        type=str,
        default="consistency",
        choices=["consistency", "llm", "logp", "rollout", "combined"],
        help="(unused in myAgent) kept for CLI compatibility.",
    )
    parser.add_argument(
        "--long_table_op",
        type=str,
        default="ignore",
        choices=["code-agent", "ignore", "short-table"],
        help="(unused in myAgent) kept for CLI compatibility.",
    )
    parser.add_argument(
        "--plan_sample",
        type=int,
        default=5,
        help="(unused in myAgent) kept for CLI compatibility.",
    )
    parser.add_argument(
        "--code_sample",
        type=int,
        default=5,
        help="(unused in myAgent) kept for CLI compatibility.",
    )
    parser.add_argument(
        "--use_pre_answer",
        type=bool,
        default=True,
        help="(unused in myAgent) kept for CLI compatibility.",
    )
    parser.add_argument(
        "--answer_aggregate",
        type=float,
        default=1.0,
        help="(unused in myAgent) kept for CLI compatibility.",
    )
    parser.add_argument(
        "--direct_reasoning",
        action="store_true",
        help="(unused in myAgent) kept for CLI compatibility.",
    )
    parser.add_argument(
        "--without_tool",
        action="store_true",
        help="(unused in myAgent) kept for CLI compatibility.",
    )
    parser.add_argument(
        "--code_endpoint",
        default="11039",
        help="(unused in myAgent) kept for CLI compatibility.",
    )
    parser.add_argument(
        "--debugging",
        action="store_true",
        help="(unused in myAgent) kept for CLI compatibility.",
    )
    parser.add_argument(
        "--code_as_observation",
        action="store_true",
        help="(unused in myAgent) kept for CLI compatibility.",
    )
    parser.add_argument(
        "--append_output",
        action="store_true",
        help="Append to output jsonl instead of replacing it.",
    )
    parser.add_argument(
        "--enable_multiview_validation",
        action="store_true",
        help="Enable Evidence/Logic critics and alternative-path cross validation for COMPLEX samples.",
    )
    parser.add_argument(
        "--collaboration_mode",
        choices=["legacy", "selective", "calibration"],
        default="selective",
        help="Select legacy myAgent or risk-driven selective collaboration.",
    )
    parser.add_argument(
        "--disable_strong_verification",
        action="store_true",
        help="Disable high-risk LLM strong verification for ablation experiments.",
    )
    parser.add_argument(
        "--disable_deterministic_shortcuts",
        action="store_true",
        help="Disable deterministic semantic verifier shortcuts for ablation experiments.",
    )
    parser.add_argument(
        "--disable_question_routing",
        action="store_true",
        help="Disable question-type routing and force the complex path for ablation experiments.",
    )
    parser.add_argument(
        "--disable_risk_scoring",
        action="store_true",
        help="Disable selective risk scoring and hold risk level at medium for ablation experiments.",
    )
    parser.add_argument(
        "--disable_table_compression",
        action="store_true",
        help="Disable question-aware table compression and expose the full table for ablation experiments.",
    )
    parser.add_argument(
        "--mact_avg_tokens",
        type=float,
        default=8867.0,
        help="MACT average token baseline for selective budget control.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Run only the first N records after loading; 0 means all records.",
    )
    parser.add_argument(
        "--max_replan",
        type=int,
        default=3,
        help="Maximum planner attempts after contract or grounding feedback.",
    )
    add_model_backend_args(parser)
    args = parser.parse_args()
    main(args)
