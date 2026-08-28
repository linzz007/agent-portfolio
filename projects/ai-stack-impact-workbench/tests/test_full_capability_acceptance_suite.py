from __future__ import annotations

import importlib


def test_full_capability_suite_covers_workbench_design_axes():
    suite = importlib.import_module("scripts.run_workbench_full_capability_acceptance")

    required_capabilities = {
        "model_runtime",
        "session_contract",
        "skill_registry",
        "slash_commands",
        "general_chat",
        "context_manifest",
        "memory_read",
        "memory_write",
        "tool_gateway",
        "permission_boundary",
        "gate",
        "stop_hook",
        "research_artifact",
        "wiki_tree",
        "policy_workflow",
        "news_workflow",
        "subject_scope_gate",
        "multi_turn",
        "frontend_trace",
    }
    covered = {
        capability
        for case in suite.CASES
        for capability in case.get("capabilities", [])
    }

    assert len(suite.CASES) >= 18
    assert required_capabilities <= covered
    assert len({case["case_id"] for case in suite.CASES}) == len(suite.CASES)
    assert all(case.get("level") in {"L1", "L2", "L3", "L4"} for case in suite.CASES)
