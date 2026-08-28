import json

from policy_impact.mcp_server import registry
from policy_impact.mcp_server.server_stdio import handle


def test_mcp_initialize_and_tools_list():
    init = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert init["result"]["serverInfo"]["name"] == "policy-impact"

    tools = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {tool["name"] for tool in tools["result"]["tools"]}
    assert "company_profile_read" in names
    assert "company_wiki_blueprint" in names


def test_mcp_tools_call_company_profile():
    registry.register_all_tools()
    response = handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "company_profile_read", "arguments": {"company_id": "company_001"}},
        }
    )
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["company_id"] == "company_001"
