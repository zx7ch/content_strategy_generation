"""Business data storage for session payloads.

This module keeps large payloads out of the lightweight `sessions` checkpoint row.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Iterable, Optional

import aiosqlite

from app.models.session import ContentStrategy, GeneratedNote, PlatformPreference, Proposal, SpiderNote


class SessionDataStore:
    """Store/load heavy business payloads with idempotent UPSERT semantics."""

    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn

    async def init_tables(self) -> None:
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS spider_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                note_id TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at TIMESTAMP,
                UNIQUE(session_id, note_id)
            )
            """
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_spider_session ON spider_data(session_id)"
        )

        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_data (
                strategy_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                content_strategy TEXT NOT NULL,
                platform_preference TEXT NOT NULL,
                created_at TIMESTAMP
            )
            """
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_strategy_session ON strategy_data(session_id)"
        )

        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS proposal_data (
                proposal_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                proposal TEXT NOT NULL,
                platform_fit_score REAL,
                uniqueness_score REAL,
                overall_score REAL,
                scored_at TIMESTAMP,
                created_at TIMESTAMP
            )
            """
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_proposal_session ON proposal_data(session_id)"
        )

        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS generation_data (
                note_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                proposal_id TEXT,
                generated_note TEXT NOT NULL,
                similarity_check TEXT,
                created_at TIMESTAMP
            )
            """
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_generation_session ON generation_data(session_id)"
        )
        await self._conn.commit()

    async def save_spider_results(self, session_id: str, posts: Iterable[Any]) -> list[str]:
        note_ids: list[str] = []
        now = datetime.utcnow().isoformat()
        for post in posts:
            note_id = getattr(post, "note_id", None)
            if not note_id:
                continue
            payload = post.model_dump() if hasattr(post, "model_dump") else dict(post)
            payload_json = json.dumps(payload, ensure_ascii=False, default=str)
            await self._conn.execute(
                """
                INSERT INTO spider_data (session_id, note_id, data, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id, note_id) DO UPDATE SET
                    data=excluded.data
                """,
                (session_id, note_id, payload_json, now),
            )
            note_ids.append(note_id)
        await self._conn.commit()
        return note_ids

    async def get_spider_results(
        self,
        session_id: str,
        note_ids: Optional[list[str]] = None,
    ) -> list[SpiderNote]:
        params: list[Any] = [session_id]
        if note_ids:
            placeholders = ", ".join("?" for _ in note_ids)
            sql = (
                "SELECT note_id, data FROM spider_data "
                f"WHERE session_id = ? AND note_id IN ({placeholders}) ORDER BY id"
            )
            params.extend(note_ids)
        else:
            sql = "SELECT note_id, data FROM spider_data WHERE session_id = ? ORDER BY id"

        rows: list[aiosqlite.Row] = []
        async with self._conn.execute(sql, params) as cursor:
            async for row in cursor:
                rows.append(row)

        notes: list[SpiderNote] = []
        for row in rows:
            raw = json.loads(row["data"])
            notes.append(
                SpiderNote(
                    note_id=raw.get("note_id", row["note_id"]),
                    title=raw.get("title", raw.get("note_display_title", "")),
                    content=raw.get("content", raw.get("note_desc", "")),
                    tags=raw.get("tags", raw.get("note_tags", [])) or [],
                )
            )
        return notes

    async def save_strategy(
        self,
        session_id: str,
        content_strategy: ContentStrategy,
        platform_preference: PlatformPreference,
        strategy_id: Optional[str] = None,
    ) -> str:
        sid = strategy_id or f"strat_{uuid.uuid4().hex[:12]}"
        now = datetime.utcnow().isoformat()
        await self._conn.execute(
            """
            INSERT INTO strategy_data (
                strategy_id, session_id, content_strategy, platform_preference, created_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(strategy_id) DO UPDATE SET
                content_strategy=excluded.content_strategy,
                platform_preference=excluded.platform_preference
            """,
            (
                sid,
                session_id,
                content_strategy.model_dump_json(),
                platform_preference.model_dump_json(),
                now,
            ),
        )
        await self._conn.commit()
        return sid

    async def get_strategy(
        self,
        session_id: str,
        strategy_id: Optional[str],
    ) -> tuple[Optional[ContentStrategy], Optional[PlatformPreference], Optional[str]]:
        if strategy_id:
            sql = """
                SELECT strategy_id, content_strategy, platform_preference
                FROM strategy_data WHERE session_id = ? AND strategy_id = ? LIMIT 1
            """
            params = (session_id, strategy_id)
        else:
            sql = """
                SELECT strategy_id, content_strategy, platform_preference
                FROM strategy_data WHERE session_id = ?
                ORDER BY created_at DESC LIMIT 1
            """
            params = (session_id,)

        async with self._conn.execute(sql, params) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None, None, None
            strategy = ContentStrategy.model_validate_json(row["content_strategy"])
            pref = PlatformPreference.model_validate_json(row["platform_preference"])
            return strategy, pref, row["strategy_id"]

    async def save_proposals(self, session_id: str, proposals: Iterable[Proposal]) -> list[str]:
        ids: list[str] = []
        now = datetime.utcnow().isoformat()
        for proposal in proposals:
            await self._conn.execute(
                """
                INSERT INTO proposal_data (
                    proposal_id, session_id, proposal, overall_score, scored_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(proposal_id) DO UPDATE SET
                    proposal=excluded.proposal,
                    overall_score=excluded.overall_score,
                    scored_at=excluded.scored_at
                """,
                (
                    proposal.proposal_id,
                    session_id,
                    proposal.model_dump_json(),
                    proposal.score,
                    now,
                    now,
                ),
            )
            ids.append(proposal.proposal_id)
        await self._conn.commit()
        return ids

    async def get_proposals(
        self,
        session_id: str,
        proposal_ids: Optional[list[str]] = None,
    ) -> list[Proposal]:
        params: list[Any] = [session_id]
        if proposal_ids:
            placeholders = ", ".join("?" for _ in proposal_ids)
            sql = (
                "SELECT proposal FROM proposal_data "
                f"WHERE session_id = ? AND proposal_id IN ({placeholders}) ORDER BY created_at"
            )
            params.extend(proposal_ids)
        else:
            sql = "SELECT proposal FROM proposal_data WHERE session_id = ? ORDER BY created_at"
        result: list[Proposal] = []
        async with self._conn.execute(sql, params) as cursor:
            async for row in cursor:
                result.append(Proposal.model_validate_json(row["proposal"]))
        return result

    async def save_generated_notes(self, session_id: str, notes: Iterable[GeneratedNote]) -> list[str]:
        ids: list[str] = []
        now = datetime.utcnow().isoformat()
        for note in notes:
            proposal_id = None
            params = note.generation_params or {}
            if isinstance(params, dict):
                proposal_id = params.get("proposal_id")
            similarity = json.dumps(note.similarity_check, ensure_ascii=False, default=str)
            await self._conn.execute(
                """
                INSERT INTO generation_data (
                    note_id, session_id, proposal_id, generated_note, similarity_check, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(note_id) DO UPDATE SET
                    generated_note=excluded.generated_note,
                    similarity_check=excluded.similarity_check
                """,
                (
                    note.note_id,
                    session_id,
                    proposal_id,
                    note.model_dump_json(),
                    similarity,
                    now,
                ),
            )
            ids.append(note.note_id)
        await self._conn.commit()
        return ids

    async def get_generated_notes(
        self,
        session_id: str,
        note_ids: Optional[list[str]] = None,
    ) -> list[GeneratedNote]:
        params: list[Any] = [session_id]
        if note_ids:
            placeholders = ", ".join("?" for _ in note_ids)
            sql = (
                "SELECT generated_note FROM generation_data "
                f"WHERE session_id = ? AND note_id IN ({placeholders}) ORDER BY created_at"
            )
            params.extend(note_ids)
        else:
            sql = "SELECT generated_note FROM generation_data WHERE session_id = ? ORDER BY created_at"
        result: list[GeneratedNote] = []
        async with self._conn.execute(sql, params) as cursor:
            async for row in cursor:
                result.append(GeneratedNote.model_validate_json(row["generated_note"]))
        return result

    async def delete_session_data(self, session_id: str) -> None:
        await self._conn.execute("DELETE FROM spider_data WHERE session_id = ?", (session_id,))
        await self._conn.execute("DELETE FROM strategy_data WHERE session_id = ?", (session_id,))
        await self._conn.execute("DELETE FROM proposal_data WHERE session_id = ?", (session_id,))
        await self._conn.execute("DELETE FROM generation_data WHERE session_id = ?", (session_id,))
        await self._conn.commit()
