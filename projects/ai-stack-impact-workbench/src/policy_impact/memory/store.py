"""SQLite memory store for company facts, corrections, and report sessions."""

from __future__ import annotations

import json
import hashlib
import sqlite3
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

from policy_impact.company_wiki.loader import company_dir
from policy_impact.harness.model_config import DEFAULT_MODEL_ID, default_model_config
from policy_impact.harness.state import utc_now_iso
from policy_impact.rag.lexical import tokenize
from policy_impact.runtime.run_status import RunStatus, require_run_transition


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False)


def _canonical_json_dumps(data: Any) -> str:
    return json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _json_loads(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def _normalize_memory_identity(value: Any) -> str:
    return str(value if value is not None else "").strip()


def canonical_memory_id(namespace: str, memory_type: str, content: str) -> str:
    identity = json.dumps(
        [
            _normalize_memory_identity(namespace),
            _normalize_memory_identity(memory_type),
            _normalize_memory_identity(content),
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"mem_{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


class PolicyMemoryStore:
    _schema_lock = threading.Lock()
    _initialized_db_paths: set[str] = set()

    def __init__(self, company_id: str) -> None:
        db_dir = company_dir(company_id) / "db"
        db_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = db_dir / "company_memory.sqlite"
        db_key = str(self.db_path.resolve())
        with self._schema_lock:
            if db_key not in self._initialized_db_paths or not self.db_path.exists():
                self._init_tables()
                self._initialized_db_paths.add(db_key)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 10000")
        return conn

    def _init_tables(self) -> None:
        with self._conn() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS companies (
                    company_id TEXT PRIMARY KEY,
                    profile_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wiki_pages (
                    page_id TEXT PRIMARY KEY,
                    path TEXT NOT NULL,
                    domain TEXT DEFAULT '',
                    importance REAL DEFAULT 0.5,
                    source_hash TEXT DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS company_facts (
                    fact_id TEXT PRIMARY KEY,
                    source_path TEXT NOT NULL,
                    value TEXT DEFAULT '',
                    importance REAL DEFAULT 0.5,
                    confidence REAL DEFAULT 0.5,
                    source TEXT DEFAULT '',
                    tags_json TEXT DEFAULT '[]',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS policy_documents (
                    policy_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    issuer TEXT DEFAULT '',
                    published_at TEXT DEFAULT '',
                    source_url TEXT DEFAULT '',
                    source_level TEXT DEFAULT '',
                    metadata_json TEXT DEFAULT '{}',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS policy_clauses (
                    clause_id TEXT PRIMARY KEY,
                    policy_id TEXT NOT NULL,
                    clause_type TEXT NOT NULL,
                    text TEXT NOT NULL,
                    metadata_json TEXT DEFAULT '{}',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_items (
                    id TEXT PRIMARY KEY,
                    namespace TEXT NOT NULL,
                    memory_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    importance REAL DEFAULT 0.5,
                    metadata TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS report_sessions (
                    report_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    report_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS correction_proposals (
                    correction_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS report_messages (
                    message_id TEXT PRIMARY KEY,
                    report_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    citations_json TEXT DEFAULT '[]',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tool_calls (
                    call_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    stage_name TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    allowed INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS run_artifacts (
                    run_id TEXT PRIMARY KEY,
                    artifact_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metrics_json TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS model_configs (
                    model_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    endpoint TEXT DEFAULT '',
                    temperature REAL DEFAULT 0.0,
                    max_tokens INTEGER DEFAULT 4096,
                    is_default INTEGER DEFAULT 0,
                    metadata_json TEXT DEFAULT '{}',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    session_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    active_skill_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metadata_json TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_message_at TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS chat_messages (
                    message_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata_json TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS staged_assistant_messages (
                    run_id TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL UNIQUE,
                    session_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata_json TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversation_summaries (
                    session_id TEXT PRIMARY KEY,
                    summary_json TEXT NOT NULL,
                    source_message_count INTEGER NOT NULL,
                    source_hash TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_runs (
                    run_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    user_message_id TEXT NOT NULL,
                    assistant_message_id TEXT DEFAULT '',
                    mode TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    skill_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metadata_json TEXT DEFAULT '{}',
                    metrics_json TEXT DEFAULT '{}',
                    artifacts_json TEXT DEFAULT '[]',
                    error TEXT DEFAULT '',
                    started_at TEXT NOT NULL,
                    finished_at TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS agent_steps (
                    step_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    step_index INTEGER NOT NULL,
                    step_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    input_json TEXT DEFAULT '{}',
                    output_json TEXT DEFAULT '{}',
                    tool_calls_json TEXT DEFAULT '[]',
                    gate_result_json TEXT DEFAULT '{}',
                    metadata_json TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS gate_evaluations (
                    evaluation_id TEXT PRIMARY KEY,
                    audit_ref TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL,
                    span_id TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    gate_id TEXT NOT NULL,
                    gate_version TEXT NOT NULL,
                    schema_name TEXT NOT NULL,
                    definition_fingerprint TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    input_refs_json TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    output_ref TEXT DEFAULT '',
                    repair_action_json TEXT DEFAULT 'null',
                    audit_metadata_json TEXT DEFAULT '{}',
                    record_fingerprint TEXT NOT NULL,
                    step_id TEXT NOT NULL UNIQUE,
                    evaluated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_chat_messages_session_created
                    ON chat_messages(session_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_agent_runs_session_started
                    ON agent_runs(session_id, started_at);
                CREATE INDEX IF NOT EXISTS idx_agent_steps_run_index
                    ON agent_steps(run_id, step_index);
                CREATE INDEX IF NOT EXISTS idx_gate_evaluations_run
                    ON gate_evaluations(run_id, evaluated_at);
                """
            )

    def seed_model_configs(self, configs: list[dict[str, Any]] | None = None) -> None:
        defaults = [default_model_config()]
        now = utc_now_iso()
        with self._conn() as conn:
            if configs is None:
                conn.execute(
                    "DELETE FROM model_configs WHERE model_id != ?",
                    (DEFAULT_MODEL_ID,),
                )
            for config in configs or defaults:
                conn.execute(
                    """
                    INSERT INTO model_configs(
                        model_id, display_name, provider, model_name, endpoint,
                        temperature, max_tokens, is_default, metadata_json, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(model_id) DO UPDATE SET
                        display_name = excluded.display_name,
                        provider = excluded.provider,
                        model_name = excluded.model_name,
                        endpoint = excluded.endpoint,
                        temperature = excluded.temperature,
                        max_tokens = excluded.max_tokens,
                        is_default = excluded.is_default,
                        metadata_json = excluded.metadata_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        str(config.get("model_id", "")),
                        str(config.get("display_name", "")),
                        str(config.get("provider", "")),
                        str(config.get("model_name", config.get("model_id", ""))),
                        str(config.get("endpoint", "")),
                        float(config.get("temperature", 0.0) or 0.0),
                        int(config.get("max_tokens", 4096) or 4096),
                        1 if config.get("is_default") else 0,
                        _json_dumps(config.get("metadata", {})),
                        now,
                    ),
                )

    def list_model_configs(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM model_configs ORDER BY is_default DESC, model_id ASC"
            ).fetchall()
        models = []
        for row in rows:
            data = dict(row)
            data["is_default"] = bool(data["is_default"])
            data["metadata"] = _json_loads(data.pop("metadata_json"), {})
            models.append(data)
        return models

    def get_model_config(self, model_id: str) -> dict[str, Any] | None:
        if not model_id:
            return None
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM model_configs WHERE model_id = ?",
                (model_id,),
            ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["is_default"] = bool(data["is_default"])
        data["metadata"] = _json_loads(data.pop("metadata_json"), {})
        return data

    def delete_model_configs(self, model_ids: list[str]) -> int:
        ids = [str(item).strip() for item in model_ids if str(item).strip()]
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self._conn() as conn:
            cursor = conn.execute(
                f"DELETE FROM model_configs WHERE model_id IN ({placeholders})",
                ids,
            )
            return int(cursor.rowcount or 0)

    def upsert_model_config(self, config: dict[str, Any]) -> dict[str, Any]:
        model_id = str(config.get("model_id", "")).strip()
        if not model_id:
            raise ValueError("model_id is required")
        now = utc_now_iso()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO model_configs(
                    model_id, display_name, provider, model_name, endpoint,
                    temperature, max_tokens, is_default, metadata_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(model_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    provider = excluded.provider,
                    model_name = excluded.model_name,
                    endpoint = excluded.endpoint,
                    temperature = excluded.temperature,
                    max_tokens = excluded.max_tokens,
                    is_default = excluded.is_default,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (
                    model_id,
                    str(config.get("display_name") or model_id),
                    str(config.get("provider") or "openai-compatible"),
                    str(config.get("model_name") or model_id),
                    str(config.get("endpoint") or ""),
                    float(config.get("temperature", 0.0) or 0.0),
                    int(config.get("max_tokens", 4096) or 4096),
                    1 if config.get("is_default") else 0,
                    _json_dumps(config.get("metadata", {})),
                    now,
                ),
            )
        for item in self.list_model_configs():
            if item["model_id"] == model_id:
                return item
        raise RuntimeError(f"failed to upsert model config: {model_id}")

    def create_chat_session(
        self,
        title: str,
        mode: str,
        model_id: str,
        active_skill_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        session_id = uuid4().hex
        now = utc_now_iso()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO chat_sessions(
                    session_id, title, mode, model_id, active_skill_id, status,
                    metadata_json, created_at, updated_at, last_message_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    title,
                    mode,
                    model_id,
                    active_skill_id,
                    "active",
                    _json_dumps(metadata or {}),
                    now,
                    now,
                    "",
                ),
            )
        return session_id

    def get_chat_session(self, session_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT s.*,
                    (SELECT COUNT(*) FROM chat_messages m WHERE m.session_id = s.session_id) AS message_count,
                    (SELECT content FROM chat_messages m
                     WHERE m.session_id = s.session_id AND m.role = 'user'
                     ORDER BY m.rowid ASC LIMIT 1) AS first_user_message
                FROM chat_sessions s
                WHERE s.session_id = ?
                """,
                (session_id,),
            ).fetchone()
        return self._chat_session_from_row(row) if row else None

    def list_chat_sessions(self, status: str | None = None) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = ""
        if status:
            where = "WHERE s.status = ?"
            params.append(status)
        with self._conn() as conn:
            rows = conn.execute(
                f"""
                SELECT s.*,
                    (SELECT COUNT(*) FROM chat_messages m WHERE m.session_id = s.session_id) AS message_count,
                    (SELECT content FROM chat_messages m
                     WHERE m.session_id = s.session_id AND m.role = 'user'
                     ORDER BY m.rowid ASC LIMIT 1) AS first_user_message
                FROM chat_sessions s
                {where}
                ORDER BY COALESCE(NULLIF(s.last_message_at, ''), s.updated_at) DESC, s.created_at DESC
                """,
                params,
            ).fetchall()
        return [self._chat_session_from_row(row) for row in rows]

    def update_chat_session_settings(
        self,
        session_id: str,
        title: str | None = None,
        mode: str | None = None,
        model_id: str | None = None,
        active_skill_id: str | None = None,
        status: str | None = None,
    ) -> None:
        updates = {
            "title": title,
            "mode": mode,
            "model_id": model_id,
            "active_skill_id": active_skill_id,
            "status": status,
        }
        assignments = [f"{field} = ?" for field, value in updates.items() if value is not None]
        params = [value for value in updates.values() if value is not None]
        if not assignments:
            return
        assignments.append("updated_at = ?")
        params.append(utc_now_iso())
        params.append(session_id)
        with self._conn() as conn:
            result = conn.execute(
                f"UPDATE chat_sessions SET {', '.join(assignments)} WHERE session_id = ?",
                params,
            )
            if result.rowcount == 0:
                raise ValueError(f"chat session not found: {session_id}")

    def save_chat_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        message_id = uuid4().hex
        now = utc_now_iso()
        with self._conn() as conn:
            session = conn.execute(
                "SELECT session_id FROM chat_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if not session:
                raise ValueError(f"chat session not found: {session_id}")
            conn.execute(
                """
                INSERT INTO chat_messages(message_id, session_id, role, content, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (message_id, session_id, role, content, _json_dumps(metadata or {}), now),
            )
            conn.execute(
                "UPDATE chat_sessions SET updated_at = ?, last_message_at = ? WHERE session_id = ?",
                (now, now, session_id),
            )
        return message_id

    def list_chat_messages(self, session_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM chat_messages
                WHERE session_id = ?
                ORDER BY created_at ASC, rowid ASC
                """,
                (session_id,),
            ).fetchall()
        messages = []
        for row in rows:
            data = dict(row)
            data["metadata"] = _json_loads(data.pop("metadata_json"), {})
            messages.append(data)
        return messages

    def get_chat_message(self, message_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM chat_messages WHERE message_id = ?",
                (message_id,),
            ).fetchone()
        if not row:
            return None
        message = dict(row)
        message["metadata"] = _json_loads(message.pop("metadata_json"), {})
        return message

    def stage_assistant_message(
        self,
        run_id: str,
        session_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        message_id = uuid4().hex
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            run = conn.execute(
                "SELECT session_id, status FROM agent_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if not run:
                raise ValueError(f"agent run not found: {run_id}")
            if run["session_id"] != session_id:
                raise ValueError("staged assistant session does not match Run")
            if run["status"] != RunStatus.RUNNING.value:
                raise ValueError("assistant output can only be staged for a running Run")
            conn.execute(
                """
                INSERT INTO staged_assistant_messages(
                    run_id, message_id, session_id, content, metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    message_id,
                    session_id,
                    content,
                    _json_dumps(metadata or {}),
                    utc_now_iso(),
                ),
            )
        return message_id

    def get_staged_assistant_message(self, run_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM staged_assistant_messages WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["metadata"] = _json_loads(data.pop("metadata_json"), {})
        return data

    def get_conversation_summary(self, session_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM conversation_summaries WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["summary"] = _json_loads(data.pop("summary_json"), {})
        return data

    def upsert_conversation_summary(
        self,
        session_id: str,
        summary: dict[str, Any],
        source_message_count: int,
        source_hash: str,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO conversation_summaries(
                    session_id, summary_json, source_message_count, source_hash, updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    summary_json = excluded.summary_json,
                    source_message_count = excluded.source_message_count,
                    source_hash = excluded.source_hash,
                    updated_at = excluded.updated_at
                """,
                (
                    session_id,
                    _json_dumps(summary),
                    int(source_message_count),
                    str(source_hash),
                    now,
                ),
            )
        result = self.get_conversation_summary(session_id)
        if not result:
            raise RuntimeError(f"failed to persist conversation summary: {session_id}")
        return result

    def create_agent_run(
        self,
        session_id: str,
        user_message_id: str,
        mode: str,
        model_id: str,
        skill_id: str,
        metadata: dict[str, Any] | None = None,
        status: RunStatus | str = RunStatus.CREATED,
        run_id: str | None = None,
    ) -> str:
        run_id = str(run_id or uuid4().hex)
        normalized_status = RunStatus(status)
        with self._conn() as conn:
            session = conn.execute(
                "SELECT session_id FROM chat_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if not session:
                raise ValueError(f"chat session not found: {session_id}")
            conn.execute(
                """
                INSERT INTO agent_runs(
                    run_id, session_id, user_message_id, assistant_message_id,
                    mode, model_id, skill_id, status, metadata_json, metrics_json,
                    artifacts_json, error, started_at, finished_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    session_id,
                    user_message_id,
                    "",
                    mode,
                    model_id,
                    skill_id,
                    normalized_status.value,
                    _json_dumps(metadata or {}),
                    "{}",
                    "[]",
                    "",
                    utc_now_iso(),
                    "",
                ),
            )
        return run_id

    def claim_agent_run(self, run_id: str) -> bool:
        require_run_transition(RunStatus.CREATED, RunStatus.RUNNING)
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            result = conn.execute(
                """
                UPDATE agent_runs
                SET status = ?
                WHERE run_id = ? AND status = ?
                """,
                (
                    RunStatus.RUNNING.value,
                    run_id,
                    RunStatus.CREATED.value,
                ),
            )
            if result.rowcount:
                return True
            exists = conn.execute(
                "SELECT 1 FROM agent_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if not exists:
                raise ValueError(f"agent run not found: {run_id}")
            return False

    def update_agent_run_metadata(self, run_id: str, patch: dict[str, Any]) -> None:
        if not isinstance(patch, dict):
            raise TypeError("agent run metadata patch must be a dict")
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT metadata_json FROM agent_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"agent run not found: {run_id}")
            metadata = _json_loads(row["metadata_json"], {})
            metadata.update(patch)
            conn.execute(
                "UPDATE agent_runs SET metadata_json = ? WHERE run_id = ?",
                (_json_dumps(metadata), run_id),
            )

    def transition_agent_run_status(
        self,
        run_id: str,
        status: RunStatus | str,
        *,
        assistant_message_id: str | None = None,
        metrics: dict[str, Any] | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        error: str | None = None,
    ) -> None:
        target = RunStatus(status)
        terminal_statuses = {
            RunStatus.DONE,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.INTERRUPTED_FAILED,
        }
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM agent_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"agent run not found: {run_id}")
            current = RunStatus(row["status"])
            require_run_transition(current, target)
            conn.execute(
                """
                UPDATE agent_runs
                SET status = ?, assistant_message_id = ?, metrics_json = ?,
                    artifacts_json = ?, error = ?, finished_at = ?
                WHERE run_id = ?
                """,
                (
                    target.value,
                    row["assistant_message_id"]
                    if assistant_message_id is None
                    else assistant_message_id,
                    row["metrics_json"] if metrics is None else _json_dumps(metrics),
                    row["artifacts_json"] if artifacts is None else _json_dumps(artifacts),
                    row["error"] if error is None else error,
                    utc_now_iso() if target in terminal_statuses else row["finished_at"],
                    run_id,
                ),
            )

    def finish_agent_run(
        self,
        run_id: str,
        status: RunStatus | str,
        assistant_message_id: str = "",
        metrics: dict[str, Any] | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        error: str = "",
    ) -> None:
        self.transition_agent_run_status(
            run_id,
            status,
            assistant_message_id=assistant_message_id,
            metrics=metrics or {},
            artifacts=artifacts or [],
            error=error,
        )

    def commit_agent_run_terminal(
        self,
        run_id: str,
        status: RunStatus | str,
        *,
        publish_assistant: bool,
        final_answer_step: dict[str, Any] | None,
        run_stopped_step: dict[str, Any],
        metadata_patch: dict[str, Any],
        metrics: dict[str, Any],
        artifacts: list[dict[str, Any]],
        error: str = "",
        memory_writes: list[dict[str, Any]] | None = None,
    ) -> str:
        target = RunStatus(status)
        if target not in {RunStatus.DONE, RunStatus.FAILED, RunStatus.CANCELLED}:
            raise ValueError(f"unsupported Task 4 terminal status: {target.value}")
        if publish_assistant and target is not RunStatus.DONE:
            raise ValueError("only a done Run may publish staged assistant output")
        if publish_assistant and final_answer_step is None:
            raise ValueError("publishing assistant output requires final_answer_step")
        require_run_transition(RunStatus.RUNNING, target)

        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            run = conn.execute(
                "SELECT * FROM agent_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if not run:
                raise ValueError(f"agent run not found: {run_id}")
            current = RunStatus(run["status"])
            require_run_transition(current, target)
            staged = conn.execute(
                "SELECT * FROM staged_assistant_messages WHERE run_id = ?",
                (run_id,),
            ).fetchone()

            assistant_message_id = ""
            now = utc_now_iso()
            if publish_assistant:
                if not staged:
                    raise ValueError("Run has no staged assistant output to publish")
                if staged["session_id"] != run["session_id"]:
                    raise ValueError("staged assistant session does not match Run")
                assistant_message_id = str(staged["message_id"])
                conn.execute(
                    """
                    INSERT INTO chat_messages(
                        message_id, session_id, role, content, metadata_json, created_at
                    )
                    VALUES (?, ?, 'assistant', ?, ?, ?)
                    """,
                    (
                        assistant_message_id,
                        staged["session_id"],
                        staged["content"],
                        staged["metadata_json"],
                        staged["created_at"],
                    ),
                )
                conn.execute(
                    """
                    UPDATE chat_sessions
                    SET updated_at = ?, last_message_at = ?
                    WHERE session_id = ?
                    """,
                    (now, now, staged["session_id"]),
                )

                for memory_write in memory_writes or []:
                    namespace = _normalize_memory_identity(
                        memory_write.get("namespace")
                    )
                    memory_type = _normalize_memory_identity(
                        memory_write.get("memory_type")
                    )
                    content = _normalize_memory_identity(memory_write.get("content"))
                    proposed_memory_id = _normalize_memory_identity(
                        memory_write.get("memory_id")
                    )
                    deduplicate = bool(memory_write.get("deduplicate"))
                    memory_created = True
                    if deduplicate:
                        if not proposed_memory_id:
                            raise ValueError(
                                "deduplicated memory write requires a proposed memory_id"
                            )
                        existing = conn.execute(
                            """
                            SELECT id FROM memory_items
                            WHERE TRIM(namespace) = ? AND TRIM(memory_type) = ?
                                AND TRIM(content) = ?
                            ORDER BY created_at DESC
                            LIMIT 1
                            """,
                            (namespace, memory_type, content),
                        ).fetchone()
                        if existing:
                            persisted_memory_id = str(existing["id"])
                            if persisted_memory_id != proposed_memory_id:
                                raise ValueError(
                                    "deduplicated memory id does not match proposed id"
                                )
                            memory_created = False
                        elif proposed_memory_id != canonical_memory_id(
                            namespace,
                            memory_type,
                            content,
                        ):
                            raise ValueError(
                                "new deduplicated memory write requires canonical memory_id"
                            )
                    memory_id = proposed_memory_id or uuid4().hex
                    if memory_created:
                        conn.execute(
                            """
                            INSERT INTO memory_items(
                                id, namespace, memory_type, content, importance,
                                metadata, created_at
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                memory_id,
                                namespace,
                                memory_type,
                                content,
                                float(memory_write.get("importance") or 0.5),
                                _json_dumps(memory_write.get("metadata") or {}),
                                now,
                            ),
                        )
                    memory_step = dict(memory_write.get("step") or {})
                    output_payload = dict(memory_step.get("output_payload") or {})
                    output_payload["memory_id"] = memory_id
                    output_payload["created"] = memory_created
                    output_payload["deduplicated"] = not memory_created
                    memory_step["output_payload"] = output_payload
                    self._insert_agent_step_conn(conn, run_id, memory_step)

                final_step = dict(final_answer_step)
                final_input = dict(final_step.get("input_payload") or {})
                final_input["assistant_message_id"] = assistant_message_id
                final_step["input_payload"] = final_input
                self._insert_agent_step_conn(conn, run_id, final_step)

            self._insert_agent_step_conn(conn, run_id, run_stopped_step)
            metadata = _json_loads(run["metadata_json"], {})
            metadata.update(metadata_patch)
            persisted_artifacts = artifacts if publish_assistant else []
            conn.execute(
                """
                UPDATE agent_runs
                SET status = ?, assistant_message_id = ?, metadata_json = ?,
                    metrics_json = ?, artifacts_json = ?, error = ?, finished_at = ?
                WHERE run_id = ?
                """,
                (
                    target.value,
                    assistant_message_id,
                    _json_dumps(metadata),
                    _json_dumps(metrics),
                    _json_dumps(persisted_artifacts),
                    error,
                    now,
                    run_id,
                ),
            )
            conn.execute(
                "DELETE FROM staged_assistant_messages WHERE run_id = ?",
                (run_id,),
            )
        return assistant_message_id

    def get_agent_run(self, run_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)).fetchone()
        return self._agent_run_from_row(row) if row else None

    def latest_agent_run(self, session_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM agent_runs
                WHERE session_id = ?
                ORDER BY started_at DESC, rowid DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        return self._agent_run_from_row(row) if row else None

    @staticmethod
    def _insert_agent_step_conn(
        conn: sqlite3.Connection,
        run_id: str,
        step: dict[str, Any],
        *,
        step_id: str | None = None,
    ) -> str:
        step_id = str(step_id or uuid4().hex)
        run = conn.execute(
            "SELECT run_id FROM agent_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if not run:
            raise ValueError(f"agent run not found: {run_id}")
        next_index = conn.execute(
            "SELECT COALESCE(MAX(step_index), 0) + 1 FROM agent_steps WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO agent_steps(
                step_id, run_id, step_index, step_type, title, status,
                input_json, output_json, tool_calls_json, gate_result_json,
                metadata_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                step_id,
                run_id,
                int(next_index),
                str(step.get("step_type") or ""),
                str(step.get("title") or ""),
                str(step.get("status") or "done"),
                _json_dumps(step.get("input_payload") or {}),
                _json_dumps(step.get("output_payload") or {}),
                _json_dumps(step.get("tool_calls") or []),
                _json_dumps(step.get("gate_result") or {}),
                _json_dumps(step.get("metadata") or {}),
                utc_now_iso(),
            ),
        )
        return step_id

    def add_agent_step(
        self,
        run_id: str,
        step_type: str,
        title: str,
        status: str,
        input_payload: dict[str, Any] | None = None,
        output_payload: dict[str, Any] | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        gate_result: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        with self._conn() as conn:
            return self._insert_agent_step_conn(
                conn,
                run_id,
                {
                    "step_type": step_type,
                    "title": title,
                    "status": status,
                    "input_payload": input_payload or {},
                    "output_payload": output_payload or {},
                    "tool_calls": tool_calls or [],
                    "gate_result": gate_result or {},
                    "metadata": metadata or {},
                },
            )

    def record_gate_evaluation(
        self,
        *,
        run_id: str,
        span_id: str,
        decision: dict[str, Any],
    ) -> str:
        run_id = str(run_id).strip()
        span_id = str(span_id).strip()
        evaluation_id = str(decision.get("evaluation_id") or "").strip()
        gate_id = str(decision.get("gate_id") or "").strip()
        gate_version = str(decision.get("gate_version") or "").strip()
        schema_name = str(decision.get("schema_name") or "").strip()
        definition_fingerprint = str(
            decision.get("definition_fingerprint") or ""
        ).strip()
        input_hash = str(decision.get("input_hash") or "").strip()
        attempt = decision.get("attempt")
        if (
            not run_id
            or not span_id
            or not evaluation_id
            or not gate_id
            or not gate_version
            or not definition_fingerprint
            or not input_hash
        ):
            raise ValueError("gate evaluation identity is incomplete")
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            raise ValueError("gate evaluation attempt must be a positive integer")

        audit_ref = f"gate-evaluation:{evaluation_id}"
        step_id = f"gate-step:{evaluation_id}"
        input_refs = list(decision.get("input_refs") or [])
        repair_action = decision.get("repair_action")
        audit_metadata = dict(decision.get("audit_metadata") or {})
        record_payload = {
            "evaluation_id": evaluation_id,
            "audit_ref": audit_ref,
            "run_id": run_id,
            "span_id": span_id,
            "attempt": attempt,
            "gate_id": gate_id,
            "gate_version": gate_version,
            "schema_name": schema_name,
            "definition_fingerprint": definition_fingerprint,
            "input_hash": input_hash,
            "input_refs": input_refs,
            "decision": str(decision.get("decision") or ""),
            "reason": str(decision.get("reason") or ""),
            "output_ref": str(decision.get("output_ref") or ""),
            "repair_action": repair_action,
            "audit_metadata": audit_metadata,
            "step_id": step_id,
        }
        record_fingerprint = hashlib.sha256(
            _canonical_json_dumps(record_payload).encode("utf-8")
        ).hexdigest()

        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT audit_ref, record_fingerprint FROM gate_evaluations WHERE evaluation_id = ?",
                (evaluation_id,),
            ).fetchone()
            if existing:
                if str(existing["record_fingerprint"]) != record_fingerprint:
                    raise ValueError(
                        f"conflicting gate evaluation payload: {evaluation_id}"
                    )
                return str(existing["audit_ref"])

            run = conn.execute(
                "SELECT status FROM agent_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if not run:
                raise ValueError(f"agent run not found: {run_id}")
            if str(run["status"]) != RunStatus.RUNNING.value:
                raise ValueError("gate evaluations require a running agent run")

            evaluated_at = utc_now_iso()
            conn.execute(
                """
                INSERT INTO gate_evaluations(
                    evaluation_id, audit_ref, run_id, span_id, attempt,
                    gate_id, gate_version, schema_name, definition_fingerprint,
                    input_hash, input_refs_json, decision, reason, output_ref,
                    repair_action_json, audit_metadata_json, record_fingerprint,
                    step_id, evaluated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evaluation_id,
                    audit_ref,
                    run_id,
                    span_id,
                    attempt,
                    gate_id,
                    gate_version,
                    schema_name,
                    definition_fingerprint,
                    input_hash,
                    _canonical_json_dumps(input_refs),
                    record_payload["decision"],
                    record_payload["reason"],
                    record_payload["output_ref"],
                    _canonical_json_dumps(repair_action),
                    _canonical_json_dumps(audit_metadata),
                    record_fingerprint,
                    step_id,
                    evaluated_at,
                ),
            )
            gate_result = {
                **record_payload,
                "audit_ref": audit_ref,
            }
            self._insert_agent_step_conn(
                conn,
                run_id,
                {
                    "step_type": "gate_decision",
                    "title": f"Gate {gate_id}@{gate_version}",
                    "status": "done",
                    "input_payload": {
                        "schema_name": schema_name,
                        "input_hash": input_hash,
                        "input_refs": input_refs,
                    },
                    "output_payload": {
                        "decision": record_payload["decision"],
                        "reason": record_payload["reason"],
                        "output_ref": record_payload["output_ref"],
                    },
                    "gate_result": gate_result,
                    "metadata": {
                        "loop_phase": "observe",
                        "evaluation_id": evaluation_id,
                        "audit_ref": audit_ref,
                        "definition_fingerprint": definition_fingerprint,
                        "attempt": attempt,
                    },
                },
                step_id=step_id,
            )
        return audit_ref

    def resolve_gate_evaluation(self, audit_ref: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM gate_evaluations WHERE audit_ref = ?",
                (str(audit_ref),),
            ).fetchone()
        return self._gate_evaluation_from_row(row) if row else None

    def list_gate_evaluations(
        self,
        run_id: str,
        *,
        gate_id: str | None = None,
    ) -> list[dict[str, Any]]:
        params: list[Any] = [str(run_id)]
        where = "run_id = ?"
        if gate_id is not None:
            where += " AND gate_id = ?"
            params.append(str(gate_id))
        with self._conn() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM gate_evaluations
                WHERE {where}
                ORDER BY evaluated_at ASC, rowid ASC
                """,
                params,
            ).fetchall()
        return [self._gate_evaluation_from_row(row) for row in rows]

    def list_agent_steps(self, run_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM agent_steps
                WHERE run_id = ?
                ORDER BY step_index ASC, created_at ASC
                """,
                (run_id,),
            ).fetchall()
        return [self._agent_step_from_row(row) for row in rows]

    def _chat_session_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["metadata"] = _json_loads(data.pop("metadata_json"), {})
        return data

    def _agent_run_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["metadata"] = _json_loads(data.pop("metadata_json"), {})
        data["metrics"] = _json_loads(data.pop("metrics_json"), {})
        data["artifacts"] = _json_loads(data.pop("artifacts_json"), [])
        return data

    def _agent_step_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["input_payload"] = _json_loads(data.pop("input_json"), {})
        data["output_payload"] = _json_loads(data.pop("output_json"), {})
        data["tool_calls"] = _json_loads(data.pop("tool_calls_json"), [])
        data["gate_result"] = _json_loads(data.pop("gate_result_json"), {})
        data["metadata"] = _json_loads(data.pop("metadata_json"), {})
        return data

    def _gate_evaluation_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["input_refs"] = _json_loads(data.pop("input_refs_json"), [])
        data["repair_action"] = _json_loads(
            data.pop("repair_action_json"), None
        )
        data["audit_metadata"] = _json_loads(
            data.pop("audit_metadata_json"), {}
        )
        return data

    def index_company_knowledge(self, profile: dict[str, Any], pages: list[dict[str, Any]]) -> None:
        now = utc_now_iso()
        current_page_ids = {str(page.get("path", "")) for page in pages if page.get("path")}
        current_fact_ids = {
            str(fact.get("fact_id", ""))
            for page in pages
            for fact in page.get("facts", [])
            if fact.get("fact_id")
        }
        with self._conn() as conn:
            company_id = str(profile.get("company_id", ""))
            profile_json = json.dumps(profile, ensure_ascii=False, sort_keys=True)
            current_profile = conn.execute(
                "SELECT profile_json FROM companies WHERE company_id = ?",
                (company_id,),
            ).fetchone()
            if not current_profile or current_profile["profile_json"] != profile_json:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO companies(company_id, profile_json, updated_at)
                    VALUES (?, ?, ?)
                    """,
                    (company_id, profile_json, now),
                )
            page_hashes = {
                str(row["page_id"]): str(row["source_hash"])
                for row in conn.execute("SELECT page_id, source_hash FROM wiki_pages").fetchall()
            }
            for page in pages:
                meta = page.get("meta", {})
                path = str(page.get("path", ""))
                source_hash = hashlib.sha256(
                    json.dumps(
                        {"body": page.get("body", ""), "facts": page.get("facts", [])},
                        ensure_ascii=False,
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest()
                if page_hashes.get(path) == source_hash:
                    continue
                conn.execute(
                    """
                    INSERT OR REPLACE INTO wiki_pages(page_id, path, domain, importance, source_hash, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        path,
                        path,
                        str(meta.get("domain", "")),
                        float(meta.get("importance", 0.5) or 0.5),
                        source_hash,
                        now,
                    ),
                )
                for fact in page.get("facts", []):
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO company_facts(
                            fact_id, source_path, value, importance, confidence, source, tags_json, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(fact.get("fact_id", "")),
                            str(fact.get("source_path", path)),
                            str(fact.get("value", "")),
                            float(fact.get("importance", 0.5) or 0.5),
                            float(fact.get("confidence", 0.5) or 0.5),
                            str(fact.get("source", "")),
                            json.dumps(fact.get("policy_relevance", []), ensure_ascii=False),
                            now,
                        ),
                    )
            if current_page_ids:
                placeholders = ",".join("?" for _ in current_page_ids)
                conn.execute(
                    f"DELETE FROM wiki_pages WHERE page_id NOT IN ({placeholders})",
                    sorted(current_page_ids),
                )
            else:
                conn.execute("DELETE FROM wiki_pages")
            if current_fact_ids:
                placeholders = ",".join("?" for _ in current_fact_ids)
                conn.execute(
                    f"DELETE FROM company_facts WHERE fact_id NOT IN ({placeholders})",
                    sorted(current_fact_ids),
                )
            else:
                conn.execute("DELETE FROM company_facts")

    def index_policy_documents(
        self,
        documents: list[dict[str, Any]],
        clauses: list[dict[str, Any]] | None = None,
    ) -> None:
        now = utc_now_iso()
        with self._conn() as conn:
            for doc in documents:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO policy_documents(
                        policy_id, title, issuer, published_at, source_url, source_level, metadata_json, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(doc.get("policy_id", "")),
                        str(doc.get("title", "")),
                        str(doc.get("issuer", "")),
                        str(doc.get("published_at", "")),
                        str(doc.get("source_url", "")),
                        str(doc.get("source_level", "")),
                        json.dumps(doc, ensure_ascii=False),
                        now,
                    ),
                )
            for clause in clauses or []:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO policy_clauses(
                        clause_id, policy_id, clause_type, text, metadata_json, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(clause.get("clause_id", "")),
                        str(clause.get("policy_id", "")),
                        str(clause.get("clause_type", "")),
                        str(clause.get("text", "")),
                        json.dumps(clause, ensure_ascii=False),
                        now,
                    ),
                )

    def find_memory_exact(
        self,
        namespace: str,
        memory_type: str,
        content: str,
    ) -> dict[str, Any] | None:
        normalized_namespace = _normalize_memory_identity(namespace)
        normalized_type = _normalize_memory_identity(memory_type)
        normalized_content = _normalize_memory_identity(content)
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM memory_items
                WHERE TRIM(namespace) = ? AND TRIM(memory_type) = ?
                    AND TRIM(content) = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (normalized_namespace, normalized_type, normalized_content),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["metadata"] = _json_loads(item.get("metadata"), {})
        return item

    def save_memory(self, namespace: str, memory_type: str, content: str, importance: float = 0.5, metadata: dict[str, Any] | None = None) -> str:
        item_id = uuid4().hex
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO memory_items(id, namespace, memory_type, content, importance, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (item_id, namespace, memory_type, content, importance, json.dumps(metadata or {}, ensure_ascii=False), utc_now_iso()),
            )
        return item_id

    def save_memory_once(
        self,
        namespace: str,
        memory_type: str,
        content: str,
        importance: float = 0.5,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[str, bool]:
        """Persist explicit memory once and return ``(memory_id, created)``."""

        normalized_namespace = _normalize_memory_identity(namespace)
        normalized_type = _normalize_memory_identity(memory_type)
        normalized_content = _normalize_memory_identity(content)
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """
                SELECT id FROM memory_items
                WHERE TRIM(namespace) = ? AND TRIM(memory_type) = ?
                    AND TRIM(content) = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (normalized_namespace, normalized_type, normalized_content),
            ).fetchone()
            if existing:
                return str(existing["id"]), False
            item_id = canonical_memory_id(
                normalized_namespace,
                normalized_type,
                normalized_content,
            )
            conn.execute(
                """
                INSERT INTO memory_items(id, namespace, memory_type, content, importance, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    normalized_namespace,
                    normalized_type,
                    normalized_content,
                    importance,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    utc_now_iso(),
                ),
            )
        return item_id, True

    def delete_memory(self, memory_id: str) -> bool:
        """Delete one memory item by id and report whether a row was removed."""

        with self._conn() as conn:
            cursor = conn.execute("DELETE FROM memory_items WHERE id = ?", (str(memory_id),))
        return bool(cursor.rowcount)

    def search_memory(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        terms = [term for term in tokenize(query) if term.strip()]
        if not terms:
            return []
        where = " OR ".join(["content LIKE ?" for _ in terms])
        params = [f"%{term}%" for term in terms] + [top_k]
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM memory_items WHERE {where} ORDER BY importance DESC, created_at DESC LIMIT ?",
                params,
            ).fetchall()
        results = []
        for row in rows:
            data = dict(row)
            data["metadata"] = _json_loads(data.get("metadata"), {})
            results.append(data)
        return results

    def save_report_session(self, report_id: str, run_id: str, report_path: str, metadata: dict[str, Any] | None = None) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO report_sessions(report_id, run_id, report_path, created_at, metadata)
                VALUES (?, ?, ?, ?, ?)
                """,
                (report_id, run_id, report_path, utc_now_iso(), json.dumps(metadata or {}, ensure_ascii=False)),
            )

    def latest_report_session(self) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM report_sessions ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def save_report_message(
        self,
        report_id: str,
        role: str,
        content: str,
        citations: list[dict[str, Any]] | None = None,
    ) -> str:
        message_id = uuid4().hex
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO report_messages(message_id, report_id, role, content, citations_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    report_id,
                    role,
                    content,
                    json.dumps(citations or [], ensure_ascii=False),
                    utc_now_iso(),
                ),
            )
        return message_id

    def list_report_messages(self, report_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM report_messages WHERE report_id = ? ORDER BY created_at ASC",
                (report_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_correction_proposal(self, payload: dict[str, Any]) -> str:
        correction_id = str(payload.get("correction_id") or uuid4().hex)
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO correction_proposals(correction_id, payload, status, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (correction_id, json.dumps(payload, ensure_ascii=False), str(payload.get("status", "pending")), utc_now_iso()),
            )
        return correction_id

    def update_correction_status(self, correction_id: str, status: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE correction_proposals SET status = ? WHERE correction_id = ?",
                (status, correction_id),
            )

    def get_correction_proposal(self, correction_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM correction_proposals WHERE correction_id = ?",
                (correction_id,),
            ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["payload"] = json.loads(data["payload"])
        return data

    def list_correction_proposals(self, status: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM correction_proposals"
        params: list[Any] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC"
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        out = []
        for row in rows:
            data = dict(row)
            data["payload"] = json.loads(data["payload"])
            out.append(data)
        return out

    def save_tool_calls(self, run_id: str, calls: list[dict[str, Any]]) -> None:
        with self._conn() as conn:
            for call in calls:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO tool_calls(
                        call_id, run_id, stage_name, tool_name, allowed, status, payload_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(call.get("call_id")),
                        run_id,
                        str(call.get("stage_name", "")),
                        str(call.get("tool_name", "")),
                        1 if call.get("allowed") else 0,
                        str(call.get("status", "")),
                        json.dumps(call, ensure_ascii=False),
                        str(call.get("started_at") or utc_now_iso()),
                    ),
                )

    def save_run_artifact(
        self,
        run_id: str,
        artifact_path: str,
        status: str,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO run_artifacts(run_id, artifact_path, status, metrics_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    artifact_path,
                    status,
                    json.dumps(metrics or {}, ensure_ascii=False),
                    utc_now_iso(),
                ),
            )
