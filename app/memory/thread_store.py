"""SQLite-backed thread and message store for Creator Workbench."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

import aiosqlite

from app.logging_config import get_logger


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


class ThreadStore:
    """Persistent store for Creator conversation threads and messages."""

    def __init__(self, db_path: str = "./data/creator_threads.db"):
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None
        self._logger = get_logger(__name__, component="thread_store")

    async def __aenter__(self) -> "ThreadStore":
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
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS creator_threads (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                active_workflow_session_id TEXT,
                active_job_id TEXT,
                accepted_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_threads_created ON creator_threads(created_at DESC)"
        )
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS creator_messages (
                id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL REFERENCES creator_threads(id),
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                intent TEXT,
                linked_session_id TEXT,
                linked_job_id TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_thread ON creator_messages(thread_id, created_at ASC)"
        )
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS publish_candidates (
                candidate_id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                note_id TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_candidates_thread ON publish_candidates(thread_id)"
        )
        await self._conn.commit()

    async def create_thread(self, title: Optional[str] = None) -> dict:
        assert self._conn is not None
        now = _now_iso()
        thread_id = _new_id()
        effective_title = title or f"对话 {now[:16].replace('T', ' ')}"
        await self._conn.execute(
            """
            INSERT INTO creator_threads (id, title, status, created_at, updated_at)
            VALUES (?, ?, 'active', ?, ?)
            """,
            (thread_id, effective_title, now, now),
        )
        await self._conn.commit()
        row = await self._get_thread_row(thread_id)
        assert row is not None
        return dict(row)

    async def list_threads(self) -> list[dict]:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT * FROM creator_threads ORDER BY created_at DESC"
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_thread(self, thread_id: str) -> Optional[dict]:
        row = await self._get_thread_row(thread_id)
        return dict(row) if row is not None else None

    async def _get_thread_row(self, thread_id: str) -> Optional[aiosqlite.Row]:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT * FROM creator_threads WHERE id = ?", (thread_id,)
        ) as cursor:
            return await cursor.fetchone()

    async def get_thread_messages(self, thread_id: str) -> list[dict]:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT * FROM creator_messages WHERE thread_id = ? ORDER BY created_at ASC",
            (thread_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def append_message(
        self,
        thread_id: str,
        role: str,
        text: str,
        intent: Optional[str] = None,
        linked_session_id: Optional[str] = None,
        linked_job_id: Optional[str] = None,
    ) -> dict:
        assert self._conn is not None
        now = _now_iso()
        message_id = _new_id()
        await self._conn.execute(
            """
            INSERT INTO creator_messages
                (id, thread_id, role, text, intent, linked_session_id, linked_job_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (message_id, thread_id, role, text, intent, linked_session_id, linked_job_id, now),
        )
        # update thread updated_at
        await self._conn.execute(
            "UPDATE creator_threads SET updated_at = ? WHERE id = ?",
            (now, thread_id),
        )
        await self._conn.commit()
        async with self._conn.execute(
            "SELECT * FROM creator_messages WHERE id = ?", (message_id,)
        ) as cursor:
            row = await cursor.fetchone()
        assert row is not None
        return dict(row)

    async def complete_thread(self, thread_id: str) -> Optional[dict]:
        """Mark thread as accepted. Returns updated thread dict or None if not found."""
        assert self._conn is not None
        now = _now_iso()
        await self._conn.execute(
            "UPDATE creator_threads SET status='accepted', accepted_at=?, updated_at=? WHERE id=?",
            (now, now, thread_id),
        )
        await self._conn.commit()
        return await self.get_thread(thread_id)

    async def save_publish_candidates(
        self,
        thread_id: str,
        session_id: str,
        candidates: list[dict],
    ) -> list[str]:
        """Insert publish candidates idempotently (skip duplicates by note_id + thread_id)."""
        assert self._conn is not None
        now = _now_iso()
        ids: list[str] = []
        for c in candidates:
            candidate_id = _new_id()
            await self._conn.execute(
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
                    thread_id,
                    session_id,
                    c["note_id"],
                    c["title"],
                    c["content"],
                    ",".join(c.get("tags", [])),
                    now,
                    thread_id,
                    c["note_id"],
                ),
            )
            ids.append(candidate_id)
        await self._conn.commit()
        return ids

    async def count_publish_candidates(self, thread_id: str) -> int:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT COUNT(*) FROM publish_candidates WHERE thread_id=?", (thread_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return row[0] if row else 0

    async def list_publish_candidates(self) -> list[dict]:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT * FROM publish_candidates ORDER BY created_at DESC"
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def count_user_messages(self, thread_id: str) -> int:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT COUNT(*) FROM creator_messages WHERE thread_id = ? AND role = 'user'",
            (thread_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return row[0] if row else 0

    async def update_thread_title(self, thread_id: str, title: str) -> None:
        assert self._conn is not None
        now = _now_iso()
        await self._conn.execute(
            "UPDATE creator_threads SET title = ?, updated_at = ? WHERE id = ?",
            (title, now, thread_id),
        )
        await self._conn.commit()

    async def delete_thread(self, thread_id: str) -> bool:
        assert self._conn is not None
        await self._conn.execute(
            "DELETE FROM publish_candidates WHERE thread_id = ?",
            (thread_id,),
        )
        await self._conn.execute(
            "DELETE FROM creator_messages WHERE thread_id = ?",
            (thread_id,),
        )
        cursor = await self._conn.execute(
            "DELETE FROM creator_threads WHERE id = ?",
            (thread_id,),
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def update_thread_active_job(
        self,
        thread_id: str,
        session_id: Optional[str],
        job_id: Optional[str],
    ) -> None:
        assert self._conn is not None
        now = _now_iso()
        await self._conn.execute(
            """
            UPDATE creator_threads
            SET active_workflow_session_id = ?,
                active_job_id = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (session_id, job_id, now, thread_id),
        )
        await self._conn.commit()
