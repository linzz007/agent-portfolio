import json
from pathlib import Path

from policy_impact.skill_executors.policy_weekly_impact import run_policy_weekly_impact


def test_final_run_artifact_contains_full_harness_surfaces():
    result = run_policy_weekly_impact(
        "company_001",
        date_from="2026-06-30",
        date_to="2026-07-05",
        refresh_live_sources=False,
        allow_fixture_fallback=True,
    )

    artifact_path = Path(result.run_artifact_path)
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))

    assert payload["current_stage"] == "done"
    assert payload["stage_trace"]
    assert payload["stage_gate_results"]
    assert payload["tool_calls"]
    assert payload["context_manifests"]
    assert payload["checkpoints"]

    manifests = payload["context_manifests"]
    assert any(
        manifest.get("metadata", {}).get("stage_name")
        and isinstance(manifest.get("metadata", {}).get("visible_keys"), list)
        for manifest in manifests
    )

    record_ids = [manifest["record_id"] for manifest in manifests]
    assert len(set(record_ids)) == len(record_ids)
