from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from experiments.xhs_extension_mvp.server.candidate_builder import NormalizedItem, build_candidates
from experiments.xhs_extension_mvp.server.hotspot_service import build_hotspot_snapshot
from experiments.xhs_extension_mvp.server.logging_utils import get_logger
from experiments.xhs_extension_mvp.server.llm_client import MVPLLMConfigError, load_llm_config
from experiments.xhs_extension_mvp.server.llm_note_recommender import (
    LLMRecommendedNoteAnalyzer,
    LLMRecommendedNotesFailure,
)
from experiments.xhs_extension_mvp.server.llm_query_expander import LLMExpansionFailure, LLMQueryExpander
from experiments.xhs_extension_mvp.server.models import (
    ActiveSearchContext,
    ActiveTask,
    ActiveTaskResponse,
    Candidate,
    CaptureItemIn,
    CollectionSummary,
    ExpandedQuery,
    ErrorSummary,
    ExtensionCaptureResponse,
    EvidenceRef,
    HotspotItem,
    HotspotList,
    HotspotSnapshotResponse,
    QueryCategory,
    RecommendedNote,
    RecommendedNotesDiagnostics,
    TaskSnapshotResponse,
    TaskSnapshotVersionResponse,
)
from experiments.xhs_extension_mvp.server.query_expander import expand_topic
from experiments.xhs_extension_mvp.server.recommendation_builder import build_recommended_notes


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


class InvalidCaptureToken(ValueError):
    pass


