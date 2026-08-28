"""Stage 3: LLM 结构化事件抽取。

这个阶段是本项目第一个真正使用 LLM 的 Harness stage。

它的职责不是做最终分析，而是把 Stage 2 的 cleaned_items 转换成稳定的
structured_events，让后续分析阶段可以围绕统一 schema 工作。这里没有 ReAct
循环，而是受约束的“批量输入 -> LLM JSON 输出 -> 本地校验 -> 失败兜底”流程。

执行步骤：

1. 选择输入数据：
   从 state.global_cleaned_items 中选择 should_analyze_global=True 的数据；
   从 state.ai_cleaned_items 中选择 should_analyze_ai=True 的数据。

2. 构造最小上下文：
   只保留结构化所需字段，例如 id、source_id、title、url、published_at、
   summary、domain、topic_tags、quality_score、raw_ref，避免把完整 raw_content
   全量塞给模型。

3. 调用 LLM：
   如果配置了 DEEPSEEK_API_KEY，就通过 OpenAI-compatible Chat Completions
   调用 DeepSeek，让模型输出 `{ "events": [...] }`。

4. 解析与校验：
   先用 after_llm_call.parse_json_output 解析 JSON，再用本文件内的
   validate_events 做 schema linter，检查必填字段、raw_ref、url、
   published_at、industry_area、hotness_score 等硬约束。

5. 修复或兜底：
   如果 LLM 输出不合格，最多 repair 一次；如果 LLM 超时、不可用或 repair 后
   仍不合格，则使用规则 fallback 生成 structured_events，并把原因写入 warning
   和 llm_runs，保证系统可继续运行且问题可追踪。

主要函数对应关系：

- structure_events(): Stage 3 入口，负责读取 State、分 global/ai 两路处理、
  写入 state.global_structured_events / state.ai_structured_events，并保存产物。
- _structure_scope(): 单个 scope 的调度函数，决定使用 LLM、报错 fallback，
  还是无 API key fallback。
- _call_and_validate_scope(): 真正调用 LLM、保存原始响应、执行 schema linter，
  以及在校验失败时触发 repair。
- _build_prompt_items(): 从 cleaned item 中裁剪出 LLM 需要的最小字段。
- _build_structure_prompt(): 构造结构化任务 prompt，并注入 skill 文档和输出约束。
- _parse_events_payload(): 把 LLM 文本解析成 events list。
- validate_events(): Stage 3 的本地 schema linter，限制 LLM 不得编造或破坏字段。
- _fallback_events() / _fallback_event(): LLM 不可靠时的确定性兜底生成逻辑。
- _write_llm_artifact(): 保存 LLM prompt、响应、repair 响应等可审计中间产物。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from insight_engine.harness.artifacts import ensure_run_dir, write_json_artifact
from insight_engine.harness.hooks.after_llm_call import parse_json_output
from insight_engine.harness.llm_client import LLMClientError, OpenAICompatibleChatClient
from insight_engine.harness.prompt_builder import build_retry_feedback
from insight_engine.harness.state import (
    STRUCTURED_EVENT_AI_AREAS,
    STRUCTURED_EVENT_FIELD_SPEC,
    STRUCTURED_EVENT_GLOBAL_AREAS,
    InsightEngineState,
)


STRUCTURED_EVENT_REQUIRED_FIELDS = [
    key for key, spec in STRUCTURED_EVENT_FIELD_SPEC.items() if spec.get("required")
]
AI_AREAS = STRUCTURED_EVENT_AI_AREAS
GLOBAL_AREAS = STRUCTURED_EVENT_GLOBAL_AREAS


def structure_events(state: InsightEngineState) -> InsightEngineState:
    """执行 LLM 结构化事件抽取阶段。"""
    require_llm = os.getenv("STRUCTURE_EVENTS_REQUIRE_LLM", "").lower() in {"1", "true", "yes"}
    use_llm = require_llm or os.getenv("STRUCTURE_EVENTS_USE_LLM", "").lower() in {"1", "true", "yes"}
    client = OpenAICompatibleChatClient.from_deepseek_env() if use_llm else None

    global_items = [
        item for item in state.global_cleaned_items if item.get("should_analyze_global")
    ]
    ai_items = [item for item in state.ai_cleaned_items if item.get("should_analyze_ai")]

    llm_runs: list[dict[str, Any]] = []
    structured_errors: list[str] = []

    def _run_scope(scope: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """执行单个 scope 的结构化抽取，失败时先走 fallback 而非直接崩溃。"""
        try:
            return _structure_scope(
                state=state, scope=scope, items=items,
                client=client, use_llm=use_llm, require_llm=require_llm,
                llm_runs=llm_runs,
            )
        except RuntimeError as exc:
            # require_llm 模式下 LLM 失败：记录错误后自动降级到 fallback
            message = f"{scope} scope LLM 调用失败，已自动降级为规则 fallback：{exc}"
            state.add_warning("structure_events", message)
            structured_errors.append(message)
            llm_runs.append({
                "scope": scope, "status": "require_llm_fallback",
                "error": repr(exc),
            })
            return _fallback_events(scope=scope, items=items)
        except Exception as exc:
            # 其他异常：同样降级
            message = f"{scope} scope 结构化异常，已降级为规则 fallback：{exc!r}"
            state.add_warning("structure_events", message)
            structured_errors.append(message)
            llm_runs.append({
                "scope": scope, "status": "unexpected_error_fallback",
                "error": repr(exc),
            })
            return _fallback_events(scope=scope, items=items)

    global_events = _run_scope("global", global_items)
    ai_events = _run_scope("ai", ai_items)

    state.global_structured_events = global_events
    state.ai_structured_events = ai_events
    all_events = global_events + ai_events

    output_path = write_json_artifact(
        state=state,
        artifact_name="structured_events",
        data={
            "run_id": state.run_id,
            "target_date": state.target_date,
            "input_counts": {
                "global_cleaned_items": len(global_items),
                "ai_cleaned_items": len(ai_items),
                "total_cleaned_items": len(global_items) + len(ai_items),
            },
            "output_counts": {
                "global_structured_events": len(global_events),
                "ai_structured_events": len(ai_events),
                "total_structured_events": len(all_events),
            },
            "llm_enabled": client is not None,
            "llm_runs": llm_runs,
            "global_events": global_events,
            "ai_events": ai_events,
            "items": all_events,
        },
        base_dir="data/processed",
        filename="structured_events.json",
    )
    state.add_artifact("global_structured_events", str(output_path))
    state.add_artifact("ai_structured_events", str(output_path))

    return state


def _structure_scope(
    state: InsightEngineState,
    scope: str,
    items: list[dict[str, Any]],
    client: OpenAICompatibleChatClient | None,
    use_llm: bool,
    require_llm: bool,
    llm_runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """对单个 scope 的 cleaned items 做结构化抽取，无 LLM 时自动 fallback。"""
    if not items:
        return []

    prompt_items = _build_prompt_items(items)
    prompt_text = _build_structure_prompt(scope=scope, items=prompt_items, state=state)

    if client is None and not use_llm:
        llm_runs.append({"scope": scope, "status": "fallback_llm_disabled"})
        return _fallback_events(scope=scope, items=items)

    if client is None:
        message = "未配置 DEEPSEEK_API_KEY，structure_events 使用规则 fallback"
        if require_llm:
            raise RuntimeError(message)
        state.add_warning("structure_events", message)
        llm_runs.append({"scope": scope, "status": "fallback_no_api_key"})
        return _fallback_events(scope=scope, items=items)

    try:
        return _call_and_validate_scope(
            state=state,
            scope=scope,
            items=items,
            prompt_text=prompt_text,
            client=client,
            llm_runs=llm_runs,
        )
    except Exception as exc:  # noqa: BLE001
        message = f"LLM 结构化失败，structure_events 使用规则 fallback：{exc!r}"
        if require_llm:
            raise RuntimeError(message) from exc
        state.add_warning("structure_events", message)
        llm_runs.append({"scope": scope, "status": "fallback_after_error", "error": repr(exc)})
        return _fallback_events(scope=scope, items=items)


def _call_and_validate_scope(
    state: InsightEngineState,
    scope: str,
    items: list[dict[str, Any]],
    prompt_text: str,
    client: OpenAICompatibleChatClient,
    llm_runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """调用 LLM 做结构化，linter 不过时 repair 一次，仍不过则抛异常。"""
    messages = [
        {
            "role": "system",
            "content": (
                "你是新闻洞察结构化引擎。你只能根据用户提供的 cleaned_items 输出 JSON，"
                "不得编造 URL、来源、发布时间或不存在的事实。"
            ),
        },
        {"role": "user", "content": prompt_text},
    ]
    response = client.chat_json(messages=messages)
    response_path = _write_llm_artifact(
        state=state,
        scope=scope,
        name="initial_response",
        data={
            "model": response.model,
            "prompt": prompt_text,
            "content": response.content,
            "raw_response": response.raw_response,
        },
    )

    events = _parse_events_payload(response.content)
    validation_errors = validate_events(events=events, source_items=items, scope=scope)
    if not validation_errors:
        llm_runs.append(
            {
                "scope": scope,
                "status": "ok",
                "model": response.model,
                "response_artifact": str(response_path),
                "event_count": len(events),
            }
        )
        return _normalize_event_ids(scope=scope, events=events)

    repair_prompt = _build_repair_prompt(
        scope=scope,
        original_prompt=prompt_text,
        invalid_output=response.content,
        validation_errors=validation_errors,
    )
    repair_response = client.chat_json(
        messages=[
            {
                "role": "system",
                "content": "你是 JSON 修复器，只能修复结构和字段，不得添加没有证据的新事实。",
            },
            {"role": "user", "content": repair_prompt},
        ]
    )
    repair_path = _write_llm_artifact(
        state=state,
        scope=scope,
        name="repair_response",
        data={
            "model": repair_response.model,
            "prompt": repair_prompt,
            "content": repair_response.content,
            "raw_response": repair_response.raw_response,
            "initial_validation_errors": validation_errors,
        },
    )
    repaired_events = _parse_events_payload(repair_response.content)
    repair_errors = validate_events(events=repaired_events, source_items=items, scope=scope)
    if repair_errors:
        raise ValueError(f"LLM repair 后仍未通过 schema linter：{repair_errors}")

    llm_runs.append(
        {
            "scope": scope,
            "status": "ok_after_repair",
            "model": repair_response.model,
            "response_artifact": str(response_path),
            "repair_artifact": str(repair_path),
            "event_count": len(repaired_events),
            "initial_validation_errors": validation_errors,
        }
    )
    return _normalize_event_ids(scope=scope, events=repaired_events)


def _build_prompt_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从 cleaned items 中提取 LLM 需要的字段，截断摘要并压缩元数据。"""
    return [
        {
            "id": item.get("id"),
            "source_scope": item.get("source_scope"),
            "source_id": item.get("source_id"),
            "source_type": item.get("source_type"),
            "title": item.get("title"),
            "url": item.get("url"),
            "published_at": item.get("published_at"),
            "summary": _truncate(str(item.get("summary", "")), 700),
            "domain": item.get("domain"),
            "topic_tags": item.get("topic_tags", []),
            "ai_match_keywords": item.get("ai_match_keywords", []),
            "quality_score": item.get("quality_score"),
            "metadata": _compact_metadata(item.get("metadata", {})),
            "raw_ref": item.get("raw_ref"),
        }
        for item in items[: int(os.getenv("STRUCTURE_EVENTS_MAX_ITEMS_PER_SCOPE", "20"))]
    ]


