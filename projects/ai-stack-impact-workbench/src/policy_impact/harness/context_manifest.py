"""Deterministic context manifest construction for stage-scoped prompts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, is_dataclass
from math import ceil
from typing import Any

from policy_impact.harness.contracts import ContextManifestRecord
from policy_impact.harness.state import PolicyImpactState


DEFAULT_TOKEN_BUDGET = {
    "system": 500,
    "task": 1000,
    "profile": 1500,
    "evidence": 3000,
    "memory": 1000,
    "tool": 1000,
}


SUPPRESSED_HIDDEN_FIELDS = {
    "run_id",
    "created_at",
    "current_stage",
    "company_id",
    "artifacts",
    "tool_calls",
    "context_manifests",
    "model_calls",
    "review_bundles",
    "impact_tickets",
    "checkpoints",
    "replay_reports",
    "eval_reports",
    "benchmark_reports",
    "quality_metrics",
    "errors",
    "warnings",
    "stage_trace",
    "stage_gate_results",
    "stage_retry_counts",
}


def estimate_tokens(value: Any) -> int:
    """Estimate mixed Chinese/Latin token use conservatively.

    Dividing every character count by four substantially under-reports Chinese
    prompts.  CJK characters are therefore counted one-for-one while the
    remaining serialized text keeps the usual four-characters-per-token
    approximation.  This is still an estimate, but it is suitable for budget
    gates and honest observability.
    """
    serialized = _stable_json(value)
    cjk_count = len(re.findall(r"[\u3400-\u9fff]", serialized))
    other_count = max(0, len(serialized) - cjk_count)
    return max(1, cjk_count + ceil(other_count / 4))


def build_context_manifest(
    state: PolicyImpactState,
    stage_name: str,
    agent_role: str,
    visible_fields: list[str],
    token_budget: dict[str, int] | None = None,
    manifest_sequence: int | None = None,
) -> ContextManifestRecord:
    budget = dict(token_budget or DEFAULT_TOKEN_BUDGET)
    visible_keys = list(visible_fields)
    state_dict = state.to_dict()
    visible_state = {key: state_dict.get(key) for key in visible_keys}
    hidden_fields = _hidden_fields(state_dict, visible_keys)
    token_estimates = {key: estimate_tokens(value) for key, value in visible_state.items()}
    visible_content_checksums = {key: _sha256(value) for key, value in visible_state.items()}

    manifest_payload = {
        "run_id": state.run_id,
        "stage_name": stage_name,
        "agent_role": agent_role,
        "visible_keys": visible_keys,
        "token_budget": budget,
    }
    if manifest_sequence is not None:
        manifest_payload["manifest_sequence"] = manifest_sequence
    manifest_id = f"ctx_{_sha256(manifest_payload)[:16]}"
    checksum = _sha256(
        {
            "manifest_payload": manifest_payload,
            "hidden_fields": hidden_fields,
            "token_estimates": token_estimates,
            "visible_content_checksums": visible_content_checksums,
        }
    )

    metadata = {
        "manifest_id": manifest_id,
        "stage_name": stage_name,
        "agent_role": agent_role,
        "visible_keys": visible_keys,
        "visible_content": visible_state,
        "token_budget": budget,
        "token_estimates": token_estimates,
        "visible_content_checksums": visible_content_checksums,
        "hidden_fields": hidden_fields,
    }
    if manifest_sequence is not None:
        metadata["manifest_sequence"] = manifest_sequence
        metadata["attempt_index"] = manifest_sequence

    return ContextManifestRecord(
        record_id=manifest_id,
        context_type="stage_context_manifest",
        source_id=state.run_id,
        checksum=checksum,
        metadata=metadata,
    )


def _hidden_fields(state_dict: dict[str, Any], visible_keys: list[str]) -> dict[str, str]:
    visible = set(visible_keys)
    return {
        key: "not_visible_to_stage"
        for key, value in state_dict.items()
        if key not in visible
        and key not in SUPPRESSED_HIDDEN_FIELDS
        and _is_non_empty(value)
    }


def _is_non_empty(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _sha256(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _stable_json(value: Any) -> str:
    return json.dumps(_normalize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _normalize(value: Any) -> Any:
    if is_dataclass(value):
        return _normalize(asdict(value))
    if isinstance(value, dict):
        return {str(key): _normalize(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if isinstance(value, set):
        return sorted((_normalize(item) for item in value), key=repr)
    return value
