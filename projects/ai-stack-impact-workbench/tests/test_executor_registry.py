import pytest
from pydantic import ValidationError

from policy_impact.runtime.execution_context import SkillExecutionContext
from policy_impact.runtime.execution_mode import ExecutionMode
from policy_impact.runtime.executor_registry import (
    ExecutionOverride,
    ExecutionOverrideRegistry,
    ExecutorRegistry,
)
from policy_impact.runtime.result import SkillResult
from policy_impact.runtime.task_brief import DelegationEvidence
from policy_impact.skills.executors.current_skills import (
    CompanyWikiBlueprintExecutor,
    ExternalImpactReportExecutor,
    GeneralChatExecutor,
    OfficialLegalReferenceExecutor,
    OfficialLegalReferenceOverrideResolver,
    PolicyWeeklyImpactExecutor,
    RecentNewsReportExecutor,
    ResearchReportExecutor,
)
from policy_impact.skills.schemas import (
    ExternalImpactOutput,
    GeneralChatOutput,
    NewsReportOutput,
    PolicyImpactOutput,
    ResearchReportOutput,
    SkillOutput,
    WikiBlueprintOutput,
)


INVALID_IDENTIFIERS = [
    "   ",
    " echo",
    "echo ",
    "Echo",
    "echo-id",
    "echo/id",
    ".echo",
    "echo.",
    "echo..nested",
]
EXECUTOR_CASES = [
    (GeneralChatExecutor, ExecutionMode.MODEL_ONCE, GeneralChatOutput),
    (ExternalImpactReportExecutor, ExecutionMode.WORKFLOW, ExternalImpactOutput),
    (PolicyWeeklyImpactExecutor, ExecutionMode.WORKFLOW, PolicyImpactOutput),
    (RecentNewsReportExecutor, ExecutionMode.WORKFLOW, NewsReportOutput),
    (ResearchReportExecutor, ExecutionMode.MODEL_ONCE, ResearchReportOutput),
    (CompanyWikiBlueprintExecutor, ExecutionMode.DETERMINISTIC, WikiBlueprintOutput),
    (OfficialLegalReferenceExecutor, ExecutionMode.DETERMINISTIC, SkillOutput),
]


class CustomExecutorOutput(SkillOutput):
    contract_marker: str


class EchoExecutor:
    def execute(self, context: SkillExecutionContext) -> SkillResult:
        return SkillResult(
            answer=context.message,
            actual_execution_mode=ExecutionMode.MODEL_ONCE,
            stop_reason="success",
        )


def _context() -> SkillExecutionContext:
    return SkillExecutionContext(
        run_id="run-1",
        session_id="session-1",
        company_id="company-1",
        message="hello",
        model_id="model-1",
        context_manifest={},
        prepared_context={},
    )


def test_registry_resolves_executor_by_id():
    registry = ExecutorRegistry()
    executor = EchoExecutor()

    registry.register("echo", executor)

    assert registry.get("echo") is executor
    assert registry.get("echo").execute(_context()).answer == "hello"


def test_registry_fails_closed_for_unknown_executor():
    with pytest.raises(KeyError, match="unknown executor: missing"):
        ExecutorRegistry().get("missing")


@pytest.mark.parametrize("executor_id", [""])
def test_registry_rejects_empty_executor_ids(executor_id):
    with pytest.raises(ValueError, match="empty executor id"):
        ExecutorRegistry().register(executor_id, EchoExecutor())


@pytest.mark.parametrize("executor_id", INVALID_IDENTIFIERS)
def test_registry_rejects_noncanonical_executor_ids(executor_id):
    registry = ExecutorRegistry()

    with pytest.raises(ValueError, match="invalid executor id"):
        registry.register(executor_id, EchoExecutor())
    with pytest.raises(ValueError, match="invalid executor id"):
        registry.get(executor_id)


@pytest.mark.parametrize("executor_id", INVALID_IDENTIFIERS)
def test_execution_override_rejects_noncanonical_executor_ids(executor_id):
    with pytest.raises(ValueError, match="invalid executor id"):
        ExecutionOverride(executor_id=executor_id)


@pytest.mark.parametrize("resolver_id", INVALID_IDENTIFIERS)
def test_override_registry_rejects_noncanonical_resolver_ids(resolver_id):
    resolver = OfficialLegalReferenceOverrideResolver(lambda message: None)

    with pytest.raises(ValueError, match="invalid override resolver id"):
        ExecutionOverrideRegistry().register(resolver_id, resolver)


def test_registry_rejects_duplicate_executor_ids():
    registry = ExecutorRegistry()
    registry.register("echo", EchoExecutor())

    with pytest.raises(ValueError, match="duplicate executor id: 'echo'"):
        registry.register("echo", EchoExecutor())


