import unittest

from core.orchestration.policies import ToolDecision, ToolPolicy


class ToolPolicyDecisionTests(unittest.TestCase):
    def test_calculator_decision_is_safe_and_allowed(self):
        decision = ToolPolicy.decide_tool_call(
            "calculator",
            {"expression": "1 + 1"},
            mode="learn",
            phase="act",
            memory_search_in_act_default=False,
        )

        self.assertIsInstance(decision, ToolDecision)
        self.assertTrue(decision.allowed)
        self.assertEqual("allowed", decision.reason)
        self.assertEqual("safe", decision.risk_level)
        self.assertEqual("off", decision.approval_mode)
        self.assertEqual("calculator:{\"expression\": \"1 + 1\"}", decision.signature)

    def test_filewriter_decision_records_write_risk_without_changing_default_gate(self):
        decision = ToolPolicy.decide_tool_call(
            "filewriter",
            {"filename": "note.md", "content": "hello"},
            mode="learn",
            phase="act",
            memory_search_in_act_default=False,
        )

        self.assertTrue(decision.allowed)
        self.assertEqual("write", decision.risk_level)
        self.assertEqual("log", decision.approval_mode)
        self.assertEqual("request_human_approval", decision.required_approval)

    def test_websearch_decision_records_external_risk(self):
        decision = ToolPolicy.decide_tool_call(
            "websearch",
            {"query": "latest AI news"},
            mode="learn",
            phase="act",
            memory_search_in_act_default=False,
        )

        self.assertTrue(decision.allowed)
        self.assertEqual("external", decision.risk_level)
        self.assertEqual("log", decision.approval_mode)

    def test_memory_search_preserves_existing_act_gate(self):
        decision = ToolPolicy.decide_tool_call(
            "memory_search",
            {"query": "错题", "course_name": "math"},
            mode="learn",
            phase="act",
            memory_search_in_act_default=False,
        )

        self.assertFalse(decision.allowed)
        self.assertEqual("memory_search_disabled_in_act", decision.reason)
        self.assertEqual("read", decision.risk_level)

    def test_legacy_tool_preflight_tuple_is_preserved(self):
        allowed, reason, capability, signature = ToolPolicy.tool_preflight(
            "calculator",
            {"expression": "2 * 3"},
            mode="learn",
            phase="act",
            memory_search_in_act_default=False,
        )

        self.assertTrue(allowed)
        self.assertEqual("allowed", reason)
        self.assertEqual("safe", capability.risk_level)
        self.assertEqual("calculator:{\"expression\": \"2 * 3\"}", signature)


if __name__ == "__main__":
    unittest.main()
