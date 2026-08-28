from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "code"))

from risk_control import (  # noqa: E402
    BudgetController,
    BudgetPolicy,
    RiskAssessment,
    RiskPolicy,
    RiskProfiler,
    weighted_score,
)


class RiskControlTests(unittest.TestCase):
    def test_weighted_score_clamps_each_input(self):
        result = weighted_score({"a": 1.5, "b": -1.0}, {"a": 0.25, "b": 0.75})
        self.assertEqual(result, 0.25)

    def test_pre_risk_formula_uses_design_weights(self):
        profiler = RiskProfiler(RiskPolicy())
        assessment = profiler.assess_pre(
            semantic_complexity=0.2,
            structure_signals={"coverage": 1.0, "dispersion": 0.5, "type": 0.0},
            ambiguity_signals={
                "entity": 0.0,
                "column": 1.0,
                "temporal": 0.0,
                "reference": 0.5,
            },
            gap_signals={
                "entity": 0.2,
                "column": 0.4,
                "missing": 0.0,
                "stability": 1.0,
            },
            operation_signals={
                "steps": 0.5,
                "dependency": 1.0,
                "contract": 0.5,
                "unit": 0.0,
                "logic": 0.5,
            },
            hard_triggers=[],
        )
        self.assertAlmostEqual(assessment.difficulty, 0.4475, places=4)
        self.assertAlmostEqual(assessment.ambiguity, 0.35, places=4)
        self.assertAlmostEqual(assessment.evidence_gap, 0.37, places=4)
        self.assertAlmostEqual(assessment.operation_risk, 0.55, places=4)
        self.assertEqual(assessment.level, "medium")

    def test_thresholds_and_hard_triggers(self):
        profiler = RiskProfiler(RiskPolicy())
        low = RiskAssessment(pre_risk=0.24, hard_triggers=())
        medium = RiskAssessment(pre_risk=0.25, hard_triggers=())
        high = RiskAssessment(pre_risk=0.55, hard_triggers=())
        forced = RiskAssessment(pre_risk=0.10, hard_triggers=("contract_failure",))
        self.assertEqual(profiler.level_for(low), "light")
        self.assertEqual(profiler.level_for(medium), "medium")
        self.assertEqual(profiler.level_for(high), "high")
        self.assertEqual(profiler.level_for(forced), "fallback")

    def test_post_risk_formula_and_fallback_boundary(self):
        profiler = RiskProfiler(RiskPolicy())
        post = profiler.assess_post(
            pre_risk=0.45,
            candidate_disagreement=0.5,
            verification_gap=0.5,
            execution_failure=0.0,
            contract_failure=0.0,
            unit_failure=0.0,
            normalization_failure=0.0,
        )
        self.assertAlmostEqual(post.post_risk, 0.70, places=4)
        self.assertTrue(post.requires_fallback)

    def test_budget_controller_tracks_mact_relative_caps(self):
        controller = BudgetController(BudgetPolicy(mact_avg_tokens=8867.0))
        self.assertEqual(controller.cap_for("light"), 2216)
        self.assertEqual(controller.cap_for("medium"), 4876)
        self.assertEqual(controller.cap_for("high"), 7536)
        self.assertEqual(controller.cap_for("fallback"), 9753)
        controller.record("light", 1000)
        controller.record("high", 7000)
        self.assertAlmostEqual(controller.avg_tokens(), 4000.0)
        self.assertTrue(controller.within_average_limit())


if __name__ == "__main__":
    unittest.main()
