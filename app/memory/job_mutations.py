"""Closed, typed mutations for the runtime job queue."""

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


def bootstrap_job_store_schema(database_path: str | Path) -> None:
    """Prepare the job schema before the runtime writer starts."""
    connection = open_bootstrap_database(database_path)
    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                user_query TEXT NOT NULL,
                platform TEXT NOT NULL DEFAULT 'xiaohongshu',
                mode TEXT NOT NULL DEFAULT 'editing',
                stage TEXT NOT NULL DEFAULT 'init',
                lifecycle_state TEXT NOT NULL DEFAULT 'alive',
                alive_until TIMESTAMP,
                spider_cooldown_until TIMESTAMP,
                purge_after TIMESTAMP,
                frozen_at TIMESTAMP,
                purged_at TIMESTAMP,
                pause_requested BOOLEAN NOT NULL DEFAULT FALSE,
                pause_requested_at TIMESTAMP,
                spider_note_ids TEXT,
                strategy_id TEXT,
                proposal_ids TEXT,
                generated_note_ids TEXT,
                similarity_report TEXT,
                quality_score REAL DEFAULT 0.0,
                used_fallback BOOLEAN DEFAULT FALSE,
                retry_stats TEXT,
                expanded_queries TEXT,
                reindex_state TEXT NOT NULL DEFAULT 'ok',
                reindex_attempts INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                error_code TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_activity_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_user_activity_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                job_type TEXT NOT NULL CHECK (job_type IN ('strategy', 'generate')),
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN ('queued', 'paused', 'running', 'retrying', 'succeeded', 'failed', 'cancelled')
                ),
                priority INTEGER NOT NULL DEFAULT 100,
                attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 5,
                not_before TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                lease_expires_at TIMESTAMP,
                idempotency_key TEXT,
                last_error_code TEXT,
                last_error_message TEXT,
                cancel_reason TEXT,
                run_id TEXT,
                step_id TEXT,
                child_task_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_runnable
            ON jobs(status, not_before, priority, created_at);
            CREATE INDEX IF NOT EXISTS idx_jobs_workflow_refs
            ON jobs(run_id, step_id, child_task_id);
            CREATE INDEX IF NOT EXISTS idx_jobs_lease ON jobs(lease_expires_at);
            CREATE UNIQUE INDEX IF NOT EXISTS uq_jobs_dedupe
            ON jobs(session_id, job_type, idempotency_key)
            WHERE idempotency_key IS NOT NULL;
            CREATE TABLE IF NOT EXISTS session_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                job_id TEXT,
                event_name TEXT NOT NULL,
                stage TEXT,
                payload_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_session_events_session_id
            ON session_events(session_id, event_id);
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


class _EnqueueJobHandler:
    mutation_kind = "enqueue_job"

    def apply(
        self,
        connection: sqlite3.Connection,
        mutation: TypedMutation,
    ) -> MutationApplication:
        payload = dict(mutation.domain_payload)
        job_id = _required(payload, "job_id", str)
        session_id = _required(payload, "session_id", str)
        job_type = _required(payload, "job_type", str)
        payload_json = _required(payload, "payload_json", str)
        priority = _required(payload, "priority", int)
        max_attempts = _required(payload, "max_attempts", int)
        idempotency_key = payload.get("idempotency_key")
        if idempotency_key is not None and not isinstance(idempotency_key, str):
            raise MutationIdentityConflictError()

        if idempotency_key is not None:
            existing = connection.execute(
                """
                SELECT id FROM jobs
                WHERE session_id = ? AND job_type = ? AND idempotency_key = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (session_id, job_type, idempotency_key),
            ).fetchone()
            if existing is not None:
                return MutationApplication(
                    result_contract="job_enqueue_result",
                    result_fields={"job_id": str(existing[0]), "created": False},
                )

        connection.execute(
            """
            INSERT INTO jobs (
                id, session_id, job_type, payload_json, status,
                priority, attempts, max_attempts,
                not_before, idempotency_key, run_id, step_id, child_task_id,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'queued', ?, 0, ?, CURRENT_TIMESTAMP, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                job_id,
                session_id,
                job_type,
                payload_json,
                priority,
                max_attempts,
                idempotency_key,
                payload.get("run_id"),
                payload.get("step_id"),
                payload.get("child_task_id"),
            ),
        )
        return MutationApplication(
            result_contract="job_enqueue_result",
            result_fields={"job_id": job_id, "created": True},
        )


