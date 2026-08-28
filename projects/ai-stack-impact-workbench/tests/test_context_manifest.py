from policy_impact.harness.context_manifest import estimate_tokens, build_context_manifest
from policy_impact.harness.context_router import build_stage_context_manifest
from policy_impact.harness.hooks import stage_hooks
from policy_impact.harness.hooks.stage_hooks import record_context_manifest
from policy_impact.harness.state import PolicyImpactState


def test_estimate_tokens_is_deterministic_and_positive():
    value = {"clauses": ["tax rebate", "export credit"], "score": 0.72}

    first_estimate = estimate_tokens(value)
    second_estimate = estimate_tokens(value)

    assert first_estimate == second_estimate
    assert first_estimate >= 1
    assert estimate_tokens("") >= 1


def test_build_context_manifest_records_visible_and_hidden_fields():
    state = PolicyImpactState(run_id="run_001", company_id="company_001")
    state.company_context_pack = {"profile": {"industry": "software"}}
    state.policy_documents = [{"policy_id": "policy_001", "title": "Export support"}]
    state.warnings = [{"stage": "fetch_recent_policies", "message": "retry"}]

    manifest = build_context_manifest(
        state=state,
        stage_name="fetch_recent_policies",
        agent_role="policy_fetcher",
        visible_fields=["company_context_pack"],
        token_budget={"system": 100, "task": 200},
    )

    assert manifest.record_id.startswith("ctx_")
    assert manifest.context_type == "stage_context_manifest"
    assert manifest.source_id == "run_001"
    assert manifest.metadata["manifest_id"] == manifest.record_id
    assert manifest.metadata["stage_name"] == "fetch_recent_policies"
    assert manifest.metadata["agent_role"] == "policy_fetcher"
    assert manifest.metadata["visible_keys"] == ["company_context_pack"]
    assert manifest.metadata["token_budget"] == {"system": 100, "task": 200}
    assert manifest.metadata["token_estimates"]["company_context_pack"] >= 1
    assert manifest.metadata["visible_content_checksums"]["company_context_pack"]
    assert manifest.metadata["hidden_fields"]["policy_documents"] == "not_visible_to_stage"
    assert "warnings" not in manifest.metadata["hidden_fields"]


def test_build_context_manifest_suppresses_metadata_and_operational_fields():
    state = PolicyImpactState(run_id="run_001", company_id="company_001")
    state.policy_documents = [{"policy_id": "policy_001", "title": "Export support"}]
    state.artifacts = {"policy_documents": "data/policy_documents.json"}
    state.context_manifests = [{"record_id": "ctx_existing"}]
    state.tool_calls = [{"tool": "policy_fetch"}]
    state.model_calls = [{"model": "test-model"}]
    state.checkpoints = [{"stage": "fetch_recent_policies"}]
    state.replay_reports = [{"report_id": "replay_001"}]
    state.eval_reports = [{"report_id": "eval_001"}]
    state.benchmark_reports = [{"report_id": "bench_001"}]

    manifest = build_context_manifest(
        state=state,
        stage_name="fetch_recent_policies",
        agent_role="policy_fetcher",
        visible_fields=["company_context_pack"],
    )

    hidden_fields = manifest.metadata["hidden_fields"]

    assert hidden_fields["policy_documents"] == "not_visible_to_stage"
    assert "run_id" not in hidden_fields
    assert "created_at" not in hidden_fields
    assert "current_stage" not in hidden_fields
    assert "company_id" not in hidden_fields
    assert "artifacts" not in hidden_fields
    assert "context_manifests" not in hidden_fields
    assert "tool_calls" not in hidden_fields
    assert "model_calls" not in hidden_fields
    assert "checkpoints" not in hidden_fields
    assert "replay_reports" not in hidden_fields
    assert "eval_reports" not in hidden_fields
    assert "benchmark_reports" not in hidden_fields


def test_build_context_manifest_id_is_deterministic_for_same_manifest_payload():
    state = PolicyImpactState(run_id="run_001", company_id="company_001")
    visible_fields = ["company_context_pack", "date_range"]

    first_manifest = build_context_manifest(
        state=state,
        stage_name="fetch_recent_policies",
        agent_role="fetch_recent_policies",
        visible_fields=visible_fields,
    )
    second_manifest = build_context_manifest(
        state=state,
        stage_name="fetch_recent_policies",
        agent_role="fetch_recent_policies",
        visible_fields=visible_fields,
    )

    assert first_manifest.record_id == second_manifest.record_id
    assert first_manifest.checksum == second_manifest.checksum


def test_build_context_manifest_checksum_changes_when_visible_content_changes():
    first_state = PolicyImpactState(run_id="run_001", company_id="company_001")
    first_state.company_context_pack = {"profile": {"industry": "software"}}
    second_state = PolicyImpactState(run_id="run_001", company_id="company_001")
    second_state.company_context_pack = {"profile": {"industry": "manufacturing"}}

    first_manifest = build_context_manifest(
        state=first_state,
        stage_name="fetch_recent_policies",
        agent_role="fetch_recent_policies",
        visible_fields=["company_context_pack"],
    )
    second_manifest = build_context_manifest(
        state=second_state,
        stage_name="fetch_recent_policies",
        agent_role="fetch_recent_policies",
        visible_fields=["company_context_pack"],
    )

    assert first_manifest.record_id == second_manifest.record_id
    assert first_manifest.checksum != second_manifest.checksum
    assert (
        first_manifest.metadata["visible_content_checksums"]["company_context_pack"]
        != second_manifest.metadata["visible_content_checksums"]["company_context_pack"]
    )


def test_build_stage_context_manifest_uses_stage_visible_fields_for_real_stage():
    state = PolicyImpactState(run_id="run_002", company_id="company_001")
    state.company_context_pack = {"profile": {"industry": "software"}}
    state.policy_documents = [{"policy_id": "policy_001", "title": "Export support"}]

    manifest = build_stage_context_manifest("fetch_recent_policies", state)

    assert manifest.metadata["stage_name"] == "fetch_recent_policies"
    assert manifest.metadata["agent_role"] == "fetch_recent_policies"
    assert manifest.metadata["visible_keys"] == ["company_context_pack", "date_range"]
    assert "policy_documents" in manifest.metadata["hidden_fields"]


def test_record_context_manifest_distinguishes_same_stage_attempts():
    state = PolicyImpactState(run_id="run_003", company_id="company_001")

    first_manifest = record_context_manifest(state, "fetch_recent_policies")
    second_manifest = record_context_manifest(state, "fetch_recent_policies")

    assert first_manifest is not None
    assert second_manifest is not None
    assert first_manifest["record_id"] != second_manifest["record_id"]
    assert first_manifest["metadata"]["manifest_sequence"] == 1
    assert second_manifest["metadata"]["manifest_sequence"] == 2
    assert state.context_manifests == [first_manifest, second_manifest]


def test_record_context_manifest_failure_warns_without_appending(monkeypatch):
    state = PolicyImpactState(run_id="run_004", company_id="company_001")

    def fail_manifest(*args, **kwargs):
        raise RuntimeError("manifest unavailable")

    monkeypatch.setattr(stage_hooks, "build_stage_context_manifest", fail_manifest)

    result = record_context_manifest(state, "fetch_recent_policies")

    assert result is None
    assert state.context_manifests == []
    assert len(state.warnings) == 1
    warning = state.warnings[0]
    assert warning["stage"] == "fetch_recent_policies"
    assert warning["message"] == "context_manifest_failed"
    assert "manifest unavailable" in warning["detail"]
    assert warning["at"]
