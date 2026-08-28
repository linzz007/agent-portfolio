"""Fixed model policy for the single-user Agent Workbench."""

from __future__ import annotations

import os
from typing import Any


DEFAULT_MODEL_ID = "deepseek-v4-flash"
DEFAULT_BASE_URL = "https://api.deepseek.com/anthropic"
DEFAULT_MAX_TOKENS = 2048
DEFAULT_TEMPERATURE = 0.0
DEFAULT_TIMEOUT_SECONDS = 45.0
DEFAULT_MAX_ATTEMPTS = 2
AUTH_ENV_VARS = ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")
MODEL_ENV_VAR = "ANTHROPIC_MODEL"
BASE_URL_ENV_VAR = "ANTHROPIC_BASE_URL"


def effective_model_config() -> dict[str, Any]:
    """Resolve and validate the single effective model configuration."""

    auth_var = next((name for name in AUTH_ENV_VARS if os.getenv(name)), "")
    model_id = (os.getenv(MODEL_ENV_VAR, DEFAULT_MODEL_ID) or DEFAULT_MODEL_ID).strip()
    base_url = (os.getenv(BASE_URL_ENV_VAR, DEFAULT_BASE_URL) or DEFAULT_BASE_URL).strip().rstrip("/")
    max_tokens = int(os.getenv("AGENT_WORKBENCH_MODEL_MAX_TOKENS", str(DEFAULT_MAX_TOKENS)))
    temperature = float(os.getenv("AGENT_WORKBENCH_MODEL_TEMPERATURE", str(DEFAULT_TEMPERATURE)))
    timeout_seconds = float(os.getenv("AGENT_WORKBENCH_MODEL_TIMEOUT", str(DEFAULT_TIMEOUT_SECONDS)))
    max_attempts = int(os.getenv("AGENT_WORKBENCH_MODEL_ATTEMPTS", str(DEFAULT_MAX_ATTEMPTS)))
    if not 256 <= max_tokens <= 32768:
        raise ValueError("AGENT_WORKBENCH_MODEL_MAX_TOKENS must be between 256 and 32768")
    if not 0 <= temperature <= 2:
        raise ValueError("AGENT_WORKBENCH_MODEL_TEMPERATURE must be between 0 and 2")
    if not 1 <= timeout_seconds <= 180:
        raise ValueError("AGENT_WORKBENCH_MODEL_TIMEOUT must be between 1 and 180 seconds")
    if not 1 <= max_attempts <= 3:
        raise ValueError("AGENT_WORKBENCH_MODEL_ATTEMPTS must be between 1 and 3")
    return {
        "model_id": model_id,
        "base_url": base_url,
        "api_key": os.getenv(auth_var, "") if auth_var else "",
        "auth_env": auth_var or AUTH_ENV_VARS[0],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "timeout_seconds": timeout_seconds,
        "max_attempts": max_attempts,
        "stream": False,
        "thinking": False,
    }


def default_model_config() -> dict[str, Any]:
    """Return the only model row this Workbench should expose."""

    effective = effective_model_config()
    return {
        "model_id": DEFAULT_MODEL_ID,
        "display_name": DEFAULT_MODEL_ID,
        "provider": "anthropic-compatible",
        "model_name": DEFAULT_MODEL_ID,
        "endpoint": effective["base_url"],
        "temperature": effective["temperature"],
        "max_tokens": effective["max_tokens"],
        "is_default": True,
        "metadata": {
            "fixed": True,
            "auth_env": AUTH_ENV_VARS[0],
            "base_url_env": BASE_URL_ENV_VAR,
            "model_env": MODEL_ENV_VAR,
            "stream": False,
            "thinking": False,
            "timeout_seconds": effective["timeout_seconds"],
            "max_attempts": effective["max_attempts"],
        },
    }


def model_runtime_status() -> dict[str, Any]:
    """Describe model availability without exposing secrets."""

    effective = effective_model_config()
    auth_var = effective["auth_env"] if effective["api_key"] else ""
    configured_model = effective["model_id"]
    base_url = effective["base_url"]
    missing = []
    if not auth_var:
        missing.append(AUTH_ENV_VARS[0])
    if configured_model != DEFAULT_MODEL_ID:
        missing.append(f"{MODEL_ENV_VAR}={DEFAULT_MODEL_ID}")
    return {
        "model_id": DEFAULT_MODEL_ID,
        "provider": "anthropic-compatible",
        "configured_model": configured_model,
        "base_url": base_url,
        "auth_configured": bool(auth_var),
        "auth_env": auth_var or AUTH_ENV_VARS[0],
        "available": not missing,
        "missing": missing,
        "temperature": effective["temperature"],
        "max_tokens": effective["max_tokens"],
        "timeout_seconds": effective["timeout_seconds"],
        "max_attempts": effective["max_attempts"],
        "stream": effective["stream"],
        "thinking": effective["thinking"],
    }


def ensure_default_model_available() -> None:
    status = model_runtime_status()
    if status["available"]:
        return
    missing = "、".join(status["missing"])
    raise ValueError(
        "当前没有可用模型，无法执行。"
        f"请在启动服务前配置 {missing}，并固定使用 {DEFAULT_MODEL_ID}。"
    )