class _LeaseJobHandler:
    mutation_kind = "lease_job"

    def apply(
        self,
        connection: sqlite3.Connection,
        mutation: TypedMutation,
    ) -> MutationApplication:
        ttl = _required(dict(mutation.domain_payload), "lease_seconds", int)
        row = connection.execute(
            """
            WITH candidate AS (
                SELECT j.id
                FROM jobs j
                LEFT JOIN sessions s ON s.session_id = j.session_id
                WHERE j.status IN ('queued', 'retrying')
                  AND j.not_before <= CURRENT_TIMESTAMP
                  AND (j.run_id IS NOT NULL OR s.lifecycle_state = 'alive')
                  AND NOT EXISTS (
                      SELECT 1 FROM jobs r
                      WHERE r.session_id = j.session_id AND r.status = 'running'
                  )
                ORDER BY j.priority ASC, j.created_at ASC
                LIMIT 1
            )
            UPDATE jobs
            SET status = 'running', attempts = attempts + 1,
                lease_expires_at = DATETIME(CURRENT_TIMESTAMP, ?),
                updated_at = CURRENT_TIMESTAMP
            WHERE id IN (SELECT id FROM candidate)
            RETURNING id
            """,
            (f"+{ttl} seconds",),
        ).fetchone()
        return MutationApplication(
            result_contract="job_lease_result",
            result_fields={"job_id": None if row is None else str(row[0])},
        )


class _RecoverExpiredJobsHandler:
    mutation_kind = "recover_expired_jobs"

    def apply(
        self,
        connection: sqlite3.Connection,
        mutation: TypedMutation,
    ) -> MutationApplication:
        del mutation
        rows = connection.execute(
            """
            SELECT id FROM jobs
            WHERE status = 'running' AND lease_expires_at < CURRENT_TIMESTAMP
            """
        ).fetchall()
        job_ids = [str(row[0]) for row in rows]
        connection.execute(
            """
            UPDATE jobs
            SET status = CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'retrying' END,
                not_before = CASE
                    WHEN attempts >= max_attempts THEN not_before ELSE CURRENT_TIMESTAMP
                END,
                lease_expires_at = NULL,
                last_error_code = COALESCE(last_error_code, 'LEASE_EXPIRED'),
                last_error_message = COALESCE(
                    last_error_message, 'worker lease expired before ack'
                ),
                updated_at = CURRENT_TIMESTAMP
            WHERE status = 'running' AND lease_expires_at < CURRENT_TIMESTAMP
            """
        )
        return MutationApplication(
            result_contract="expired_jobs_recovered",
            result_fields={"job_ids": job_ids, "recovered": len(job_ids)},
        )


class _MarkJobSucceededHandler:
    mutation_kind = "mark_job_succeeded"

    def apply(
        self,
        connection: sqlite3.Connection,
        mutation: TypedMutation,
    ) -> MutationApplication:
        job_id = _required(dict(mutation.domain_payload), "job_id", str)
        cursor = connection.execute(
            """
            UPDATE jobs SET status = 'succeeded', lease_expires_at = NULL,
                updated_at = CURRENT_TIMESTAMP WHERE id = ?
            """,
            (job_id,),
        )
        return MutationApplication(
            result_contract="job_transition_result",
            result_fields={"updated": cursor.rowcount > 0},
        )


class _MarkJobFailedHandler:
    mutation_kind = "mark_job_failed"

    def apply(
        self,
        connection: sqlite3.Connection,
        mutation: TypedMutation,
    ) -> MutationApplication:
        payload = dict(mutation.domain_payload)
        job_id = _required(payload, "job_id", str)
        error_code = _required(payload, "error_code", str)
        error_message = _required(payload, "error_message", str)
        cursor = connection.execute(
            """
            UPDATE jobs SET status = 'failed', lease_expires_at = NULL,
                last_error_code = ?, last_error_message = ?,
                updated_at = CURRENT_TIMESTAMP WHERE id = ?
            """,
            (error_code, error_message, job_id),
        )
        return MutationApplication(
            result_contract="job_transition_result",
            result_fields={"updated": cursor.rowcount > 0},
        )