def test_registry_rejects_objects_that_do_not_satisfy_skill_executor():
    with pytest.raises(TypeError, match="SkillExecutor"):
        ExecutorRegistry().register("invalid", object())


@pytest.mark.parametrize(
    ("executor_type", "expected_mode", "output_schema"),
    EXECUTOR_CASES,
)
def test_current_skill_executors_report_real_implementation_modes(
    executor_type,
    expected_mode,
    output_schema,
):
    executor = executor_type(
        lambda context: {
            "answer": context.message,
            "citations": [],
            "artifacts": {},
        },
        output_schema,
    )

    result = executor.execute(_context())

    assert result.actual_execution_mode is expected_mode
    assert result.stop_reason == "success"


def test_policy_executor_reports_subagent_workflow_only_with_structured_success_evidence():
    evidence = DelegationEvidence(
        task_id="task-1",
        parent_run_id="run-1",
        role="skeptic",
        parent_span_id="span-parent",
        span_id="span-child",
        context_manifest_id="ctx-child",
        artifact_refs=("counterexample-1",),
    )
    executor = PolicyWeeklyImpactExecutor(
        lambda context: {
            "answer": "policy report ready",
            "citations": [],
            "artifacts": {},
            "delegation_evidence": [evidence.model_dump(mode="json")],
        },
        PolicyImpactOutput,
    )

    result = executor.execute(_context())

    assert result.actual_execution_mode is ExecutionMode.SUBAGENT_WORKFLOW
    assert result.delegation_evidence == (evidence,)


def test_policy_executor_rejects_delegation_evidence_from_another_run():
    evidence = DelegationEvidence(
        task_id="task-1",
        parent_run_id="other-run",
        role="skeptic",
        parent_span_id="span-parent",
        span_id="span-child",
        context_manifest_id="ctx-child",
    )
    executor = PolicyWeeklyImpactExecutor(
        lambda context: {
            "answer": "policy report ready",
            "citations": [],
            "artifacts": {},
            "delegation_evidence": [evidence.model_dump(mode="json")],
        },
        PolicyImpactOutput,
    )

    with pytest.raises(ValueError, match="parent_run_id"):
        executor.execute(_context())


def test_policy_executor_ignores_unstructured_mode_claim_without_delegation_evidence():
    executor = PolicyWeeklyImpactExecutor(
        lambda context: {
            "answer": "deterministic workflow report",
            "citations": [],
            "artifacts": {},
            "actual_execution_mode": "subagent_workflow",
        },
        PolicyImpactOutput,
    )

    result = executor.execute(_context())

    assert result.actual_execution_mode is ExecutionMode.WORKFLOW
    assert result.delegation_evidence == ()


@pytest.mark.parametrize(
    ("executor_type", "output_schema"),
    [(executor_type, output_schema) for executor_type, _, output_schema in EXECUTOR_CASES],
)
def test_current_skill_executors_validate_raw_output(executor_type, output_schema):
    executor = executor_type(
        lambda context: {"citations": [], "artifacts": {}},
        output_schema,
    )

    with pytest.raises(ValidationError, match="answer"):
        executor.execute(_context())


def test_current_skill_executor_uses_supplied_output_schema():
    executor = GeneralChatExecutor(
        lambda context: {
            "answer": context.message,
            "citations": [],
            "artifacts": {},
        },
        CustomExecutorOutput,
    )

    with pytest.raises(ValidationError, match="contract_marker"):
        executor.execute(_context())


def test_current_skill_executor_receives_context_and_preserves_skill_result_fields():
    received = []
    context = _context()

    def execute(current_context):
        received.append(current_context)
        return {
            "answer": "validated answer",
            "citations": [
                {
                    "type": "legacy_source",
                    "title": "Source title",
                    "url": "https://example.test/source",
                    "path": "evidence/source.json",
                },
                {
                    "citation_type": "canonical_source",
                    "source_ref": "source-2",
                },
            ],
            "artifacts": {"report": "report.md"},
            "artifact_refs": ["explicit-artifact-ref"],
            "memory_proposals": ["remember this"],
            "gate_decision_refs": ["gate-1"],
            "resumable": True,
            "stop_reason": "awaiting_approval",
        }

    result = GeneralChatExecutor(execute, GeneralChatOutput).execute(context)

    assert received == [context]
    assert result.answer == "validated answer"
    assert result.artifacts == {"report": "report.md"}
    assert result.artifact_refs == ["explicit-artifact-ref"]
    assert result.memory_proposals == ["remember this"]
    assert result.gate_decision_refs == ["gate-1"]
    assert result.resumable is True
    assert result.stop_reason == "awaiting_approval"
    assert result.citations[0].model_dump() == {
        "citation_type": "legacy_source",
        "title": "Source title",
        "url": "https://example.test/source",
        "source_ref": "",
        "path": "evidence/source.json",
    }
    assert result.citations[1].citation_type == "canonical_source"
    assert result.citations[1].source_ref == "source-2"


