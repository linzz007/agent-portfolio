"""LLM 输出 Hook。

V1 主要是规则代码，但保留这个 Hook。
后续引入 LLM 时，所有 LLM 原始输出都先经过这里解析和校验
这个文件是专门给 LLM 输出用的 Hook。。
"""

from __future__ import annotations

import json
import re
from typing import Any


def strip_code_fence(text: str) -> str:
    """去掉 Markdown 代码块包裹。"""
    stripped = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL)
    if match:
        return match.group(1).strip()
    return stripped


def parse_json_output(text: str) -> dict[str, Any] | list[Any]:
    """把 LLM 文本解析成 JSON 对象。"""
    payload = strip_code_fence(text)
    return json.loads(payload)


def require_keys(payload: dict[str, Any], required_keys: list[str]) -> None:
    """校验 JSON 对象是否包含必填字段。"""
    missing = [key for key in required_keys if key not in payload]
    if missing:
        raise ValueError(f"LLM 输出缺少必填字段：{missing}")