def _build_structure_prompt(
    scope: str,
    items: list[dict[str, Any]],
    state: InsightEngineState | None = None,
) -> str:
    """构造结构化抽取的完整 prompt，包含约束、重试反馈、输出示例和输入数据。"""
    allowed_areas = sorted(AI_AREAS if scope == "ai" else GLOBAL_AREAS)

    # 重试反馈：告诉 LLM 上一次为什么被 linter 拦截
    retry_feedback = ""
    if state is not None:
        retry_feedback = build_retry_feedback(state, "structure_events")

    return "\n".join(
        [
            f"# 任务：将 {scope} cleaned_items 结构化为事件 JSON",
            "",
            "你会收到一组 cleaned_items。请把每条值得分析的 item 转成一个 structured_event。",
            "当前版本不要求合并多条新闻；默认一条 item 对应一条 event。",
            "",
            retry_feedback,
            "## 约束",
            "- 只能输出 JSON object，不要输出 Markdown。",
            "- 顶层必须是 `{ \"events\": [...] }`。",
            "- 不得编造 URL、source_name、published_at。",
            "- `raw_ref` 必须等于输入 item 的 `id`。",
            "- `evidence.source_url` 必须等于输入 item 的 `url`。",
            "- `hotness_score` 必须是 0 到 100 的整数。",
            "- `importance_level` 只能是 `high`、`medium`、`low`。",
            f"- `industry_area` 只能从这些值中选择：{', '.join(allowed_areas)}。",
            "",
            "## 每个 event 必须包含字段",
            json.dumps(STRUCTURED_EVENT_REQUIRED_FIELDS, ensure_ascii=False),
            "",
            "## 输出示例",
            "```json",
            json.dumps(
                {
                    "events": [
                        {
                            "id": f"event_{scope}_1",
                            "source_scope": scope,
                            "title": "事件标题",
                            "source_name": "source_id",
                            "source_type": "media",
                            "url": "https://example.com",
                            "published_at": "2026-05-18T00:00:00+00:00",
                            "industry_area": "ai" if scope == "global" else "foundation_model",
                            "topic_tags": ["tag1", "tag2"],
                            "hotness_score": 70,
                            "importance_level": "high",
                            "summary": "一句话总结事实。",
                            "key_entities": ["实体1"],
                            "impact_analysis": "基于证据写出的影响分析。",
                            "risk_or_opportunity": "风险或机会判断。",
                            "evidence": {
                                "source_title": "原始标题",
                                "source_url": "https://example.com",
                                "supporting_text": "原始摘要中的证据句",
                            },
                            "raw_ref": f"clean_{scope}_1",
                        }
                    ]
                },
                ensure_ascii=False,
                indent=2,
            ),
            "```",
            "",
            "## 输入 cleaned_items",
            "```json",
            json.dumps(items, ensure_ascii=False, indent=2),
            "```",
        ]
    )


