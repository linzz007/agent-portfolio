"""Model gateway contracts and the production Anthropic-compatible adapter."""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol
from urllib import error, request

from policy_impact.harness.model_config import effective_model_config
from policy_impact.harness.state import utc_now_iso

_SUBAGENT_SCHEMA_OUTPUT_KEYS = {
    "evidence_collection.v1": ("evidence",),
    "claim_set.v1": ("claims",),
    "counterexample_set.v1": ("counterexamples", "notes"),
    "claim_verdict_set.v1": ("verdicts",),
    "published_answer.v1": ("answer", "verified_claim_refs"),
}
_ACTION_TYPES = {"final", "tool_call", "delegate", "request_approval"}


@dataclass
class ModelResponse:
    response_id: str
    payload: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelAdapter(Protocol):
    def complete(self, messages: list[dict[str, Any]], schema_name: str) -> ModelResponse:
        ...


class ScriptedModelAdapter:
    """Explicit test double. Production code must not instantiate this adapter."""

    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self._payloads = list(payloads)
        self._index = 0

    def complete(self, messages: list[dict[str, Any]], schema_name: str) -> ModelResponse:
        self._index += 1
        if self._index <= len(self._payloads):
            payload = self._payloads[self._index - 1]
        else:
            payload = {"type": "final", "output": {}}
        return ModelResponse(response_id=f"scripted_{self._index}", payload=payload)


class ModelInvocationError(RuntimeError):
    """A sanitized model error suitable for API responses and trace records."""


