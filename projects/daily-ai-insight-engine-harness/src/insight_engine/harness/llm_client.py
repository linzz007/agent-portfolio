"""LLM 客户端。

当前只实现 OpenAI-compatible Chat Completions 协议。
DeepSeek API 可通过环境变量配置：

- DEEPSEEK_API_KEY: API Key
- DEEPSEEK_API_BASE: 默认 https://api.deepseek.com
- DEEPSEEK_MODEL: 默认 deepseek-v4-flash
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Any

from insight_engine.harness.env import load_project_env


@dataclass(frozen=True)
class LLMResponse:
    """一次 LLM 调用结果。"""

    model: str
    content: str
    raw_response: dict[str, Any]


class LLMClientError(RuntimeError):
    """LLM 调用错误。"""


class OpenAICompatibleChatClient:
    """OpenAI-compatible Chat Completions 客户端。"""

    def __init__(
        self,
        api_key: str,
        api_base: str,
        model: str,
        timeout_seconds: int = 60,
    ) -> None:
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_deepseek_env(cls) -> "OpenAICompatibleChatClient | None":
        """从环境变量创建 DeepSeek 客户端。未配置 key 时返回 None。"""
        load_project_env()
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            return None

        timeout_seconds = int(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "120"))
        return cls(
            api_key=api_key,
            api_base=os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com"),
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            timeout_seconds=timeout_seconds,
        )

    def chat_json(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.1,
        max_retries: int | None = None,
    ) -> LLMResponse:
        """调用 Chat Completions，并要求模型输出 JSON。

        超时或网络错误时自动重试（默认 2 次），每次重试超时翻倍。
        """
        if max_retries is None:
            max_retries = int(os.getenv("DEEPSEEK_MAX_RETRIES", "2"))

        url = f"{self.api_base}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }

        last_error = None
        for attempt in range(max_retries + 1):
            try:
                return self._send_request(url, payload, attempt)
            except (LLMClientError, OSError) as exc:
                last_error = exc
                if attempt < max_retries:
                    import time
                    wait = 2 ** attempt
                    time.sleep(wait)

        raise LLMClientError(
            f"LLM 请求失败（已重试 {max_retries} 次）：{last_error!r}"
        ) from last_error

    def _send_request(
        self, url: str, payload: dict, attempt: int
    ) -> LLMResponse:
        """发送单次 LLM 请求。"""
        timeout = self.timeout_seconds * (2 ** attempt)
        request = urllib.request.Request(
            url=url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response_text = response.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            raise LLMClientError(f"LLM 请求失败：{exc!r}") from exc

        try:
            raw_response = json.loads(response_text)
            content = raw_response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise LLMClientError("LLM 响应格式不符合 Chat Completions 协议") from exc

        return LLMResponse(
            model=self.model,
            content=str(content),
            raw_response=raw_response,
        )
