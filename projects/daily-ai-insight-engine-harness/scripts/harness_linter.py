"""Coding-Agent Harness Linter —— 项目级工程约束的静态检查。

这个 linter 检查的是工程约束（文件是否存在、AGENTS.md 是否完整、
确定性 stage 是否偷偷用了 LLM），不检查日报内容质量。

每条报错都遵循 ❌ + ✅ FIX + 📖 See 三要素格式，
让 AI 编程助手（Claude Code / Codex / Cursor）拿到报错后不需要额外提示就能自动修复。

用法：
    python scripts/harness_linter.py          # 检查全部规则
    python scripts/harness_linter.py --json   # JSON 格式输出
    python scripts/harness_linter.py --quiet  # 只输出错误
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# ============================================================================
# 规则配置
# ============================================================================

REQUIRED_FILES = {
    "AGENTS.md": "AI 编程助手的项目级常驻上下文（地图模式）",
    "README.md": "人类读者的项目说明",
    "feature_list.json": "功能完成标准和当前状态",
    "progress.json": "跨会话学习进度和下一步计划",
    "run_chat.py": "页面对话入口",
    "run_full_pipeline.py": "完整流水线入口",
    ".github/workflows/harness.yml": "CI Harness 检查流水线",
    "docs/runtime/global_rules.md": "所有 LLM stage 共用的运行时全局规则",
    "docs/runtime/final_output_format.md": "最终报告和结构化输出格式",
    "docs/architecture/overview.md": "系统架构概览",
    "docs/architecture/boundaries.md": "模块边界和依赖规则",
    "docs/architecture/data-flow.md": "数据流转图",
    "docs/conventions/README.md": "编码规范总览",
    "docs/conventions/naming.md": "命名规范",
    "docs/conventions/error-handling.md": "错误处理规范",
    "docs/conventions/testing.md": "测试规范",
    "docs/conventions/logging.md": "日志规范",
    "src/insight_engine/conversation/router.py": "对话意图路由",
    "src/insight_engine/skill_executors/daily_news_report.py": "日报 Skill 执行器",
    "src/insight_engine/harness/state.py": "运行时共享状态和字段合同",
    "src/insight_engine/harness/graph.py": "Stage 状态机",
    "src/insight_engine/harness/stage_gates.py": "Stage gate 调度器",
    "src/insight_engine/harness/hooks/stage_hooks.py": "StageHooks 插槽系统",
    "src/insight_engine/harness/hooks/after_llm_call.py": "LLM 输出解析和校验",
}

SKILL_FILES = {
    "skills/daily_news_report/SKILL.md": "日报 Skill 定义",
}

PROMPT_FILES = {
    "prompts/agents/structuring_agent.md": "结构化 Agent 角色定义",
    "prompts/agents/analysis_agent.md": "分析 Agent 角色定义",
    "prompts/agents/report_agent.md": "报告 Agent 角色定义",
}

STAGE_LINTER_PAIRS = {
    "collect_raw_items": (
        "src/insight_engine/stages/collect_raw_items.py",
        "src/insight_engine/linters/collect_raw_items.py",
    ),
    "clean_items": (
        "src/insight_engine/stages/clean_items.py",
        "src/insight_engine/linters/clean_items.py",
    ),
    "structure_events": (
        "src/insight_engine/stages/structure_events.py",
        "src/insight_engine/linters/structure_events.py",
    ),
    "analyze_insights": (
        "src/insight_engine/stages/analyze_insights.py",
        "src/insight_engine/linters/analyze_insights.py",
    ),
    "generate_report": (
        "src/insight_engine/stages/generate_report.py",
        "src/insight_engine/linters/generate_report.py",
    ),
}

DETERMINISTIC_STAGE_FILES = [
    "src/insight_engine/stages/collect_raw_items.py",
    "src/insight_engine/stages/clean_items.py",
]

# ============================================================================
# 确定性 stage 不调 LLM 检查 —— 结构性检查，不是字符串黑名单
# ============================================================================
# 设计思路：
#   所有 LLM 调用最终都会访问 AI 服务商的 API 域名。
#   换什么 SDK（openai、anthropic、langchain、自写 http client）都改不了域名。
#   所以核心检查是"代码里有没有 LLM API 的域名"，辅以常见 AI SDK 的 import 检测。
#
# 扩展方式：
#   当你发现 Agent 用了新的绕过手法，在这里加一行对应的模式即可。
#   域名类的放在 LLM_API_DOMAINS，import 类的放在 LLM_SDK_IMPORTS。
# ============================================================================

# 已知 LLM 服务商的 API 域名片段 —— Agent 绕不过去，因为调用 API 必须用域名
LLM_API_DOMAINS = [
    "api.openai.com",
    "api.anthropic.com",
    "api.deepseek.com",
    "api.mistral.ai",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",  # Gemini
    "api.minimax.chat",
    "api.baichuan-ai.com",
    "dashscope.aliyuncs.com",             # 通义千问
    "api.moonshot.cn",                    # Kimi
    "api.zhipuai.cn",                     # 智谱
    "open.bigmodel.cn",                   # 智谱备用
    "api.deepseek.com/v1",               # DeepSeek v1
    "api.stability.ai",
    "api.cohere.ai",
    "api.together.xyz",
    "api.fireworks.ai",
    "api.groq.com",
]

# 常见 AI/LLM SDK 的 import 模式 —— 辅助检测
LLM_SDK_IMPORTS = [
    "import openai",
    "from openai",
    "import anthropic",
    "from anthropic",
    "import langchain",
    "from langchain",
    "ChatOpenAI",
    "ChatAnthropic",
    "HuggingFace",
    "Huggingface",
    "transformers",
    "vllm",
    "llama_index",
    "from llama_index",
]

AGENTS_REQUIRED_SECTIONS = [
    ("项目简介", "一句话描述项目是什么"),
    ("快速导航", '面向任务的"你想做什么 -> 去哪里看"表格'),
    ("两层 Harness 边界", "明确区分 Coding Agent Harness 和 Runtime Agent Harness"),
    ("硬性规则", "CI 会强制验证的规则列表"),
    ("项目文件纲要", "完整目录结构，让 Agent 定位文件"),
    ("修改约束", "告诉 Agent 改代码时必须遵守什么"),
    ("验证命令", "compileall + harness_linter + pytest"),
]

AGENTS_REQUIRED_KEYWORDS = [
    "Coding Agent Harness",
    "Runtime Agent Harness",
    "Claude Code",
    "state.py",
    "graph.py",
    "run_artifact",
    "scripts/harness_linter.py",
    "docs/architecture/",
]

JSON_CONTRACTS = {
    "feature_list.json": {
        "top_level": ["project", "purpose", "features"],
        "non_empty_lists": ["features"],
        "description": "功能完成标准和当前状态的 JSON 文件",
    },
    "progress.json": {
        "top_level": [
            "project",
            "current_learning_goal",
            "last_updated",
            "completed",
            "active_harness_concepts",
            "next_steps",
        ],
        "non_empty_lists": ["completed", "active_harness_concepts", "next_steps"],
        "description": "跨会话学习进度和下一步计划的 JSON 文件",
    },
}


# ============================================================================
# 格式化工具：生成 ❌ + ✅ FIX + 📖 See 格式的报错
# ============================================================================

def _fmt(problem: str, fix: str, doc: str) -> str:
    """生成统一格式的报错信息，让 Agent 能看懂并自动修复。"""
    return f"❌ {problem}\n✅ FIX: {fix}\n📖 See: {doc}"


# ============================================================================
# 检查函数
# ============================================================================

def check_required_files() -> list[str]:
    """检查 AGENTS.md 要求的所有必需文件是否存在。"""
    issues = []
    for path, purpose in REQUIRED_FILES.items():
        full = PROJECT_ROOT / path
        if not full.exists():
            issues.append(
                _fmt(
                    f"缺少必需文件：`{path}`（{purpose}）",
                    f"在项目根目录下创建 `{path}`。如果你不确定内容，"
                    f"可以先创建空文件占位，然后参考 AGENTS.md 中的文件纲要补全。",
                    "docs/architecture/overview.md 了解项目结构",
                )
            )
    return issues


def check_no_confusing_agent_file() -> list[str]:
    """检查是否使用了错误的 Agent 入口文件名。"""
    issues = []
    for bad_name in ["agent.md", "AGENT.md", "Agent.md"]:
        if (PROJECT_ROOT / bad_name).exists():
            issues.append(
                _fmt(
                    f"根目录存在 `{bad_name}`（不应使用这个文件名）",
                    f"删除 `{bad_name}`。AI 编程助手的入口统一使用 `AGENTS.md`（全大写+S）。"
                    f"人类说明使用 `README.md`。",
                    "AGENTS.md 了解 Coding Agent Harness 入口规范",
                )
            )
    return issues


def check_agents_contract() -> list[str]:
    """检查 AGENTS.md 的内容是否满足 Harness 工程合同。"""
    path = PROJECT_ROOT / "AGENTS.md"
    if not path.exists():
        return [
            _fmt(
                "AGENTS.md 文件不存在",
                "在项目根目录创建 AGENTS.md。参考 docs/HARNESS_ENGINEERING_GUIDE.md"
                " 中的模板，确保包含：项目简介、快速导航表、硬性规则、文件纲要、修改约束。",
                "docs/HARNESS_ENGINEERING_GUIDE.md 查看 AGENTS.md 模板",
            )
        ]

    text = path.read_text(encoding="utf-8")
    issues = []

    # 检查每个必要章节
    for section_name, section_desc in AGENTS_REQUIRED_SECTIONS:
        if f"## {section_name}" not in text and section_name not in text:
            issues.append(
                _fmt(
                    f"AGENTS.md 缺少 `{section_name}` 章节（{section_desc}）",
                    f"在 AGENTS.md 中添加 `## {section_name}` 章节。",
                    "docs/HARNESS_ENGINEERING_GUIDE.md 查看完整的 AGENTS.md 模板",
                )
            )

    # 检查关键短语
    for keyword in AGENTS_REQUIRED_KEYWORDS:
        if keyword not in text:
            issues.append(
                _fmt(
                    f"AGENTS.md 缺少 Harness 关键概念：`{keyword}`",
                    f"在 AGENTS.md 中补充 `{keyword}` 的相关说明，"
                    f"让 AI 编程助手理解这个概念的用途和位置。",
                    "docs/architecture/overview.md 了解 Harness 工程架构",
                )
            )

    # 行数检查
    line_count = len(text.splitlines())
    if line_count > 100:
        issues.append(
            _fmt(
                f"AGENTS.md 有 {line_count} 行（推荐 ≤ 100 行）",
                "将详细内容移到 docs/ 目录下的对应文档，"
                "AGENTS.md 只保留快速导航链接和硬性规则。",
                "docs/HARNESS_ENGINEERING_GUIDE.md 了解地图模式原则",
            )
        )

    return issues


def check_json_contracts() -> list[str]:
    """检查 feature_list.json 和 progress.json 是否满足字段合同。"""
    issues = []
    for filename, contract in JSON_CONTRACTS.items():
        path = PROJECT_ROOT / filename
        if not path.exists():
            issues.append(
                _fmt(
                    f"缺少项目跟踪文件：`{filename}`（{contract['description']}）",
                    f"创建 `{filename}` 并包含顶层字段：{', '.join(contract['top_level'])}。",
                    "docs/HARNESS_ENGINEERING_GUIDE.md 查看跟踪文件模板",
                )
            )
            continue

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            issues.append(
                _fmt(
                    f"`{filename}` 不是合法 JSON：{exc}",
                    f"修复 `{filename}` 中的 JSON 语法错误。"
                    f'可以用 `python3 -c "import json; json.load(open(\'{filename}\'))"` 验证。',
                    "docs/reference/error-codes.md 了解错误码体系",
                )
            )
            continue

        if not isinstance(data, dict):
            issues.append(
                _fmt(
                    f"`{filename}` 顶层必须是 JSON object（当前是 {type(data).__name__}）",
                    f"确保 `{filename}` 以 `{{` 开头、`}}` 结尾。",
                    f"docs/reference/api-spec.yaml 查看数据格式规范",
                )
            )
            continue

        for key in contract["top_level"]:
            if key not in data:
                issues.append(
                    _fmt(
                        f"`{filename}` 缺少顶层字段：`{key}`",
                        f"在 `{filename}` 中添加 `{key}` 字段。",
                        f"docs/reference/api-spec.yaml 查看字段定义",
                    )
                )

        for key in contract["non_empty_lists"]:
            if key in data and (not isinstance(data[key], list) or not data[key]):
                issues.append(
                    _fmt(
                        f"`{filename}.{key}` 必须是非空数组",
                        f"在 `{filename}` 的 `{key}` 字段中至少添加一条记录。",
                        "docs/plans/current-sprint.md 了解当前迭代任务",
                    )
                )

    return issues


def check_deterministic_stages_do_not_call_llm() -> list[str]:
    """确保确定性 stage 没有偷偷调用 LLM。

    两层检测：
    1. 域名检测（绕不过去——调 AI API 必须用域名）
    2. SDK import 检测（辅助——Agent 可能用你没见过的 SDK）
    """
    issues = []
    for relative_path in DETERMINISTIC_STAGE_FILES:
        path = PROJECT_ROOT / relative_path
        if not path.exists():
            issues.append(_fmt(
                f"确定性 stage 文件不存在：`{relative_path}`",
                f"创建 `{relative_path}` 文件。确定性 stage 只做数据抓取和清洗，不调用 LLM。",
                "docs/architecture/boundaries.md",
            ))
            continue

        text = path.read_text(encoding="utf-8")

        # 第 1 层：域名检测（核心防线）
        for domain in LLM_API_DOMAINS:
            if domain in text:
                # 找到域名在代码中出现的上下文
                line_idx = text.index(domain)
                context_start = max(0, line_idx - 40)
                context_end = min(len(text), line_idx + len(domain) + 60)
                context = text[context_start:context_end].replace('\n', ' ').strip()
                issues.append(_fmt(
                    f"`{relative_path}` 包含 LLM API 域名：`{domain}`\n"
                    f"   代码上下文：...{context}...",
                    f"确定性 stage 不允许调用任何 AI 模型 API。"
                    f"移除所有对 `{domain}` 的网络请求代码。"
                    f"如果需要 AI 能力，把逻辑放到 LLM stage 中。",
                    "docs/architecture/boundaries.md 了解确定性/LLM stage 的边界",
                ))

        # 第 2 层：SDK import 检测（辅助防线）
        for pattern in LLM_SDK_IMPORTS:
            if pattern in text:
                issues.append(_fmt(
                    f"`{relative_path}` 导入了 AI SDK：`{pattern}`",
                    f"确定性 stage 不能导入任何 AI/LLM 相关的库。"
                    f"移除这个 import 和所有相关的调用。"
                    f"如果需要 AI 能力，在 LLM stage（structure_events/analyze_insights/generate_report）中实现。",
                    "docs/architecture/boundaries.md 了解确定性/LLM stage 的边界",
                ))

    return issues


def check_skills_and_prompts() -> list[str]:
    """检查 Skill 和 Agent prompt 文件是否存在且非空。"""
    issues = []
    for path, desc in {**SKILL_FILES, **PROMPT_FILES}.items():
        full = PROJECT_ROOT / path
        if not full.exists():
            issues.append(
                _fmt(
                    f"缺少 prompt/skill 文件：`{path}`（{desc}）",
                    f"创建 `{path}` 文件，定义对应的角色说明或 Skill 描述。",
                    "skills/daily_news_report/SKILL.md 参考 Skill 文件格式",
                )
            )
        elif not full.read_text(encoding="utf-8").strip():
            issues.append(
                _fmt(
                    f"prompt/skill 文件为空：`{path}`",
                    f"在 `{path}` 中填写对应的 Agent 角色说明或 Skill 描述。",
                    "prompts/agents/ 目录查看其他 Agent prompt 示例",
                )
            )
    return issues


def check_stage_linter_pairs() -> list[str]:
    """检查每个 stage 是否有对应的 linter 文件。"""
    issues = []
    for stage_name, (stage_path, linter_path) in STAGE_LINTER_PAIRS.items():
        stage_full = PROJECT_ROOT / stage_path
        linter_full = PROJECT_ROOT / linter_path

        if not stage_full.exists():
            issues.append(
                _fmt(
                    f"`{stage_name}` 缺少 stage 实现文件：`{stage_path}`",
                    f"创建 `{stage_path}` 并实现 `{stage_name}(state) -> state` 函数。",
                    "src/insight_engine/stages/ 查看其他 stage 实现示例",
                )
            )
        if not linter_full.exists():
            issues.append(
                _fmt(
                    f"`{stage_name}` 缺少对应 linter：`{linter_path}`",
                    f"创建 `{linter_path}` 并实现 `lint(state) -> dict` 函数。"
                    f"linter 负责检查 stage 产物是否满足字段合同。",
                    "src/insight_engine/linters/common.py 查看 lint_result 格式",
                )
            )
    return issues


def check_full_pipeline_has_show_flag() -> list[str]:
    """确保 run_full_pipeline.py 支持 --show 标志。"""
    path = PROJECT_ROOT / "run_full_pipeline.py"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    if "--show" not in text:
        return [
            _fmt(
                "run_full_pipeline.py 不支持 --show 标志",
                "在 ArgumentParser 中添加 `--show` 参数，"
                "当设置时打印完整 pipeline_summary 内容。",
                "run_full_pipeline.py 查看当前入口实现",
            )
        ]
    return []


def check_chat_entrypoint() -> list[str]:
    """检查对话入口是否通过正确的路由进入系统。"""
    path = PROJECT_ROOT / "run_chat.py"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    if "handle_message" not in text:
        return [
            _fmt(
                "run_chat.py 必须通过 conversation.router.handle_message 进入系统",
                "在 run_chat.py 中导入并使用 `handle_message()` 函数处理用户消息。",
                "src/insight_engine/conversation/router.py 查看 handle_message 接口",
            )
        ]
    return []


def check_ci_workflow() -> list[str]:
    """检查 CI 配置是否包含三类必要检查。"""
    path = PROJECT_ROOT / ".github/workflows/harness.yml"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    issues = []
    checks = {
        "compileall": "编译检查：python -m compileall src scripts tests",
        "scripts/harness_linter.py": "Harness 静态检查：python scripts/harness_linter.py",
        "pytest": "单元测试：python -m pytest",
    }
    for token, desc in checks.items():
        if token not in text:
            issues.append(
                _fmt(
                    f".github/workflows/harness.yml 缺少检查命令：`{token}`（{desc}）",
                    f"在 CI 配置中添加 `{token}` 对应的 step。",
                    ".github/workflows/harness.yml 查看当前 CI 配置",
                )
            )
    return issues


def check_file_size_limits() -> list[str]:
    """检查源文件是否超过 300 行上限。"""
    issues = []
    max_lines = 300
    for py_file in (PROJECT_ROOT / "src").rglob("*.py"):
        line_count = len(py_file.read_text(encoding="utf-8").splitlines())
        if line_count > max_lines:
            rel = py_file.relative_to(PROJECT_ROOT)
            issues.append(
                _fmt(
                    f"`{rel}` 有 {line_count} 行（上限 {max_lines} 行）",
                    f"将 `{rel}` 拆分为更小的模块。"
                    f"把辅助函数移到 utils/，把子逻辑拆成独立文件。",
                    "docs/conventions/naming.md 了解文件组织规范",
                )
            )
    return issues


def check_stage_handler_docstrings() -> list[str]:
    """检查每个 stage 的入口函数是否有中文 docstring。"""
    import ast

    issues = []
    stages_dir = PROJECT_ROOT / "src/insight_engine/stages"

    for py_file in sorted(stages_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue

        stage_name = py_file.stem
        text = py_file.read_text(encoding="utf-8")

        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue

        handler = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == stage_name:
                handler = node
                break

        if handler is None:
            continue

        doc = ast.get_docstring(handler)
        has_chinese = doc is not None and any(
            "一" <= c <= "鿿" for c in doc
        )

        if not has_chinese:
            rel = py_file.relative_to(PROJECT_ROOT)
            issues.append(
                _fmt(
                    f"`{rel}` 的入口函数 `{stage_name}()` 缺少中文 docstring",
                    f"在 `{stage_name}()` 函数添加中文 docstring，"
                    f"说明这个 stage 的职责。"
                    f"例如：def {stage_name}(state): ...  "
                    f'"""执行 XXX 阶段。"""',
                    "docs/architecture/overview.md 了解各 stage 职责",
                )
            )

    return issues


def check_hook_files_exist() -> list[str]:
    """检查 git hooks 和 agent workflow 脚本是否已安装。"""
    issues = []
    script_dir = PROJECT_ROOT / "scripts"
    hook_files = {
        "pre-commit": "Git pre-commit hook：commit 前运行 harness_linter + compileall + pytest",
        "agent-verify.sh": "Agent 验证脚本：在隔离 git worktree 中运行所有检查",
        "agent-guardrails.sh": "Agent 护栏：一键运行全部质量检查",
    }
    for filename, desc in hook_files.items():
        full = script_dir / filename
        if not full.exists():
            issues.append(
                _fmt(
                    f"缺少 Agent 护栏脚本：`scripts/{filename}`（{desc}）",
                    f"创建 `scripts/{filename}` 脚本。参考 docs/HARNESS_ENGINEERING_GUIDE.md"
                    f" 中的模板。",
                    "docs/HARNESS_ENGINEERING_GUIDE.md 查看 Phase 2/3 护栏模板",
                )
            )
    return issues


def check_contract_doc_freshness() -> list[str]:
    """检查 state-contracts.md 是否与 state.py 同步。

    合同文档由 generate_contract_docs.py 从 state.py 自动生成。
    如果 state.py 中的 FIELD_SPEC 改了但文档没重新生成，CI 应该报警。
    """
    issues = []
    contract_doc = PROJECT_ROOT / "docs/reference/state-contracts.md"
    generate_script = PROJECT_ROOT / "scripts/generate_contract_docs.py"

    if not generate_script.exists():
        return issues  # 生成脚本还不存在，跳过检查

    if not contract_doc.exists():
        issues.append(
            _fmt(
                "docs/reference/state-contracts.md 不存在",
                "运行 `python scripts/generate_contract_docs.py` 从 state.py 生成合同文档。"
                "这个文档是 5 份 FIELD_SPEC 合同的可读版本，Agent 需要它来了解数据字段约定。",
                "src/insight_engine/harness/state.py 查看 FIELD_SPEC 定义",
            )
        )
        return issues

    import subprocess

    result = subprocess.run(
        [sys.executable, str(generate_script), "--check"],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    if result.returncode != 0:
        issues.append(
            _fmt(
                "docs/reference/state-contracts.md 与 state.py 不同步。"
                "state.py 中的 FIELD_SPEC 已被修改但合同文档未重新生成。",
                "运行 `python scripts/generate_contract_docs.py` 重新生成合同文档。"
                "每次修改 state.py 中的 FIELD_SPEC 后都必须执行此命令。",
                "docs/reference/state-contracts.md 查看当前合同文档版本",
            )
        )
    return issues


# ============================================================================
# 主入口
# ============================================================================

CHECKERS = [
    ("必需文件检查", check_required_files),
    ("Agent 入口文件名检查", check_no_confusing_agent_file),
    ("AGENTS.md 合同检查", check_agents_contract),
    ("JSON 跟踪文件合同检查", check_json_contracts),
    ("确定性 stage 不调 LLM 检查", check_deterministic_stages_do_not_call_llm),
    ("Skill 和 Prompt 文件检查", check_skills_and_prompts),
    ("Stage-Linter 配对检查", check_stage_linter_pairs),
    ("流水线入口检查", check_full_pipeline_has_show_flag),
    ("对话入口检查", check_chat_entrypoint),
    ("CI 配置检查", check_ci_workflow),
    ("文件大小检查", check_file_size_limits),
    ("Stage 入口函数中文 docstring 检查", check_stage_handler_docstrings),
    ("Agent 护栏脚本检查", check_hook_files_exist),
    ("State 合同文档同步检查", check_contract_doc_freshness),
]


def run_all_checks() -> dict[str, Any]:
    """运行所有 Harness 检查，返回结构化结果。"""
    all_issues: list[str] = []
    check_results: list[dict[str, Any]] = []

    for check_name, check_fn in CHECKERS:
        issues = check_fn()
        check_results.append(
            {
                "check": check_name,
                "passed": not issues,
                "issue_count": len(issues),
                "issues": issues,
            }
        )
        all_issues.extend(issues)

    return {
        "passed": not all_issues,
        "total_checks": len(CHECKERS),
        "failed_checks": sum(1 for r in check_results if not r["passed"]),
        "total_issues": len(all_issues),
        "results": check_results,
    }


def print_results(payload: dict[str, Any], *, json_mode: bool = False) -> None:
    """输出检查结果。"""
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if payload["passed"]:
        print("✅ 所有 Harness 检查通过！")
        print(f"   共 {payload['total_checks']} 项检查，0 个问题")
        return

    print(f"❌ Harness 检查未通过")
    print(f"   共 {payload['total_checks']} 项检查，"
          f"{payload['failed_checks']} 项失败，"
          f"{payload['total_issues']} 个问题")
    print()

    for result in payload["results"]:
        if result["passed"]:
            continue
        print(f"── {result['check']}（{result['issue_count']} 个问题）──")
        for issue in result["issues"]:
            print()
            print(issue)
            print()
        print()


def main() -> int:
    json_mode = "--json" in sys.argv
    quiet_mode = "--quiet" in sys.argv

    payload = run_all_checks()

    if quiet_mode and not payload["passed"]:
        # quiet 模式下只打印问题，不打印框架信息
        for result in payload["results"]:
            for issue in result["issues"]:
                print(issue)
                print("---")

    elif not quiet_mode:
        print_results(payload, json_mode=json_mode)

    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
