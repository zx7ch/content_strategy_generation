"""Session State Manager - lightweight checkpoint + external business data."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Dict, List, Optional

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.config import settings
from app.logging_config import get_logger, log_event
from app.memory.session_data_store import SessionDataStore
from app.models.session import (
    ContentStrategy,
    PlatformPreference,
    RetryStats,
    Session,
    SessionError,
    SessionLifecycleState,
    SessionStage,
)


class SessionManager:
    """Session storage manager backed by SQLite + LangGraph checkpointer."""

    JSON_COLUMNS = {
        "spider_note_ids",
        "proposal_ids",
        "generated_note_ids",
        "similarity_report",
        "retry_stats",
        "expanded_queries",
        "error",
    }

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or settings.SQLITE_DB_PATH
        self._conn: Optional[aiosqlite.Connection] = None
        self._checkpointer: Optional[AsyncSqliteSaver] = None
        self.data_store: Optional[SessionDataStore] = None
        self._logger = get_logger(__name__, component="session")

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
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
        self.data_store = SessionDataStore(self._conn)
        await self.data_store.init_tables()
        self._checkpointer = AsyncSqliteSaver(self._conn)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None
            self.data_store = None
            self._checkpointer = None

    async def _init_tables(self) -> None:
        assert self._conn is not None
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
            "CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id)"
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_stage ON sessions(stage)"
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_lifecycle ON sessions(lifecycle_state)"
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_last_activity ON sessions(last_activity_at)"
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

        await self._ensure_session_columns()
        await self._conn.commit()

    async def _ensure_session_columns(self) -> None:
        """Backfill columns when using an existing DB created by older schema."""
        assert self._conn is not None

        required: Dict[str, str] = {
            "lifecycle_state": "TEXT NOT NULL DEFAULT 'alive'",
            "alive_until": "TIMESTAMP",
            "spider_cooldown_until": "TIMESTAMP",
            "purge_after": "TIMESTAMP",
            "frozen_at": "TIMESTAMP",
            "purged_at": "TIMESTAMP",
            "pause_requested": "BOOLEAN NOT NULL DEFAULT FALSE",
            "pause_requested_at": "TIMESTAMP",
            "last_user_activity_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            "spider_note_ids": "TEXT",
            "strategy_id": "TEXT",
            "proposal_ids": "TEXT",
            "generated_note_ids": "TEXT",
            "similarity_report": "TEXT",
            "retry_stats": "TEXT",
            "expanded_queries": "TEXT",
            "reindex_state": "TEXT NOT NULL DEFAULT 'ok'",
            "reindex_attempts": "INTEGER NOT NULL DEFAULT 0",
        }

        async with self._conn.execute("PRAGMA table_info(sessions)") as cursor:
            columns = {row[1] async for row in cursor}

        for name, ddl in required.items():
            if name not in columns:
                await self._conn.execute(f"ALTER TABLE sessions ADD COLUMN {name} {ddl}")

        if "rag_sync_status" in columns:
            await self._conn.execute(
                """
                UPDATE sessions
                SET reindex_state = CASE rag_sync_status
                    WHEN 'pending' THEN 'pending'
                    WHEN 'deadletter' THEN 'deadletter'
                    ELSE 'ok'
                END
                WHERE rag_sync_status IS NOT NULL
                  AND (reindex_state IS NULL OR reindex_state = 'ok')
                """
            )
        if "rag_reindex_attempts" in columns:
            await self._conn.execute(
                """
                UPDATE sessions
                SET reindex_attempts = rag_reindex_attempts
                WHERE rag_reindex_attempts IS NOT NULL
                  AND (reindex_attempts IS NULL OR reindex_attempts = 0)
                """
            )

    def _serialize_json(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        if hasattr(value, "model_dump"):
            value = value.model_dump()
        return json.dumps(value, ensure_ascii=False, default=str)

    def _deserialize_json(self, value: Optional[str]) -> Any:
        if not value:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None

    def _to_datetime(self, value: Any) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        return datetime.utcnow()

    def _compute_lifecycle(
        self,
        last_user_activity_at: datetime,
        active_job_count: int,
        *,
        pause_requested: bool = False,
        now: Optional[datetime] = None,
    ) -> SessionLifecycleState:
        now = now or datetime.utcnow()
        if active_job_count > 0:
            return SessionLifecycleState.ALIVE

        idle_seconds = (now - last_user_activity_at).total_seconds()
        if idle_seconds > settings.SESSION_PURGE_AFTER_DAYS * 86400:
            return SessionLifecycleState.PURGED
        if pause_requested:
            return SessionLifecycleState.FROZEN
        if idle_seconds > settings.SESSION_FROZEN_AFTER_HOURS * 3600:
            return SessionLifecycleState.FROZEN
        return SessionLifecycleState.ALIVE

    async def _count_active_jobs(self, session_id: str) -> int:
        """Count active/runnable jobs that keep session alive.

        Per agreed lifecycle model, paused jobs are excluded.
        """
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'"
        ) as cursor:
            exists = await cursor.fetchone()
        if exists is None:
            return 0

        async with self._conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM jobs
            WHERE session_id = ?
              AND status IN ('queued', 'retrying', 'running')
            """,
            (session_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["c"] if row else 0)

    async def create_session(
        self,
        session_id: str,
        user_id: str,
        user_query: str,
        platform: str = "xiaohongshu",
        mode: str = "editing",
    ) -> Session:
        assert self._conn is not None

        now = datetime.utcnow()
        alive_until = now + timedelta(hours=settings.SESSION_ALIVE_HOURS)
        purge_after = now + timedelta(days=settings.SESSION_PURGE_AFTER_DAYS)

        await self._conn.execute(
            """
            INSERT INTO sessions (
                session_id, user_id, user_query, platform, mode, stage,
                lifecycle_state, alive_until, purge_after,
                quality_score, used_fallback,
                retry_stats, reindex_state, reindex_attempts,
                created_at, updated_at, last_activity_at, last_user_activity_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                user_id=excluded.user_id,
                user_query=excluded.user_query,
                platform=excluded.platform,
                mode=excluded.mode,
                updated_at=excluded.updated_at,
                last_activity_at=excluded.last_activity_at,
                last_user_activity_at=excluded.last_user_activity_at,
                alive_until=excluded.alive_until,
                purge_after=excluded.purge_after,
                lifecycle_state='alive'
            """,
            (
                session_id,
                user_id,
                user_query,
                platform,
                mode,
                SessionStage.INIT.value,
                SessionLifecycleState.ALIVE.value,
                alive_until.isoformat(),
                purge_after.isoformat(),
                0.0,
                False,
                self._serialize_json(RetryStats()),
                "ok",
                0,
                now.isoformat(),
                now.isoformat(),
                now.isoformat(),
                now.isoformat(),
            ),
        )
        await self._conn.commit()

        session = await self.get_session(session_id)
        assert session is not None
        return session

    async def refresh_lifecycle_state(self, session_id: str) -> Optional[SessionLifecycleState]:
        assert self._conn is not None

        async with self._conn.execute(
            """
            SELECT last_user_activity_at, lifecycle_state, frozen_at, purged_at, pause_requested, stage
            FROM sessions
            WHERE session_id = ?
            """,
            (session_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None

        now = datetime.utcnow()
        current_state = row["lifecycle_state"]
        if current_state == SessionLifecycleState.PURGED.value:
            return SessionLifecycleState.PURGED

        last_user_activity = self._to_datetime(row["last_user_activity_at"])
        active_job_count = await self._count_active_jobs(session_id)
        lifecycle = self._compute_lifecycle(
            last_user_activity,
            active_job_count,
            pause_requested=bool(row["pause_requested"]),
            now=now,
        )
        state_changed = lifecycle.value != current_state
        cancelled_jobs = 0
        if lifecycle == SessionLifecycleState.PURGED:
            cancelled_jobs = await self._cancel_unfinished_jobs_for_purge(session_id)

        updates: list[str] = [
            "lifecycle_state = ?",
            "updated_at = ?",
            "alive_until = ?",
            "purge_after = ?",
        ]
        params: list[Any] = [
            lifecycle.value,
            now.isoformat(),
            (last_user_activity + timedelta(hours=settings.SESSION_ALIVE_HOURS)).isoformat(),
            (last_user_activity + timedelta(days=settings.SESSION_PURGE_AFTER_DAYS)).isoformat(),
        ]

        if lifecycle == SessionLifecycleState.FROZEN:
            updates.append("frozen_at = COALESCE(frozen_at, ?)")
            params.append(now.isoformat())
        if lifecycle == SessionLifecycleState.PURGED:
            updates.append("purged_at = COALESCE(purged_at, ?)")
            params.append(now.isoformat())

        params.append(session_id)
        await self._conn.execute(
            f"UPDATE sessions SET {', '.join(updates)} WHERE session_id = ?",
            params,
        )
        if state_changed and lifecycle == SessionLifecycleState.FROZEN:
            await self._append_session_event(
                session_id=session_id,
                event_name="session_frozen",
                stage=row["stage"],
                payload={
                    "message": "session frozen",
                    "progress": None,
                    "error_code": None,
                    "details": {"frozen_at": now.isoformat()},
                },
            )
        if state_changed and lifecycle == SessionLifecycleState.PURGED:
            await self._append_session_event(
                session_id=session_id,
                event_name="session_purged",
                stage=row["stage"],
                payload={
                    "message": "session purged",
                    "progress": None,
                    "error_code": None,
                    "details": {
                        "purged_at": now.isoformat(),
                        "cancelled_jobs": cancelled_jobs,
                    },
                },
            )
        await self._conn.commit()
        if state_changed and lifecycle == SessionLifecycleState.FROZEN:
            log_event(
                self._logger,
                event_name="session_frozen",
                level="warning",
                component="session",
                session_id=session_id,
                stage=None,
                frozen_at=now.isoformat(),
            )
        if state_changed and lifecycle == SessionLifecycleState.PURGED:
            log_event(
                self._logger,
                event_name="session_purged",
                level="warning",
                component="session",
                session_id=session_id,
                stage=None,
                purged_at=now.isoformat(),
                cancelled_jobs=cancelled_jobs,
            )
        return lifecycle

    async def _append_session_event(
        self,
        *,
        session_id: str,
        event_name: str,
        payload: dict[str, Any],
        stage: Optional[str] = None,
    ) -> None:
        assert self._conn is not None
        await self._conn.execute(
            """
            INSERT INTO session_events(session_id, job_id, event_name, stage, payload_json)
            VALUES (?, NULL, ?, ?, ?)
            """,
            (session_id, event_name, stage, json.dumps(payload, ensure_ascii=False, default=str)),
        )

    async def _cancel_unfinished_jobs_for_purge(self, session_id: str) -> int:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'"
        ) as cursor:
            exists = await cursor.fetchone()
        if exists is None:
            return 0

        async with self._conn.execute(
            """
            UPDATE jobs
            SET status = 'cancelled',
                cancel_reason = 'session_purged',
                lease_expires_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE session_id = ?
              AND status IN ('queued', 'paused', 'retrying', 'running')
            """,
            (session_id,),
        ) as cursor:
            return cursor.rowcount

    async def get_session(self, session_id: str) -> Optional[Session]:
        assert self._conn is not None
        assert self.data_store is not None

        await self.refresh_lifecycle_state(session_id)

        async with self._conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?",
            (session_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None

        spider_note_ids = self._deserialize_json(row["spider_note_ids"]) or []
        proposal_ids = self._deserialize_json(row["proposal_ids"]) or []
        generated_note_ids = self._deserialize_json(row["generated_note_ids"]) or []

        spider_notes = await self.data_store.get_spider_results(row["session_id"], spider_note_ids)
        content_strategy, platform_preference, strategy_id = await self.data_store.get_strategy(
            row["session_id"], row["strategy_id"]
        )
        proposals = await self.data_store.get_proposals(row["session_id"], proposal_ids)
        generated_notes = await self.data_store.get_generated_notes(
            row["session_id"], generated_note_ids
        )

        retry_stats_raw = self._deserialize_json(row["retry_stats"]) or {}
        error_raw = self._deserialize_json(row["error"])

        return Session(
            session_id=row["session_id"],
            user_id=row["user_id"],
            user_query=row["user_query"],
            platform=row["platform"],
            mode=row["mode"],
            stage=SessionStage(row["stage"]),
            lifecycle_state=SessionLifecycleState(row["lifecycle_state"]),
            alive_until=self._to_datetime(row["alive_until"]) if row["alive_until"] else None,
            pause_requested=bool(row["pause_requested"]) if "pause_requested" in row.keys() else False,
            pause_requested_at=self._to_datetime(row["pause_requested_at"]) if row["pause_requested_at"] else None,
            spider_cooldown_until=self._to_datetime(row["spider_cooldown_until"]) if row["spider_cooldown_until"] else None,
            purge_after=self._to_datetime(row["purge_after"]) if row["purge_after"] else None,
            frozen_at=self._to_datetime(row["frozen_at"]) if row["frozen_at"] else None,
            purged_at=self._to_datetime(row["purged_at"]) if row["purged_at"] else None,
            spider_notes=spider_notes or None,
            quality_score=row["quality_score"],
            platform_preference=platform_preference,
            content_strategy=content_strategy,
            expanded_queries=self._deserialize_json(row["expanded_queries"]),
            used_fallback=bool(row["used_fallback"]),
            proposals=proposals or None,
            generated_notes=generated_notes or None,
            similarity_report=self._deserialize_json(row["similarity_report"]),
            spider_note_ids=spider_note_ids or None,
            strategy_id=strategy_id,
            proposal_ids=proposal_ids or None,
            generated_note_ids=generated_note_ids or None,
            retry_stats=RetryStats.model_validate(retry_stats_raw),
            reindex_state=row["reindex_state"] if "reindex_state" in row.keys() else "ok",
            reindex_attempts=int(row["reindex_attempts"] or 0) if "reindex_attempts" in row.keys() else 0,
            error=SessionError.model_validate(error_raw) if error_raw else None,
            created_at=self._to_datetime(row["created_at"]),
            updated_at=self._to_datetime(row["updated_at"]),
            last_activity_at=self._to_datetime(row["last_activity_at"]),
            last_user_activity_at=self._to_datetime(row["last_user_activity_at"]) if row["last_user_activity_at"] else self._to_datetime(row["last_activity_at"]),
        )

    async def update_session(self, session_id: str, **fields: Any) -> Optional[Session]:
        assert self._conn is not None
        assert self.data_store is not None

        current = await self.get_session(session_id)
        if current is None:
            return None

        now = datetime.utcnow()
        update_fields: List[str] = []
        params: List[Any] = []

        if "spider_notes" in fields:
            note_ids = await self.data_store.save_spider_results(session_id, fields["spider_notes"] or [])
            update_fields.append("spider_note_ids = ?")
            params.append(self._serialize_json(note_ids))

        strategy: Optional[ContentStrategy] = fields.get("content_strategy")
        platform_pref: Optional[PlatformPreference] = fields.get("platform_preference")
        if strategy is not None or platform_pref is not None:
            if strategy is None or platform_pref is None:
                loaded_strategy, loaded_pref, _ = await self.data_store.get_strategy(
                    session_id, current.strategy_id
                )
                strategy = strategy or loaded_strategy
                platform_pref = platform_pref or loaded_pref
            if strategy is not None and platform_pref is not None:
                strategy_id = await self.data_store.save_strategy(
                    session_id,
                    strategy,
                    platform_pref,
                    strategy_id=current.strategy_id,
                )
                update_fields.append("strategy_id = ?")
                params.append(strategy_id)

        if "proposals" in fields:
            proposal_ids = await self.data_store.save_proposals(session_id, fields["proposals"] or [])
            update_fields.append("proposal_ids = ?")
            params.append(self._serialize_json(proposal_ids))

        if "generated_notes" in fields:
            generated_note_ids = await self.data_store.save_generated_notes(
                session_id, fields["generated_notes"] or []
            )
            update_fields.append("generated_note_ids = ?")
            params.append(self._serialize_json(generated_note_ids))

        if "error" in fields:
            error_value = fields["error"]
            update_fields.append("error = ?")
            params.append(self._serialize_json(error_value))

        for key, value in fields.items():
            if key in {"spider_notes", "content_strategy", "platform_preference", "proposals", "generated_notes", "error"}:
                continue
            if key == "stage" and isinstance(value, SessionStage):
                value = value.value
            if key == "lifecycle_state" and isinstance(value, SessionLifecycleState):
                value = value.value
            if key == "retry_stats" and isinstance(value, RetryStats):
                value = value.model_dump()
            if key in self.JSON_COLUMNS:
                value = self._serialize_json(value)
            update_fields.append(f"{key} = ?")
            params.append(value)

        update_fields.extend(["updated_at = ?", "last_activity_at = ?"])
        params.extend([now.isoformat(), now.isoformat()])

        params.append(session_id)
        await self._conn.execute(
            f"UPDATE sessions SET {', '.join(update_fields)} WHERE session_id = ?",
            params,
        )
        await self._conn.commit()

        return await self.get_session(session_id)

    async def delete_session(self, session_id: str) -> bool:
        assert self._conn is not None
        assert self.data_store is not None

        await self.data_store.delete_session_data(session_id)
        async with self._conn.execute(
            "DELETE FROM sessions WHERE session_id = ?", (session_id,)
        ) as cursor:
            await self._conn.commit()
            return cursor.rowcount > 0

    async def save_spider_results_with_consistency(
        self,
        session_id: str,
        posts: List[Any],
        rag_indexer: Optional[Callable[[], Awaitable[Any] | Any]] = None,
    ) -> List[str]:
        """Persist spider results first, then trigger vector indexing.

        If vector indexing fails, keep SQLite data and mark session for rebuild.
        """
        assert self._conn is not None
        assert self.data_store is not None

        current = await self.get_session(session_id)
        if current is None:
            return []

        note_ids = await self.data_store.save_spider_results(session_id, posts or [])
        now = datetime.utcnow().isoformat()
        await self._conn.execute(
            """
            UPDATE sessions
            SET spider_note_ids = ?,
                updated_at = ?,
                last_activity_at = ?
            WHERE session_id = ?
            """,
            (self._serialize_json(note_ids), now, now, session_id),
        )
        await self._conn.commit()

        if rag_indexer is not None:
            try:
                log_event(
                    self._logger,
                    event_name="reindex_started",
                    level="info",
                    component="session",
                    session_id=session_id,
                    stage=None,
                )
                result = rag_indexer()
                if asyncio.iscoroutine(result):
                    await result
                await self.mark_rag_reindex_result(session_id, success=True)
            except Exception:
                await self.mark_rag_needs_rebuild(session_id)

        return note_ids

    async def list_user_sessions(self, user_id: str, limit: int = 10) -> List[Session]:
        assert self._conn is not None

        sessions: List[Session] = []
        ids: List[str] = []
        async with self._conn.execute(
            """
            SELECT session_id FROM sessions
            WHERE user_id = ?
            ORDER BY last_activity_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ) as cursor:
            async for row in cursor:
                ids.append(row["session_id"])

        for sid in ids:
            session = await self.get_session(sid)
            if session:
                sessions.append(session)
        return sessions

    async def is_session_expired(
        self,
        session_id: str,
        timeout_minutes: Optional[int] = None,
    ) -> bool:
        session = await self.get_session(session_id)
        if session is None:
            return True
        timeout = timeout_minutes or settings.SESSION_TIMEOUT_MINUTES
        return datetime.utcnow() > session.last_activity_at + timedelta(minutes=timeout)

    async def update_activity(self, session_id: str) -> bool:
        """Record user activity and force session back to alive (resume path)."""
        now = datetime.utcnow().isoformat()
        async with self._conn.execute(
            """
            UPDATE sessions
            SET last_activity_at = ?,
                last_user_activity_at = ?,
                updated_at = ?,
                lifecycle_state = 'alive',
                alive_until = ?,
                pause_requested = FALSE,
                pause_requested_at = NULL,
                purge_after = ?
            WHERE session_id = ?
            """,
            (
                now,
                now,
                now,
                (datetime.utcnow() + timedelta(hours=settings.SESSION_ALIVE_HOURS)).isoformat(),
                (datetime.utcnow() + timedelta(days=settings.SESSION_PURGE_AFTER_DAYS)).isoformat(),
                session_id,
            ),
        ) as cursor:
            await self._conn.commit()
            return cursor.rowcount > 0

    async def touch_user_activity(self, session_id: str) -> bool:
        """Record user-triggered touch without forcing lifecycle transitions."""
        now = datetime.utcnow().isoformat()
        async with self._conn.execute(
            """
            UPDATE sessions
            SET last_user_activity_at = ?,
                last_activity_at = ?,
                updated_at = ?,
                alive_until = ?,
                purge_after = ?
            WHERE session_id = ?
            """,
            (
                now,
                now,
                now,
                (datetime.utcnow() + timedelta(hours=settings.SESSION_ALIVE_HOURS)).isoformat(),
                (datetime.utcnow() + timedelta(days=settings.SESSION_PURGE_AFTER_DAYS)).isoformat(),
                session_id,
            ),
        ) as cursor:
            await self._conn.commit()
            return cursor.rowcount > 0

    async def cleanup_expired_sessions(self, timeout_minutes: Optional[int] = None) -> int:
        assert self._conn is not None

        timeout = timeout_minutes or settings.SESSION_TIMEOUT_MINUTES
        expiry_threshold = (datetime.utcnow() - timedelta(minutes=timeout)).isoformat()

        session_ids: List[str] = []
        async with self._conn.execute(
            "SELECT session_id FROM sessions WHERE last_activity_at < ?",
            (expiry_threshold,),
        ) as cursor:
            async for row in cursor:
                session_ids.append(row["session_id"])

        for sid in session_ids:
            await self.delete_session(sid)

        return len(session_ids)

    async def mark_rag_needs_rebuild(self, session_id: str) -> bool:
        return await self.mark_reindex_pending(session_id)

    async def mark_reindex_pending(self, session_id: str) -> bool:
        assert self._conn is not None

        async with self._conn.execute(
            """
            UPDATE sessions
            SET reindex_state = 'pending',
                updated_at = ?
            WHERE session_id = ?
            """,
            (datetime.utcnow().isoformat(), session_id),
        ) as cursor:
            await self._conn.commit()
            updated = cursor.rowcount > 0
        if updated:
            log_event(
                self._logger,
                event_name="reindex_scheduled",
                level="warning",
                component="session",
                session_id=session_id,
                stage=None,
            )
        return updated

    async def mark_rag_reindex_result(self, session_id: str, success: bool) -> bool:
        return await self.mark_reindex_result(session_id, success)

    async def mark_reindex_result(self, session_id: str, success: bool) -> bool:
        assert self._conn is not None

        if success:
            async with self._conn.execute(
                """
                UPDATE sessions
                SET reindex_state = 'ok',
                    reindex_attempts = 0,
                    updated_at = ?
                WHERE session_id = ?
                """,
                (datetime.utcnow().isoformat(), session_id),
            ) as cursor:
                await self._conn.commit()
                updated = cursor.rowcount > 0
            if updated:
                log_event(
                    self._logger,
                    event_name="reindex_succeeded",
                    level="info",
                    component="session",
                    session_id=session_id,
                    stage=None,
                )
            return updated

        async with self._conn.execute(
            "SELECT reindex_attempts FROM sessions WHERE session_id = ?",
            (session_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return False
            attempts = int(row["reindex_attempts"] or 0) + 1

        status = "pending"
        if attempts >= settings.REINDEX_MAX_ATTEMPTS:
            status = "deadletter"

        async with self._conn.execute(
            """
            UPDATE sessions
            SET reindex_state = ?,
                reindex_attempts = ?,
                updated_at = ?
            WHERE session_id = ?
            """,
            (status, attempts, datetime.utcnow().isoformat(), session_id),
        ) as cursor:
            await self._conn.commit()
            updated = cursor.rowcount > 0
        if updated and status == "deadletter":
            log_event(
                self._logger,
                event_name="reindex_deadlettered",
                level="error",
                component="session",
                session_id=session_id,
                stage=None,
                attempts=attempts,
            )
        return updated

    async def get_rag_sync_status(self, session_id: str) -> Optional[Dict[str, Any]]:
        return await self.get_reindex_status(session_id)

    async def get_reindex_status(self, session_id: str) -> Optional[Dict[str, Any]]:
        assert self._conn is not None

        async with self._conn.execute(
            "SELECT reindex_state, reindex_attempts FROM sessions WHERE session_id = ?",
            (session_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            return {
                "status": row["reindex_state"],
                "attempts": int(row["reindex_attempts"] or 0),
            }

    def get_checkpointer(self) -> AsyncSqliteSaver:
        if self._checkpointer is None:
            raise RuntimeError("SessionManager not connected")
        return self._checkpointer
