"""基于 myAgent 流水线的 WikiTableQuestions/TSV 简化入口。

用法示例：
python run_wtq_myagent.py ^
  --plan_model_name your-model-name ^
  --model_path your-model-path ^
  --dataset_path "../dataset/WikiTableQuestions-master/WikiTableQuestions-master/data/myAgentdataset.tsv"

TSV 文件最小字段要求：
- 第一行是表头，至少包含：id, question, table_path, answer
- table_path 为相对于 TSV 文件所在目录的 CSV 路径
"""

import argparse
import json
import os
import time
import traceback

import pandas as pd

from model_backends import add_model_backend_args, build_llm_fn
from my_agents import (
    RouterAgent,
    PlannerAgent,
    Calculator,
    CriticAgent,
    FinalAnswerAgent,
    LLMCallTracker,
    TableQAPipeline,
    TQASessionState,
    _build_table_schema,
    load_csv_row_col_names,
)


def load_tsv_dataset(tsv_path: str):
    """读取 WikiTableQuestions 风格的 TSV 数据。"""
    rows = []
    with open(tsv_path, "r", encoding="utf-8") as f:
        lines = [line.rstrip("\n") for line in f if line.strip()]
    if not lines:
        return rows
    header = lines[0].split("\t")
    for line in lines[1:]:
        parts = line.split("\t")
        row = {k: v for k, v in zip(header, parts)}
        rows.append(row)
    return rows


def _json_default(value):
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"Object of type {value.__class__.__name__} is not JSON serializable")


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
    if hasattr(value, "item"):
        return value.item()
    return value


def main(args):
    raw_llm_fn = build_llm_fn(args)
    llm_tracker = LLMCallTracker(raw_llm_fn)
    llm_fn = llm_tracker

    # 组装 myAgent 各个子模块
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
        mact_avg_tokens=getattr(args, "mact_avg_tokens", 8867.0),
        max_replan=2,
    )

    # 读取 TSV 数据集
    dataset = load_tsv_dataset(args.dataset_path)
    if getattr(args, "limit", 0):
        dataset = dataset[: args.limit]

    plan_model_name = (args.plan_model_name or "").split("/")[-1].strip()
    output_path = args.output_path or f"wtq_{plan_model_name}_myAgent.jsonl"
    if output_path and not args.append_output and os.path.exists(output_path):
        os.remove(output_path)

    base_dir = os.path.dirname(args.dataset_path)

    trial = 0
    for idx, row in enumerate(dataset):
        try:
            llm_tracker.reset()
            sample_started_at = time.perf_counter()

            question = row.get("question") or row.get("utterance") or row.get("statement") or ""
            table_rel_path = row.get("table_path") or row.get("table") or row.get("context") or ""
            csv_path = os.path.join(base_dir, table_rel_path)
            if not os.path.exists(csv_path):
                csv_path = os.path.normpath(os.path.join(base_dir, "..", table_rel_path))

            # 读取完整 df（复杂路径用）
            df = pd.read_csv(csv_path)

            # 从 CSV 推出“行名/列名”字符串，供 Router 的 LLM 判断难度 & 单元格数量
            row_names_str, col_names_str = load_csv_row_col_names(csv_path)
            # 这里只保留最核心的结构信息：列名 + 行/列名称字符串
            table_schema = _build_table_schema(df)
            table_schema["row_names_str"] = row_names_str
            table_schema["col_names_str"] = col_names_str

            state = TQASessionState(question=question, df=df, table_schema=table_schema)

            if args.router_only:
                # 仅测试 Router：只跑路由，不进入 Planner/Calculator/Critic/FinalAnswer
                state = router.route(state)

                ctx = state.routing_context or {}
                print(
                    f"[RouterTest] id={row.get('id', idx)} "
                    f"route_type={state.route_type} "
                    f"sem_score={ctx.get('sem_score')} "
                    f"cell_score={ctx.get('cell_score')} "
                    f"total_score={ctx.get('total_score')} "
                    f"difficulty_level={state.difficulty_level}"
                )
            else:
                # 正常完整流水线
                state = pipeline.run(state)

            item = dict(row)
            item["route_type"] = state.route_type
            item["difficulty_score"] = state.difficulty_score
            item["difficulty_level"] = state.difficulty_level
            item["routing_context"] = state.routing_context
            item["compression_info"] = state.compression_info
            item["cost_metrics"] = state.cost_metrics
            item["llm_metrics"] = llm_tracker.snapshot()
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
            item["risk_level"] = state.risk_level
            item["risk_assessment"] = _to_serializable(state.risk_assessment)
            item["post_risk_assessment"] = _to_serializable(state.post_risk_assessment)
            item["evidence_pack"] = _to_serializable(state.evidence_pack)
            item["candidate_answers"] = _to_serializable(state.candidate_answers)
            item["agreement_decision"] = _to_serializable(state.agreement_decision)
            item["budget_state"] = _to_serializable(state.budget_state)
            item["final_answer"] = state.final_answer
            item["gold_answer"] = row.get("answer") or row.get("targetValue") or row.get("target_value") or ""

            with open(output_path, "a", encoding="utf-8") as fout:
                fout.write(json.dumps(item, ensure_ascii=False, default=_json_default) + "\n")

            trial += 1
            print(f"Finished sample {trial}/{len(dataset)}")
        except Exception:  # noqa: BLE001
            print(traceback.format_exc())
            break


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--plan_model_name",
        default="",
        help="name of the planning model.",
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
        default="../dataset/WikiTableQuestions-master/WikiTableQuestions-master/data/myAgentdataset.tsv",
        help="path to the WTQ-style tsv dataset.",
    )
    parser.add_argument(
        "--output_path",
        type=str,
        default="",
        help="output jsonl file path (optional).",
    )
    parser.add_argument(
        "--router_only",
        action="store_true",
        help="仅测试 Router：只跑路由并输出路由相关信息，不执行 Planner/Calculator/Critic/FinalAnswer。",
    )
    parser.add_argument(
        "--append_output",
        action="store_true",
        help="Append to output_path instead of replacing it.",
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
    add_model_backend_args(parser)
    args = parser.parse_args()
    main(args)