class AnthropicCompatibleModelAdapter:
    """Call an Anthropic-compatible ``/v1/messages`` endpoint with JSON output."""

    def __init__(
        self,
        *,
        model_id: str,
        base_url: str,
        api_key: str,
        max_tokens: int = 2048,
        temperature: float = 0.0,
        timeout_seconds: float = 45.0,
        max_attempts: int = 2,
    ) -> None:
        if not api_key:
            raise ValueError("当前没有可用模型，无法执行。缺少模型鉴权信息。")
        self.model_id = model_id
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max(1, max_attempts)

    @classmethod
    def from_env(cls) -> "AnthropicCompatibleModelAdapter":
        config = effective_model_config()
        return cls(
            model_id=config["model_id"],
            base_url=config["base_url"],
            api_key=config["api_key"],
            max_tokens=config["max_tokens"],
            temperature=config["temperature"],
            timeout_seconds=config["timeout_seconds"],
            max_attempts=config["max_attempts"],
        )

    def complete(self, messages: list[dict[str, Any]], schema_name: str) -> ModelResponse:
        system, anthropic_messages = self._split_messages(messages)
        original_messages = [dict(item) for item in anthropic_messages]
        last_error = ""
        retry_errors: list[str] = []
        attempts_used = 0
        total_started = time.perf_counter()

        for attempt in range(1, self.max_attempts + 1):
            attempts_used = attempt
            started = time.perf_counter()
            response_text = ""
            try:
                data = self._request(system, anthropic_messages)
                response_text = self._response_text(data)
                if str(data.get("stop_reason") or "") == "max_tokens":
                    raise ValueError("模型输出被 max_tokens 截断")
                payload = self._parse_json_payload(response_text)
                payload = self._normalize_schema_envelope(payload, schema_name)
                self._validate_schema(payload, schema_name)
                latency_ms = round((time.perf_counter() - started) * 1000, 2)
                return ModelResponse(
                    response_id=str(data.get("id") or f"anthropic_{int(time.time() * 1000)}"),
                    payload=payload,
                    metadata={
                        "adapter": type(self).__name__,
                        "provider": "anthropic-compatible",
                        "model_id": str(data.get("model") or self.model_id),
                        "schema_name": schema_name,
                        "attempts": attempt,
                        "latency_ms": latency_ms,
                        "total_latency_ms": round((time.perf_counter() - total_started) * 1000, 2),
                        "stop_reason": str(data.get("stop_reason") or ""),
                        "usage": data.get("usage") or {},
                        "temperature": self.temperature,
                        "max_tokens": self.max_tokens,
                        "timeout_seconds": self.timeout_seconds,
                        "max_attempts": self.max_attempts,
                        "retry_errors": list(retry_errors),
                    },
                )
            except (ModelInvocationError, ValueError) as exc:
                last_error = str(exc)
                retry_errors.append(last_error[:240])
                if attempt >= self.max_attempts:
                    break
                if isinstance(exc, ModelInvocationError) and not self._is_retryable_transport_error(last_error):
                    break
                if "max_tokens" in last_error:
                    # Do not feed a truncated multi-thousand-token fragment back to the model.
                    # Re-run the original request with a stricter bounded-output instruction.
                    anthropic_messages = [dict(item) for item in original_messages]
                    anthropic_messages[-1]["content"] += (
                        "\n\n上一轮输出过长。重新独立生成：只返回合法 JSON；"
                        "完整 JSON 不超过 900 个中文字符；每个数组严格 2 项；"
                        "每项不超过 60 个中文字符；不要复述问题或重复段落。"
                    )
                else:
                    anthropic_messages = [
                        *original_messages,
                        *(
                            [{"role": "assistant", "content": response_text}]
                            if response_text
                            else []
                        ),
                        {
                            "role": "user",
                            "content": (
                                "上一轮响应没有满足 JSON 契约。"
                                f"错误：{last_error}。请缩短内容，只返回一个合法 JSON 对象，"
                                "不要使用 Markdown 代码块。最终面向用户的字段不得提及格式错误、"
                                "重试、契约修复或上一轮响应。"
                            ),
                        },
                    ]
                time.sleep(min(2.0, 0.25 * (2 ** (attempt - 1))))

        recovered = self._recover_bounded_payload(response_text, schema_name)
        if recovered is not None:
            return ModelResponse(
                response_id=f"recovered_{int(time.time() * 1000)}",
                payload=recovered,
                metadata={
                    "adapter": type(self).__name__,
                    "provider": "anthropic-compatible",
                    "model_id": self.model_id,
                    "schema_name": schema_name,
                    "attempts": attempts_used,
                    "total_latency_ms": round((time.perf_counter() - total_started) * 1000, 2),
                    "contract_recovered": True,
                    "usage": {},
                    "temperature": self.temperature,
                    "max_tokens": self.max_tokens,
                    "timeout_seconds": self.timeout_seconds,
                    "max_attempts": self.max_attempts,
                    "retry_errors": list(retry_errors),
                },
            )
        raise ModelInvocationError(
            f"模型调用失败，已尝试 {attempts_used} 次：{last_error or '未知错误'}"
        )

    @staticmethod
    def _is_retryable_transport_error(message: str) -> bool:
        match = re.search(r"\bHTTP\s+(\d{3})\b", str(message or ""), flags=re.I)
        if match:
            status = int(match.group(1))
            return status in {408, 409, 425, 429} or status >= 500
        lowered = str(message or "").lower()
        return any(term in lowered for term in ("网络错误", "超时", "timeout", "temporarily unavailable"))

    @staticmethod
    def _recover_bounded_payload(content: str, schema_name: str) -> dict[str, Any] | None:
        """Recover only a user-facing chat answer from a malformed final JSON.

        Structured policy, research, and gate outputs are never repaired this
        way because doing so could silently change business semantics.
        """
        if schema_name not in {"general_chat.v1", "general_chat_agent_loop.v1"}:
            return None
        text = str(content or "").strip()
        if not text:
            return None
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
        match = re.search(r'"answer"\s*:\s*"(?P<answer>.*)', text, re.S)
        if match:
            answer = match.group("answer")
            answer = re.sub(r'"\s*}?\s*}?\s*$', "", answer, flags=re.S)
            answer = answer.replace(r"\n", "\n").replace(r'\"', '"').strip()
        elif not text.startswith("{"):
            answer = text
        else:
            return None
        if len(answer) < 4:
            return None
        return {"type": "general_chat", "output": {"answer": answer[:4000]}}

    def _request(self, system: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model_id,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "thinking": {"type": "disabled"},
            "messages": messages,
        }
        if system:
            payload["system"] = system
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            f"{self.base_url}/v1/messages",
            data=body,
            method="POST",
            headers={
                "x-api-key": self.api_key,
                "Authorization": f"Bearer {self.api_key}",
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json; charset=utf-8",
            },
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            detail = detail[:600].replace(self.api_key, "***")
            raise ModelInvocationError(f"HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise ModelInvocationError(f"网络错误：{exc.reason}") from exc
        except TimeoutError as exc:
            raise ModelInvocationError(f"模型调用超时（{self.timeout_seconds:g}s）") from exc

    @staticmethod
    def _split_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, str]]]:
        system_parts: list[str] = []
        out: list[dict[str, str]] = []
        for item in messages:
            role = str(item.get("role") or "user")
            content = item.get("content", "")
            text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            if role == "system":
                system_parts.append(text)
            else:
                out.append({"role": "assistant" if role == "assistant" else "user", "content": text})
        if not out:
            raise ValueError("模型消息不能为空")
        return "\n\n".join(system_parts), out

    @staticmethod
    def _response_text(data: dict[str, Any]) -> str:
        blocks = data.get("content") or []
        texts = [str(block.get("text") or "") for block in blocks if block.get("type") == "text"]
        text = "\n".join(part for part in texts if part).strip()
        if not text:
            raise ModelInvocationError("模型没有返回文本内容")
        return text

    @staticmethod
    def _parse_json_payload(content: str) -> dict[str, Any]:
        text = content.strip()
        if text.startswith("```"):
            match = re.search(r"```(?:json)?\s*(.*?)```", text, re.S | re.I)
            if match:
                text = match.group(1).strip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end <= start:
                raise ValueError(f"响应不是合法 JSON：{exc.msg}") from exc
            try:
                payload = json.loads(text[start : end + 1])
            except json.JSONDecodeError as nested:
                raise ValueError(f"响应不是合法 JSON：{nested.msg}") from nested
        if not isinstance(payload, dict):
            raise ValueError("响应顶层必须是 JSON 对象")
        return payload

    @staticmethod
    def _normalize_schema_envelope(payload: dict[str, Any], schema_name: str) -> dict[str, Any]:
        if isinstance(payload.get("output"), dict):
            if (
                schema_name in _SUBAGENT_SCHEMA_OUTPUT_KEYS
                and str(payload.get("type") or "") not in _ACTION_TYPES
            ):
                return {"type": "final", "output": payload["output"]}
            if schema_name in {"agent_action.v1", "general_chat_agent_loop.v1"} and payload.get("type") in {
                "general_chat",
                "research_report",
            }:
                return {"type": "final", "output": payload["output"]}
            return payload
        subagent_keys = _SUBAGENT_SCHEMA_OUTPUT_KEYS.get(schema_name)
        if subagent_keys and any(key in payload for key in subagent_keys):
            return {"type": "final", "output": payload}
        if schema_name == "policy_applicability.v1" and isinstance(payload.get("assessments"), list):
            return {"type": "final", "output": {"assessments": payload["assessments"]}}
        if schema_name == "review_bundle" and any(
            key in payload for key in ("verification_status", "final_decision", "notes")
        ):
            return {"type": "final", "output": payload}
        return payload

    @staticmethod
    def _validate_schema(payload: dict[str, Any], schema_name: str) -> None:
        if schema_name in {"agent_action.v1", "general_chat_agent_loop.v1"}:
            action_type = str(payload.get("type") or "").strip()
            if action_type == "final":
                if not isinstance(payload.get("output"), dict):
                    raise ValueError(f"{schema_name} final 缺少 JSON 对象 output")
                if schema_name == "general_chat_agent_loop.v1" and not str(
                    payload["output"].get("answer") or ""
                ).strip():
                    raise ValueError("general_chat_agent_loop.v1 final 缺少 output.answer")
                return
            if action_type == "tool_call":
                if not str(payload.get("tool_name") or "").strip():
                    raise ValueError(f"{schema_name} tool_call 缺少 tool_name")
                if not isinstance(payload.get("arguments"), dict):
                    raise ValueError(f"{schema_name} tool_call 缺少 arguments 对象")
                return
            if action_type == "delegate":
                if not str(payload.get("role") or "").strip():
                    raise ValueError(f"{schema_name} delegate 缺少 role")
                if not str(payload.get("task") or "").strip():
                    raise ValueError(f"{schema_name} delegate 缺少 task")
                refs = payload.get("input_artifact_refs", [])
                if refs is not None and not isinstance(refs, list):
                    raise ValueError(f"{schema_name} delegate 的 input_artifact_refs 必须是数组")
                return
            if action_type == "request_approval":
                if not str(payload.get("tool_name") or "").strip():
                    raise ValueError(f"{schema_name} request_approval 缺少 tool_name")
                if not isinstance(payload.get("arguments"), dict):
                    raise ValueError(f"{schema_name} request_approval 缺少 arguments 对象")
                if not str(payload.get("reason") or "").strip():
                    raise ValueError(f"{schema_name} request_approval 缺少 reason")
                return
            raise ValueError(f"{schema_name} 的 type 必须是 final/tool_call/delegate/request_approval")
        output = payload.get("output")
        if not isinstance(output, dict):
            raise ValueError("响应缺少 JSON 对象 output")
        if schema_name == "subagent_plan.v1":
            if str(payload.get("type") or "") not in {"subagent_plan", "final"}:
                raise ValueError("subagent_plan.v1 的 type 必须是 subagent_plan 或 final")
            if not isinstance(output.get("tasks"), list):
                raise ValueError("subagent_plan.v1 缺少 output.tasks 数组")
            return
        if schema_name == "general_chat.v1" and not str(output.get("answer") or "").strip():
            raise ValueError("general_chat.v1 缺少 output.answer")
        if schema_name == "research_report.v1":
            if not str(output.get("thesis") or "").strip():
                raise ValueError("research_report.v1 缺少 output.thesis")
            for key in ("evidence", "risks", "recommendations", "next_checks"):
                if not isinstance(output.get(key), list):
                    raise ValueError(f"research_report.v1 的 output.{key} 必须是数组")
        if schema_name == "conversation_summary.v1":
            if not str(output.get("summary") or "").strip():
                raise ValueError("conversation_summary.v1 缺少 output.summary")
            for key in ("user_facts", "decisions", "open_loops"):
                if not isinstance(output.get(key), list):
                    raise ValueError(f"conversation_summary.v1 的 output.{key} 必须是数组")
        if schema_name == "policy_applicability.v1" and not isinstance(output.get("assessments"), list):
            raise ValueError("policy_applicability.v1 缺少 output.assessments 数组")
