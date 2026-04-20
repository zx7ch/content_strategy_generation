"""Persistent SQLite job queue for background task execution."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Optional

import aiosqlite

from app.config import settings
from app.logging_config import get_logger, log_event


@dataclass(slots=True)
class JobRecord:
    """A row from the jobs table."""

    id: str
    session_id: str
    job_type: str
    payload_json: str
    status: str
    priority: int
    attempts: int
    max_attempts: int
    not_before: Optional[str]
    lease_expires_at: Optional[str]
    idempotency_key: Optional[str]
    last_error_code: Optional[str]
    last_error_message: Optional[str]
    cancel_reason: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]

    @property
    def payload(self) -> dict[str, Any]:
        try:
            data = json.loads(self.payload_json)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
        return {}


@dataclass(slots=True)
class SessionEventRecord:
    event_id: int
    session_id: str
    job_id: Optional[str]
    event_name: str
    stage: Optional[str]
    payload_json: str
    created_at: str

    @property
    def payload(self) -> dict[str, Any]:
        try:
            data = json.loads(self.payload_json)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
        return {}


class JobStore:
    """SQLite-backed job queue with lease-based consumption."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or settings.SQLITE_DB_PATH
        self._conn: Optional[aiosqlite.Connection] = None
        self._logger = get_logger(__name__, component="worker")

    async def __aenter__(self) -> "JobStore":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def connect(self) -> None:
        if self._conn is not None:
            return

        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._init_tables()

    async def close(self) -> None:
        if self._conn is None:
            return
        await self._conn.close()
        self._conn = None

    async def _init_tables(self) -> None:
        assert self._conn is not None

        # Keep sessions schema aligned with SessionManager so job queue can safely
        # run even when it initializes tables first.
        await self._conn.execute(
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
            )
            """
        )

        await self._conn.execute(
            """
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_runnable ON jobs(status, not_before, priority, created_at)"
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_lease ON jobs(lease_expires_at)"
        )
        await self._conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_jobs_dedupe
            ON jobs(session_id, job_type, idempotency_key)
            WHERE idempotency_key IS NOT NULL
            """
        )

        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS session_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                job_id TEXT,
                event_name TEXT NOT NULL,
                stage TEXT,
                payload_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_events_session_id ON session_events(session_id, event_id)"
        )
        await self._conn.commit()

    @staticmethod
    def _row_to_job(row: aiosqlite.Row) -> JobRecord:
        return JobRecord(
            id=row["id"],
            session_id=row["session_id"],
            job_type=row["job_type"],
            payload_json=row["payload_json"],
            status=row["status"],
            priority=int(row["priority"]),
            attempts=int(row["attempts"]),
            max_attempts=int(row["max_attempts"]),
            not_before=row["not_before"],
            lease_expires_at=row["lease_expires_at"],
            idempotency_key=row["idempotency_key"],
            last_error_code=row["last_error_code"],
            last_error_message=row["last_error_message"],
            cancel_reason=row["cancel_reason"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_session_event(row: aiosqlite.Row) -> SessionEventRecord:
        return SessionEventRecord(
            event_id=int(row["event_id"]),
            session_id=row["session_id"],
            job_id=row["job_id"],
            event_name=row["event_name"],
            stage=row["stage"],
            payload_json=row["payload_json"],
            created_at=row["created_at"],
        )

    async def get_job(self, job_id: str) -> Optional[JobRecord]:
        assert self._conn is not None
        async with self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_job(row)

    async def count_jobs(self, session_id: str, job_type: Optional[str] = None) -> int:
        assert self._conn is not None
        sql = "SELECT COUNT(*) AS c FROM jobs WHERE session_id = ?"
        params: list[Any] = [session_id]
        if job_type:
            sql += " AND job_type = ?"
            params.append(job_type)
        async with self._conn.execute(sql, params) as cursor:
            row = await cursor.fetchone()
        return int(row["c"])

    async def get_latest_job_for_session(self, session_id: str) -> Optional[JobRecord]:
        assert self._conn is not None
        async with self._conn.execute(
            """
            SELECT *
            FROM jobs
            WHERE session_id = ?
            ORDER BY
                CASE
                    WHEN status IN ('running', 'retrying', 'queued', 'paused') THEN 0
                    ELSE 1
                END,
                updated_at DESC,
                created_at DESC
            LIMIT 1
            """,
            (session_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_job(row)

    async def get_job_by_idempotency(
        self,
        *,
        session_id: str,
        job_type: str,
        idempotency_key: str,
    ) -> Optional[JobRecord]:
        assert self._conn is not None
        async with self._conn.execute(
            """
            SELECT *
            FROM jobs
            WHERE session_id = ?
              AND job_type = ?
              AND idempotency_key = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (session_id, job_type, idempotency_key),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_job(row)

    async def enqueue(
        self,
        *,
        session_id: str,
        job_type: str,
        payload: Optional[dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        priority: int = 100,
        max_attempts: Optional[int] = None,
    ) -> tuple[JobRecord, bool]:
        """Enqueue a job. Returns (job, created)."""
        assert self._conn is not None

        if job_type not in {"strategy", "generate"}:
            raise ValueError(f"Unsupported job_type: {job_type}")

        if idempotency_key:
            async with self._conn.execute(
                """
                SELECT * FROM jobs
                WHERE session_id = ? AND job_type = ? AND idempotency_key = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (session_id, job_type, idempotency_key),
            ) as cursor:
                existing = await cursor.fetchone()
                if existing is not None:
                    return self._row_to_job(existing), False

        job_id = f"job_{uuid.uuid4().hex[:16]}"
        payload_json = json.dumps(payload or {}, ensure_ascii=False, default=str)
        effective_max_attempts = settings.JOB_MAX_RETRIES if max_attempts is None else max_attempts

        await self._conn.execute(
            """
            INSERT INTO jobs (
                id, session_id, job_type, payload_json, status,
                priority, attempts, max_attempts,
                not_before, idempotency_key, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'queued', ?, 0, ?, CURRENT_TIMESTAMP, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (job_id, session_id, job_type, payload_json, priority, effective_max_attempts, idempotency_key),
        )
        await self._conn.commit()

        job = await self.get_job(job_id)
        assert job is not None
        log_event(
            self._logger,
            event_name="job_enqueued",
            level="info",
            component="worker",
            session_id=session_id,
            job_id=job_id,
            stage=job_type,
            status="queued",
            idempotency_key=idempotency_key,
        )
        return job, True

    async def lease_one(self, lease_seconds: Optional[int] = None) -> Optional[JobRecord]:
        """Lease one runnable job; atomically transition status to running."""
        assert self._conn is not None

        ttl = lease_seconds or settings.JOB_LEASE_SECONDS
        await self._conn.execute("BEGIN IMMEDIATE")
        async with self._conn.execute(
            """
            WITH candidate AS (
                SELECT j.id
                FROM jobs j
                JOIN sessions s ON s.session_id = j.session_id
                WHERE j.status IN ('queued', 'retrying')
                  AND j.not_before <= CURRENT_TIMESTAMP
                  AND s.lifecycle_state = 'alive'
                  AND NOT EXISTS (
                      SELECT 1 FROM jobs r
                      WHERE r.session_id = j.session_id
                        AND r.status = 'running'
                  )
                ORDER BY j.priority ASC, j.created_at ASC
                LIMIT 1
            )
            UPDATE jobs
            SET status = 'running',
                attempts = attempts + 1,
                lease_expires_at = DATETIME(CURRENT_TIMESTAMP, ?),
                updated_at = CURRENT_TIMESTAMP
            WHERE id IN (SELECT id FROM candidate)
            RETURNING *
            """,
            (f"+{ttl} seconds",),
        ) as cursor:
            row = await cursor.fetchone()

        await self._conn.commit()
        if row is None:
            return None

        job = self._row_to_job(row)
        log_event(
            self._logger,
            event_name="job_leased",
            level="info",
            component="worker",
            session_id=job.session_id,
            job_id=job.id,
            stage=job.job_type,
            attempts=job.attempts,
            lease_expires_at=job.lease_expires_at,
        )
        return job

    async def recover_expired_running_jobs(self) -> int:
        """Recover expired running jobs to retrying or failed based on retry budget."""
        assert self._conn is not None

        async with self._conn.execute(
            """
            UPDATE jobs
            SET status = CASE
                    WHEN attempts >= max_attempts THEN 'failed'
                    ELSE 'retrying'
                END,
                not_before = CASE
                    WHEN attempts >= max_attempts THEN not_before
                    ELSE CURRENT_TIMESTAMP
                END,
                lease_expires_at = NULL,
                last_error_code = COALESCE(last_error_code, 'LEASE_EXPIRED'),
                last_error_message = COALESCE(last_error_message, 'worker lease expired before ack'),
                updated_at = CURRENT_TIMESTAMP
            WHERE status = 'running'
              AND lease_expires_at < CURRENT_TIMESTAMP
            """
        ) as cursor:
            recovered = cursor.rowcount
        await self._conn.commit()
        return recovered

    async def mark_succeeded(self, job_id: str) -> bool:
        assert self._conn is not None
        job = await self.get_job(job_id)
        if job is None:
            return False

        async with self._conn.execute(
            """
            UPDATE jobs
            SET status = 'succeeded',
                lease_expires_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (job_id,),
        ) as cursor:
            ok = cursor.rowcount > 0
        await self._conn.commit()
        if ok:
            log_event(
                self._logger,
                event_name="job_completed",
                level="info",
                component="worker",
                session_id=job.session_id,
                job_id=job.id,
                stage=job.job_type,
            )
        return ok

    async def mark_failed(self, job_id: str, *, error_code: str, error_message: str) -> bool:
        assert self._conn is not None
        job = await self.get_job(job_id)
        if job is None:
            return False

        async with self._conn.execute(
            """
            UPDATE jobs
            SET status = 'failed',
                lease_expires_at = NULL,
                last_error_code = ?,
                last_error_message = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (error_code, error_message, job_id),
        ) as cursor:
            ok = cursor.rowcount > 0
        await self._conn.commit()
        if ok:
            log_event(
                self._logger,
                event_name="job_failed",
                level="error",
                component="worker",
                session_id=job.session_id,
                job_id=job.id,
                stage=job.job_type,
                error_code=error_code,
            )
        return ok

    async def schedule_retry(self, job_id: str, *, error_code: str, error_message: str) -> bool:
        """Schedule retry with 2^attempts seconds backoff. If max retries reached, fail the job."""
        assert self._conn is not None

        job = await self.get_job(job_id)
        if job is None:
            return False

        if job.attempts >= job.max_attempts:
            return await self.mark_failed(job_id, error_code=error_code, error_message=error_message)

        delay_seconds = settings.XHS_SPIDER_BACKOFF_BASE ** max(1, job.attempts)
        async with self._conn.execute(
            """
            UPDATE jobs
            SET status = 'retrying',
                not_before = DATETIME(CURRENT_TIMESTAMP, ?),
                lease_expires_at = NULL,
                last_error_code = ?,
                last_error_message = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (f"+{delay_seconds} seconds", error_code, error_message, job_id),
        ) as cursor:
            ok = cursor.rowcount > 0
        await self._conn.commit()

        if ok:
            log_event(
                self._logger,
                event_name="job_retry_scheduled",
                level="warning",
                component="worker",
                session_id=job.session_id,
                job_id=job.id,
                stage=job.job_type,
                error_code=error_code,
                retry_delay_seconds=delay_seconds,
                attempts=job.attempts,
            )
        return ok

    async def pause_session_jobs(self, session_id: str) -> int:
        """Pause all queued/retrying jobs for a frozen session."""
        assert self._conn is not None

        async with self._conn.execute(
            """
            UPDATE jobs
            SET status = 'paused',
                updated_at = CURRENT_TIMESTAMP
            WHERE session_id = ?
              AND status IN ('queued', 'retrying')
            """,
            (session_id,),
        ) as cursor:
            count = cursor.rowcount
        await self._conn.commit()
        return count

    async def resume_paused_jobs(self, session_id: str) -> int:
        """Resume paused jobs for a resumed session."""
        assert self._conn is not None

        async with self._conn.execute(
            """
            UPDATE jobs
            SET status = 'queued',
                not_before = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE session_id = ?
              AND status = 'paused'
            """,
            (session_id,),
        ) as cursor:
            count = cursor.rowcount
        await self._conn.commit()
        return count

    async def cancel_session_jobs(self, session_id: str, reason: str = "session_purged") -> int:
        assert self._conn is not None

        async with self._conn.execute(
            """
            UPDATE jobs
            SET status = 'cancelled',
                cancel_reason = ?,
                lease_expires_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE session_id = ?
              AND status IN ('queued', 'paused', 'retrying', 'running')
            """,
            (reason, session_id),
        ) as cursor:
            count = cursor.rowcount
        await self._conn.commit()
        return count

    async def append_session_event(
        self,
        *,
        session_id: str,
        event_name: str,
        payload: Optional[dict[str, Any]] = None,
        job_id: Optional[str] = None,
        stage: Optional[str] = None,
    ) -> SessionEventRecord:
        assert self._conn is not None

        payload_json = json.dumps(payload or {}, ensure_ascii=False, default=str)
        async with self._conn.execute(
            """
            INSERT INTO session_events(session_id, job_id, event_name, stage, payload_json)
            VALUES (?, ?, ?, ?, ?)
            RETURNING *
            """,
            (session_id, job_id, event_name, stage, payload_json),
        ) as cursor:
            row = await cursor.fetchone()
        await self._conn.commit()
        assert row is not None
        return self._row_to_session_event(row)

    async def list_session_events(
        self,
        session_id: str,
        *,
        after_event_id: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[SessionEventRecord]:
        assert self._conn is not None

        clauses = ["session_id = ?"]
        params: list[Any] = [session_id]
        if after_event_id is not None:
            clauses.append("event_id > ?")
            params.append(after_event_id)

        sql = f"""
            SELECT *
            FROM session_events
            WHERE {' AND '.join(clauses)}
            ORDER BY event_id ASC
        """
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        async with self._conn.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_session_event(row) for row in rows]
        return count
