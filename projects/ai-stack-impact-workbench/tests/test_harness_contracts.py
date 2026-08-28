from pathlib import Path
import subprocess
import sys
from textwrap import dedent

import pytest

from scripts.harness_linter import REQUIRED_PATHS, lint_repository


_LINTER_BASELINE = {
    "src/policy_impact/skills/registry.py": dedent(
        """\
        _DEFAULT_MANIFESTS = (
            SkillManifest(id="general_chat", executor_id="general_chat"),
            SkillManifest(id="policy_weekly_impact", executor_id="policy_weekly_impact"),
            SkillManifest(id="recent_news_report", executor_id="recent_news_report"),
            SkillManifest(id="research_report", executor_id="research_report"),
            SkillManifest(id="company_wiki_blueprint", executor_id="company_wiki_blueprint"),
        )
        """
    ),
    "src/policy_impact/harness/agent_runtime.py": dedent(
        """\
        class AgentRuntime:
            def _build_executor_registry(self):
                registry = ExecutorRegistry()
                general_chat = self.registry.get_manifest("general_chat")
                registry.register(general_chat.executor_id, object())
                policy = self.registry.get_manifest("policy_weekly_impact")
                registry.register(policy.executor_id, object())
                news = self.registry.get_manifest("recent_news_report")
                registry.register(news.executor_id, object())
                research = self.registry.get_manifest("research_report")
                registry.register(research.executor_id, object())
                wiki = self.registry.get_manifest("company_wiki_blueprint")
                registry.register(wiki.executor_id, object())
                return registry
        """
    ),
    "src/policy_impact/runtime/turn_coordinator.py": dedent(
        """\
        class TurnCoordinator:
            def execute(self, context, logger):
                result = self.executor_registry.get("general_chat").execute(context)
                metadata = {}
                metadata["execution_mode"] = result.actual_execution_mode.value
                logger.info("general_chat")
                display = {"skill_id": "general_chat"}
                return metadata, display
        """
    ),
    "src/policy_impact/harness/agent_loop.py": dedent(
        """\
        _ACTION_ADAPTER = TypeAdapter(AgentAction)
        _OTHER_ADAPTER = TypeAdapter(dict)

        class AgentLoop:
            def run(self, responses):
                for response in responses:
                    try:
                        action = _ACTION_ADAPTER.validate_python(response.payload)
                    except ValidationError:
                        continue
                    if isinstance(action, FinalAction):
                        return action
                return None
        """
    ),
    "src/policy_impact/runtime/gates.py": dedent(
        """\
        class GateRegistry:
            def _evaluate(self, payload):
                return payload

        class GateRunner:
            pass
        """
    ),
    "src/policy_impact/harness/gate_engine.py": dedent(
        """\
        from policy_impact.harness.contracts import GateDecision

        class GateEngine:
            def __init__(self, runner):
                self.runner = runner
        """
    ),
    "src/policy_impact/harness/stage_gates.py": dedent(
        """\
        from policy_impact.harness.contracts import GateDecision

        def display_gate(decision: GateDecision):
            return decision
        """
    ),
}


def _write_linter_repository(tmp_path: Path, overrides: dict[str, str] | None = None) -> Path:
    for relative_path in REQUIRED_PATHS:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture\n", encoding="utf-8")
    for relative_path, source in {**_LINTER_BASELINE, **(overrides or {})}.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return tmp_path


def _issues_for_rule(root: Path, rule_id: str):
    return [issue for issue in lint_repository(root) if issue.rule_id == rule_id]