def _build_repair_prompt(
    scope: str,
    original_prompt: str,
    invalid_output: str,
    validation_errors: list[str],
) -> str:
    """构造修复 prompt，把原始任务、校验错误和无效输出一起发给 LLM。"""
    return "\n".join(
        [
            f"# 修复 {scope} structured_events JSON",
            "",
            "下面的 LLM 输出没有通过本地 schema linter。请只修复 JSON 结构和字段。",
            "不得添加输入中不存在的新事实。",
            "",
            "## 校验错误",
            json.dumps(validation_errors, ensure_ascii=False, indent=2),
            "",
            "## 原始任务",
            original_prompt,
            "",
            "## 无效输出",
            "```json",
            invalid_output,
            "```",
        ]
    )


def _parse_events_payload(text: str) -> list[dict[str, Any]]:
    """把 LLM 文本解析为事件列表，校验顶层结构必须包含 events list。"""
    payload = parse_json_output(text)
    if not isinstance(payload, dict):
        raise ValueError("LLM 输出顶层必须是 JSON object")
    events = payload.get("events")
    if not isinstance(events, list):
        raise ValueError("LLM 输出必须包含 list 字段 events")
    return [event for event in events if isinstance(event, dict)]


def validate_events(
    events: list[dict[str, Any]],
    source_items: list[dict[str, Any]],
    scope: str,
) -> list[str]:
    """本地 schema linter。"""
    errors: list[str] = []
    source_by_id = {str(item.get("id")): item for item in source_items}
    allowed_areas = AI_AREAS if scope == "ai" else GLOBAL_AREAS

    if not events:
        errors.append("events 不能为空")
        return errors

    for index, event in enumerate(events):
        prefix = f"events[{index}]"
        missing = [field for field in STRUCTURED_EVENT_REQUIRED_FIELDS if field not in event]
        if missing:
            errors.append(f"{prefix} 缺少字段：{missing}")
            continue

        raw_ref = str(event.get("raw_ref", ""))
        source_item = source_by_id.get(raw_ref)
        if source_item is None:
            errors.append(f"{prefix}.raw_ref 不存在于输入 items：{raw_ref}")
            continue

        if str(event.get("source_scope")) != scope:
            errors.append(f"{prefix}.source_scope 必须是 {scope}")
        if str(event.get("source_name")) != str(source_item.get("source_id")):
            errors.append(f"{prefix}.source_name 必须等于输入 source_id")
        if str(event.get("url")) != str(source_item.get("url")):
            errors.append(f"{prefix}.url 必须等于输入 url")
        if str(event.get("published_at")) != str(source_item.get("published_at")):
            errors.append(f"{prefix}.published_at 必须等于输入 published_at")
        if str(event.get("industry_area")) not in allowed_areas:
            errors.append(f"{prefix}.industry_area 不在允许范围")
        if event.get("importance_level") not in {"high", "medium", "low"}:
            errors.append(f"{prefix}.importance_level 不合法")
        if not isinstance(event.get("topic_tags"), list):
            errors.append(f"{prefix}.topic_tags 必须是 list")
        if not isinstance(event.get("key_entities"), list):
            errors.append(f"{prefix}.key_entities 必须是 list")

        try:
            score = int(event.get("hotness_score"))
        except (TypeError, ValueError):
            errors.append(f"{prefix}.hotness_score 必须是整数")
        else:
            if score < 0 or score > 100:
                errors.append(f"{prefix}.hotness_score 必须在 0-100")

        evidence = event.get("evidence")
        if not isinstance(evidence, dict):
            errors.append(f"{prefix}.evidence 必须是 object")
            continue
        if evidence.get("source_url") != source_item.get("url"):
            errors.append(f"{prefix}.evidence.source_url 必须等于输入 url")

    return errors


