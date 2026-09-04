"""Closed mutations for Creator threads and visible messages."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from app.core.runtime_write_coordinator import (
    MutationApplication,
    MutationIdentityConflictError,
    RuntimeMutationHandler,
    TypedMutation,
)
from app.core.sqlite_connection_roles import open_bootstrap_database


def bootstrap_thread_store_schema(database_path: str | Path) -> None:
    connection = open_bootstrap_database(database_path)
    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS creator_threads (
                id TEXT PRIMARY KEY,
                workspace_id TEXT,
                brand_id TEXT,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                active_workflow_session_id TEXT,
                active_job_id TEXT,
                active_run_id TEXT,
                accepted_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_threads_created
            ON creator_threads(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_threads_scope
            ON creator_threads(workspace_id, brand_id, created_at DESC);
            CREATE TABLE IF NOT EXISTS creator_messages (
                id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL REFERENCES creator_threads(id),
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                message_type TEXT NOT NULL DEFAULT 'text',
                intent TEXT,
                linked_session_id TEXT,
                linked_job_id TEXT,
                run_id TEXT,
                artifact_refs_json TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_messages_thread
            ON creator_messages(thread_id, created_at ASC);
            CREATE TABLE IF NOT EXISTS publish_candidates (
                candidate_id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                note_id TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_candidates_thread
            ON publish_candidates(thread_id);
            """
        )
        connection.commit()
    finally:
        connection.close()


def _required(payload: dict[str, Any], name: str, expected: type[Any]) -> Any:
    value = payload.get(name)
    if not isinstance(value, expected):
        raise MutationIdentityConflictError()
    return value


class _ThreadMutationHandler:
    mutation_kind = "mutate_creator_thread"

    def apply(
        self,
        connection: sqlite3.Connection,
        mutation: TypedMutation,
    ) -> MutationApplication:
        payload = dict(mutation.domain_payload)
        action = _required(payload, "action", str)
        result: dict[str, Any]

        if action == "create_thread":
            connection.execute(
                """
                INSERT INTO creator_threads
                    (id, workspace_id, brand_id, title, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    _required(payload, "thread_id", str),
                    payload.get("workspace_id"),
                    payload.get("brand_id"),
                    _required(payload, "title", str),
                    _required(payload, "now", str),
                    _required(payload, "now", str),
                ),
            )
            result = {"thread_id": payload["thread_id"]}
        elif action == "append_message":
            message_id = _required(payload, "message_id", str)
            thread_id = _required(payload, "thread_id", str)
            now = _required(payload, "now", str)
            connection.execute(
                """
                INSERT INTO creator_messages (
                    id, thread_id, role, text, message_type, intent,
                    linked_session_id, linked_job_id, run_id, artifact_refs_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    thread_id,
                    _required(payload, "role", str),
                    _required(payload, "text", str),
                    _required(payload, "message_type", str),
                    payload.get("intent"),
                    payload.get("linked_session_id"),
                    payload.get("linked_job_id"),
                    payload.get("run_id"),
                    payload.get("artifact_refs_json"),
                    now,
                ),
            )
            connection.execute(
                "UPDATE creator_threads SET updated_at=? WHERE id=?",
                (now, thread_id),
            )
            result = {"message_id": message_id}
        elif action == "complete_thread":
            thread_id = _required(payload, "thread_id", str)
            now = _required(payload, "now", str)
            connection.execute(
                """
                UPDATE creator_threads SET status='accepted', accepted_at=?, updated_at=?
                WHERE id=?
                """,
                (now, now, thread_id),
            )
            result = {"thread_id": thread_id}
        elif action == "save_publish_candidates":
            candidates = payload.get("candidates")
            if not isinstance(candidates, list):
                raise MutationIdentityConflictError()
            ids: list[str] = []
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    raise MutationIdentityConflictError()
                candidate_id = _required(candidate, "candidate_id", str)
                ids.append(candidate_id)
                connection.execute(
                    """
                    INSERT OR IGNORE INTO publish_candidates
                        (candidate_id, thread_id, session_id, note_id, title, content, tags, created_at)
                    SELECT ?, ?, ?, ?, ?, ?, ?, ?
                    WHERE NOT EXISTS (
                        SELECT 1 FROM publish_candidates WHERE thread_id=? AND note_id=?
                    )
                    """,
                    (
                        candidate_id,
                        _required(candidate, "thread_id", str),
                        _required(candidate, "session_id", str),
                        _required(candidate, "note_id", str),
                        _required(candidate, "title", str),
                        _required(candidate, "content", str),
                        _required(candidate, "tags", str),
                        _required(candidate, "now", str),
                        candidate["thread_id"],
                        candidate["note_id"],
                    ),
                )
            result = {"candidate_ids": ids}
        elif action == "update_title":
            thread_id = _required(payload, "thread_id", str)
            connection.execute(
                "UPDATE creator_threads SET title=?, updated_at=? WHERE id=?",
                (
                    _required(payload, "title", str),
                    _required(payload, "now", str),
                    thread_id,
                ),
            )
            result = {"thread_id": thread_id}
        elif action == "delete_thread":
            thread_id = _required(payload, "thread_id", str)
            connection.execute(
                "DELETE FROM publish_candidates WHERE thread_id=?", (thread_id,)
            )
            connection.execute(
                "DELETE FROM creator_messages WHERE thread_id=?", (thread_id,)
            )
            cursor = connection.execute(
                "DELETE FROM creator_threads WHERE id=?", (thread_id,)
            )
            result = {"deleted": cursor.rowcount > 0}
        elif action == "update_active_job":
            thread_id = _required(payload, "thread_id", str)
            connection.execute(
                """
                UPDATE creator_threads SET active_workflow_session_id=?, active_job_id=?,
                    updated_at=? WHERE id=?
                """,
                (
                    payload.get("session_id"),
                    payload.get("job_id"),
                    _required(payload, "now", str),
                    thread_id,
                ),
            )
            result = {"thread_id": thread_id}
        elif action == "update_active_run":
            thread_id = _required(payload, "thread_id", str)
            connection.execute(
                "UPDATE creator_threads SET active_run_id=?, updated_at=? WHERE id=?",
                (payload.get("run_id"), _required(payload, "now", str), thread_id),
            )
            result = {"thread_id": thread_id}
        else:
            raise MutationIdentityConflictError()

        return MutationApplication(
            result_contract="creator_thread_mutation_result",
            result_fields=result,
        )


def thread_mutation_handlers() -> tuple[RuntimeMutationHandler, ...]:
    return (_ThreadMutationHandler(),)