def test_harness_contracts_dump_plain_dicts():
    from policy_impact.harness.contracts import (
        ClaimReviewBundle,
        ContextManifestRecord,
        EvidenceSpan,
        GateDecision,
        ImpactClaim,
        ImpactTicket,
        ProfileFact,
        SourceSnapshot,
    )

    profile_fact = ProfileFact(
        key="revenue_segment",
        value="software_exports",
        source="company_profile",
        confidence=0.91,
    )
    source_snapshot = SourceSnapshot(
        source_id="policy_2026_001",
        title="Export Credit Notice",
        url="https://example.test/policy",
        captured_at="2026-06-27T00:00:00+00:00",
        metadata={"agency": "trade_office"},
    )
    evidence_span = EvidenceSpan(
        source_id="policy_2026_001",
        text="Eligible firms may apply for export credit support.",
        start_char=10,
        end_char=60,
        metadata={"section": "2"},
    )
    impact_claim = ImpactClaim(
        claim_id="claim_001",
        company_id="company_001",
        policy_id="policy_2026_001",
        summary="Company may qualify for export credit support.",
        impact_area="finance",
        severity="medium",
        evidence_spans=[evidence_span],
        metadata={"score": 0.78},
    )
    review_bundle = ClaimReviewBundle(
        bundle_id="bundle_001",
        claim=impact_claim,
        evidence_spans=[evidence_span],
        review_notes=["Verify application deadline."],
        metadata={"reviewer": "rule_gate"},
    )
    gate_decision = GateDecision(
        gate_name="evidence_minimum",
        passed=True,
        reasons=["one evidence span present"],
        metadata={"threshold": 1},
    )
    manifest_record = ContextManifestRecord(
        record_id="ctx_001",
        context_type="company_profile",
        source_id="company_001",
        checksum="abc123",
        metadata={"fields": 4},
    )
    impact_ticket = ImpactTicket(
        ticket_id="ticket_001",
        claim_id="claim_001",
        company_id="company_001",
        action="Prepare finance team review.",
        priority="p2",
        metadata={"owner": "finance"},
    )

    assert profile_fact.to_dict()["key"] == "revenue_segment"
    assert source_snapshot.to_dict()["metadata"]["agency"] == "trade_office"
    assert evidence_span.to_dict()["start_char"] == 10
    assert impact_claim.to_dict()["evidence_spans"][0]["source_id"] == "policy_2026_001"
    assert review_bundle.to_dict()["claim"]["claim_id"] == "claim_001"
    assert review_bundle.to_dict()["evidence_spans"][0]["text"].startswith("Eligible firms")
    assert gate_decision.to_dict()["passed"] is True
    assert manifest_record.to_dict()["checksum"] == "abc123"
    assert impact_ticket.to_dict()["priority"] == "p2"


def test_policy_impact_state_initializes_new_harness_surfaces():
    from policy_impact.harness.state import PolicyImpactState

    state = PolicyImpactState()

    assert state.context_manifests == []
    assert state.model_calls == []
    assert state.review_bundles == []
    assert state.impact_tickets == []
    assert state.checkpoints == []
    assert state.replay_reports == []
    assert state.eval_reports == []
    assert state.benchmark_reports == []


def test_policy_impact_state_append_helper_updates_to_dict():
    from policy_impact.harness.state import PolicyImpactState

    state = PolicyImpactState()

    state.add_context_manifest({"record_id": "ctx_001", "context_type": "company_profile"})

    assert state.context_manifests == [
        {"record_id": "ctx_001", "context_type": "company_profile"}
    ]
    assert state.to_dict()["context_manifests"] == [
        {"record_id": "ctx_001", "context_type": "company_profile"}
    ]