class _ScheduleJobRetryHandler:
    mutation_kind = "schedule_job_retry"

    def apply(
        self,
        connection: sqlite3.Connection,
        mutation: TypedMutation,
    ) -> MutationApplication:
        payload = dict(mutation.domain_payload)
        job_id = _required(payload, "job_id", str)
        delay_seconds = _required(payload, "delay_seconds", int)
        error_code = _required(payload, "error_code", str)
        error_message = _required(payload, "error_message", str)
        cursor = connection.execute(
            """
            UPDATE jobs SET status = 'retrying',
                not_before = DATETIME(CURRENT_TIMESTAMP, ?), lease_expires_at = NULL,
                last_error_code = ?, last_error_message = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (f"+{delay_seconds} seconds", error_code, error_message, job_id),
        )
        return MutationApplication(
            result_contract="job_transition_result",
            result_fields={"updated": cursor.rowcount > 0},
        )


class _SingleJobTransitionHandler:
    def __init__(self, mutation_kind: str, statement: str, *, takes_reason: bool = False):
        self.mutation_kind = mutation_kind
        self._statement = statement
        self._takes_reason = takes_reason

    def apply(
        self,
        connection: sqlite3.Connection,
        mutation: TypedMutation,
    ) -> MutationApplication:
        payload = dict(mutation.domain_payload)
        job_id = _required(payload, "job_id", str)
        params: tuple[object, ...]
        if self._takes_reason:
            params = (_required(payload, "reason", str), job_id)
        else:
            params = (job_id,)
        connection.execute(self._statement, params)
        return MutationApplication(
            result_contract="job_transition_result",
            result_fields={"job_id": job_id},
        )


class _BatchJobTransitionHandler:
    def __init__(
        self,
        mutation_kind: str,
        statement: str,
        scope_field: str,
        *,
        takes_reason: bool = False,
    ):
        self.mutation_kind = mutation_kind
        self._statement = statement
        self._scope_field = scope_field
        self._takes_reason = takes_reason

    def apply(
        self,
        connection: sqlite3.Connection,
        mutation: TypedMutation,
    ) -> MutationApplication:
        payload = dict(mutation.domain_payload)
        scope = _required(payload, self._scope_field, str)
        params: tuple[object, ...]
        if self._takes_reason:
            params = (_required(payload, "reason", str), scope)
        else:
            params = (scope,)
        cursor = connection.execute(self._statement, params)
        return MutationApplication(
            result_contract="job_batch_transition_result",
            result_fields={"updated": cursor.rowcount},
        )


class _AppendSessionEventHandler:
    mutation_kind = "append_session_event"

    def apply(
        self,
        connection: sqlite3.Connection,
        mutation: TypedMutation,
    ) -> MutationApplication:
        payload = dict(mutation.domain_payload)
        row = connection.execute(
            """
            INSERT INTO session_events(session_id, job_id, event_name, stage, payload_json)
            VALUES (?, ?, ?, ?, ?) RETURNING event_id
            """,
            (
                _required(payload, "session_id", str),
                payload.get("job_id"),
                _required(payload, "event_name", str),
                payload.get("stage"),
                _required(payload, "payload_json", str),
            ),
        ).fetchone()
        if row is None:
            raise MutationIdentityConflictError()
        return MutationApplication(
            result_contract="session_event_appended",
            result_fields={"event_id": int(row[0])},
        )


def job_mutation_handlers() -> tuple[RuntimeMutationHandler, ...]:
    return (
        _EnqueueJobHandler(),
        _LeaseJobHandler(),
        _RecoverExpiredJobsHandler(),
        _MarkJobSucceededHandler(),
        _MarkJobFailedHandler(),
        _ScheduleJobRetryHandler(),
        _SingleJobTransitionHandler(
            "pause_job",
            "UPDATE jobs SET status='paused', updated_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND status IN ('queued', 'retrying')",
        ),
        _SingleJobTransitionHandler(
            "resume_job",
            "UPDATE jobs SET status='queued', not_before=CURRENT_TIMESTAMP, "
            "updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='paused'",
        ),
        _SingleJobTransitionHandler(
            "cancel_job",
            "UPDATE jobs SET status='cancelled', cancel_reason=?, lease_expires_at=NULL, "
            "updated_at=CURRENT_TIMESTAMP WHERE id=? "
            "AND status IN ('queued', 'paused', 'retrying', 'running')",
            takes_reason=True,
        ),
        _BatchJobTransitionHandler(
            "pause_session_jobs",
            "UPDATE jobs SET status='paused', updated_at=CURRENT_TIMESTAMP "
            "WHERE session_id=? AND status IN ('queued', 'retrying')",
            "session_id",
        ),
        _BatchJobTransitionHandler(
            "resume_session_jobs",
            "UPDATE jobs SET status='queued', not_before=CURRENT_TIMESTAMP, "
            "updated_at=CURRENT_TIMESTAMP WHERE session_id=? AND status='paused'",
            "session_id",
        ),
        _BatchJobTransitionHandler(
            "cancel_session_jobs",
            "UPDATE jobs SET status='cancelled', cancel_reason=?, lease_expires_at=NULL, "
            "updated_at=CURRENT_TIMESTAMP WHERE session_id=? "
            "AND status IN ('queued', 'paused', 'retrying', 'running')",
            "session_id",
            takes_reason=True,
        ),
        _BatchJobTransitionHandler(
            "pause_workflow_jobs",
            "UPDATE jobs SET status='paused', updated_at=CURRENT_TIMESTAMP "
            "WHERE run_id=? AND status IN ('queued', 'retrying')",
            "run_id",
        ),
        _BatchJobTransitionHandler(
            "resume_workflow_jobs",
            "UPDATE jobs SET status='queued', not_before=CURRENT_TIMESTAMP, "
            "updated_at=CURRENT_TIMESTAMP WHERE run_id=? AND status='paused'",
            "run_id",
        ),
        _BatchJobTransitionHandler(
            "cancel_workflow_jobs",
            "UPDATE jobs SET status='cancelled', cancel_reason=?, lease_expires_at=NULL, "
            "updated_at=CURRENT_TIMESTAMP WHERE run_id=? "
            "AND status IN ('queued', 'paused', 'retrying', 'running')",
            "run_id",
            takes_reason=True,
        ),
        _AppendSessionEventHandler(),
    )
