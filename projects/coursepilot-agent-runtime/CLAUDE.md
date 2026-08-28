# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

CoursePilot is a multi-agent AI course learning assistant for university students. It combines RAG (textbook retrieval), multi-agent orchestration (Router/Tutor/QuizMaster/Grader), and MCP-based tool integration to deliver three learning modes: Learn, Practice, and Exam.

- **Stack**: Python 3.9+, FastAPI (backend), Streamlit (frontend), FAISS (vector DB), SQLite (memory), OpenAI-compatible LLM (DeepSeek by default)
- **Embedding model**: `BAAI/bge-base-zh-v1.5` (Chinese-optimized, 768-dim)
- **Agent methodology**: Router = Plan+Replan, Tutor = ReAct, QuizMaster = Plan-Solve, Grader = Plan + ReAct-Solve

## Commands

```bash
# Backend (start first — frontend depends on it)
python -m backend.api                         # defaults to localhost:8001

# Frontend
streamlit run frontend/streamlit_app.py       # defaults to localhost:8501

# Run all tests (uses plain Python, no pytest)
python tests/test_basic.py

# Rebuild FAISS indexes across all courses
python rebuild_indexes.py

# Demo / architecture overview
python examples/demo.py [--api] [--arch]

# Benchmarking
python scripts/perf/bench_runner.py --help
```

Configuration is in `.env` at the project root (copy from `.env.example`). The environment is managed via conda: `conda activate study_agent`. Dependencies are in both `requirements.txt` and `pyproject.toml` — the two may diverge; `requirements.txt` is the authoritative source with pinned versions.

## Architecture

### Data flow

```
Streamlit (frontend) → FastAPI (backend/api.py) → OrchestrationRunner (core/orchestration/runner.py)
                                                       ├─ RouterAgent (plan + optional replan)
                                                       ├─ RAG Retriever (hybrid: FAISS dense + BM25, RRF fusion)
                                                       ├─ memory_search (SQLite pre-fetch)
                                                       ├─ Mode dispatch:
                                                       │    learn → TutorAgent (ReAct, all 6 tools)
                                                       │    practice/exam 出题 → QuizMasterAgent (Plan-Solve, minimal tools)
                                                       │    answer submission → GraderAgent (Plan + ReAct-Solve, calculator only)
                                                       └─ MCP Tools (all via mcp_stdio, no local fallback)
```

### Key architectural principles

1. **LLM does language reasoning, Python does flow control.** `OrchestrationRunner` is a hardcoded Python scheduler — it makes routing decisions (learn vs. practice vs. exam, 出题 vs. 评卷), not an LLM. Router/Tutor/QuizMaster/Grader are controlled, replaceable execution units, not autonomous agents.
2. **All tool calls go through MCP stdio.** `MCPTools.call_tool()` → `_StdioMCPClient` (lazy-spawned subprocess) → `server_stdio.py`. There is no local direct-call fallback; failures return `success=False, via=mcp_stdio`.
3. **ToolPolicy currently allows ALL_TOOLS for all modes.** Actual tool constraints come from Runner routing + Agent prompt rules — Grader is restricted to `calculator` only, QuizMaster defaults to no tool loop, etc. Do not rely on the policy whitelist for security.
4. **Context budgeter** runs before each agent invocation: history (recent turns + summary) → RAG (sentence-compressed) → memory (short snippets) → hard truncation. `top_k` defaults: learn/practice=4, exam=6.

### Module map

| Directory | Purpose |
|---|---|
| `backend/` | FastAPI app — `/workspaces`, `/upload`, `/build-index`, `/chat`, `/chat/stream` (SSE) |
| `frontend/` | Single Streamlit app (`streamlit_app.py`) |
| `core/agents/` | `router.py`, `tutor.py`, `quizmaster.py`, `grader.py` — each agent owns its LLM calls and tool-use loop |
| `core/orchestration/` | `runner.py` (main dispatcher), `context_budgeter.py`, `policies.py`, `prompts.py` |
| `core/llm/` | `openai_compat.py` — OpenAI-compatible client, loads base_url/model from env |
| `core/harness/` | Lightweight agent harness: `runtime.py` (wrapper around runner), `session.py`, `artifact.py`, `hooks.py`, `skills.py` — gated by `ENABLE_HARNESS_RUNTIME` env var |
| `core/metrics/` | `collector.py` — request-level tracing (request_id, trace_id, latency) |
| `rag/` | `ingest.py` (multi-format parsing), `chunk.py` (chapter-aware or fixed), `embed.py`, `store_faiss.py`, `retrieve.py` (hybrid/BM25/dense), `lexical.py` (BM25) |
| `mcp_tools/` | `client.py` (MCP client + tool implementations), `server_stdio.py` (MCP stdio server) |
| `memory/` | `store.py` (SQLite with FTS5 + LIKE fallback), `manager.py` |
| `benchmarks/` | Test cases (`cases_v1.jsonl`, `rag_gold_v1.jsonl`) and fixtures |
| `tests/` | Integration tests — `test_basic.py` through `test_harness_*.py` |
| `scripts/perf/` | `bench_runner.py` for benchmark runs with checkpoint/resume |

### Six MCP tools

`calculator`, `websearch` (SerpAPI), `filewriter` (writes to course `notes/`), `memory_search` (SQLite query), `mindmap_generator` (Mermaid), `get_datetime`

### Data layout

```
data/
  workspaces/<course_name>/{uploads/, index/, notes/, mistakes/, practices/, exams/}
  memory/memory.db
```

Workspace isolation uses `basename(course_name)` — never construct paths from raw user input.

## Adding features

- **New agent**: Add file in `core/agents/`, add prompt in `core/orchestration/prompts.py`, integrate in `runner.py`
- **New tool**: Add schema + implementation in `mcp_tools/client.py`, register in `_to_mcp_tools()` and route in `call_tool()`
- **New mode**: Add to `Literal` in `backend/schemas.py`, wire up in `runner.py`
- **New document format**: Add parser in `rag/ingest.py`, register extension in `parse_document()` and `ALLOWED_EXTENSIONS` in `backend/api.py`

## Testing notes

Tests use plain functions with `print("✅ ...")` / `print("❌ ...")` — not pytest. Import paths assume the project root is on `sys.path`. Some tests (RAG, harness) need a valid `.env` with API keys and may touch the filesystem under `data/`.
