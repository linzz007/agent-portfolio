from pathlib import Path
import sys
import unittest

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "code"))

from my_agents import (  # noqa: E402
    ALTERNATIVE_PLANNER_PROMPT_TEMPLATE,
    Calculator,
    CriticAgent,
    EVIDENCE_CRITIC_PROMPT_TEMPLATE,
    FinalAnswerAgent,
    LOGIC_CRITIC_PROMPT_TEMPLATE,
    PLANNER_PROMPT_TEMPLATE,
    PlannerAgent,
    ROUTER_PROMPT_TEMPLATE,
    RouterAgent,
    TableQAPipeline,
    TQASessionState,
    _build_table_schema,
)


class PatentDirectionGuardrailTests(unittest.TestCase):
    def test_prompt_templates_have_no_common_mojibake_sequences(self):
        prompt_text = "\n".join(
            [
                ROUTER_PROMPT_TEMPLATE,
                PLANNER_PROMPT_TEMPLATE,
                EVIDENCE_CRITIC_PROMPT_TEMPLATE,
                LOGIC_CRITIC_PROMPT_TEMPLATE,
                ALTERNATIVE_PLANNER_PROMPT_TEMPLATE,
            ]
        )
        forbidden = [
            "\ufffd",
            "\u9436\u73af",
            "\u6d63\u72b3",
            "\u7487\u8702",
            "\u6f8d\u70ba",
            "\u9234",
        ]
        for token in forbidden:
            self.assertNotIn(token, prompt_text)
        self.assertIn("\u73af\u6bd4", ROUTER_PROMPT_TEMPLATE)
        self.assertIn("\u8bc1\u636e\u652f\u6301", EVIDENCE_CRITIC_PROMPT_TEMPLATE)

    def test_router_prompt_keeps_two_top_level_paths(self):
        self.assertIn('"SIMPLE" or "COMPLEX"', ROUTER_PROMPT_TEMPLATE)
        for legacy_route_name in ("Light", "Tool", "Collab"):
            self.assertNotIn(legacy_route_name, ROUTER_PROMPT_TEMPLATE)

    def test_rule_based_route_covers_generic_english_operations(self):
        router = RouterAgent(lambda prompt: "")
        self.assertEqual(
            router._rule_based_route("What is the average score after 2010?"),
            "COMPLEX",
        )
        self.assertEqual(
            router._rule_based_route("What was the Revenue in 2021?"),
            "SIMPLE",
        )

    def test_default_pipeline_keeps_core_mechanisms_enabled(self):
        fake_llm = lambda prompt: '{"score": 0.1}'
        pipeline = TableQAPipeline(
            RouterAgent(fake_llm),
            planner=PlannerAgent(fake_llm),
            calculator=Calculator(),
            critic=CriticAgent(fake_llm),
            final_answer_agent=FinalAnswerAgent(fake_llm),
        )
        self.assertFalse(pipeline.disable_table_compression)
        self.assertFalse(pipeline.disable_question_routing)
        self.assertFalse(pipeline.disable_risk_scoring)
        self.assertTrue(pipeline.enable_deterministic_shortcuts)
        self.assertTrue(pipeline.enable_strong_verification)

    def test_simple_lookup_answer_template_is_readable(self):
        df = pd.DataFrame({"Year": ["2021"], "Revenue": [120]})
        state = TQASessionState(
            question="What was the Revenue in 2021?",
            df=df,
            table_schema=_build_table_schema(df),
        )
        state.route_type = "SIMPLE"
        state.simple_lookup_success = True
        state.simple_lookup_value = 120
        state.simple_lookup_evidence = {
            "row_label": "2021",
            "col_name": "Revenue",
            "value": "120",
        }

        result = FinalAnswerAgent(lambda prompt: "").respond(state)

        self.assertIn("\u6839\u636e\u8868\u683c\u4e2d", result.final_answer)
        self.assertIn("Revenue", result.final_answer)


if __name__ == "__main__":
    unittest.main()
