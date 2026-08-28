"""Harness 控制组件测试。

验证 after_llm_call 解析校验函数和 tool_gateway 白名单控制。
"""

import pytest

from insight_engine.harness.hooks.after_llm_call import parse_json_output, require_keys
from insight_engine.harness.tool_gateway import ToolGateway


def test_after_llm_call_parses_json_code_fence():
    """能从 markdown 代码块中提取 JSON。"""
    payload = parse_json_output('```json\n{"passed": true, "score": 100}\n```')

    assert payload == {"passed": True, "score": 100}


def test_after_llm_call_requires_keys():
    """必填字段缺失时抛出 ValueError。"""
    require_keys({"passed": True}, ["passed"])

    with pytest.raises(ValueError):
        require_keys({"passed": True}, ["passed", "score"])


def test_tool_gateway_rejects_unregistered_stage():
    """不在白名单的 stage 调用工具应抛出 PermissionError。"""
    gateway = ToolGateway()

    with pytest.raises(PermissionError):
        gateway.call("unknown_stage", "any_tool")


def test_tool_gateway_rejects_unregistered_tool():
    """stage 在白名单但工具未注册时抛出 KeyError。"""
    from insight_engine.harness.tool_gateway import STAGE_ALLOWED_TOOLS

    STAGE_ALLOWED_TOOLS["test_stage"] = {"fake_tool"}

    gateway = ToolGateway()
    with pytest.raises(KeyError):
        gateway.call("test_stage", "fake_tool")