@pytest.mark.parametrize(
    ("rule_id", "relative_path", "source", "expected_line"),
    [
        (
            "HK001",
            "src/policy_impact/runtime/turn_coordinator.py",
            dedent(
                """\
                class TurnCoordinator:
                    def route(self, skill_id, logger):
                        logger.info("policy_weekly_impact")
                        display = {"skill_id": "general_chat"}
                        if skill_id == "general_chat":
                            return display
                        return None
                """
            ),
            5,
        ),
        (
            "HK001",
            "src/policy_impact/runtime/turn_coordinator.py",
            dedent(
                """\
                class TurnCoordinator:
                    def route(self, skill_id):
                        return "report" if skill_id == "research_report" else "chat"
                """
            ),
            3,
        ),
        (
            "HK001",
            "src/policy_impact/runtime/turn_coordinator.py",
            dedent(
                """\
                class TurnCoordinator:
                    def route(self, skill_id):
                        if is_skill(skill_id, "recent_news_report"):
                            return "news"
                        return "other"
                """
            ),
            3,
        ),
        (
            "HK001",
            "src/policy_impact/runtime/turn_coordinator.py",
            dedent(
                """\
                class TurnCoordinator:
                    def route(self, skill_id):
                        match skill_id:
                            case "company_wiki_blueprint":
                                return "wiki"
                            case _:
                                return "other"
                """
            ),
            3,
        ),
        (
            "HK002",
            "src/policy_impact/harness/agent_runtime.py",
            dedent(
                """\
                class AgentRuntime:
                    def emit(self):
                        metadata = {"loop": "subagent_driven"}
                        return metadata
                """
            ),
            3,
        ),
        (
            "HK002",
            "src/policy_impact/runtime/turn_coordinator.py",
            dedent(
                """\
                class TurnCoordinator:
                    def execute(self, executor, context):
                        result = executor.execute(context)
                        metadata = {}
                        metadata["execution_mode"] = "subagent_workflow"
                        return metadata
                """
            ),
            5,
        ),
        (
            "HK003",
            "src/policy_impact/skills/registry.py",
            dedent(
                """\
                _DEFAULT_MANIFESTS = (
                    SkillManifest(id="general_chat", executor_id="general_chat"),
                    SkillManifest(id="policy_weekly_impact", executor_id="policy_weekly_impact"),
                    SkillManifest(id="recent_news_report", executor_id="recent_news_report"),
                    SkillManifest(id="research_report", executor_id="research_report"),
                    SkillManifest(id="company_wiki_blueprint", executor_id="company_wiki_blueprint"),
                    SkillManifest(id="orphan", executor_id="orphan"),
                )
                """
            ),
            7,
        ),
        (
            "HK003",
            "src/policy_impact/harness/agent_runtime.py",
            dedent(
                """\
                class AgentRuntime:
                    def _build_executor_registry(self):
                        registry = ExecutorRegistry()
                        registry.register(resolve_executor_id(), object())
                        return registry
                """
            ),
            4,
        ),
        (
            "HK004",
            "src/policy_impact/harness/agent_loop.py",
            dedent(
                """\
                class AgentLoop:
                    def run(self, response):
                        action = response.payload
                        if action["type"] == "final":
                            return action
                        return None
                """
            ),
            4,
        ),
        (
            "HK004",
            "src/policy_impact/harness/agent_loop.py",
            dedent(
                """\
                _ACTION_ADAPTER = TypeAdapter(AgentAction)

                class AgentLoop:
                    def run(self, response, log):
                        try:
                            action = _ACTION_ADAPTER.validate_python(response.payload)
                        except ValidationError:
                            log("invalid")
                        if isinstance(action, FinalAction):
                            return action
                        return None
                """
            ),
            7,
        ),
        (
            "HK004",
            "src/policy_impact/harness/agent_loop.py",
            dedent(
                """\
                _ACTION_ADAPTER = TypeAdapter(dict)

                class AgentLoop:
                    def run(self, responses):
                        for response in responses:
                            try:
                                action = _ACTION_ADAPTER.validate_python(response.payload)
                            except ValidationError:
                                continue
                            if isinstance(action, FinalAction):
                                return action
                        return None
                """
            ),
            1,
        ),
        (
            "HK004",
            "src/policy_impact/harness/agent_loop.py",
            dedent(
                """\
                _ACTION_ADAPTER = TypeAdapter(AgentAction)

                class AgentLoop:
                    def run(self, response):
                        action = {}
                        if isinstance(action, FinalAction):
                            return action
                        try:
                            action = _ACTION_ADAPTER.validate_python(response.payload)
                        except ValidationError:
                            return None
                        return action
                """
            ),
            6,
        ),
        (
            "HK005",
            "src/policy_impact/harness/stage_gates.py",
            dedent(
                """\
                def bypass_gate(registry, payload):
                    return registry._evaluate(payload)
                """
            ),
            2,
        ),
        (
            "HK006",
            "src/policy_impact/harness/gate_engine.py",
            dedent(
                """\
                class GateEngine:
                    def __init__(self):
                        self.registry = GateRegistry()
                """
            ),
            3,
        ),
        (
            "HK006",
            "src/policy_impact/harness/stage_gates.py",
            dedent(
                """\
                from typing import Literal
                GateDecision = Literal["pass", "block", "repair"]
                """
            ),
            2,
        ),
        (
            "HK006",
            "src/policy_impact/harness/stage_gates.py",
            dedent(
                """\
                from enum import Enum

                class GateDecision(Enum):
                    PASS = "pass"
                    BLOCK = "block"
                """
            ),
            3,
        ),
        (
            "HK006",
            "src/policy_impact/harness/gate_engine.py",
            dedent(
                """\
                from policy_impact.runtime.gates import GateRegistry as RuntimeGateRegistry

                class GateEngine:
                    def __init__(self):
                        self.registry = RuntimeGateRegistry()
                """
            ),
            5,
        ),
        (
            "HK007",
            "src/policy_impact/skill_executors/policy_weekly_impact.py",
            dedent(
                """\
                from policy_impact.runtime.gates import GateRunner

                def build(registry):
                    return GateRunner(registry)
                """
            ),
            4,
        ),
        (
            "HK007",
            "src/policy_impact/skill_executors/policy_weekly_impact.py",
            dedent(
                """\
                from policy_impact.runtime.gates import GateRunner

                def build(parts):
                    return GateRunner(*parts)
                """
            ),
            4,
        ),
        (
            "HK007",
            "src/policy_impact/skill_executors/policy_weekly_impact.py",
            dedent(
                """\
                from policy_impact.runtime.gates import GateRunner

                def build(registry, options):
                    return GateRunner(registry, **options)
                """
            ),
            4,
        ),
        (
            "HK008",
            "src/policy_impact/skill_executors/policy_weekly_impact.py",
            dedent(
                """\
                from policy_impact.harness.gate_engine import GateEngine

                def build():
                    return GateEngine()
                """
            ),
            4,
        ),
        (
            "HK009",
            "src/policy_impact/conversation/main_agent.py",
            'MODEL_ID = "gpt-4.1-mini"\n',
            1,
        ),
        (
            "HK009",
            "src/policy_impact/conversation/main_agent.py",
            'POLICY_IMPACT_LLM_MODEL = "deepseek-v4-pro"\n',
            1,
        ),
        (
            "HK001",
            "src/policy_impact/runtime/turn_coordinator.py",
            dedent(
                """\
                GENERAL_CHAT = "general_chat"

                class TurnCoordinator:
                    def route(self, skill_id):
                        if skill_id == GENERAL_CHAT:
                            return "chat"
                        return "other"
                """
            ),
            5,
        ),
        (
            "HK002",
            "src/policy_impact/runtime/turn_coordinator.py",
            dedent(
                """\
                class TurnCoordinator:
                    def execute(self, context):
                        metadata = {}
                        metadata["execution_mode"] = result.actual_execution_mode.value
                        result = self.executor_registry.get("general_chat").execute(context)
                        return metadata
                """
            ),
            4,
        ),
        (
            "HK002",
            "src/policy_impact/runtime/turn_coordinator.py",
            dedent(
                """\
                class TurnCoordinator:
                    def execute(self, executor, context):
                        result = executor.execute(context)
                        metadata = {"execution_mode": result.actual_execution_mode.value}
                        return metadata
                """
            ),
            4,
        ),
        (
            "HK003",
            "src/policy_impact/harness/agent_runtime.py",
            dedent(
                """\
                class AgentRuntime:
                    def _build_executor_registry(self):
                        registry = ExecutorRegistry()
                        if False:
                            registry.register("general_chat", object())
                        return registry
                """
            ),
            5,
        ),
        (
            "HK004",
            "src/policy_impact/harness/agent_loop.py",
            dedent(
                """\
                _ACTION_ADAPTER = TypeAdapter(AgentAction)

                class AgentLoop:
                    def run(self, response, should_validate):
                        if should_validate:
                            try:
                                action = _ACTION_ADAPTER.validate_python(response.payload)
                            except ValidationError:
                                return None
                        if isinstance(action, FinalAction):
                            return action
                        return None
                """
            ),
            10,
        ),
        (
            "HK005",
            "src/policy_impact/harness/stage_gates.py",
            dedent(
                """\
                def bypass_gate(registry, payload):
                    evaluator = registry._evaluate
                    return evaluator(payload)
                """
            ),
            2,
        ),
        (
            "HK006",
            "src/policy_impact/harness/stage_gates.py",
            dedent(
                """\
                from enum import Enum
                GateDecision = Enum("GateDecision", {"PASS": "pass", "BLOCK": "block"})
                """
            ),
            2,
        ),
        (
            "HK007",
            "src/policy_impact/skill_executors/policy_weekly_impact.py",
            dedent(
                """\
                from policy_impact.runtime.gates import GateRunner

                def build(registry):
                    return GateRunner(registry, None)
                """
            ),
            4,
        ),
        (
            "HK008",
            "src/policy_impact/skill_executors/policy_weekly_impact.py",
            dedent(
                """\
                from policy_impact.harness.gate_engine import GateEngine

                def build():
                    return GateEngine(runner=None)
                """
            ),
            4,
        ),
        (
            "HK008",
            "src/policy_impact/skill_executors/policy_weekly_impact.py",
            dedent(
                """\
                from policy_impact.harness.gate_engine import GateEngine

                def build(parts):
                    return GateEngine(*parts)
                """
            ),
            4,
        ),
        (
            "HK009",
            "src/policy_impact/conversation/main_agent.py",
            'MODEL_ID = "gpt-4.1" + "-mini"\n',
            1,
        ),
        (
            "HK001",
            "src/policy_impact/runtime/turn_coordinator.py",
            dedent(
                """\
                class TurnCoordinator:
                    def route(self, skill_id):
                        selected = skill_id
                        if selected == "general_chat":
                            return "chat"
                        return "other"
                """
            ),
            4,
        ),
        (
            "HK002",
            "src/policy_impact/runtime/turn_coordinator.py",
            dedent(
                """\
                class TurnCoordinator:
                    def execute(self, context, fallback, enabled):
                        result = fallback
                        if enabled:
                            result = self.executor_registry.get("general_chat").execute(context)
                        metadata = {"execution_mode": result.actual_execution_mode.value}
                        return metadata
                """
            ),
            6,
        ),
        (
            "HK003",
            "src/policy_impact/harness/agent_runtime.py",
            dedent(
                """\
                class AgentRuntime:
                    def _build_executor_registry(self):
                        registry = ExecutorRegistry()
                        return registry
                        registry.register("general_chat", object())
                """
            ),
            5,
        ),
        (
            "HK004",
            "src/policy_impact/harness/agent_loop.py",
            dedent(
                """\
                _ACTION_ADAPTER = TypeAdapter(AgentAction)

                class AgentLoop:
                    def run(self, response):
                        if is_final(response.payload):
                            return response.payload
                        try:
                            action = _ACTION_ADAPTER.validate_python(response.payload)
                        except ValidationError:
                            return None
                        return action
                """
            ),
            5,
        ),
        (
            "HK006",
            "src/policy_impact/harness/stage_gates.py",
            dedent(
                """\
                from policy_impact.runtime.gates import GateRegistry
                RegistryFactory = GateRegistry
                def build():
                    return RegistryFactory()
                """
            ),
            4,
        ),
        (
            "HK007",
            "src/policy_impact/skill_executors/policy_weekly_impact.py",
            dedent(
                """\
                from policy_impact.runtime.gates import GateRunner

                def build(registry):
                    sink = None
                    return GateRunner(registry, sink)
                """
            ),
            5,
        ),
        (
            "HK008",
            "src/policy_impact/skill_executors/policy_weekly_impact.py",
            dedent(
                """\
                from policy_impact.harness.gate_engine import GateEngine

                def build():
                    runner = None
                    return GateEngine(runner=runner)
                """
            ),
            5,
        ),
    ],
    ids=[
        "HK001-if",
        "HK001-if-exp",
        "HK001-match",
        "HK001-skill-predicate",
        "HK002-fake-loop",
        "HK002-untrusted-mode",
        "HK003-missing-executor",
        "HK003-unprovable-registration",
        "HK004-raw-payload-branch",
        "HK004-validation-handler-falls-through",
        "HK004-wrong-adapter",
        "HK004-action-used-before-validation",
        "HK005-private-evaluate",
        "HK006-private-registry",
        "HK006-decision-literal",
        "HK006-decision-enum",
        "HK006-private-registry-alias",
        "HK007-missing-sink",
        "HK007-star-args",
        "HK007-star-kwargs",
        "HK008-parameterless-engine",
        "HK009-legacy-model-literal",
        "HK009-legacy-env-name",
        "HK001-skill-id-alias",
        "HK002-mode-write-before-result",
        "HK002-mode-from-foreign-executor",
        "HK003-conditional-registration",
        "HK004-conditional-validation",
        "HK005-private-evaluate-alias",
        "HK006-functional-decision-enum",
        "HK007-none-sink",
        "HK008-none-runner",
        "HK008-starred-runner",
        "HK009-concatenated-model-literal",
        "HK001-skill-route-data-alias",
        "HK002-untrusted-reaching-definition",
        "HK003-registration-after-return",
        "HK004-payload-helper-branch",
        "HK006-gate-registry-constructor-alias",
        "HK007-none-sink-alias",
        "HK008-none-runner-alias",
    ],
)
def test_harness_linter_reports_precise_ast_violations(
    tmp_path: Path,
    rule_id: str,
    relative_path: str,
    source: str,
    expected_line: int,
):
    root = _write_linter_repository(tmp_path, {relative_path: source})

    issues = _issues_for_rule(root, rule_id)

    assert any(
        issue.path == relative_path and issue.line == expected_line
        for issue in issues
    ), issues


