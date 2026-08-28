"""Model backend selection for myAgent experiment entry points."""

from __future__ import annotations

import os
from argparse import ArgumentParser, Namespace
from typing import Any, Callable, Optional


DEEPSEEK_API_BASE = "https://api.deepseek.com"
MODEL_PROVIDERS = ("auto", "deepseek", "openai_compatible", "azure", "local")
DEEPSEEK_MODEL_ALIASES = {
    "deepseek-v4flash": "deepseek-v4-flash",
}


def add_model_backend_args(parser: ArgumentParser) -> None:
    """Register provider-independent model backend arguments."""
    parser.add_argument(
        "--model_provider",
        choices=MODEL_PROVIDERS,
        default="auto",
        help="Model backend. 'auto' infers DeepSeek/Azure/local from the model name.",
    )
    parser.add_argument(
        "--api_base",
        default=DEEPSEEK_API_BASE,
        help="Base URL for DeepSeek or another OpenAI-compatible API.",
    )
    parser.add_argument(
        "--api_key_env",
        default="DEEPSEEK_API_KEY",
        help="Environment variable containing the API key; the key is never accepted as a CLI value.",
    )
    parser.add_argument(
        "--thinking",
        choices=("disabled", "enabled"),
        default="disabled",
        help="DeepSeek thinking mode. Disabled is recommended for the initial cost baseline.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Sampling temperature used by API backends.",
    )
    parser.add_argument(
        "--max_tokens",
        type=int,
        default=2048,
        help="Maximum number of generated tokens per API call.",
    )
    parser.add_argument(
        "--api_timeout",
        type=float,
        default=120.0,
        help="API request timeout in seconds.",
    )
    parser.add_argument(
        "--api_max_retries",
        type=int,
        default=5,
        help="Maximum automatic retries for transient API failures.",
    )


def resolve_model_provider(args: Namespace) -> str:
    """Resolve the explicit provider or preserve the legacy model-name behavior."""
    provider = getattr(args, "model_provider", "auto") or "auto"
    if provider != "auto":
        return provider

    model_name = (getattr(args, "plan_model_name", "") or "").lower()
    if "deepseek" in model_name:
        return "deepseek"
    if "gpt" in model_name:
        return "azure"
    return "local"


def _require_text_response(completion: Any) -> str:
    try:
        content = completion.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as exc:
        raise RuntimeError("The model API returned an invalid chat completion response.") from exc
    if not content or not str(content).strip():
        raise RuntimeError("The model API returned an empty response.")
    return str(content)


class ChatCompletionCallable:
    """Callable chat backend with provider-returned usage counters."""

    def __init__(
        self,
        client: Any,
        provider: str,
        model_name: str,
        temperature: float,
        max_tokens: int,
        thinking: str,
    ) -> None:
        self.client = client
        self.provider = provider
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.thinking = thinking
        self.request_count = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def _complete_chat(
        self,
        prompt: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        request = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
            "stream": False,
        }
        if self.provider == "deepseek":
            request["extra_body"] = {"thinking": {"type": self.thinking}}
        elif self.provider == "openai_compatible" and "qwen3" in self.model_name.lower():
            request["extra_body"] = {
                "chat_template_kwargs": {"enable_thinking": self.thinking == "enabled"}
            }
        completion = self.client.chat.completions.create(**request)
        self.request_count += 1
        usage = getattr(completion, "usage", None)
        self.prompt_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
        self.completion_tokens += int(getattr(usage, "completion_tokens", 0) or 0)
        return _require_text_response(completion)

    def __call__(self, prompt: str) -> str:
        return self._complete_chat(prompt)

    def complete(
        self,
        prompt: str,
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> str:
        return self._complete_chat(
            prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def snapshot(self) -> dict[str, int]:
        return {
            "request_count": self.request_count,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
        }


def _build_openai_compatible_fn(
    args: Namespace,
    provider: str,
    client_factory: Optional[Callable[..., Any]],
) -> Callable[[str], str]:
    api_key_env = getattr(args, "api_key_env", "DEEPSEEK_API_KEY")
    api_key = os.getenv(api_key_env)
    if not api_key:
        raise RuntimeError(
            f"Environment variable {api_key_env} is not set. "
            "Set it before starting the experiment."
        )

    if client_factory is None:
        from openai import OpenAI

        client_factory = OpenAI

    base_url = getattr(args, "api_base", DEEPSEEK_API_BASE) or DEEPSEEK_API_BASE
    timeout = float(getattr(args, "api_timeout", 120.0))
    max_retries = int(getattr(args, "api_max_retries", 5))
    client = client_factory(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        max_retries=max_retries,
    )
    model_name = getattr(args, "plan_model_name", "") or ""
    if not model_name:
        raise RuntimeError("--plan_model_name is required for API model providers.")
    if provider == "deepseek":
        model_name = DEEPSEEK_MODEL_ALIASES.get(model_name.lower(), model_name)

    temperature = float(getattr(args, "temperature", 0.0))
    max_tokens = int(getattr(args, "max_tokens", 2048))
    thinking = getattr(args, "thinking", "disabled")

    return ChatCompletionCallable(
        client=client,
        provider=provider,
        model_name=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        thinking=thinking,
    )


def _build_azure_fn() -> Callable[[str], str]:
    from agents import get_completion, load_gpt_azure

    client = load_gpt_azure()

    def llm_fn(prompt: str) -> str:
        outputs = get_completion(prompt, client=client, n=1)
        if not outputs or not outputs[0]:
            raise RuntimeError("The Azure model returned an empty response.")
        return outputs[0]

    return llm_fn


def _build_local_fn(args: Namespace) -> Callable[[str], str]:
    model_path = getattr(args, "model_path", "") or ""
    if not model_path:
        raise RuntimeError("--model_path is required when --model_provider=local.")

    from transformers import AutoTokenizer
    from vllm import LLM

    from llm import OpenSourceLLM

    tokenizer = AutoTokenizer.from_pretrained(model_path)
    model = LLM(model=model_path)
    os_llm = OpenSourceLLM(
        model_name=getattr(args, "plan_model_name", "") or model_path,
        model=model,
        vllm=model,
        tokenizer=tokenizer,
    )

    def llm_fn(prompt: str) -> str:
        outputs = os_llm(prompt, num_return_sequences=1, return_prob=False)
        if not outputs or not outputs[0]:
            raise RuntimeError("The local model returned an empty response.")
        return outputs[0]

    return llm_fn


def build_llm_fn(
    args: Namespace,
    openai_client_factory: Optional[Callable[..., Any]] = None,
) -> Callable[[str], str]:
    """Build the common ``prompt -> text`` callable used by all agents."""
    provider = resolve_model_provider(args)
    if provider in {"deepseek", "openai_compatible"}:
        return _build_openai_compatible_fn(args, provider, openai_client_factory)
    if provider == "azure":
        return _build_azure_fn()
    if provider == "local":
        return _build_local_fn(args)
    raise ValueError(f"Unsupported model provider: {provider}")