def test_current_skill_executor_derives_artifact_refs_from_artifacts():
    executor = ResearchReportExecutor(
        lambda context: {
            "answer": "report ready",
            "citations": [],
            "artifacts": {"report": "report.md", "html": "report.html"},
        },
        ResearchReportOutput,
    )

    result = executor.execute(_context())

    assert result.artifact_refs == ["report.md", "report.html"]


@pytest.mark.parametrize(
    "field_name",
    ["artifact_refs", "memory_proposals", "gate_decision_refs"],
)
@pytest.mark.parametrize(
    "invalid_value",
    ["scalar", b"bytes", {"bad": "mapping"}, 7, None],
    ids=["string", "bytes", "mapping", "number", "none"],
)
def test_current_skill_executor_rejects_scalar_collection_fields(
    field_name,
    invalid_value,
):
    raw = {
        "answer": "validated answer",
        "citations": [],
        "artifacts": {},
        field_name: invalid_value,
    }
    executor = GeneralChatExecutor(lambda context: raw, GeneralChatOutput)

    with pytest.raises(TypeError, match=rf"{field_name} must be a list or tuple"):
        executor.execute(_context())


def test_current_skill_executor_accepts_tuple_collection_fields():
    executor = GeneralChatExecutor(
        lambda context: {
            "answer": "validated answer",
            "citations": [],
            "artifacts": {},
            "artifact_refs": ("artifact-1",),
            "memory_proposals": ("memory-1",),
            "gate_decision_refs": ("gate-1",),
        },
        GeneralChatOutput,
    )

    result = executor.execute(_context())

    assert result.artifact_refs == ["artifact-1"]
    assert result.memory_proposals == ["memory-1"]
    assert result.gate_decision_refs == ["gate-1"]


class StaticOverrideResolver:
    def __init__(self, override):
        self.override = override
        self.messages = []

    def resolve(self, message):
        self.messages.append(message)
        return self.override


def test_execution_override_registry_rejects_duplicate_resolver_ids():
    registry = ExecutionOverrideRegistry()
    registry.register("legal.reference", StaticOverrideResolver(None))

    with pytest.raises(ValueError, match="duplicate override resolver id: 'legal.reference'"):
        registry.register("legal.reference", StaticOverrideResolver(None))


def test_execution_override_registry_resolves_registered_override():
    resolver = StaticOverrideResolver(
        ExecutionOverride(
            executor_id="deterministic_override",
            services={"reference": {"ids": ["ref-1"]}},
        )
    )
    registry = ExecutionOverrideRegistry()
    registry.register("legal_reference", resolver)

    override = registry.resolve("verify article")

    assert resolver.messages == ["verify article"]
    assert override.executor_id == "deterministic_override"
    assert override.services["reference"]["ids"] == ("ref-1",)
    with pytest.raises(TypeError):
        override.services["reference"]["new"] = "mutation"
    with pytest.raises(AttributeError):
        override.services["reference"]["ids"].append("ref-2")


def test_execution_override_registry_returns_none_without_a_match():
    registry = ExecutionOverrideRegistry()
    registry.register("no_match", StaticOverrideResolver(None))

    assert registry.resolve("ordinary message") is None


def test_execution_override_registry_rejects_invalid_resolver_results():
    registry = ExecutionOverrideRegistry()
    registry.register("invalid", StaticOverrideResolver({"executor_id": "untyped"}))

    with pytest.raises(TypeError, match="ExecutionOverride"):
        registry.resolve("message")


def test_official_legal_reference_override_resolver_targets_registered_executor():
    calls = []

    def resolve(message):
        calls.append(message)
        if message != "official question":
            return None
        return {
            "reference_id": "article-1",
            "title": "Article 1",
            "official_url": "https://example.test/law",
            "official_text": "Official text",
            "answer": "Verified answer",
        }

    resolver = OfficialLegalReferenceOverrideResolver(resolve)

    assert resolver.resolve("ordinary question") is None
    override = resolver.resolve("official question")

    assert calls == ["ordinary question", "official question"]
    assert override.executor_id == "official_legal_reference"
    assert override.services["legal_reference"]["reference_id"] == "article-1"
    with pytest.raises(TypeError):
        override.services["legal_reference"]["answer"] = "changed"
