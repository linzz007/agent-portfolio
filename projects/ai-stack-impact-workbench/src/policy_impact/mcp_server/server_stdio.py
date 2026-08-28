"""Minimal stdio MCP-like server for local policy impact tools."""

from __future__ import annotations

import json
import sys
from typing import Any

from policy_impact.mcp_server import registry


def _read_message() -> dict[str, Any] | None:
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        line = line.decode("utf-8").strip()
        if not line:
            break
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return None
    return json.loads(sys.stdin.buffer.read(length).decode("utf-8"))


def _write_message(payload: dict[str, Any]) -> None:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(raw)}\r\n\r\n".encode("utf-8") + raw)
    sys.stdout.buffer.flush()


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    msg_id = message.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"protocolVersion": "2024-11-05", "serverInfo": {"name": "policy-impact", "version": "0.1.0"}}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": registry.tool_schemas()}}
    if method == "tools/call":
        params = message.get("params") or {}
        result = registry.call_tool(params.get("name", ""), **(params.get("arguments") or {}))
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}}
    if method == "notifications/initialized":
        return None
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"Unknown method: {method}"}}


def main() -> None:
    while True:
        msg = _read_message()
        if msg is None:
            break
        response = handle(msg)
        if response is not None:
            _write_message(response)


if __name__ == "__main__":
    main()