class MVPStorage:
    def __init__(self, db_path: str | Path, *, secret: str) -> None:
        self.db_path = Path(db_path)
        self.secret = secret.encode("utf-8")
        self._logger = get_logger()

    def init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS mvp_tasks (
                    task_id TEXT PRIMARY KEY,
                    topic TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    query_generation_source TEXT NOT NULL DEFAULT 'fallback_rule',
                    query_generation_notice TEXT
                );

                CREATE TABLE IF NOT EXISTS mvp_queries (
                    query_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    query_text TEXT NOT NULL,
                    sort_order INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES mvp_tasks(task_id)
                );

                CREATE TABLE IF NOT EXISTS mvp_captures (
                    capture_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    page_type TEXT NOT NULL,
                    query_text TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    capture_mode TEXT NOT NULL,
                    item_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES mvp_tasks(task_id)
                );

                CREATE TABLE IF NOT EXISTS mvp_capture_items (
                    item_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    capture_id TEXT NOT NULL,
                    note_id TEXT,
                    source_url TEXT NOT NULL,
                    raw_href TEXT NOT NULL DEFAULT '',
                    xsec_token TEXT NOT NULL DEFAULT '',
                    xsec_source TEXT NOT NULL DEFAULT '',
                    debug_url_source TEXT NOT NULL DEFAULT '',
                    page_type TEXT NOT NULL,
                    query_text TEXT NOT NULL,
                    title TEXT NOT NULL,
                    author TEXT NOT NULL DEFAULT '',
                    visible_text_excerpt TEXT NOT NULL DEFAULT '',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    likes INTEGER NOT NULL DEFAULT 0,
                    comments INTEGER NOT NULL DEFAULT 0,
                    collections INTEGER NOT NULL DEFAULT 0,
                    cover_image_url TEXT NOT NULL DEFAULT '',
                    dedupe_key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(task_id, dedupe_key),
                    FOREIGN KEY(task_id) REFERENCES mvp_tasks(task_id),
                    FOREIGN KEY(capture_id) REFERENCES mvp_captures(capture_id)
                );

                CREATE TABLE IF NOT EXISTS mvp_item_query_hits (
                    task_id TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    query_text TEXT NOT NULL,
                    hit_source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (task_id, item_id, query_text)
                );

                CREATE TABLE IF NOT EXISTS mvp_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    why_now TEXT NOT NULL,
                    angle TEXT NOT NULL,
                    score REAL NOT NULL,
                    supporting_note_count INTEGER NOT NULL DEFAULT 0,
                    query_coverage_count INTEGER NOT NULL DEFAULT 0,
                    score_explanation TEXT NOT NULL DEFAULT '',
                    evidence_refs_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES mvp_tasks(task_id)
                );

                CREATE TABLE IF NOT EXISTS mvp_hotspot_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    error_message TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(task_id) REFERENCES mvp_tasks(task_id)
                );

                CREATE TABLE IF NOT EXISTS mvp_hotspot_items (
                    snapshot_item_id TEXT PRIMARY KEY,
                    snapshot_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    metric TEXT NOT NULL,
                    rank_index INTEGER NOT NULL,
                    note_id TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL,
                    source_url TEXT NOT NULL DEFAULT '',
                    author TEXT NOT NULL DEFAULT '',
                    excerpt TEXT NOT NULL DEFAULT '',
                    likes INTEGER NOT NULL DEFAULT 0,
                    comments INTEGER NOT NULL DEFAULT 0,
                    collections INTEGER NOT NULL DEFAULT 0,
                    query_sources_json TEXT NOT NULL DEFAULT '[]',
                    FOREIGN KEY(snapshot_id) REFERENCES mvp_hotspot_snapshots(snapshot_id),
                    FOREIGN KEY(task_id) REFERENCES mvp_tasks(task_id)
                );

                CREATE TABLE IF NOT EXISTS mvp_active_task (
                    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                    task_id TEXT NOT NULL,
                    capture_token TEXT NOT NULL,
                    token_expires_at TEXT NOT NULL,
                    activated_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES mvp_tasks(task_id)
                );

                CREATE TABLE IF NOT EXISTS mvp_active_search_context (
                    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                    task_id TEXT NOT NULL,
                    query TEXT NOT NULL,
                    source TEXT NOT NULL,
                    opened_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES mvp_tasks(task_id)
                );

                CREATE TABLE IF NOT EXISTS mvp_extension_capture_requests (
                    request_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES mvp_tasks(task_id)
                );

                CREATE TABLE IF NOT EXISTS mvp_recommended_note_cache (
                    task_id TEXT PRIMARY KEY,
                    snapshot_version INTEGER NOT NULL,
                    notes_json TEXT NOT NULL,
                    diagnostics_json TEXT NOT NULL DEFAULT '{}',
                    analysis_source TEXT NOT NULL DEFAULT 'fallback_rule',
                    analysis_notice TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES mvp_tasks(task_id)
                );
                """
            )
            self._ensure_capture_item_columns(conn)
            self._ensure_candidate_columns(conn)
            self._ensure_task_columns(conn)
            self._ensure_recommended_note_cache_columns(conn)
            conn.commit()
        self._logger.info(
            "Initialized MVP storage schema",
            extra={"event_name": "mvp_storage_initialized", "detail": str(self.db_path)},
        )

    def create_task(self, topic: str) -> tuple[str, list[ExpandedQuery], str, str | None]:
        task_id = str(uuid.uuid4())
        created_at = iso_now()
        expansions, generation_source, generation_notice = self._build_queries_for_task(task_id=task_id, topic=topic)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO mvp_tasks (task_id, topic, created_at, query_generation_source, query_generation_notice)
                VALUES (?, ?, ?, ?, ?)
                """,
                (task_id, topic.strip(), created_at, generation_source, generation_notice),
            )
            for index, expansion in enumerate(expansions):
                conn.execute(
                    """
                    INSERT INTO mvp_queries (query_id, task_id, category, query_text, sort_order, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (str(uuid.uuid4()), task_id, expansion.category, expansion.query_text, index, created_at),
                )
            conn.commit()
        self._logger.info(
            "Created MVP task",
            extra={
                "event_name": "mvp_task_created",
                "task_id": task_id,
                "candidate_count": 0,
                "detail": topic.strip(),
                "query_generation_source": generation_source,
            },
        )
        return task_id, self._load_queries(task_id), generation_source, generation_notice

    def add_custom_queries(self, *, task_id: str, text: str) -> tuple[int, int]:
        lines = [self._normalize_query_line(line) for line in text.splitlines()]
        requested_queries = [line for line in lines if line]
        if not requested_queries:
            return 0, 0

        created_count = 0
        skipped_count = 0
        created_at = iso_now()
        with self._connect() as conn:
            if not self._task_exists(conn, task_id):
                raise KeyError(task_id)

            existing_rows = conn.execute(
                "SELECT query_text FROM mvp_queries WHERE task_id = ?",
                (task_id,),
            ).fetchall()
            existing_keys = {self._query_key(row["query_text"]) for row in existing_rows}
            sort_order = int(
                conn.execute(
                    "SELECT COALESCE(MAX(sort_order), -1) AS max_order FROM mvp_queries WHERE task_id = ?",
                    (task_id,),
                ).fetchone()["max_order"]
            )

            for query_text in requested_queries:
                query_key = self._query_key(query_text)
                if not query_key or query_key in existing_keys:
                    skipped_count += 1
                    continue
                sort_order += 1
                conn.execute(
                    """
                    INSERT INTO mvp_queries (query_id, task_id, category, query_text, sort_order, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (str(uuid.uuid4()), task_id, "custom", query_text, sort_order, created_at),
                )
                existing_keys.add(query_key)
                created_count += 1
            conn.commit()

        self._logger.info(
            "Added custom queries",
            extra={
                "event_name": "mvp_custom_queries_added",
                "task_id": task_id,
                "created_count": created_count,
                "skipped_count": skipped_count,
            },
        )
        return created_count, skipped_count

    def delete_custom_query(self, *, task_id: str, query_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT query_id, category FROM mvp_queries WHERE task_id = ? AND query_id = ?",
                (task_id, query_id),
            ).fetchone()
            if row is None:
                raise KeyError(query_id)
            if row["category"] != "custom":
                raise ValueError("Only custom queries can be deleted")

            conn.execute(
                "DELETE FROM mvp_queries WHERE task_id = ? AND query_id = ?",
                (task_id, query_id),
            )
            conn.commit()

        self._logger.info(
            "Deleted custom query",
            extra={"event_name": "mvp_custom_query_deleted", "task_id": task_id, "detail": query_id},
        )
        return True

    def get_task_snapshot(self, task_id: str) -> TaskSnapshotResponse | None:
        with self._connect() as conn:
            task_row = conn.execute(
                """
                SELECT task_id, topic, created_at, query_generation_source, query_generation_notice
                FROM mvp_tasks
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if task_row is None:
                return None

            capture_batch_count = int(
                conn.execute(
                    "SELECT COUNT(*) AS c FROM mvp_captures WHERE task_id = ?",
                    (task_id,),
                ).fetchone()["c"]
            )
            imported_item_count = int(
                conn.execute(
                    "SELECT COUNT(*) AS c FROM mvp_capture_items WHERE task_id = ?",
                    (task_id,),
                ).fetchone()["c"]
            )
            manual_seed_count = int(
                conn.execute(
                    "SELECT COUNT(*) AS c FROM mvp_capture_items WHERE task_id = ? AND page_type = 'manual'",
                    (task_id,),
                ).fetchone()["c"]
            )
            candidate_rows = conn.execute(
                """
                SELECT candidate_id, title, why_now, angle, score,
                       supporting_note_count, query_coverage_count, score_explanation, evidence_refs_json
                FROM mvp_candidates
                WHERE task_id = ?
                ORDER BY score DESC, created_at ASC
                """,
                (task_id,),
            ).fetchall()
            recommendation_items = self._load_normalized_items(conn, task_id)
            query_hits = self._load_item_query_hits(conn, task_id)
            updated_at = self._load_task_updated_at(conn, task_id, task_row["created_at"])
            cached_recommended_bundle = self._load_cached_recommended_notes(conn, task_id, capture_batch_count)

        recommended_notes, recommended_notes_diagnostics = (
            cached_recommended_bundle
            if cached_recommended_bundle is not None
            else self._build_and_cache_recommended_notes(
                task_id=task_id,
                topic=task_row["topic"],
                items=recommendation_items,
                query_hits=query_hits,
                snapshot_version=capture_batch_count,
            )
        )

        return TaskSnapshotResponse(
            task_id=task_row["task_id"],
            topic=task_row["topic"],
            created_at=datetime.fromisoformat(task_row["created_at"]),
            updated_at=updated_at,
            query_generation_source=task_row["query_generation_source"] or "fallback_rule",
            query_generation_notice=task_row["query_generation_notice"],
            snapshot_version=capture_batch_count,
            candidate_count=len(candidate_rows),
            capture_count=capture_batch_count,
            expanded_queries=self._load_queries(task_id),
            imported_page_count=capture_batch_count,
            imported_item_count=imported_item_count,
            collection_summary=CollectionSummary(
                capture_batch_count=capture_batch_count,
                deduped_item_count=imported_item_count,
                manual_seed_count=manual_seed_count,
            ),
            recommended_notes=recommended_notes,
            recommended_notes_diagnostics=recommended_notes_diagnostics,
            candidates=[
                Candidate(
                    candidate_id=row["candidate_id"],
                    title=row["title"],
                    why_now=row["why_now"],
                    angle=row["angle"],
                    score=float(row["score"]),
                    supporting_note_count=int(row["supporting_note_count"] or 0),
                    query_coverage_count=int(row["query_coverage_count"] or 0),
                    score_explanation=row["score_explanation"] or "",
                    evidence_refs=[EvidenceRef.model_validate(entry) for entry in json.loads(row["evidence_refs_json"])],
                )
                for row in candidate_rows
            ],
        )

    def get_task_snapshot_version(self, task_id: str) -> TaskSnapshotVersionResponse | None:
        with self._connect() as conn:
            task_row = conn.execute(
                "SELECT task_id, created_at FROM mvp_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if task_row is None:
                return None
            capture_count, candidate_count = self._load_task_counts(conn, task_id)
            updated_at = self._load_task_updated_at(conn, task_id, task_row["created_at"])
        return TaskSnapshotVersionResponse(
            task_id=task_id,
            snapshot_version=capture_count,
            updated_at=updated_at,
            candidate_count=candidate_count,
            capture_count=capture_count,
        )

    def task_exists(self, task_id: str) -> bool:
        with self._connect() as conn:
            return self._task_exists(conn, task_id)

    def create_capture_token(self, task_id: str, *, ttl_seconds: int = 900) -> tuple[str, datetime]:
        expires_at = utc_now() + timedelta(seconds=ttl_seconds)
        payload = {"task_id": task_id, "exp": int(expires_at.timestamp())}
        serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        signature = hmac.new(self.secret, serialized, hashlib.sha256).digest()
        token = f"{self._urlsafe_encode(serialized)}.{self._urlsafe_encode(signature)}"
        self._logger.info(
            "Generated capture token",
            extra={"event_name": "mvp_capture_token_created", "task_id": task_id, "detail": expires_at.isoformat()},
        )
        return token, expires_at

    def set_active_task(self, *, task_id: str, capture_token: str, token_expires_at: datetime) -> ActiveTaskResponse:
        activated_at = iso_now()
        with self._connect() as conn:
            if not self._task_exists(conn, task_id):
                raise KeyError(task_id)
            conn.execute(
                """
                INSERT INTO mvp_active_task (singleton_id, task_id, capture_token, token_expires_at, activated_at)
                VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(singleton_id) DO UPDATE SET
                    task_id = excluded.task_id,
                    capture_token = excluded.capture_token,
                    token_expires_at = excluded.token_expires_at,
                    activated_at = excluded.activated_at
                """,
                (task_id, capture_token, token_expires_at.isoformat(), activated_at),
            )
            conn.commit()
        self._logger.info(
            "Set active MVP task",
            extra={"event_name": "mvp_active_task_set", "task_id": task_id},
        )
        return self.get_active_task_response()

    def activate_task(self, task_id: str) -> ActiveTaskResponse:
        if not self.task_exists(task_id):
            raise KeyError(task_id)
        token, expires_at = self.create_capture_token(task_id)
        return self.set_active_task(task_id=task_id, capture_token=token, token_expires_at=expires_at)

    def set_active_search_context(
        self,
        *,
        task_id: str,
        query: str,
        source: str,
        opened_at: datetime | None = None,
    ) -> ActiveTaskResponse:
        if not self.task_exists(task_id):
            raise KeyError(task_id)
        response = self.activate_task(task_id)
        opened_at_value = opened_at or utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO mvp_active_search_context (singleton_id, task_id, query, source, opened_at)
                VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(singleton_id) DO UPDATE SET
                    task_id = excluded.task_id,
                    query = excluded.query,
                    source = excluded.source,
                    opened_at = excluded.opened_at
                """,
                (task_id, query.strip(), source.strip() or "expanded_query", opened_at_value.isoformat()),
            )
            conn.commit()
        self._logger.info(
            "Set active search context",
            extra={"event_name": "mvp_active_search_context_set", "task_id": task_id, "detail": query.strip()},
        )
        return ActiveTaskResponse(active_task=response.active_task, active_search_context=self._get_active_search_context())

    def has_active_task(self) -> bool:
        return self.get_active_task_response().active_task is not None

    def get_active_task_response(self) -> ActiveTaskResponse:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT active.task_id, active.capture_token, active.token_expires_at, active.activated_at,
                       task.topic, task.created_at
                FROM mvp_active_task active
                JOIN mvp_tasks task ON task.task_id = active.task_id
                WHERE active.singleton_id = 1
                """
            ).fetchone()
            if row is None:
                return ActiveTaskResponse(
                    active_task=None,
                    error_summary=ErrorSummary(
                        code="no_active_task",
                        message="No active task detected. Please return to the workbench and create a task first.",
                    ),
                )

            task_id = row["task_id"]
            capture_count, candidate_count = self._load_task_counts(conn, task_id)
            context = self._get_active_search_context(conn)

        token_expires_at = datetime.fromisoformat(row["token_expires_at"])
        status = "expired" if utc_now() > token_expires_at else "active"
        return ActiveTaskResponse(
            active_task=ActiveTask(
                task_id=task_id,
                capture_token=row["capture_token"],
                topic=row["topic"],
                created_at=datetime.fromisoformat(row["created_at"]),
                activated_at=datetime.fromisoformat(row["activated_at"]),
                status=status,
                snapshot_version=capture_count,
                capture_count=capture_count,
                candidate_count=candidate_count,
            ),
            active_search_context=context,
        )

    def validate_capture_token(self, token: str) -> str:
        try:
            payload_part, signature_part = token.split(".", 1)
        except ValueError as exc:
            raise InvalidCaptureToken("Malformed capture token") from exc

        payload = self._urlsafe_decode(payload_part)
        signature = self._urlsafe_decode(signature_part)
        expected = hmac.new(self.secret, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise InvalidCaptureToken("Invalid capture token signature")

        try:
            decoded = json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise InvalidCaptureToken("Invalid capture token payload") from exc

        expires_at = int(decoded.get("exp", 0))
        if utc_now().timestamp() > expires_at:
            raise InvalidCaptureToken("Capture token expired")
        task_id = str(decoded.get("task_id", "")).strip()
        if not task_id:
            raise InvalidCaptureToken("Capture token missing task_id")
        self._logger.info(
            "Validated capture token",
            extra={"event_name": "mvp_capture_token_validated", "task_id": task_id},
        )
        return task_id

    def ingest_extension_capture(
        self,
        *,
        task_id: str,
        request_id: str,
        page_type: str,
        query_text: str,
        items: list[CaptureItemIn],
    ) -> ExtensionCaptureResponse:
        request_key = request_id.strip()
        if not request_key:
            raise ValueError("request_id is required")
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT response_json FROM mvp_extension_capture_requests WHERE request_id = ?",
                (request_key,),
            ).fetchone()
        if existing is not None:
            return ExtensionCaptureResponse.model_validate_json(existing["response_json"])

        captured_count = len(items)
        imported_count, _ = self.ingest_capture(
            task_id=task_id,
            page_type=page_type,
            query_text=query_text,
            items=items,
            capture_mode="extension",
        )
        snapshot = self.get_task_snapshot(task_id)
        snapshot_version = snapshot.collection_summary.capture_batch_count if snapshot else 0
        duplicate_count = max(0, captured_count - imported_count)
        status = "duplicate_only" if captured_count > 0 and imported_count == 0 else "accepted"
        response = ExtensionCaptureResponse(
            task_id=task_id,
            request_id=request_key,
            ingestion_run_id=request_key,
            snapshot_version=snapshot_version,
            captured_count=captured_count,
            new_count=imported_count,
            duplicate_count=duplicate_count,
            status=status,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO mvp_extension_capture_requests (request_id, task_id, response_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (request_key, task_id, response.model_dump_json(), iso_now()),
            )
            conn.commit()
        return response

    def ingest_capture(
        self,
        *,
        task_id: str,
        page_type: str,
        query_text: str,
        items: list[CaptureItemIn],
        capture_mode: str,
    ) -> tuple[int, list[Candidate]]:
        if not items:
            self._logger.info(
                "Received empty capture payload",
                extra={
                    "event_name": "mvp_capture_empty",
                    "task_id": task_id,
                    "page_type": page_type,
                    "query_text": query_text.strip(),
                    "item_count": 0,
                },
            )
            return 0, self.rebuild_candidates(task_id)

        created_at = iso_now()
        source_url = items[0].source_url
        capture_id = str(uuid.uuid4())
        imported_count = 0
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO mvp_captures (capture_id, task_id, page_type, query_text, source_url, capture_mode, item_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (capture_id, task_id, page_type, query_text.strip(), source_url, capture_mode, len(items), created_at),
            )

            for item in items:
                dedupe_key = self._build_dedupe_key(item)
                tags_json = json.dumps(self._normalize_tags(item.tags), ensure_ascii=False)
                existing_row = conn.execute(
                    """
                    SELECT item_id FROM mvp_capture_items
                    WHERE task_id = ? AND dedupe_key = ?
                    """,
                    (task_id, dedupe_key),
                ).fetchone()
                item_id = existing_row["item_id"] if existing_row else str(uuid.uuid4())
                conn.execute(
                    """
                    INSERT INTO mvp_capture_items (
                        item_id, task_id, capture_id, note_id, source_url, raw_href, xsec_token, xsec_source, debug_url_source,
                        page_type, query_text, title, author,
                        visible_text_excerpt, tags_json, likes, comments, collections, cover_image_url,
                        dedupe_key, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(task_id, dedupe_key) DO UPDATE SET
                        capture_id = excluded.capture_id,
                        note_id = excluded.note_id,
                        source_url = excluded.source_url,
                        raw_href = excluded.raw_href,
                        xsec_token = excluded.xsec_token,
                        xsec_source = excluded.xsec_source,
                        debug_url_source = excluded.debug_url_source,
                        page_type = excluded.page_type,
                        query_text = excluded.query_text,
                        title = excluded.title,
                        author = excluded.author,
                        visible_text_excerpt = excluded.visible_text_excerpt,
                        tags_json = excluded.tags_json,
                        likes = excluded.likes,
                        comments = excluded.comments,
                        collections = excluded.collections,
                        cover_image_url = excluded.cover_image_url,
                        updated_at = excluded.updated_at
                    """,
                    (
                        item_id,
                        task_id,
                        capture_id,
                        item.note_id.strip(),
                        item.source_url.strip(),
                        item.raw_href.strip(),
                        item.xsec_token.strip(),
                        item.xsec_source.strip(),
                        item.debug_url_source.strip(),
                        item.page_type,
                        item.query_text.strip() or query_text.strip(),
                        item.title.strip(),
                        item.author.strip(),
                        item.visible_text_excerpt.strip(),
                        tags_json,
                        max(0, int(item.likes)),
                        max(0, int(item.comments)),
                        max(0, int(item.collections)),
                        item.cover_image_url.strip(),
                        dedupe_key,
                        created_at,
                        created_at,
                    ),
                )
                hit_query = (item.query_text.strip() or query_text.strip())
                if hit_query:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO mvp_item_query_hits (task_id, item_id, query_text, hit_source, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (task_id, item_id, hit_query, capture_mode, created_at),
                    )
                imported_count += 1 if existing_row is None else 0

            conn.commit()

        candidates = self.rebuild_candidates(task_id)
        self._logger.info(
            "Ingested capture payload",
            extra={
                "event_name": "mvp_capture_ingested",
                "task_id": task_id,
                "page_type": page_type,
                "query_text": query_text.strip(),
                "item_count": len(items),
                "imported_count": imported_count,
                "candidate_count": len(candidates),
                "detail": f"{source_url} | xsec_present={any(bool(item.xsec_token.strip()) for item in items)}",
            },
        )
        return imported_count, candidates

    def ingest_manual_text(self, *, task_id: str, text: str) -> tuple[int, list[Candidate]]:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        items = [
            CaptureItemIn(
                source_url=line if line.startswith(("http://", "https://")) else "",
                raw_href="",
                xsec_token="",
                xsec_source="",
                debug_url_source="manual_input",
                page_type="manual",
                query_text="manual",
                note_id="",
                title=line[:120],
                author="manual_input",
                visible_text_excerpt=line,
                tags=[],
                likes=0,
                comments=0,
                collections=0,
                cover_image_url="",
            )
            for line in lines
        ]
        imported_count, candidates = self.ingest_capture(
            task_id=task_id,
            page_type="manual",
            query_text="manual",
            items=items,
            capture_mode="manual",
        )
        self._logger.info(
            "Imported manual seeds",
            extra={
                "event_name": "mvp_manual_seeds_ingested",
                "task_id": task_id,
                "imported_count": imported_count,
                "candidate_count": len(candidates),
                "item_count": len(lines),
            },
        )
        return imported_count, candidates

    def ingest_scraper_items(
        self,
        *,
        task_id: str,
        keyword: str,
        items: list[CaptureItemIn],
    ) -> tuple[int, int]:
        """Ingest server-initiated scraper items via the existing capture pipeline.

        No token validation — access control happens at the endpoint layer.
        Returns (captured_count, new_count).
        """
        captured_count = len(items)
        if captured_count == 0:
            return 0, 0
        imported_count, _ = self.ingest_capture(
            task_id=task_id,
            page_type="search_result",
            query_text=keyword,
            items=items,
            capture_mode="scraper",
        )
        return captured_count, imported_count

    def rebuild_candidates(self, task_id: str) -> list[Candidate]:
        snapshot = self.get_task_snapshot(task_id)
        if snapshot is None:
            return []

        with self._connect() as conn:
            normalized_items = self._load_normalized_items(conn, task_id)
            candidates = build_candidates(snapshot.topic, normalized_items, limit=5)

            conn.execute("DELETE FROM mvp_candidates WHERE task_id = ?", (task_id,))
            created_at = iso_now()
            for candidate in candidates:
                conn.execute(
                    """
                    INSERT INTO mvp_candidates (
                        candidate_id, task_id, title, why_now, angle, score,
                        supporting_note_count, query_coverage_count, score_explanation,
                        evidence_refs_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        candidate.candidate_id,
                        task_id,
                        candidate.title,
                        candidate.why_now,
                        candidate.angle,
                        candidate.score,
                        candidate.supporting_note_count,
                        candidate.query_coverage_count,
                        candidate.score_explanation,
                        json.dumps([ref.model_dump() for ref in candidate.evidence_refs], ensure_ascii=False),
                        created_at,
                    ),
                )
            conn.commit()
        self._logger.info(
            "Rebuilt deterministic candidates",
            extra={
                "event_name": "mvp_candidates_rebuilt",
                "task_id": task_id,
                "candidate_count": len(candidates),
                "item_count": len(normalized_items),
                "detail": snapshot.topic,
            },
        )
        return candidates

    async def refresh_hotspots(self, task_id: str, spider_client: Any | None = None) -> HotspotSnapshotResponse:
        with self._connect() as conn:
            task_row = conn.execute(
                "SELECT task_id, topic FROM mvp_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if task_row is None:
                raise KeyError(task_id)
            queries = self._load_hotspot_queries(conn, task_id)

        try:
            snapshot = await build_hotspot_snapshot(
                task_id=task_id,
                topic=task_row["topic"],
                queries=queries,
                spider_client=spider_client,
            )
        except Exception as exc:  # noqa: BLE001
            snapshot = HotspotSnapshotResponse(
                task_id=task_id,
                status="error",
                generated_at=utc_now(),
                error_message=str(exc),
                lists=[],
            )

        fallback_lists: list[HotspotList] = []
        if snapshot.status == "error":
            previous_success = self._get_latest_successful_hotspots(task_id)
            if previous_success is not None:
                fallback_lists = previous_success.lists
                snapshot.lists = previous_success.lists
                snapshot.stale_seconds = previous_success.stale_seconds

        self._persist_hotspot_snapshot(snapshot, fallback_lists if snapshot.status == "error" else snapshot.lists)
        self._logger.info(
            "Refreshed hotspot snapshot",
            extra={
                "event_name": "mvp_hotspots_refreshed",
                "task_id": task_id,
                "status": snapshot.status,
                "detail": snapshot.error_message or "ok",
            },
        )
        return snapshot

    def get_hotspots(self, task_id: str) -> HotspotSnapshotResponse | None:
        with self._connect() as conn:
            if not self._task_exists(conn, task_id):
                return None
            latest_row = conn.execute(
                """
                SELECT snapshot_id, status, generated_at, error_message
                FROM mvp_hotspot_snapshots
                WHERE task_id = ?
                ORDER BY generated_at DESC
                LIMIT 1
                """,
                (task_id,),
            ).fetchone()
            if latest_row is None:
                return HotspotSnapshotResponse(task_id=task_id, status="empty")

            if latest_row["status"] == "error":
                success_row = conn.execute(
                    """
                    SELECT snapshot_id, generated_at
                    FROM mvp_hotspot_snapshots
                    WHERE task_id = ? AND status = 'ready'
                    ORDER BY generated_at DESC
                    LIMIT 1
                    """,
                    (task_id,),
                ).fetchone()
                lists = self._load_hotspot_lists(conn, success_row["snapshot_id"]) if success_row else []
            else:
                lists = self._load_hotspot_lists(conn, latest_row["snapshot_id"])

        generated_at = datetime.fromisoformat(latest_row["generated_at"])
        return HotspotSnapshotResponse(
            task_id=task_id,
            status=latest_row["status"],
            generated_at=generated_at,
            stale_seconds=max(0, int((utc_now() - generated_at).total_seconds())),
            error_message=latest_row["error_message"] or "",
            lists=lists,
        )

    def _load_queries(self, task_id: str) -> list[ExpandedQuery]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT query_id, category, query_text, sort_order
                FROM mvp_queries
                WHERE task_id = ?
                ORDER BY sort_order ASC, created_at ASC
                """,
                (task_id,),
            ).fetchall()
        return [
            ExpandedQuery(
                query_id=row["query_id"],
                category=self._coerce_query_category(row["category"]),
                query_text=row["query_text"],
                order=int(row["sort_order"]),
            )
            for row in rows
        ]

    def _load_hotspot_queries(self, conn: sqlite3.Connection, task_id: str) -> list[str]:
        del conn, task_id
        return []

    def _load_normalized_items(self, conn: sqlite3.Connection, task_id: str) -> list[NormalizedItem]:
        rows = conn.execute(
            """
            SELECT item_id, note_id, title, author, source_url, query_text, visible_text_excerpt, tags_json,
                   page_type, raw_href, xsec_token, xsec_source, debug_url_source,
                   likes, comments, collections
            FROM mvp_capture_items
            WHERE task_id = ?
            """,
            (task_id,),
        ).fetchall()
        return [
            NormalizedItem(
                note_id=row["note_id"] or "",
                title=row["title"],
                author=row["author"],
                source_url=row["source_url"],
                raw_href=row["raw_href"] or "",
                xsec_token=row["xsec_token"] or "",
                xsec_source=row["xsec_source"] or "",
                debug_url_source=row["debug_url_source"] or "",
                query_text=row["query_text"],
                excerpt=row["visible_text_excerpt"],
                tags=list(json.loads(row["tags_json"])),
                likes=int(row["likes"]),
                comments=int(row["comments"]),
                collections=int(row["collections"]),
                item_id=row["item_id"],
            )
            for row in rows
            if row["note_id"] or row["page_type"] == "manual"
        ]

    def _load_item_query_hits(self, conn: sqlite3.Connection, task_id: str) -> dict[str, set[str]]:
        rows = conn.execute(
            """
            SELECT item_id, query_text
            FROM mvp_item_query_hits
            WHERE task_id = ?
            """,
            (task_id,),
        ).fetchall()
        hit_map: dict[str, set[str]] = {}
        for row in rows:
            item_id = row["item_id"]
            hit_map.setdefault(item_id, set()).add(row["query_text"])
        return hit_map

    def _get_latest_successful_hotspots(self, task_id: str) -> HotspotSnapshotResponse | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT snapshot_id, generated_at
                FROM mvp_hotspot_snapshots
                WHERE task_id = ? AND status = 'ready'
                ORDER BY generated_at DESC
                LIMIT 1
                """,
                (task_id,),
            ).fetchone()
            if row is None:
                return None
            generated_at = datetime.fromisoformat(row["generated_at"])
            return HotspotSnapshotResponse(
                task_id=task_id,
                status="ready",
                generated_at=generated_at,
                stale_seconds=max(0, int((utc_now() - generated_at).total_seconds())),
                lists=self._load_hotspot_lists(conn, row["snapshot_id"]),
            )

    def _persist_hotspot_snapshot(self, snapshot: HotspotSnapshotResponse, lists_to_store: list[HotspotList]) -> None:
        snapshot_id = str(uuid.uuid4())
        generated_at = (snapshot.generated_at or utc_now()).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO mvp_hotspot_snapshots (snapshot_id, task_id, status, generated_at, error_message)
                VALUES (?, ?, ?, ?, ?)
                """,
                (snapshot_id, snapshot.task_id, snapshot.status, generated_at, snapshot.error_message),
            )
            for hotspot_list in lists_to_store:
                for index, item in enumerate(hotspot_list.items):
                    conn.execute(
                        """
                        INSERT INTO mvp_hotspot_items (
                            snapshot_item_id, snapshot_id, task_id, metric, rank_index, note_id, title,
                            source_url, author, excerpt, likes, comments, collections, query_sources_json
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(uuid.uuid4()),
                            snapshot_id,
                            snapshot.task_id,
                            hotspot_list.metric,
                            index,
                            item.note_id or "",
                            item.title,
                            item.source_url,
                            item.author,
                            item.excerpt,
                            item.likes,
                            item.comments,
                            item.collections,
                            json.dumps(item.query_sources, ensure_ascii=False),
                        ),
                    )
            conn.commit()

    def _load_hotspot_lists(self, conn: sqlite3.Connection, snapshot_id: str) -> list[HotspotList]:
        rows = conn.execute(
            """
            SELECT metric, rank_index, note_id, title, source_url, author, excerpt,
                   likes, comments, collections, query_sources_json
            FROM mvp_hotspot_items
            WHERE snapshot_id = ?
            ORDER BY metric ASC, rank_index ASC
            """,
            (snapshot_id,),
        ).fetchall()
        grouped: dict[str, list[HotspotItem]] = {}
        metric_order: list[str] = []
        for row in rows:
            metric = row["metric"]
            if metric not in grouped:
                grouped[metric] = []
                metric_order.append(metric)
            grouped[metric].append(
                HotspotItem(
                    note_id=row["note_id"] or None,
                    title=row["title"],
                    source_url=row["source_url"],
                    author=row["author"],
                    excerpt=row["excerpt"],
                    likes=int(row["likes"]),
                    comments=int(row["comments"]),
                    collections=int(row["collections"]),
                    query_sources=list(json.loads(row["query_sources_json"])),
                )
            )
        return [
            HotspotList(metric=metric, items=grouped[metric])  # type: ignore[arg-type]
            for metric in sorted(metric_order, key=lambda value: {"likes": 0, "collections": 1, "comments": 2}.get(value, 9))
        ]

    def _load_task_counts(self, conn: sqlite3.Connection, task_id: str) -> tuple[int, int]:
        capture_count = int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM mvp_captures WHERE task_id = ?",
                (task_id,),
            ).fetchone()["c"]
        )
        candidate_count = int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM mvp_candidates WHERE task_id = ?",
                (task_id,),
            ).fetchone()["c"]
        )
        return capture_count, candidate_count

    def _load_task_updated_at(self, conn: sqlite3.Connection, task_id: str, fallback_created_at: str) -> datetime:
        row = conn.execute(
            """
            SELECT MAX(updated_at) AS updated_at
            FROM (
                SELECT created_at AS updated_at FROM mvp_tasks WHERE task_id = ?
                UNION ALL
                SELECT created_at AS updated_at FROM mvp_captures WHERE task_id = ?
                UNION ALL
                SELECT created_at AS updated_at FROM mvp_candidates WHERE task_id = ?
            )
            """,
            (task_id, task_id, task_id),
        ).fetchone()
        value = row["updated_at"] if row is not None else fallback_created_at
        return datetime.fromisoformat(value or fallback_created_at)

    def _get_active_search_context(self, conn: sqlite3.Connection | None = None) -> ActiveSearchContext | None:
        owns_connection = conn is None
        active_conn = conn or self._connect()
        try:
            row = active_conn.execute(
                """
                SELECT task_id, query, source, opened_at
                FROM mvp_active_search_context
                WHERE singleton_id = 1
                """
            ).fetchone()
        finally:
            if owns_connection:
                active_conn.close()
        if row is None:
            return None
        return ActiveSearchContext(
            task_id=row["task_id"],
            query=row["query"],
            source=row["source"],
            opened_at=datetime.fromisoformat(row["opened_at"]),
        )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
        except sqlite3.DatabaseError:
            # Some SQLite backends such as in-memory databases may ignore WAL mode.
            pass
        return conn

    def _ensure_capture_item_columns(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute("PRAGMA table_info(mvp_capture_items)").fetchall()
        existing = {row["name"] for row in rows}
        required = {
            "raw_href": "TEXT NOT NULL DEFAULT ''",
            "xsec_token": "TEXT NOT NULL DEFAULT ''",
            "xsec_source": "TEXT NOT NULL DEFAULT ''",
            "debug_url_source": "TEXT NOT NULL DEFAULT ''",
        }
        for name, ddl in required.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE mvp_capture_items ADD COLUMN {name} {ddl}")

    def _ensure_candidate_columns(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute("PRAGMA table_info(mvp_candidates)").fetchall()
        existing = {row["name"] for row in rows}
        required = {
            "supporting_note_count": "INTEGER NOT NULL DEFAULT 0",
            "query_coverage_count": "INTEGER NOT NULL DEFAULT 0",
            "score_explanation": "TEXT NOT NULL DEFAULT ''",
        }
        for name, ddl in required.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE mvp_candidates ADD COLUMN {name} {ddl}")

    def _ensure_task_columns(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute("PRAGMA table_info(mvp_tasks)").fetchall()
        existing = {row["name"] for row in rows}
        required = {
            "query_generation_source": "TEXT NOT NULL DEFAULT 'fallback_rule'",
            "query_generation_notice": "TEXT DEFAULT NULL",
        }
        for name, ddl in required.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE mvp_tasks ADD COLUMN {name} {ddl}")

    def _ensure_recommended_note_cache_columns(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute("PRAGMA table_info(mvp_recommended_note_cache)").fetchall()
        existing = {row["name"] for row in rows}
        required = {
            "diagnostics_json": "TEXT NOT NULL DEFAULT '{}'",
        }
        for name, ddl in required.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE mvp_recommended_note_cache ADD COLUMN {name} {ddl}")

    def _load_cached_recommended_notes(
        self,
        conn: sqlite3.Connection,
        task_id: str,
        snapshot_version: int,
    ) -> tuple[list[RecommendedNote], RecommendedNotesDiagnostics] | None:
        row = conn.execute(
            """
            SELECT notes_json, diagnostics_json
            FROM mvp_recommended_note_cache
            WHERE task_id = ? AND snapshot_version = ?
            """,
            (task_id, snapshot_version),
        ).fetchone()
        if row is None:
            return None
        diagnostics_json = row["diagnostics_json"] if "diagnostics_json" in row.keys() else ""
        diagnostics = RecommendedNotesDiagnostics.model_validate(json.loads(diagnostics_json)) if diagnostics_json else RecommendedNotesDiagnostics()
        return [RecommendedNote.model_validate(entry) for entry in json.loads(row["notes_json"])], diagnostics

    def _persist_recommended_notes_cache(
        self,
        *,
        task_id: str,
        snapshot_version: int,
        notes: list[RecommendedNote],
        diagnostics: RecommendedNotesDiagnostics,
        analysis_source: str,
        analysis_notice: str | None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO mvp_recommended_note_cache
                    (task_id, snapshot_version, notes_json, diagnostics_json, analysis_source, analysis_notice, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    snapshot_version = excluded.snapshot_version,
                    notes_json = excluded.notes_json,
                    diagnostics_json = excluded.diagnostics_json,
                    analysis_source = excluded.analysis_source,
                    analysis_notice = excluded.analysis_notice,
                    updated_at = excluded.updated_at
                """,
                (
                    task_id,
                    snapshot_version,
                    json.dumps([note.model_dump() for note in notes], ensure_ascii=False),
                    json.dumps(diagnostics.model_dump(), ensure_ascii=False),
                    analysis_source,
                    analysis_notice,
                    iso_now(),
                ),
            )
            conn.commit()

    def _build_and_cache_recommended_notes(
        self,
        *,
        task_id: str,
        topic: str,
        items: list[NormalizedItem],
        query_hits: dict[str, set[str]],
        snapshot_version: int,
    ) -> tuple[list[RecommendedNote], RecommendedNotesDiagnostics]:
        if not items:
            diagnostics = RecommendedNotesDiagnostics()
            self._persist_recommended_notes_cache(
                task_id=task_id,
                snapshot_version=snapshot_version,
                notes=[],
                diagnostics=diagnostics,
                analysis_source="fallback_rule",
                analysis_notice=None,
            )
            return [], diagnostics

        try:
            config = load_llm_config()
            self._logger.info(
                "Starting MVP recommended notes LLM analysis",
                extra={
                    "event_name": "mvp_recommended_notes_llm_started",
                    "task_id": task_id,
                    "detail": topic.strip(),
                    "provider": "openai_compatible",
                    "base_url": self._sanitize_base_url(config.base_url),
                    "model": config.model,
                },
            )
            analysis = LLMRecommendedNoteAnalyzer().analyze(
                topic,
                items,
                query_hits_by_item_id=query_hits,
                limit=5,
            )
            self._logger.info(
                "MVP recommended notes LLM analysis succeeded",
                extra={
                    "event_name": "mvp_recommended_notes_llm_succeeded",
                    "task_id": task_id,
                    "detail": topic.strip(),
                    "provider": "openai_compatible",
                    "base_url": self._sanitize_base_url(config.base_url),
                    "model": config.model,
                },
            )
            self._persist_recommended_notes_cache(
                task_id=task_id,
                snapshot_version=snapshot_version,
                notes=analysis.notes,
                diagnostics=analysis.diagnostics,
                analysis_source="llm",
                analysis_notice=None,
            )
            return analysis.notes, analysis.diagnostics
        except MVPLLMConfigError as exc:
            config = self._read_llm_config_for_logging()
            self._logger.warning(
                "MVP recommended notes LLM analysis failed",
                extra={
                    "event_name": "mvp_recommended_notes_llm_failed",
                    "task_id": task_id,
                    "detail": topic.strip(),
                    "provider": "openai_compatible",
                    "base_url": config["base_url"],
                    "model": config["model"],
                    "failure_stage": "config_missing",
                    "error_type": type(exc).__name__,
                    "error_message": self._truncate_error(str(exc)),
                },
            )
            diagnostics = RecommendedNotesDiagnostics(total_note_count=len([item for item in items if item.note_id]))
        except LLMRecommendedNotesFailure as exc:
            config = self._read_llm_config_for_logging()
            self._logger.warning(
                "MVP recommended notes LLM analysis failed",
                extra={
                    "event_name": "mvp_recommended_notes_llm_failed",
                    "task_id": task_id,
                    "detail": topic.strip(),
                    "provider": "openai_compatible",
                    "base_url": config["base_url"],
                    "model": config["model"],
                    "failure_stage": exc.stage,
                    "error_type": type(exc).__name__,
                    "error_message": self._truncate_error(str(exc)),
                },
            )
            diagnostics = exc.diagnostics or RecommendedNotesDiagnostics(total_note_count=len([item for item in items if item.note_id]))

        fallback_notes = build_recommended_notes(
            topic,
            items,
            query_hits_by_item_id=query_hits,
            limit=5,
        )
        diagnostics.llm_recommended_count = len(fallback_notes)
        diagnostics.analysis_source = "fallback_rule"
        self._logger.info(
            "MVP recommended notes fallback used",
            extra={
                "event_name": "mvp_recommended_notes_fallback_used",
                "task_id": task_id,
                "detail": topic.strip(),
                "query_generation_source": "fallback_rule",
            },
        )
        self._persist_recommended_notes_cache(
            task_id=task_id,
            snapshot_version=snapshot_version,
            notes=fallback_notes,
            diagnostics=diagnostics,
            analysis_source="fallback_rule",
            analysis_notice=None,
        )
        return fallback_notes, diagnostics

    def _build_queries_for_task(self, *, task_id: str, topic: str) -> tuple[list[ExpandedQuery], str, str | None]:
        try:
            config = load_llm_config()
            self._logger.info(
                "Starting MVP LLM query expansion",
                extra={
                    "event_name": "mvp_query_expansion_llm_started",
                    "task_id": task_id,
                    "detail": topic.strip(),
                    "provider": "openai_compatible",
                    "base_url": self._sanitize_base_url(config.base_url),
                    "model": config.model,
                },
            )
            expansions = LLMQueryExpander().expand_topic(topic)
            self._logger.info(
                "MVP LLM query expansion succeeded",
                extra={
                    "event_name": "mvp_query_expansion_llm_succeeded",
                    "task_id": task_id,
                    "detail": topic.strip(),
                    "provider": "openai_compatible",
                    "base_url": self._sanitize_base_url(config.base_url),
                    "model": config.model,
                    "query_generation_source": "llm",
                },
            )
            return self._to_expanded_queries(expansions), "llm", None
        except MVPLLMConfigError as exc:
            config = self._read_llm_config_for_logging()
            notice = self._notice_for_failure_stage("config_missing")
            self._logger.warning(
                "MVP LLM query expansion failed",
                extra={
                    "event_name": "mvp_query_expansion_llm_failed",
                    "task_id": task_id,
                    "detail": topic.strip(),
                    "provider": "openai_compatible",
                    "base_url": config["base_url"],
                    "model": config["model"],
                    "failure_stage": "config_missing",
                    "error_type": type(exc).__name__,
                    "error_message": self._truncate_error(str(exc)),
                },
            )
            fallback = self._to_expanded_queries(expand_topic(topic))
            self._logger.info(
                "MVP query expansion fallback used",
                extra={
                    "event_name": "mvp_query_expansion_fallback_used",
                    "task_id": task_id,
                    "detail": topic.strip(),
                    "provider": "openai_compatible",
                    "base_url": config["base_url"],
                    "model": config["model"],
                    "failure_stage": "config_missing",
                    "query_generation_source": "fallback_rule",
                },
            )
            return fallback, "fallback_rule", notice
        except LLMExpansionFailure as exc:
            notice = self._notice_for_failure_stage(exc.stage)
            config = self._read_llm_config_for_logging()
            self._logger.warning(
                "MVP LLM query expansion failed",
                extra={
                    "event_name": "mvp_query_expansion_llm_failed",
                    "task_id": task_id,
                    "detail": topic.strip(),
                    "provider": "openai_compatible",
                    "base_url": config["base_url"],
                    "model": config["model"],
                    "failure_stage": exc.stage,
                    "error_type": type(exc).__name__,
                    "error_message": self._truncate_error(str(exc)),
                },
            )
            fallback = self._to_expanded_queries(expand_topic(topic))
            self._logger.info(
                "MVP query expansion fallback used",
                extra={
                    "event_name": "mvp_query_expansion_fallback_used",
                    "task_id": task_id,
                    "detail": topic.strip(),
                    "provider": "openai_compatible",
                    "base_url": config["base_url"],
                    "model": config["model"],
                    "failure_stage": exc.stage,
                    "query_generation_source": "fallback_rule",
                },
            )
            return fallback, "fallback_rule", notice

    def _to_expanded_queries(self, expansions: list[Any]) -> list[ExpandedQuery]:
        return [
            ExpandedQuery(
                query_id=str(uuid.uuid4()),
                category=expansion.category,
                query_text=expansion.query_text,
                order=index,
            )
            for index, expansion in enumerate(expansions)
        ]

    def _notice_for_failure_stage(self, stage: str) -> str:
        if stage == "config_missing":
            return "AI 拓展词未启用，已使用规则生成。"
        return "AI 拓展词暂时不可用，已自动降级为规则生成。"

    def _truncate_error(self, message: str, *, limit: int = 240) -> str:
        text = (message or "").strip()
        if len(text) <= limit:
            return text
        return f"{text[: limit - 3]}..."

    def _read_llm_config_for_logging(self) -> dict[str, str]:
        try:
            config = load_llm_config()
            return {
                "model": config.model,
                "base_url": self._sanitize_base_url(config.base_url),
            }
        except Exception:  # noqa: BLE001
            return {"model": "", "base_url": ""}

    def _sanitize_base_url(self, value: str) -> str:
        text = (value or "").strip()
        if not text:
            return ""
        return text.split("?", 1)[0].split("#", 1)[0]

    def _build_dedupe_key(self, item: CaptureItemIn) -> str:
        note_id = item.note_id.strip()
        if note_id:
            return f"note:{note_id}"
        basis = "|".join(
            [
                item.source_url.strip(),
                item.title.strip().lower(),
                item.visible_text_excerpt.strip().lower()[:120],
            ]
        )
        digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()
        return f"hash:{digest}"

    def _normalize_tags(self, tags: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for tag in tags:
            normalized = tag.strip().lstrip("#").lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
        return result

    def _normalize_query_line(self, value: str) -> str:
        return " ".join(value.strip().split())[:120]

    def _query_key(self, value: str) -> str:
        return self._normalize_query_line(value).lower()

    def _coerce_query_category(self, value: str) -> QueryCategory:
        allowed: set[str] = {"core", "crowd", "scenario", "problem", "compare", "decision", "custom"}
        return "custom" if value not in allowed else value  # type: ignore[return-value]

    def _task_exists(self, conn: sqlite3.Connection, task_id: str) -> bool:
        row = conn.execute("SELECT 1 FROM mvp_tasks WHERE task_id = ?", (task_id,)).fetchone()
        return row is not None

    def _urlsafe_encode(self, data: bytes) -> str:
        return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")

    def _urlsafe_decode(self, data: str) -> bytes:
        padding = "=" * (-len(data) % 4)
        try:
            return base64.urlsafe_b64decode(data + padding)
        except Exception as exc:  # noqa: BLE001
            raise InvalidCaptureToken("Malformed capture token encoding") from exc


def default_mvp_db_path() -> Path:
    return Path("data") / "xhs_extension_mvp.db"


def default_secret() -> str:
    return os.environ.get("XHS_EXTENSION_MVP_SECRET", "xhs-extension-mvp-secret")