@pytest.mark.parametrize(
    ("rule_id", "overrides"),
    [
        (
            "HK001",
            {
                "src/policy_impact/runtime/turn_coordinator.py": dedent(
                    """\
                    class TurnCoordinator:
                        def display(self, output_format):
                            if output_format == "general_chat":
                                return "compact"
                            return "default"
                    """
                )
            },
        ),
        (
            "HK002",
            {
                "src/policy_impact/runtime/turn_coordinator.py": dedent(
                    """\
                    class TurnCoordinator:
                        def execute(self, context):
                            result = self.executor_registry.get("general_chat").execute(context)
                            metadata = {}
                            metadata["execution_mode"] = result.actual_execution_mode.value
                            return metadata
                    """
                )
            },
        ),
        ("HK003", {}),
        ("HK004", {}),
        (
            "HK005",
            {
                "src/policy_impact/runtime/gates.py": dedent(
                    """\
                    class GateRegistry:
                        def _evaluate(self, payload):
                            return payload

                    def evaluate_inside_authority(registry, payload):
                        return registry._evaluate(payload)
                    """
                )
            },
        ),
        ("HK006", {}),
        (
            "HK007",
            {
                "src/policy_impact/skill_executors/policy_weekly_impact.py": dedent(
                    """\
                    from policy_impact.runtime.gates import GateRunner

                    def build(registry, sink):
                        positional = GateRunner(registry, sink)
                        keyword = GateRunner(registry=registry, sink=sink)
                        return positional, keyword
                    """
                )
            },
        ),
        (
            "HK008",
            {
                "src/policy_impact/skill_executors/policy_weekly_impact.py": dedent(
                    """\
                    from policy_impact.harness.gate_engine import GateEngine

                    def build(runner):
                        return GateEngine(runner=runner)
                    """
                )
            },
        ),
        (
            "HK009",
            {
                "src/policy_impact/conversation/main_agent.py": dedent(
                    '''\
                    """Migration notes: gpt-4.1-mini and POLICY_IMPACT_LLM_MODEL were removed."""
                    # POLICY_IMPACT_LLM_API_KEY is intentionally documented as legacy.
                    MODEL_ID = "deepseek-v4-pro"
                    '''
                )
            },
        ),
    ],
    ids=[
        f"{rule_id}-legal"
        for rule_id in ("HK001", "HK002", "HK003", "HK004", "HK005", "HK006", "HK007", "HK008", "HK009")
    ],
)
def test_harness_linter_accepts_required_legal_controls(
    tmp_path: Path,
    rule_id: str,
    overrides: dict[str, str],
):
    root = _write_linter_repository(tmp_path, overrides)

    assert _issues_for_rule(root, rule_id) == []


