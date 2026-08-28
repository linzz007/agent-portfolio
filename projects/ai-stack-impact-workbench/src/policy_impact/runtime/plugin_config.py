"""File-backed plugin configuration helpers.

Runtime code owns execution semantics; plugin files own descriptors, prompts,
tool bindings, and policy-facing metadata.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = PROJECT_ROOT / "plugins"


def plugin_path(*parts: str) -> Path:
    return PLUGIN_ROOT.joinpath(*parts)


def read_plugin_json(*parts: str) -> Any:
    path = plugin_path(*parts)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"plugin config not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"plugin config is not valid JSON: {path}: {exc}") from exc


def read_plugin_text(*parts: str) -> str:
    path = plugin_path(*parts)
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"plugin text not found: {path}") from exc


def import_ref(reference: str) -> Any:
    module_name, separator, object_path = str(reference or "").partition(":")
    if not separator or not module_name or not object_path:
        raise ValueError(f"plugin import reference must use module:object syntax: {reference!r}")
    module = importlib.import_module(module_name)
    value: Any = module
    for part in object_path.split("."):
        if not part:
            raise ValueError(f"invalid plugin import reference: {reference!r}")
        value = getattr(value, part)
    return value


__all__ = [
    "PLUGIN_ROOT",
    "PROJECT_ROOT",
    "import_ref",
    "plugin_path",
    "read_plugin_json",
    "read_plugin_text",
]
