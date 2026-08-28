from policy_impact.harness.subagent_planner import QuerySubagentPlanner
from policy_impact.skills.registry import SkillRegistry


def test_subagent_planner_prioritizes_explicit_roles_over_evidence_gap_terms():
    manifest = SkillRegistry().get_manifest("general_chat")

    tasks = QuerySubagentPlanner().plan(
        "请从 skeptic 和 analyst 两个角度，判断 Agent Workbench 的 subagent 设计是否成熟，并指出证据缺口。",
        skill_manifest=manifest,
        artifact_ref="main_chat_context:test",
        prepared_context={},
    )

    assert [task.role for task in tasks] == ["skeptic", "analyst"]
    assert [task.trigger for task in tasks] == ["explicit_skeptic", "explicit_analyst"]