def test_harness_linter_cli_accepts_root_and_returns_nonzero(tmp_path: Path):
    root = _write_linter_repository(
        tmp_path,
        {
            "src/policy_impact/skill_executors/policy_weekly_impact.py": dedent(
                """\
                from policy_impact.harness.gate_engine import GateEngine

                def build():
                    return GateEngine()
                """
            )
        },
    )
    script = Path(__file__).resolve().parents[1] / "scripts" / "harness_linter.py"

    completed = subprocess.run(
        [sys.executable, str(script), "--root", str(root)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 1
    assert "HK008 src/policy_impact/skill_executors/policy_weekly_impact.py:4:" in completed.stdout


def test_harness_linter_fails_closed_on_python_syntax_error(tmp_path: Path):
    root = _write_linter_repository(
        tmp_path,
        {"src/policy_impact/harness/context_router.py": "def broken(:\n"},
    )

    issues = lint_repository(root)

    assert any(
        issue.rule_id == "HK000"
        and issue.path == "src/policy_impact/harness/context_router.py"
        and issue.line == 1
        for issue in issues
    )


def test_harness_linter_fails_closed_on_python_encoding_error(tmp_path: Path):
    root = _write_linter_repository(tmp_path)
    invalid_source = root / "src/policy_impact/harness/context_router.py"
    invalid_source.write_bytes(b"\xff\xfe\xfa")

    issues = lint_repository(root)

    assert any(
        issue.rule_id == "HK000"
        and issue.path == "src/policy_impact/harness/context_router.py"
        and issue.line == 1
        for issue in issues
    )


def test_real_repository_passes_harness_linter():
    repository_root = Path(__file__).resolve().parents[1]

    assert lint_repository(repository_root) == ()
