"""Fail-closed static harness linter for the policy_impact repository."""

from __future__ import annotations

import argparse
import ast
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_PATHS = [
    "readme.md",
    "src/policy_impact/schemas.py",
    "src/policy_impact/harness/graph.py",
    "src/policy_impact/harness/state.py",
    "src/policy_impact/harness/stage_gates.py",
    "src/policy_impact/harness/tool_gateway.py",
    "src/policy_impact/harness/context_router.py",
    "src/policy_impact/harness/artifacts.py",
    "src/policy_impact/harness/contracts.py",
    "src/policy_impact/harness/context_manifest.py",
    "src/policy_impact/harness/permissions.py",
    "src/policy_impact/harness/model_gateway.py",
    "src/policy_impact/harness/agent_loop.py",
    "src/policy_impact/harness/agent_runtime.py",
    "src/policy_impact/harness/skills.py",
    "src/policy_impact/harness/subagents.py",
    "src/policy_impact/harness/gate_engine.py",
    "src/policy_impact/harness/checkpoints.py",
    "src/policy_impact/harness/replay.py",
    "src/policy_impact/harness/hooks/stage_hooks.py",
    "src/policy_impact/eval/runner.py",
    "src/policy_impact/benchmark/runner.py",
    "src/policy_impact/linters/common.py",
    "src/policy_impact/company_wiki/loader.py",
    "src/policy_impact/company_wiki/guide.py",
    "src/policy_impact/company_wiki/updater.py",
    "src/policy_impact/company_wiki/indexer.py",
    "src/policy_impact/memory/store.py",
    "src/policy_impact/mcp_server/registry.py",
    "src/policy_impact/rag/retriever.py",
    "src/policy_impact/rag/sentence_compressor.py",
    "src/policy_impact/policy_data/repository.py",
    "src/policy_impact/policy_data/clause_extract.py",
    "src/policy_impact/stages/load_company_context.py",
    "src/policy_impact/stages/fetch_recent_policies.py",
    "src/policy_impact/stages/policy_ingest_and_index.py",
    "src/policy_impact/stages/retrieve_relevant_clauses.py",
    "src/policy_impact/stages/extract_policy_clauses.py",
    "src/policy_impact/stages/match_company_policy.py",
    "src/policy_impact/stages/analyze_policy_applicability.py",
    "src/policy_impact/stages/score_policy_impact.py",
    "src/policy_impact/stages/review_evidence_and_risk.py",
    "src/policy_impact/stages/generate_weekly_report.py",
    "src/policy_impact/skill_executors/policy_weekly_impact.py",
    "src/policy_impact/skill_executors/recent_news_report.py",
    "src/policy_impact/app/api.py",
    "src/policy_impact/app/chat_workbench_service.py",
    "src/policy_impact/app/service.py",
    "src/policy_impact/app/streamlit_app.py",
    "src/policy_impact/conversation/context_manager.py",
    "src/policy_impact/conversation/main_agent.py",
    "src/policy_impact/conversation/report_chat.py",
    "skills/policy_weekly_impact/SKILL.md",
    "skills/recent_news_report/SKILL.md",
    "scripts/run_eval.py",
    "scripts/run_benchmark.py",
    "scripts/run_replay.py",
    "docs/runtime/global_rules.md",
    "docs/runtime/final_output_format.md",
    "prompts/agents/policy_match_agent.md",
    "prompts/agents/policy_applicability_agent.md",
    "prompts/agents/report_agent.md",
    "plugins/skills/manifest.json",
    "plugins/subagents/roles.json",
    "plugins/subagents/manifests.json",
    "plugins/tools/registry.json",
    "plugins/prompts/general_chat_system.md",
    "plugins/prompts/conversation_summary_system.md",
    "plugins/prompts/research_report_system.md",
    "config/sources.json",
    "data/companies/company_001/company.yaml",
    "tests/test_agent_runtime.py",
    "tests/test_chat_workbench_service.py",
    "tests/test_chat_workbench_store.py",
    "tests/test_skill_registry.py",
]

FORBIDDEN_RUNTIME_TOKENS = (
    "POLICY_IMPACT_LLM_ENABLED",
    "POLICY_IMPACT_LLM_API_KEY",
    "POLICY_IMPACT_LLM_MODEL",
    "gpt-4.1-mini",
)

_PRODUCTION_PREFIX = "src/policy_impact/"
_COMPATIBILITY_GATE_PATHS = frozenset(
    {
        "src/policy_impact/harness/gate_engine.py",
        "src/policy_impact/harness/stage_gates.py",
    }
)
_NON_ROUTING_CONDITION_CALLS = frozenset({"_persist_model_call"})


@dataclass(frozen=True, order=True)
class LintIssue:
    rule_id: str
    path: str
    line: int
    column: int
    message: str

    def render(self) -> str:
        return f"{self.rule_id} {self.path}:{self.line}:{self.column}: {self.message}"


@dataclass(frozen=True)
class SourceFile:
    path: Path
    relative_path: str
    source: str
    tree: ast.Module


class SourceIndex:
    """UTF-8 source snapshot parsed without importing project modules."""

    def __init__(self, root: Path, files: Iterable[SourceFile]) -> None:
        self.root = root
        ordered = tuple(sorted(files, key=lambda item: item.relative_path))
        self.files = ordered
        self._by_path = {item.relative_path: item for item in ordered}

    @classmethod
    def build(cls, root: Path) -> tuple["SourceIndex", tuple[LintIssue, ...]]:
        root = root.resolve()
        files: list[SourceFile] = []
        issues: list[LintIssue] = []
        candidates: set[Path] = set()
        for directory_name in ("src", "scripts", "tests"):
            directory = root / directory_name
            if directory.is_dir():
                candidates.update(directory.rglob("*.py"))
        for path in sorted(candidates):
            relative_path = path.relative_to(root).as_posix()
            try:
                source = path.read_bytes().decode("utf-8-sig")
            except (OSError, UnicodeDecodeError) as exc:
                issues.append(
                    LintIssue(
                        "HK000",
                        relative_path,
                        1,
                        1,
                        f"Python source is not readable UTF-8: {type(exc).__name__}",
                    )
                )
                continue
            try:
                tree = ast.parse(source, filename=relative_path)
            except SyntaxError as exc:
                issues.append(
                    LintIssue(
                        "HK000",
                        relative_path,
                        int(exc.lineno or 1),
                        int(exc.offset or 1),
                        f"Python syntax error: {exc.msg}",
                    )
                )
                continue
            files.append(SourceFile(path, relative_path, source, tree))
        return cls(root, files), tuple(issues)

    def get(self, relative_path: str) -> SourceFile | None:
        return self._by_path.get(Path(relative_path).as_posix())

    def production_files(self) -> tuple[SourceFile, ...]:
        return tuple(
            source_file
            for source_file in self.files
            if source_file.relative_path.startswith(_PRODUCTION_PREFIX)
        )


