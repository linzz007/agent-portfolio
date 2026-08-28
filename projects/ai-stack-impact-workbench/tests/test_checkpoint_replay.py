import json
from pathlib import Path

from policy_impact.harness.checkpoints import write_checkpoint
from policy_impact.harness.hooks import stage_hooks
from policy_impact.harness.hooks.stage_hooks import build_default_hooks, checkpoint_stage
from policy_impact.harness.replay import dry_replay_checkpoint
from policy_impact.harness.state import PolicyImpactState
from policy_impact.harness.tool_gateway import (
    ToolGateway,
    register_tool,
    reset_tool_audit_log,
)


def test_write_checkpoint_creates_file_and_records_state_checkpoint(tmp_path):
    state = PolicyImpactState(company_id="company_test", run_id="run_test")
    state.tool_calls.append({"tool": "policy_search", "status": "ok"})
    state.add_stage_gate_result({"stage": "load_company_context", "passed": True})
    state.add_context_manifest({"stage": "load_company_context", "token_count": 12})
    state.add_model_call({"stage": "load_company_context", "model": "test-model"})

    checkpoint = write_checkpoint(state, "load_company_context", base_dir=tmp_path)

    checkpoint_path = tmp_path / "run_test" / "load_company_context.checkpoint.json"
    assert checkpoint["stage_name"] == "load_company_context"
    assert checkpoint["path"].endswith("load_company_context.checkpoint.json")
    assert checkpoint["path"] == str(checkpoint_path)
    assert checkpoint_path.exists()
    assert state.checkpoints == [checkpoint]

    payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == "run_test"
    assert payload["company_id"] == "company_test"
    assert payload["stage_name"] == "load_company_context"
    assert payload["state"]["run_id"] == "run_test"
    assert payload["tool_calls"] == [{"tool": "policy_search", "status": "ok"}]
    assert payload["stage_gate_results"] == [{"stage": "load_company_context", "passed": True}]
    assert payload["context_manifests"] == [{"stage": "load_company_context", "token_count": 12}]
    assert payload["model_calls"] == [{"stage": "load_company_context", "model": "test-model"}]


def test_write_checkpoint_preserves_repeated_same_stage_attempts(tmp_path):
    state = PolicyImpactState(company_id="company_test", run_id="run_test")

    first = write_checkpoint(state, "fetch_recent_policies", base_dir=tmp_path)
    second = write_checkpoint(state, "fetch_recent_policies", base_dir=tmp_path)

    assert first["path"] != second["path"]
    assert first["stage_name"] == "fetch_recent_policies"
    assert second["stage_name"] == "fetch_recent_policies"
    assert len(state.checkpoints) == 2
    assert state.checkpoints == [first, second]
    assert all(path.endswith(".checkpoint.json") for path in [first["path"], second["path"]])
    assert Path(first["path"]).exists()
    assert Path(second["path"]).exists()


def test_checkpoint_stage_warns_and_continues_when_checkpoint_write_fails(monkeypatch):
    state = PolicyImpactState(company_id="company_test", run_id="run_test")

    def fail_write_checkpoint(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(stage_hooks, "write_checkpoint", fail_write_checkpoint)

    result = checkpoint_stage(state, "fetch_recent_policies", context={})

    assert result is None
    assert state.warnings
    assert state.warnings[-1]["stage"] == "fetch_recent_policies"
    assert state.warnings[-1]["message"] == "checkpoint_failed"
    assert "disk full" in state.warnings[-1]["detail"]


def test_default_hooks_checkpoint_includes_current_tool_audit(monkeypatch):
    reset_tool_audit_log()
    register_tool("policy_fetch_recent", lambda **kwargs: [{"policy_id": "p1"}])
    state = PolicyImpactState(company_id="company_test", run_id="run_test")
    ToolGateway().call("fetch_recent_policies", "policy_fetch_recent", company_id="company_test")

    captured = {}

    def capture_checkpoint(checkpoint_state, stage_name, *args, **kwargs):
        captured["stage_name"] = stage_name
        captured["tool_calls"] = list(checkpoint_state.tool_calls)
        return {
            "stage_name": stage_name,
            "path": "checkpoint.json",
            "created_at": "2026-06-27T00:00:00+00:00",
        }

    monkeypatch.setattr(stage_hooks, "write_checkpoint", capture_checkpoint)

    build_default_hooks().fire_after(state, "fetch_recent_policies", context={})

    assert captured["stage_name"] == "fetch_recent_policies"
    assert captured["tool_calls"]
    assert captured["tool_calls"][0]["tool_name"] == "policy_fetch_recent"
    assert captured["tool_calls"][0]["status"] == "ok"


def test_dry_replay_checkpoint_returns_summary_counts(tmp_path):
    state = PolicyImpactState(company_id="company_test", run_id="run_test")
    state.add_artifact("policy_documents", "data/policies/policy_documents.json")
    state.tool_calls.extend(
        [
            {"tool": "policy_search", "status": "ok"},
            {"tool": "policy_fetch", "status": "ok"},
        ]
    )
    state.add_stage_gate_result({"stage": "fetch_recent_policies", "passed": True})
    state.add_context_manifest({"stage": "fetch_recent_policies", "token_count": 15})
    state.add_model_call({"stage": "fetch_recent_policies", "model": "test-model"})

    checkpoint = write_checkpoint(state, "fetch_recent_policies", base_dir=tmp_path)

    replay = dry_replay_checkpoint(checkpoint["path"])

    assert replay["status"] == "replayed"
    assert replay["mode"] == "dry_replay"
    assert replay["stage_name"] == "fetch_recent_policies"
    assert replay["source_checkpoint"] == checkpoint["path"]
    assert replay["state"]["company_id"] == "company_test"
    assert replay["tool_call_count"] == 2
    assert replay["gate_count"] == 1
    assert replay["summary"] == {
        "schema_version": "checkpoint_replay.v1",
        "run_id": "run_test",
        "company_id": "company_test",
        "stage_name": "fetch_recent_policies",
        "checkpoint_created_at": checkpoint["created_at"],
        "tool_call_count": 2,
        "gate_count": 1,
        "context_manifest_count": 1,
        "model_call_count": 1,
        "artifact_keys": ["policy_documents"],
    }