def _fallback_events(scope: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """LLM 不可用时用规则生成结构化事件，保证系统仍可运行。"""
    return [
        _fallback_event(scope=scope, item=item, index=index)
        for index, item in enumerate(items)
    ]


def _fallback_event(scope: str, item: dict[str, Any], index: int) -> dict[str, Any]:
    """用规则为单条 cleaned item 生成结构化事件。"""
    title = str(item.get("title", ""))
    summary = str(item.get("summary") or item.get("clean_text") or title)
    industry_area = _fallback_area(scope=scope, item=item)
    hotness_score = _fallback_hotness(item=item, scope=scope)
    return {
        "id": f"event_{scope}_{index + 1}",
        "source_scope": scope,
        "title": title,
        "source_name": str(item.get("source_id", "")),
        "source_type": str(item.get("source_type", "")),
        "url": str(item.get("url", "")),
        "published_at": str(item.get("published_at", "")),
        "industry_area": industry_area,
        "topic_tags": list(item.get("topic_tags") or [industry_area])[:8],
        "hotness_score": hotness_score,
        "importance_level": _importance_level(hotness_score),
        "summary": summary[:500],
        "key_entities": _extract_entities(f"{title} {summary}"),
        "impact_analysis": _fallback_impact(industry_area=industry_area, scope=scope),
        "risk_or_opportunity": _fallback_risk_or_opportunity(industry_area),
        "evidence": {
            "source_title": title,
            "source_url": str(item.get("url", "")),
            "supporting_text": summary[:500],
        },
        "raw_ref": str(item.get("id", f"clean_{scope}_{index + 1}")),
    }


def _normalize_event_ids(scope: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """重新编号事件 ID 并规范化 hotness_score 为整数。"""
    normalized: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        updated = dict(event)
        updated["id"] = f"event_{scope}_{index + 1}"
        updated["hotness_score"] = int(updated.get("hotness_score") or 0)
        normalized.append(updated)
    return normalized


def _write_llm_artifact(
    state: InsightEngineState,
    scope: str,
    name: str,
    data: dict[str, Any],
) -> Path:
    """保存 LLM 调用产物到 data/llm/{run_id}/structure_events/ 并记录到 State。"""
    output_dir = ensure_run_dir("data/llm", state.run_id) / "structure_events"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{scope}_{name}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    state.add_artifact(f"llm:structure_events:{scope}:{name}", str(path))
    return path


def _fallback_area(scope: str, item: dict[str, Any]) -> str:
    """规则判断 industry_area：global 用 domain 值，AI 用关键词匹配。"""
    domain = str(item.get("domain") or "other")
    tags = [str(tag) for tag in item.get("topic_tags") or []]
    text = " ".join([domain, *tags, str(item.get("title", "")), str(item.get("summary", ""))]).lower()

    if scope == "global":
        return domain if domain in GLOBAL_AREAS else "other"
    if any(keyword in text for keyword in ["openai", "anthropic", "claude", "gemini", "llm", "model"]):
        return "foundation_model"
    if any(keyword in text for keyword in ["gpu", "chip", "nvidia", "inference", "data center"]):
        return "ai_infra"
    if any(keyword in text for keyword in ["regulation", "safety", "copyright", "lawsuit", "trial"]):
        return "policy"
    if any(keyword in text for keyword in ["funding", "raises", "valuation", "startup"]):
        return "investment"
    if any(keyword in text for keyword in ["arxiv", "research", "paper", "benchmark"]):
        return "research"
    if any(keyword in text for keyword in ["agent", "assistant", "app", "product"]):
        return "ai_app"
    return "other"


def _fallback_hotness(item: dict[str, Any], scope: str) -> int:
    """规则计算热度分：基础分 + 质量分 + scope/source_type/social 加权。"""
    score = 30
    score += int(float(item.get("quality_score") or 0) * 30)
    if scope == "ai" and item.get("is_ai_related"):
        score += 20
    if item.get("source_type") == "aggregator":
        score += 10
    if item.get("source_type") == "media":
        score += 8
    if item.get("source_type") == "social":
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        score += min(int(metadata.get("points") or 0), 20)
        score += min(int(metadata.get("num_comments") or 0), 20)
    return max(0, min(score, 100))


def _importance_level(score: int) -> str:
    """按热度分阈值映射为 high/medium/low。"""
    if score >= 70:
        return "high"
    if score >= 45:
        return "medium"
    return "low"


def _fallback_impact(industry_area: str, scope: str) -> str:
    """按行业方向生成模板化影响分析文本。"""
    if scope == "global":
        return f"该事件反映 {industry_area} 方向的公共关注度变化，可作为今日全球舆情背景信号。"
    if industry_area == "foundation_model":
        return "该事件可能影响基础模型竞争格局、产品路线或主流 AI 厂商的能力边界。"
    if industry_area == "ai_infra":
        return "该事件可能影响 AI 算力供给、推理成本或基础设施部署效率。"
    if industry_area == "policy":
        return "该事件可能影响 AI 产品合规、安全治理或企业部署边界。"
    if industry_area == "investment":
        return "该事件可能反映资本对 AI 商业化方向的阶段性偏好。"
    return "该事件与 AI 领域相关，后续需要结合更多上下文判断影响。"


def _fallback_risk_or_opportunity(industry_area: str) -> str:
    """按行业方向生成模板化风险或机会判断文本。"""
    if industry_area == "policy":
        return "潜在风险：监管和合规要求可能提高产品落地成本。"
    if industry_area == "ai_infra":
        return "潜在机会：基础设施优化可能降低模型训练或推理成本。"
    if industry_area == "investment":
        return "潜在机会：资本关注可能推动相关方向加速商业化。"
    if industry_area == "foundation_model":
        return "潜在机会：模型能力或产品策略变化可能带来新的应用窗口。"
    return "暂无明确风险或机会，需要在分析阶段进一步判断。"


def _extract_entities(text: str) -> list[str]:
    """用预定义名单从文本中匹配关键实体名称，返回去重排序后的列表。"""
    patterns = [
        "OpenAI",
        "Anthropic",
        "Google",
        "DeepMind",
        "Meta",
        "Microsoft",
        "Amazon",
        "NVIDIA",
        "AMD",
        "Apple",
        "Tesla",
        "GitHub",
        "arXiv",
        "Hacker News",
        "DeepSeek",
        "Qwen",
    ]
    entities = [
        name
        for name in patterns
        if re.search(rf"\b{re.escape(name)}\b", text, flags=re.IGNORECASE)
    ]
    return sorted(set(entities))[:8]


def _compact_metadata(metadata: Any) -> dict[str, Any]:
    """只保留 metadata 中允许的键，减少 prompt 体积。"""
    if not isinstance(metadata, dict):
        return {}
    allowed_keys = {"points", "num_comments", "repo", "tag_name", "feed_url", "comments_url"}
    return {key: value for key, value in metadata.items() if key in allowed_keys}


def _truncate(text: str, limit: int) -> str:
    """按字符数截断文本，超出限制时末尾加 ..."""
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."