@dataclass(frozen=True)
class _ManifestRecord:
    skill_id: str
    executor_id: str
    source_file: SourceFile
    node: ast.AST


def _node_issue(rule_id: str, source_file: SourceFile, node: ast.AST, message: str) -> LintIssue:
    return LintIssue(
        rule_id,
        source_file.relative_path,
        int(getattr(node, "lineno", 1)),
        int(getattr(node, "col_offset", 0)) + 1,
        message,
    )


def _expression_name(node: ast.AST | None) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _expression_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _tail_name(node: ast.AST | None) -> str:
    name = _expression_name(node)
    return name.rsplit(".", 1)[-1] if name else ""


def _constant_string(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _constant_string_expression(node: ast.AST | None) -> str | None:
    literal = _constant_string(node)
    if literal is not None:
        return literal
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _constant_string_expression(node.left)
        right = _constant_string_expression(node.right)
        if left is not None and right is not None:
            return left + right
    if isinstance(node, ast.JoinedStr):
        parts = [_constant_string_expression(value) for value in node.values]
        if all(part is not None for part in parts):
            return "".join(part or "" for part in parts)
    return None


def _subscript_key(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Subscript):
        return None
    return _constant_string(node.slice)


def _class_definitions(source_file: SourceFile, class_name: str) -> tuple[ast.ClassDef, ...]:
    return tuple(
        node
        for node in ast.walk(source_file.tree)
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )


def _assignment_targets(node: ast.Assign | ast.AnnAssign) -> tuple[str, ...]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    names: list[str] = []
    for target in targets:
        if isinstance(target, ast.Name):
            names.append(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            names.extend(item.id for item in target.elts if isinstance(item, ast.Name))
    return tuple(names)


def _required_path_issues(root: Path) -> list[LintIssue]:
    issues: list[LintIssue] = []
    for relative_path in REQUIRED_PATHS:
        path = root / relative_path
        if not path.exists():
            issues.append(LintIssue("HK000", relative_path, 1, 1, "required path is missing"))
            continue
        if not path.is_file():
            continue
        try:
            content = path.read_bytes().decode("utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            issues.append(
                LintIssue(
                    "HK000",
                    relative_path,
                    1,
                    1,
                    f"required file is not readable UTF-8: {type(exc).__name__}",
                )
            )
            continue
        if not content.strip():
            issues.append(LintIssue("HK000", relative_path, 1, 1, "required file is empty"))
    return issues


def _docstring_constant_ids(tree: ast.Module) -> frozenset[int]:
    docstrings: set[int] = set()
    containers = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if not isinstance(node, containers) or not node.body:
            continue
        first_statement = node.body[0]
        if (
            isinstance(first_statement, ast.Expr)
            and isinstance(first_statement.value, ast.Constant)
            and isinstance(first_statement.value.value, str)
        ):
            docstrings.add(id(first_statement.value))
    return frozenset(docstrings)


def _legacy_token_issues(index: SourceIndex) -> list[LintIssue]:
    issues: list[LintIssue] = []
    forbidden = frozenset(FORBIDDEN_RUNTIME_TOKENS)
    for source_file in index.production_files():
        docstring_ids = _docstring_constant_ids(source_file.tree)
        for node in ast.walk(source_file.tree):
            token: str | None = None
            if isinstance(node, ast.Name) and node.id in forbidden:
                token = node.id
            elif not (isinstance(node, ast.Constant) and id(node) in docstring_ids):
                expression_value = _constant_string_expression(node)
                if expression_value in forbidden:
                    token = expression_value
            if token is not None:
                issues.append(
                    _node_issue(
                        "HK009",
                        source_file,
                        node,
                        f"forbidden legacy model path token: {token}",
                    )
                )
    return issues


def _extract_default_manifests(index: SourceIndex) -> tuple[tuple[_ManifestRecord, ...], list[LintIssue]]:
    relative_path = "src/policy_impact/skills/registry.py"
    source_file = index.get(relative_path)
    if source_file is None:
        return (), [LintIssue("HK003", relative_path, 1, 1, "default manifest source is unavailable")]
    assignment: ast.Assign | ast.AnnAssign | None = None
    for node in source_file.tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and "_DEFAULT_MANIFESTS" in _assignment_targets(node):
            assignment = node
            break
    if assignment is None:
        return _extract_plugin_manifests(source_file)
    value = assignment.value
    if not isinstance(value, (ast.Tuple, ast.List)):
        return _extract_plugin_manifests(source_file)

    records: list[_ManifestRecord] = []
    issues: list[LintIssue] = []
    seen_skill_ids: set[str] = set()
    for item in value.elts:
        if not isinstance(item, ast.Call) or _tail_name(item.func) != "SkillManifest":
            issues.append(_node_issue("HK003", source_file, item, "default manifest entry is not a SkillManifest call"))
            continue
        keywords = {keyword.arg: keyword.value for keyword in item.keywords if keyword.arg is not None}
        skill_id = _constant_string(keywords.get("id"))
        executor_id = _constant_string(keywords.get("executor_id"))
        if not skill_id or not executor_id:
            issues.append(
                _node_issue(
                    "HK003",
                    source_file,
                    item,
                    "default manifest id/executor_id cannot be proven as string literals",
                )
            )
            continue
        if skill_id in seen_skill_ids:
            issues.append(_node_issue("HK003", source_file, item, f"duplicate default manifest id: {skill_id}"))
            continue
        seen_skill_ids.add(skill_id)
        records.append(_ManifestRecord(skill_id, executor_id, source_file, item))
    if not records:
        issues.append(_node_issue("HK003", source_file, assignment, "no provable default manifests"))
    return tuple(records), issues


def _extract_plugin_manifests(source_file: SourceFile) -> tuple[tuple[_ManifestRecord, ...], list[LintIssue]]:
    path = ROOT / "plugins" / "skills" / "manifest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return (), [
            _node_issue(
                "HK003",
                source_file,
                source_file.tree,
                "plugins/skills/manifest.json is missing",
            )
        ]
    except json.JSONDecodeError as exc:
        return (), [
            _node_issue(
                "HK003",
                source_file,
                source_file.tree,
                f"plugins/skills/manifest.json is invalid JSON: {exc}",
            )
        ]
    if not isinstance(payload, list):
        return (), [
            _node_issue(
                "HK003",
                source_file,
                source_file.tree,
                "plugins/skills/manifest.json must contain a list",
            )
        ]
    records: list[_ManifestRecord] = []
    issues: list[LintIssue] = []
    seen_skill_ids: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            issues.append(
                _node_issue(
                    "HK003",
                    source_file,
                    source_file.tree,
                    "plugin manifest entry is not an object",
                )
            )
            continue
        skill_id = str(item.get("id") or "").strip()
        executor_id = str(item.get("executor_id") or "").strip()
        if not skill_id or not executor_id:
            issues.append(
                _node_issue(
                    "HK003",
                    source_file,
                    source_file.tree,
                    "plugin manifest id/executor_id cannot be proven as strings",
                )
            )
            continue
        if skill_id in seen_skill_ids:
            issues.append(
                _node_issue(
                    "HK003",
                    source_file,
                    source_file.tree,
                    f"duplicate plugin manifest id: {skill_id}",
                )
            )
            continue
        seen_skill_ids.add(skill_id)
        records.append(_ManifestRecord(skill_id, executor_id, source_file, source_file.tree))
    if not records:
        issues.append(
            _node_issue("HK003", source_file, source_file.tree, "no provable plugin manifests")
        )
    return tuple(records), issues


def _skill_id_aliases(source_file: SourceFile, skill_ids: frozenset[str]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(source_file.tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = _constant_string_expression(node.value)
        if value not in skill_ids:
            continue
        for target in _assignment_targets(node):
            aliases[target] = value
    return aliases


def _resolved_skill_id(
    node: ast.AST,
    skill_ids: frozenset[str],
    aliases: dict[str, str],
) -> str | None:
    literal = _constant_string_expression(node)
    if literal in skill_ids:
        return literal
    if isinstance(node, ast.Name):
        return aliases.get(node.id)
    return None


def _is_skill_route_expression(
    node: ast.AST,
    route_aliases: frozenset[str] = frozenset(),
) -> bool:
    if any(isinstance(item, ast.Name) and item.id in route_aliases for item in ast.walk(node)):
        return True
    name = _expression_name(node).lower()
    return bool(name) and any(token in name for token in ("skill", "route", "manifest"))


def _skill_route_aliases(class_node: ast.ClassDef) -> frozenset[str]:
    aliases: set[str] = set()
    assignments = [
        node
        for node in ast.walk(class_node)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
    ]
    changed = True
    while changed:
        changed = False
        for node in assignments:
            if not _is_skill_route_expression(node.value, frozenset(aliases)):
                continue
            for target in _assignment_targets(node):
                if target not in aliases:
                    aliases.add(target)
                    changed = True
    return frozenset(aliases)


def _compare_contains_default_skill_id(
    node: ast.AST,
    skill_ids: frozenset[str],
    aliases: dict[str, str],
    route_aliases: frozenset[str],
) -> bool:
    if isinstance(node, ast.Compare):
        operands = (node.left, *node.comparators)
        return any(_is_skill_route_expression(item, route_aliases) for item in operands) and any(
            _resolved_skill_id(item, skill_ids, aliases) is not None for item in operands
        )
    if isinstance(node, ast.Call):
        predicate_name = _expression_name(node.func).lower()
        if "skill" not in predicate_name and "route" not in predicate_name:
            return False
        return any(
            _resolved_skill_id(item, skill_ids, aliases) is not None
            for item in (*node.args, *(keyword.value for keyword in node.keywords))
        )
    if isinstance(node, (ast.BoolOp, ast.UnaryOp)):
        return any(
            _compare_contains_default_skill_id(child, skill_ids, aliases, route_aliases)
            for child in ast.iter_child_nodes(node)
        )
    return False


def _hk001_turn_coordinator_branches(
    index: SourceIndex,
    manifests: Sequence[_ManifestRecord],
) -> list[LintIssue]:
    source_file = index.get("src/policy_impact/runtime/turn_coordinator.py")
    if source_file is None:
        return [
            LintIssue(
                "HK001",
                "src/policy_impact/runtime/turn_coordinator.py",
                1,
                1,
                "TurnCoordinator source is unavailable",
            )
        ]
    skill_ids = frozenset(record.skill_id for record in manifests)
    aliases = _skill_id_aliases(source_file, skill_ids)
    issues: list[LintIssue] = []
    for class_node in _class_definitions(source_file, "TurnCoordinator"):
        route_aliases = _skill_route_aliases(class_node)
        for node in ast.walk(class_node):
            if isinstance(node, (ast.If, ast.IfExp)) and _compare_contains_default_skill_id(
                node.test,
                skill_ids,
                aliases,
                route_aliases,
            ):
                issues.append(
                    _node_issue(
                        "HK001",
                        source_file,
                        node.test,
                        "TurnCoordinator control flow branches on a default Skill ID",
                    )
                )
            elif isinstance(node, ast.Match) and _is_skill_route_expression(node.subject, route_aliases):
                matched_ids = {
                    item.value
                    for case in node.cases
                    for item in ast.walk(case.pattern)
                    if isinstance(item, ast.Constant) and item.value in skill_ids
                }
                if matched_ids:
                    issues.append(
                        _node_issue(
                            "HK001",
                            source_file,
                            node,
                            "TurnCoordinator match branches on default Skill IDs: "
                            + ", ".join(sorted(matched_ids)),
                        )
                    )
    return issues


def _is_subagent_loop_literal(node: ast.AST | None) -> bool:
    return _constant_string(node) == "subagent_driven"


def _hardcoded_loop_nodes(class_node: ast.ClassDef) -> tuple[ast.AST, ...]:
    nodes: list[ast.AST] = []
    for node in ast.walk(class_node):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if _constant_string(key) == "loop" and _is_subagent_loop_literal(value):
                    nodes.append(value)
        elif isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg == "loop" and _is_subagent_loop_literal(keyword.value):
                    nodes.append(keyword.value)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if _is_subagent_loop_literal(value) and any(
                _subscript_key(target) == "loop"
                or (isinstance(target, ast.Attribute) and target.attr == "loop")
                for target in targets
            ):
                nodes.append(value)
    return tuple(nodes)


def _attribute_chain(node: ast.AST) -> tuple[str, ...]:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return tuple(reversed(parts))
    return ()


def _is_executor_registry_execute_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr != "execute":
        return False
    return any(
        isinstance(item, ast.Attribute)
        and item.attr == "executor_registry"
        and isinstance(item.value, ast.Name)
        and item.value.id == "self"
        for item in ast.walk(node.func.value)
    )


def _trusted_result_definitions(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict[str, int]:
    definitions: dict[str, int] = {}
    for node in ast.walk(function):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        if not _is_executor_registry_execute_call(node.value):
            continue
        line = int(getattr(node, "lineno", 0))
        for name in _assignment_targets(node):
            definitions[name] = min(definitions.get(name, line), line)
    return definitions


def _condition_proves_non_none(node: ast.AST, name: str) -> bool:
    if isinstance(node, ast.Name):
        return node.id == name
    if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.IsNot):
        operands = (node.left, node.comparators[0])
        return any(isinstance(item, ast.Name) and item.id == name for item in operands) and any(
            isinstance(item, ast.Constant) and item.value is None for item in operands
        )
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And):
        return any(_condition_proves_non_none(value, name) for value in node.values)
    return False


def _write_is_non_none_guarded(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    location: ast.AST,
    name: str,
) -> bool:
    parents = _parent_map(function)
    current = location
    while current in parents:
        parent = parents[current]
        if (
            isinstance(parent, ast.If)
            and current in parent.body
            and _condition_proves_non_none(parent.test, name)
        ):
            return True
        current = parent
    return False


def _result_reaching_definitions_are_trusted(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
    *,
    location: ast.AST,
) -> bool:
    at_line = int(getattr(location, "lineno", 0))
    trusted_seen = False
    none_initializer_seen = False
    for node in ast.walk(function):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or name not in _assignment_targets(node):
            continue
        if int(getattr(node, "lineno", 0)) >= at_line:
            continue
        if _is_executor_registry_execute_call(node.value):
            trusted_seen = True
            continue
        if isinstance(node.value, ast.Constant) and node.value.value is None:
            none_initializer_seen = True
            continue
        return False
    if not trusted_seen:
        return False
    return not none_initializer_seen or _write_is_non_none_guarded(function, location, name)


def _is_actual_execution_mode(
    node: ast.AST,
    result_definitions: dict[str, int],
    *,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    location: ast.AST,
) -> bool:
    at_line = int(getattr(location, "lineno", 0))
    chain = _attribute_chain(node)
    return bool(
        len(chain) >= 2
        and chain[0] in result_definitions
        and result_definitions[chain[0]] < at_line
        and chain[1] == "actual_execution_mode"
        and (len(chain) == 2 or chain[2:] == ("value",))
        and _result_reaching_definitions_are_trusted(
            function,
            chain[0],
            location=location,
        )
    )


def _execution_mode_writes(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[tuple[ast.AST, ast.AST], ...]:
    writes: list[tuple[ast.AST, ast.AST]] = []
    for node in ast.walk(function):
        if isinstance(node, ast.Dict):
            writes.extend(
                (value, value)
                for key, value in zip(node.keys, node.values)
                if _constant_string(key) == "execution_mode"
            )
        elif isinstance(node, ast.Call):
            writes.extend(
                (keyword.value, keyword.value)
                for keyword in node.keywords
                if keyword.arg == "execution_mode"
            )
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(
                _subscript_key(target) == "execution_mode"
                or (isinstance(target, ast.Attribute) and target.attr == "execution_mode")
                for target in targets
            ):
                writes.append((node.value, node.value))
    return tuple(writes)


def _hk002_truthful_execution_mode(index: SourceIndex) -> list[LintIssue]:
    issues: list[LintIssue] = []
    runtime_file = index.get("src/policy_impact/harness/agent_runtime.py")
    if runtime_file is None:
        issues.append(
            LintIssue(
                "HK002",
                "src/policy_impact/harness/agent_runtime.py",
                1,
                1,
                "AgentRuntime source is unavailable",
            )
        )
    else:
        for class_node in _class_definitions(runtime_file, "AgentRuntime"):
            for node in _hardcoded_loop_nodes(class_node):
                issues.append(
                    _node_issue(
                        "HK002",
                        runtime_file,
                        node,
                        "AgentRuntime hardcodes loop=subagent_driven",
                    )
                )

    coordinator_file = index.get("src/policy_impact/runtime/turn_coordinator.py")
    if coordinator_file is None:
        issues.append(
            LintIssue(
                "HK002",
                "src/policy_impact/runtime/turn_coordinator.py",
                1,
                1,
                "actual ExecutionMode provenance cannot be proven",
            )
        )
        return issues
    coordinator_classes = _class_definitions(coordinator_file, "TurnCoordinator")
    proven_write = False
    untrusted_writes: list[ast.AST] = []
    for class_node in coordinator_classes:
        functions = (
            node
            for node in class_node.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        )
        for function in functions:
            result_definitions = _trusted_result_definitions(function)
            for value, location in _execution_mode_writes(function):
                if _is_actual_execution_mode(
                    value,
                    result_definitions,
                    function=function,
                    location=location,
                ):
                    proven_write = True
                else:
                    untrusted_writes.append(location)
    for node in untrusted_writes:
        issues.append(
            _node_issue(
                "HK002",
                coordinator_file,
                node,
                "execution_mode write does not originate from SkillResult.actual_execution_mode",
            )
        )
    if not proven_write:
        location: ast.AST = untrusted_writes[0] if untrusted_writes else (coordinator_classes[0] if coordinator_classes else coordinator_file.tree)
        issues.append(
            _node_issue(
                "HK002",
                coordinator_file,
                location,
                "no SkillResult.actual_execution_mode persistence chain is provable",
            )
        )
    return issues


def _reachable_direct_statements(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[ast.stmt, ...]:
    statements: list[ast.stmt] = []
    for statement in function.body:
        statements.append(statement)
        if isinstance(statement, (ast.Raise, ast.Return)):
            break
    return tuple(statements)


def _manifest_aliases(function: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in _reachable_direct_statements(function):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if not isinstance(value, ast.Call) or _tail_name(value.func) != "get_manifest" or not value.args:
            continue
        skill_id = _constant_string(value.args[0])
        if not skill_id:
            continue
        for target in _assignment_targets(node):
            aliases[target] = skill_id
    return aliases


def _executor_registry_variables(function: ast.FunctionDef | ast.AsyncFunctionDef) -> frozenset[str]:
    variables: set[str] = set()
    for node in _reachable_direct_statements(function):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        if isinstance(node.value, ast.Call) and _tail_name(node.value.func) == "ExecutorRegistry":
            variables.update(_assignment_targets(node))
    return frozenset(variables)


def _direct_statement_call(statement: ast.stmt) -> ast.Call | None:
    if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
        return statement.value
    if isinstance(statement, (ast.Assign, ast.AnnAssign)) and isinstance(statement.value, ast.Call):
        return statement.value
    return None


def _returns_registry_directly(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    registry_variables: frozenset[str],
) -> bool:
    return any(
        isinstance(statement, ast.Return)
        and isinstance(statement.value, ast.Name)
        and statement.value.id in registry_variables
        for statement in _reachable_direct_statements(function)
    )


def _resolve_registered_executor_id(
    node: ast.AST,
    manifest_aliases: dict[str, str],
    manifests_by_skill: dict[str, _ManifestRecord],
) -> str | None:
    literal = _constant_string(node)
    if literal:
        return literal
    if isinstance(node, ast.Attribute) and node.attr == "executor_id" and isinstance(node.value, ast.Name):
        skill_id = manifest_aliases.get(node.value.id)
        manifest = manifests_by_skill.get(skill_id or "")
        return manifest.executor_id if manifest is not None else None
    return None


def _hk003_executor_registration(
    index: SourceIndex,
    manifests: Sequence[_ManifestRecord],
) -> list[LintIssue]:
    source_file = index.get("src/policy_impact/harness/agent_runtime.py")
    if source_file is None:
        return [
            LintIssue(
                "HK003",
                "src/policy_impact/harness/agent_runtime.py",
                1,
                1,
                "ExecutorRegistry composition root is unavailable",
            )
        ]
    manifests_by_skill = {record.skill_id: record for record in manifests}
    registered_ids: set[str] = set()
    issues: list[LintIssue] = []
    composition_root_found = False
    for class_node in _class_definitions(source_file, "AgentRuntime"):
        for function in class_node.body:
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            registry_variables = _executor_registry_variables(function)
            if not registry_variables:
                continue
            if not _returns_registry_directly(function, registry_variables):
                issues.append(
                    _node_issue(
                        "HK003",
                        source_file,
                        function,
                        "ExecutorRegistry composition root does not directly return its registry",
                    )
                )
                continue
            composition_root_found = True
            aliases = _manifest_aliases(function)
            direct_registration_ids: set[int] = set()
            for statement in _reachable_direct_statements(function):
                node = _direct_statement_call(statement)
                if node is None or not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr != "register" or not isinstance(node.func.value, ast.Name):
                    continue
                if node.func.value.id not in registry_variables:
                    continue
                direct_registration_ids.add(id(node))
                argument = node.args[0] if node.args else next(
                    (keyword.value for keyword in node.keywords if keyword.arg == "executor_id"),
                    None,
                )
                if argument is None:
                    issues.append(_node_issue("HK003", source_file, node, "ExecutorRegistry.register has no provable executor ID"))
                    continue
                executor_id = _resolve_registered_executor_id(argument, aliases, manifests_by_skill)
                if executor_id is None:
                    issues.append(
                        _node_issue(
                            "HK003",
                            source_file,
                            node,
                            "ExecutorRegistry.register executor ID cannot be statically proven",
                        )
                    )
                    continue
                registered_ids.add(executor_id)
            for node in ast.walk(function):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "register"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in registry_variables
                    and id(node) not in direct_registration_ids
                ):
                    issues.append(
                        _node_issue(
                            "HK003",
                            source_file,
                            node,
                            "ExecutorRegistry registration is conditional, nested, or otherwise unreachable from the direct composition root",
                        )
                    )
    if not composition_root_found:
        class_nodes = _class_definitions(source_file, "AgentRuntime")
        location: ast.AST = class_nodes[0] if class_nodes else source_file.tree
        issues.append(_node_issue("HK003", source_file, location, "AgentRuntime has no provable ExecutorRegistry composition root"))
    for manifest in manifests:
        if manifest.executor_id not in registered_ids:
            issues.append(
                _node_issue(
                    "HK003",
                    manifest.source_file,
                    manifest.node,
                    f"default Skill {manifest.skill_id!r} has no registered executor {manifest.executor_id!r}",
                )
            )
    return issues


def _type_adapter_target(node: ast.Call) -> str:
    if _tail_name(node.func) != "TypeAdapter" or len(node.args) != 1:
        return ""
    return _tail_name(node.args[0])


def _agent_action_adapter_names(source_file: SourceFile) -> tuple[frozenset[str], tuple[ast.AST, ...]]:
    validation_receivers: dict[str, list[ast.Call]] = {}
    for class_node in _class_definitions(source_file, "AgentLoop"):
        for node in ast.walk(class_node):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "validate_python"
                and isinstance(node.func.value, ast.Name)
            ):
                validation_receivers.setdefault(node.func.value.id, []).append(node)

    valid_names: set[str] = set()
    declared_receivers: set[str] = set()
    invalid_nodes: list[ast.AST] = []
    for node in ast.walk(source_file.tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or not isinstance(node.value, ast.Call):
            continue
        assigned_names = frozenset(_assignment_targets(node))
        relevant_names = assigned_names.intersection(validation_receivers)
        if not relevant_names:
            continue
        declared_receivers.update(relevant_names)
        target = _type_adapter_target(node.value)
        if target == "AgentAction":
            valid_names.update(assigned_names)
        else:
            invalid_nodes.append(node.value)
    for receiver, calls in validation_receivers.items():
        if receiver not in valid_names and receiver not in declared_receivers:
            invalid_nodes.extend(calls)
    return frozenset(valid_names), tuple(invalid_nodes)


def _is_valid_action_validation_call(node: ast.AST, adapter_names: frozenset[str]) -> bool:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr != "validate_python":
        return False
    receiver = node.func.value
    if isinstance(receiver, ast.Name):
        return receiver.id in adapter_names
    return isinstance(receiver, ast.Call) and _type_adapter_target(receiver) == "AgentAction"


def _handler_catches_validation_error(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return False
    return any(
        isinstance(node, (ast.Name, ast.Attribute)) and _tail_name(node) == "ValidationError"
        for node in ast.walk(handler.type)
    )


def _block_guarantees_controlled_exit(statements: Sequence[ast.stmt]) -> bool:
    for statement in statements:
        if isinstance(statement, (ast.Continue, ast.Raise, ast.Return)):
            return True
        if isinstance(statement, ast.If):
            if statement.orelse and _block_guarantees_controlled_exit(statement.body) and _block_guarantees_controlled_exit(statement.orelse):
                return True
        if isinstance(statement, ast.Match) and statement.cases:
            has_wildcard = any(
                isinstance(case.pattern, ast.MatchAs) and case.pattern.pattern is None
                for case in statement.cases
            )
            if has_wildcard and all(_block_guarantees_controlled_exit(case.body) for case in statement.cases):
                return True
    return False


def _expression_contains_payload(node: ast.AST) -> bool:
    return any(isinstance(item, ast.Attribute) and item.attr == "payload" for item in ast.walk(node))


def _expression_derives_from_tainted_value(node: ast.AST, tainted_names: frozenset[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in tainted_names
    if isinstance(node, (ast.Attribute, ast.Subscript)):
        return _expression_derives_from_tainted_value(node.value, tainted_names)
    if isinstance(node, ast.Call):
        return _expression_derives_from_tainted_value(node.func, tainted_names)
    if isinstance(node, ast.Dict):
        return any(
            _expression_derives_from_tainted_value(item, tainted_names)
            for item in (*[key for key in node.keys if key is not None], *node.values)
        )
    if isinstance(node, (ast.List, ast.Tuple, ast.Set, ast.BoolOp)):
        values = node.elts if hasattr(node, "elts") else node.values
        return any(_expression_derives_from_tainted_value(item, tainted_names) for item in values)
    if isinstance(node, ast.UnaryOp):
        return _expression_derives_from_tainted_value(node.operand, tainted_names)
    if isinstance(node, ast.BinOp):
        return _expression_derives_from_tainted_value(node.left, tainted_names) or _expression_derives_from_tainted_value(
            node.right,
            tainted_names,
        )
    if isinstance(node, ast.IfExp):
        return any(
            _expression_derives_from_tainted_value(item, tainted_names)
            for item in (node.test, node.body, node.orelse)
        )
    return False


def _tainted_payload_names(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    adapter_names: frozenset[str],
) -> frozenset[str]:
    tainted: set[str] = set()
    assignments = [
        node
        for node in ast.walk(function)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
    ]
    changed = True
    while changed:
        changed = False
        for node in assignments:
            if _is_valid_action_validation_call(node.value, adapter_names):
                continue
            if not _expression_contains_payload(node.value) and not _expression_derives_from_tainted_value(
                node.value,
                frozenset(tainted),
            ):
                continue
            for target in _assignment_targets(node):
                if target not in tainted:
                    tainted.add(target)
                    changed = True
    return frozenset(tainted)


def _root_name(node: ast.AST) -> str:
    current = node
    while isinstance(current, (ast.Attribute, ast.Subscript)):
        current = current.value
    return current.id if isinstance(current, ast.Name) else ""


def _condition_uses_untrusted_value(node: ast.AST, tainted_names: frozenset[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in tainted_names
    if isinstance(node, ast.Attribute):
        return node.attr == "payload" or _root_name(node) in tainted_names
    if isinstance(node, ast.Subscript):
        return _condition_uses_untrusted_value(node.value, tainted_names)
    if isinstance(node, ast.Call):
        call_items: tuple[ast.AST, ...] = (node.func,)
        if _tail_name(node.func) not in _NON_ROUTING_CONDITION_CALLS:
            call_items = (
                node.func,
                *node.args,
                *(keyword.value for keyword in node.keywords),
            )
        return any(
            _condition_uses_untrusted_value(item, tainted_names)
            for item in call_items
        )
    if isinstance(node, ast.Compare):
        return any(
            _condition_uses_untrusted_value(item, tainted_names)
            for item in (node.left, *node.comparators)
        )
    if isinstance(node, ast.BoolOp):
        return any(_condition_uses_untrusted_value(item, tainted_names) for item in node.values)
    if isinstance(node, ast.UnaryOp):
        return _condition_uses_untrusted_value(node.operand, tainted_names)
    if isinstance(node, ast.BinOp):
        return _condition_uses_untrusted_value(node.left, tainted_names) or _condition_uses_untrusted_value(
            node.right,
            tainted_names,
        )
    if isinstance(node, ast.IfExp):
        return _condition_uses_untrusted_value(node.test, tainted_names)
    if isinstance(node, ast.NamedExpr):
        return _condition_uses_untrusted_value(node.value, tainted_names)
    return False


@dataclass(frozen=True)
class _ValidationRecord:
    target_name: str
    call: ast.Call
    guard: ast.Try


def _parent_map(root: ast.AST) -> dict[ast.AST, ast.AST]:
    return {
        child: parent
        for parent in ast.walk(root)
        for child in ast.iter_child_nodes(parent)
    }


def _nearest_guarding_try(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> ast.Try | None:
    current = node
    while current in parents:
        parent = parents[current]
        if isinstance(parent, ast.Try) and current in parent.body:
            return parent
        current = parent
    return None


def _action_object_names(condition: ast.AST) -> frozenset[str]:
    action_object_names: set[str] = set()
    for node in ast.walk(condition):
        if isinstance(node, ast.Attribute) and node.attr == "type" and isinstance(node.value, ast.Name):
            action_object_names.add(node.value.id)
        elif isinstance(node, ast.Call) and _tail_name(node.func) == "isinstance" and node.args:
            if isinstance(node.args[0], ast.Name) and any(
                isinstance(item, (ast.Name, ast.Attribute)) and _tail_name(item).endswith("Action")
                for item in ast.walk(node.args[1])
            ):
                action_object_names.add(node.args[0].id)
        elif isinstance(node, ast.Subscript) and _subscript_key(node) == "type" and isinstance(node.value, ast.Name):
            action_object_names.add(node.value.id)
    return frozenset(action_object_names)


def _control_ancestors(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
    *,
    ignored_try: ast.Try | None = None,
) -> frozenset[ast.AST]:
    controls = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Match, ast.Try, ast.ExceptHandler)
    ancestors: set[ast.AST] = set()
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, controls) and current is not ignored_try:
            ancestors.add(current)
    return frozenset(ancestors)


def _guard_section_allows_validated_value(
    condition: ast.AST,
    guard: ast.Try,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    current = condition
    while current in parents:
        parent = parents[current]
        if parent is guard:
            return current in guard.body or current in guard.orelse
        current = parent
    return True


def _name_is_reassigned_between(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    record: _ValidationRecord,
    condition_line: int,
) -> bool:
    validation_line = int(getattr(record.call, "lineno", 0))
    for node in ast.walk(function):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        line = int(getattr(node, "lineno", 0))
        if not (validation_line < line < condition_line):
            continue
        if record.target_name in _assignment_targets(node):
            return True
    return False


def _validation_dominates_condition(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    record: _ValidationRecord,
    condition: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    condition_line = int(getattr(condition, "lineno", 0))
    if int(getattr(record.call, "lineno", 0)) >= condition_line:
        return False
    if not _guard_section_allows_validated_value(condition, record.guard, parents):
        return False
    validation_controls = _control_ancestors(record.call, parents, ignored_try=record.guard)
    condition_controls = _control_ancestors(condition, parents)
    if not validation_controls.issubset(condition_controls):
        return False
    return not _name_is_reassigned_between(function, record, condition_line)


def _branch_uses_unvalidated_action(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    condition: ast.AST,
    validation_records: Sequence[_ValidationRecord],
    tainted_names: frozenset[str],
    parents: dict[ast.AST, ast.AST],
) -> bool:
    if _condition_uses_untrusted_value(condition, tainted_names):
        return True
    for action_name in _action_object_names(condition):
        if not any(
            record.target_name == action_name
            and _validation_dominates_condition(function, record, condition, parents)
            for record in validation_records
        ):
            return True
    return False


def _hk004_agent_action_validation(index: SourceIndex) -> list[LintIssue]:
    source_file = index.get("src/policy_impact/harness/agent_loop.py")
    if source_file is None:
        return [
            LintIssue(
                "HK004",
                "src/policy_impact/harness/agent_loop.py",
                1,
                1,
                "AgentLoop source is unavailable",
            )
        ]
    adapter_names, invalid_adapter_nodes = _agent_action_adapter_names(source_file)
    issues = [
        _node_issue("HK004", source_file, node, "Agent Action adapter is not TypeAdapter(AgentAction)")
        for node in invalid_adapter_nodes
    ]
    loop_classes = _class_definitions(source_file, "AgentLoop")
    valid_validation_seen = False
    for class_node in loop_classes:
        for function in class_node.body:
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            parents = _parent_map(function)
            validation_records: list[_ValidationRecord] = []
            validation_assignment_calls: set[int] = set()
            for node in ast.walk(function):
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                if not _is_valid_action_validation_call(node.value, adapter_names):
                    continue
                call = node.value
                validation_assignment_calls.add(id(call))
                try_node = _nearest_guarding_try(call, parents)
                if try_node is None:
                    issues.append(
                        _node_issue(
                            "HK004",
                            source_file,
                            call,
                            "AgentAction validation is not guarded by ValidationError",
                        )
                    )
                    continue
                matching_handlers = [
                    handler
                    for handler in try_node.handlers
                    if _handler_catches_validation_error(handler)
                ]
                if not matching_handlers:
                    issues.append(
                        _node_issue(
                            "HK004",
                            source_file,
                            call,
                            "AgentAction validation is not guarded by ValidationError",
                        )
                    )
                    continue
                unsafe_handlers = [
                    handler
                    for handler in try_node.handlers
                    if not _block_guarantees_controlled_exit(handler.body)
                ]
                if unsafe_handlers:
                    issues.extend(
                        _node_issue(
                            "HK004",
                            source_file,
                            handler,
                            "validation exception handler can fall through before Action use",
                        )
                        for handler in unsafe_handlers
                    )
                    continue
                for target in _assignment_targets(node):
                    validation_records.append(_ValidationRecord(target, call, try_node))
                valid_validation_seen = True
            for node in ast.walk(function):
                if isinstance(node, ast.Call) and _is_valid_action_validation_call(node, adapter_names):
                    if id(node) not in validation_assignment_calls:
                        issues.append(
                            _node_issue(
                                "HK004",
                                source_file,
                                node,
                                "AgentAction validation lacks controlled ValidationError flow",
                            )
                        )
            tainted_names = _tainted_payload_names(function, adapter_names)
            for node in ast.walk(function):
                condition: ast.AST | None = None
                if isinstance(node, (ast.If, ast.IfExp)):
                    condition = node.test
                elif isinstance(node, ast.Match):
                    condition = node.subject
                if condition is not None and _branch_uses_unvalidated_action(
                    function,
                    condition,
                    validation_records,
                    tainted_names,
                    parents,
                ):
                    issues.append(
                        _node_issue(
                            "HK004",
                            source_file,
                            condition,
                            "AgentLoop branches on an unvalidated model Action payload",
                        )
                    )
    if not valid_validation_seen:
        location: ast.AST = invalid_adapter_nodes[0] if invalid_adapter_nodes else (loop_classes[0] if loop_classes else source_file.tree)
        issues.append(
            _node_issue(
                "HK004",
                source_file,
                location,
                "no controlled TypeAdapter(AgentAction).validate_python path is provable",
            )
        )
    return issues


def _hk005_private_gate_evaluation(index: SourceIndex) -> list[LintIssue]:
    issues: list[LintIssue] = []
    authority_path = "src/policy_impact/runtime/gates.py"
    for source_file in index.production_files():
        if source_file.relative_path == authority_path:
            continue
        for node in ast.walk(source_file.tree):
            if isinstance(node, ast.Attribute) and node.attr == "_evaluate":
                issues.append(
                    _node_issue(
                        "HK005",
                        source_file,
                        node,
                        "private GateRegistry._evaluate access is outside runtime/gates.py",
                    )
                )
    return issues


def _decision_alias_target(node: ast.Assign | ast.AnnAssign) -> str:
    targets = _assignment_targets(node)
    return targets[0] if len(targets) == 1 else ""


def _gate_registry_constructor_aliases(source_file: SourceFile) -> frozenset[str]:
    imported_symbols = _imported_symbols(source_file)
    aliases = {
        "GateRegistry",
        *(
            local_name
            for local_name, imported_name in imported_symbols.items()
            if imported_name.endswith(".GateRegistry")
        ),
    }
    assignments = [
        node
        for node in ast.walk(source_file.tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
    ]
    changed = True
    while changed:
        changed = False
        for node in assignments:
            if _tail_name(node.value) not in aliases:
                continue
            for target in _assignment_targets(node):
                if target not in aliases:
                    aliases.add(target)
                    changed = True
    return frozenset(aliases)


def _hk006_single_gate_authority(index: SourceIndex) -> list[LintIssue]:
    issues: list[LintIssue] = []
    for relative_path in sorted(_COMPATIBILITY_GATE_PATHS):
        source_file = index.get(relative_path)
        if source_file is None:
            issues.append(LintIssue("HK006", relative_path, 1, 1, "compatibility Gate module is unavailable"))
            continue
        constructor_aliases = _gate_registry_constructor_aliases(source_file)
        for node in ast.walk(source_file.tree):
            if isinstance(node, ast.Call) and _tail_name(node.func) in constructor_aliases:
                issues.append(
                    _node_issue(
                        "HK006",
                        source_file,
                        node,
                        "compatibility Gate module constructs a private GateRegistry",
                    )
                )
            elif isinstance(node, ast.ClassDef) and node.name == "GateRegistry":
                issues.append(
                    _node_issue(
                        "HK006",
                        source_file,
                        node,
                        "compatibility Gate module defines a private GateRegistry",
                    )
                )
            elif isinstance(node, ast.ClassDef) and "decision" in node.name.lower() and any(
                _tail_name(base) in {"Enum", "StrEnum", "IntEnum"} for base in node.bases
            ):
                issues.append(
                    _node_issue(
                        "HK006",
                        source_file,
                        node,
                        "compatibility Gate module defines a second authoritative Decision enum",
                    )
                )
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                target = _decision_alias_target(node)
                value = node.value
                if (
                    target
                    and "decision" in target.lower()
                    and isinstance(value, ast.Subscript)
                    and _tail_name(value.value) == "Literal"
                ):
                    issues.append(
                        _node_issue(
                            "HK006",
                            source_file,
                            node,
                            "compatibility Gate module defines a second authoritative Decision Literal",
                        )
                    )
                elif (
                    target
                    and "decision" in target.lower()
                    and isinstance(value, ast.Call)
                    and _tail_name(value.func) in {"Enum", "IntEnum", "StrEnum"}
                ):
                    issues.append(
                        _node_issue(
                            "HK006",
                            source_file,
                            node,
                            "compatibility Gate module defines a second authoritative functional Decision enum",
                        )
                    )
    return issues


def _imported_symbols(source_file: SourceFile) -> dict[str, str]:
    symbols: dict[str, str] = {}
    for node in source_file.tree.body:
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                local = alias.asname or alias.name
                symbols[local] = f"{module}.{alias.name}" if module else alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".", 1)[0]
                symbols[local] = alias.name
    return symbols


def _is_constructor_call(source_file: SourceFile, call: ast.Call, class_name: str) -> bool:
    direct_name = _tail_name(call.func)
    if direct_name == class_name:
        return True
    imports = _imported_symbols(source_file)
    if isinstance(call.func, ast.Name):
        return imports.get(call.func.id, "").rsplit(".", 1)[-1] == class_name
    return False


def _none_aliases(source_file: SourceFile) -> frozenset[str]:
    aliases: set[str] = set()
    assignments = [
        node
        for node in ast.walk(source_file.tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
    ]
    changed = True
    while changed:
        changed = False
        for node in assignments:
            value_is_none = isinstance(node.value, ast.Constant) and node.value.value is None
            value_is_alias = isinstance(node.value, ast.Name) and node.value.id in aliases
            if not (value_is_none or value_is_alias):
                continue
            for target in _assignment_targets(node):
                if target not in aliases:
                    aliases.add(target)
                    changed = True
    return frozenset(aliases)


def _is_provably_none(node: ast.AST, aliases: frozenset[str]) -> bool:
    return (isinstance(node, ast.Constant) and node.value is None) or (
        isinstance(node, ast.Name) and node.id in aliases
    )


def _hk007_explicit_gate_sink(index: SourceIndex) -> list[LintIssue]:
    issues: list[LintIssue] = []
    for source_file in index.production_files():
        none_aliases = _none_aliases(source_file)
        for node in ast.walk(source_file.tree):
            if not isinstance(node, ast.Call) or not _is_constructor_call(source_file, node, "GateRunner"):
                continue
            has_expansion = any(isinstance(argument, ast.Starred) for argument in node.args) or any(
                keyword.arg is None for keyword in node.keywords
            )
            sink_argument: ast.AST | None = None
            if len(node.args) >= 2 and not isinstance(node.args[1], ast.Starred):
                sink_argument = node.args[1]
            else:
                sink_argument = next(
                    (keyword.value for keyword in node.keywords if keyword.arg == "sink"),
                    None,
                )
            if (
                has_expansion
                or sink_argument is None
                or _is_provably_none(sink_argument, none_aliases)
            ):
                issues.append(
                    _node_issue(
                        "HK007",
                        source_file,
                        node,
                        "GateRunner construction does not prove an explicit GateDecisionSink",
                    )
                )
    return issues


def _hk008_parameterless_gate_engine(index: SourceIndex) -> list[LintIssue]:
    issues: list[LintIssue] = []
    for source_file in index.production_files():
        none_aliases = _none_aliases(source_file)
        for node in ast.walk(source_file.tree):
            if not isinstance(node, ast.Call) or not _is_constructor_call(source_file, node, "GateEngine"):
                continue
            has_expansion = any(isinstance(argument, ast.Starred) for argument in node.args) or any(
                keyword.arg is None for keyword in node.keywords
            )
            runner_argument: ast.AST | None = None
            if node.args and not isinstance(node.args[0], ast.Starred):
                runner_argument = node.args[0]
            else:
                runner_argument = next(
                    (keyword.value for keyword in node.keywords if keyword.arg == "runner"),
                    None,
                )
            if (
                has_expansion
                or runner_argument is None
                or _is_provably_none(runner_argument, none_aliases)
            ):
                issues.append(
                    _node_issue(
                        "HK008",
                        source_file,
                        node,
                        "GateEngine must be constructed with an injected GateRunner",
                    )
                )
    return issues


def lint_repository(root: str | Path) -> tuple[LintIssue, ...]:
    repository_root = Path(root).resolve()
    index, parse_issues = SourceIndex.build(repository_root)
    issues: list[LintIssue] = list(parse_issues)
    issues.extend(_required_path_issues(repository_root))
    issues.extend(_legacy_token_issues(index))
    manifests, manifest_issues = _extract_default_manifests(index)
    issues.extend(manifest_issues)
    issues.extend(_hk001_turn_coordinator_branches(index, manifests))
    issues.extend(_hk002_truthful_execution_mode(index))
    issues.extend(_hk003_executor_registration(index, manifests))
    issues.extend(_hk004_agent_action_validation(index))
    issues.extend(_hk005_private_gate_evaluation(index))
    issues.extend(_hk006_single_gate_authority(index))
    issues.extend(_hk007_explicit_gate_sink(index))
    issues.extend(_hk008_parameterless_gate_engine(index))
    return tuple(sorted(set(issues)))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root to lint")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    issues = lint_repository(args.root)
    if issues:
        for issue in issues:
            print(issue.render())
        return 1
    print("policy_impact harness lint passed (HK001-HK009)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
