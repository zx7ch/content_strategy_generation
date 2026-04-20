"""Postgres-backed ingestion store for V2 P1-2 evidence ingestion."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.v2.ingestion.models import (
    AuthorRecord,
    CommentRecord,
    ContentItemRecord,
    ContentMetricsSnapshotRecord,
    DataImportPreviewRecord,
    ExtensionCaptureSessionRecord,
    IngestionRunRecord,
    TopicRecord,
)


def _load_psycopg_jsonb():
    try:
        from psycopg import connect  # type: ignore
        from psycopg.rows import dict_row  # type: ignore
        from psycopg.types.json import Jsonb  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on optional runtime package
        raise RuntimeError(
            "psycopg is required when POSTGRES_DSN is configured. "
            "Install project dependencies with psycopg[binary] support."
        ) from exc
    return connect, dict_row, Jsonb


def _default_connector(dsn: str):
    connect, dict_row, _jsonb = _load_psycopg_jsonb()
    return connect(dsn, row_factory=dict_row)


class PostgresIngestionStore:
    def __init__(
        self,
        dsn: str,
        *,
        connector: Callable[[str], Any] | None = None,
    ) -> None:
        self._dsn = dsn
        self._connector = connector or _default_connector

    def save_ingestion_run(self, run: IngestionRunRecord) -> IngestionRunRecord:
        row = self._fetchone(
            """
            INSERT INTO ingestion_runs (
                id, workspace_id, brand_id, entry_type, source_type, source_adapter, dedupe_key,
                source_config, stats, error_summary, status, started_at, finished_at, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                source_adapter = EXCLUDED.source_adapter,
                dedupe_key = EXCLUDED.dedupe_key,
                source_config = EXCLUDED.source_config,
                stats = EXCLUDED.stats,
                error_summary = EXCLUDED.error_summary,
                status = EXCLUDED.status,
                started_at = EXCLUDED.started_at,
                finished_at = EXCLUDED.finished_at
            RETURNING id, workspace_id, brand_id, entry_type, source_type, source_adapter, dedupe_key,
                      source_config, stats, error_summary, status, started_at, finished_at, created_at
            """,
            (
                run.id,
                run.workspace_id,
                run.brand_id,
                run.entry_type,
                run.source_type,
                run.source_adapter,
                run.dedupe_key,
                self._jsonb(run.source_config),
                self._jsonb(run.stats),
                self._jsonb(run.error_summary),
                run.status,
                run.started_at,
                run.finished_at,
                run.created_at,
            ),
        )
        return self._run_from_row(row)

    def list_ingestion_runs(self, brand_id: str, limit: int = 10) -> list[IngestionRunRecord]:
        rows = self._fetchall(
            """
            SELECT id, workspace_id, brand_id, entry_type, source_type, source_adapter, dedupe_key,
                   source_config, stats, error_summary, status, started_at, finished_at, created_at
            FROM ingestion_runs
            WHERE brand_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (brand_id, limit),
        )
        return [self._run_from_row(row) for row in rows]

    def save_extension_capture_session(
        self,
        session: ExtensionCaptureSessionRecord,
    ) -> ExtensionCaptureSessionRecord:
        row = self._fetchone(
            """
            INSERT INTO extension_capture_sessions (
                capture_session_id, workspace_id, brand_id, channel_id, capture_token, status, expires_at,
                created_at, captured_at, preview_payload, ingestion_receipt, error_summary
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (capture_session_id) DO UPDATE SET
                channel_id = EXCLUDED.channel_id,
                capture_token = EXCLUDED.capture_token,
                status = EXCLUDED.status,
                expires_at = EXCLUDED.expires_at,
                captured_at = EXCLUDED.captured_at,
                preview_payload = EXCLUDED.preview_payload,
                ingestion_receipt = EXCLUDED.ingestion_receipt,
                error_summary = EXCLUDED.error_summary
            RETURNING capture_session_id, workspace_id, brand_id, channel_id, capture_token, status, expires_at,
                      created_at, captured_at, preview_payload, ingestion_receipt, error_summary
            """,
            (
                session.capture_session_id,
                session.workspace_id,
                session.brand_id,
                session.channel_id,
                session.capture_token,
                session.status,
                session.expires_at,
                session.created_at,
                session.captured_at,
                self._jsonb(session.preview_payload) if session.preview_payload is not None else None,
                self._jsonb(session.ingestion_receipt) if session.ingestion_receipt is not None else None,
                self._jsonb(session.error_summary) if session.error_summary is not None else None,
            ),
        )
        return self._extension_capture_session_from_row(row)

    def get_extension_capture_session(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        capture_session_id: str,
    ) -> ExtensionCaptureSessionRecord | None:
        row = self._fetchone(
            """
            SELECT capture_session_id, workspace_id, brand_id, channel_id, capture_token, status, expires_at,
                   created_at, captured_at, preview_payload, ingestion_receipt, error_summary
            FROM extension_capture_sessions
            WHERE capture_session_id = %s AND workspace_id = %s AND brand_id = %s
            """,
            (capture_session_id, workspace_id, brand_id),
        )
        return self._extension_capture_session_from_row(row) if row else None

    def find_extension_capture_session(
        self,
        capture_session_id: str,
    ) -> ExtensionCaptureSessionRecord | None:
        row = self._fetchone(
            """
            SELECT capture_session_id, workspace_id, brand_id, channel_id, capture_token, status, expires_at,
                   created_at, captured_at, preview_payload, ingestion_receipt, error_summary
            FROM extension_capture_sessions
            WHERE capture_session_id = %s
            """,
            (capture_session_id,),
        )
        return self._extension_capture_session_from_row(row) if row else None

    def list_extension_capture_sessions(
        self,
        *,
        brand_id: str,
        limit: int = 10,
    ) -> list[ExtensionCaptureSessionRecord]:
        rows = self._fetchall(
            """
            SELECT capture_session_id, workspace_id, brand_id, channel_id, capture_token, status, expires_at,
                   created_at, captured_at, preview_payload, ingestion_receipt, error_summary
            FROM extension_capture_sessions
            WHERE brand_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (brand_id, limit),
        )
        return [self._extension_capture_session_from_row(row) for row in rows]

    def save_data_import_preview(self, preview: DataImportPreviewRecord) -> DataImportPreviewRecord:
        row = self._fetchone(
            """
            INSERT INTO data_import_previews (
                preview_id, workspace_id, brand_id, file_name, status, uploaded_at, parsed_row_count,
                preview_payload, ingestion_receipt, field_errors, error_summary
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (preview_id) DO UPDATE SET
                file_name = EXCLUDED.file_name,
                status = EXCLUDED.status,
                uploaded_at = EXCLUDED.uploaded_at,
                parsed_row_count = EXCLUDED.parsed_row_count,
                preview_payload = EXCLUDED.preview_payload,
                ingestion_receipt = EXCLUDED.ingestion_receipt,
                field_errors = EXCLUDED.field_errors,
                error_summary = EXCLUDED.error_summary
            RETURNING preview_id, workspace_id, brand_id, file_name, status, uploaded_at, parsed_row_count,
                      preview_payload, ingestion_receipt, field_errors, error_summary
            """,
            (
                preview.preview_id,
                preview.workspace_id,
                preview.brand_id,
                preview.file_name,
                preview.status,
                preview.uploaded_at,
                preview.parsed_row_count,
                self._jsonb(preview.preview_payload) if preview.preview_payload is not None else None,
                self._jsonb(preview.ingestion_receipt) if preview.ingestion_receipt is not None else None,
                self._jsonb(preview.field_errors),
                self._jsonb(preview.error_summary) if preview.error_summary is not None else None,
            ),
        )
        return self._data_import_preview_from_row(row)

    def get_data_import_preview(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        preview_id: str,
    ) -> DataImportPreviewRecord | None:
        row = self._fetchone(
            """
            SELECT preview_id, workspace_id, brand_id, file_name, status, uploaded_at, parsed_row_count,
                   preview_payload, ingestion_receipt, field_errors, error_summary
            FROM data_import_previews
            WHERE preview_id = %s AND workspace_id = %s AND brand_id = %s
            """,
            (preview_id, workspace_id, brand_id),
        )
        return self._data_import_preview_from_row(row) if row else None

    def list_data_import_previews(
        self,
        *,
        brand_id: str,
        limit: int = 10,
    ) -> list[DataImportPreviewRecord]:
        rows = self._fetchall(
            """
            SELECT preview_id, workspace_id, brand_id, file_name, status, uploaded_at, parsed_row_count,
                   preview_payload, ingestion_receipt, field_errors, error_summary
            FROM data_import_previews
            WHERE brand_id = %s
            ORDER BY uploaded_at DESC
            LIMIT %s
            """,
            (brand_id, limit),
        )
        return [self._data_import_preview_from_row(row) for row in rows]

    def save_author(self, author: AuthorRecord) -> AuthorRecord:
        row = self._fetchone(
            """
            INSERT INTO authors (
                id, workspace_id, platform, platform_author_id, display_name, profile_url, follower_count,
                metadata, first_seen_at, last_seen_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (workspace_id, platform, platform_author_id) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                profile_url = EXCLUDED.profile_url,
                follower_count = EXCLUDED.follower_count,
                metadata = EXCLUDED.metadata,
                last_seen_at = EXCLUDED.last_seen_at
            RETURNING id, workspace_id, platform, platform_author_id, display_name, profile_url,
                      follower_count, metadata, first_seen_at, last_seen_at
            """,
            (
                author.id,
                author.workspace_id,
                author.platform,
                author.platform_author_id,
                author.display_name,
                author.profile_url,
                author.follower_count,
                self._jsonb(author.metadata),
                author.first_seen_at,
                author.last_seen_at,
            ),
        )
        return self._author_from_row(row)

    def get_author_by_platform_identity(
        self,
        *,
        workspace_id: str,
        platform: str,
        platform_author_id: str,
    ) -> AuthorRecord | None:
        row = self._fetchone(
            """
            SELECT id, workspace_id, platform, platform_author_id, display_name, profile_url,
                   follower_count, metadata, first_seen_at, last_seen_at
            FROM authors
            WHERE workspace_id = %s AND platform = %s AND platform_author_id = %s
            """,
            (workspace_id, platform, platform_author_id),
        )
        return self._author_from_row(row) if row else None

    def save_topic(self, topic: TopicRecord) -> TopicRecord:
        row = self._fetchone(
            """
            INSERT INTO topics (
                id, workspace_id, brand_id, normalized_name, display_name, topic_type, metadata, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (brand_id, normalized_name) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                topic_type = EXCLUDED.topic_type,
                metadata = EXCLUDED.metadata,
                updated_at = EXCLUDED.updated_at
            RETURNING id, workspace_id, brand_id, normalized_name, display_name, topic_type, metadata, created_at, updated_at
            """,
            (
                topic.id,
                topic.workspace_id,
                topic.brand_id,
                topic.normalized_name,
                topic.display_name,
                topic.topic_type,
                self._jsonb(topic.metadata),
                topic.created_at,
                topic.updated_at,
            ),
        )
        return self._topic_from_row(row)

    def get_topic_by_normalized_name(
        self,
        *,
        brand_id: str,
        normalized_name: str,
    ) -> TopicRecord | None:
        row = self._fetchone(
            """
            SELECT id, workspace_id, brand_id, normalized_name, display_name, topic_type, metadata, created_at, updated_at
            FROM topics
            WHERE brand_id = %s AND normalized_name = %s
            LIMIT 1
            """,
            (brand_id, normalized_name),
        )
        return self._topic_from_row(row) if row else None

    def list_topics(self, brand_id: str) -> list[TopicRecord]:
        rows = self._fetchall(
            """
            SELECT id, workspace_id, brand_id, normalized_name, display_name, topic_type, metadata, created_at, updated_at
            FROM topics
            WHERE brand_id = %s
            ORDER BY created_at ASC
            """,
            (brand_id,),
        )
        return [self._topic_from_row(row) for row in rows]

    def save_content_item(self, item: ContentItemRecord) -> ContentItemRecord:
        row = self._fetchone(
            """
            INSERT INTO content_items (
                id, workspace_id, brand_id, channel_id, author_id, platform, platform_content_id,
                source_type, source_url, title, body_text, published_at, collected_at, content_hash,
                tags, topic_ids, raw_payload, metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                brand_id = EXCLUDED.brand_id,
                channel_id = EXCLUDED.channel_id,
                author_id = EXCLUDED.author_id,
                source_type = EXCLUDED.source_type,
                source_url = EXCLUDED.source_url,
                title = EXCLUDED.title,
                body_text = EXCLUDED.body_text,
                published_at = EXCLUDED.published_at,
                collected_at = EXCLUDED.collected_at,
                content_hash = EXCLUDED.content_hash,
                tags = EXCLUDED.tags,
                topic_ids = EXCLUDED.topic_ids,
                raw_payload = EXCLUDED.raw_payload,
                metadata = EXCLUDED.metadata
            RETURNING id, workspace_id, brand_id, channel_id, author_id, platform, platform_content_id,
                      source_type, source_url, title, body_text, published_at, collected_at, content_hash,
                      tags, topic_ids, raw_payload, metadata
            """,
            (
                item.id,
                item.workspace_id,
                item.brand_id,
                item.channel_id,
                item.author_id,
                item.platform,
                item.platform_content_id,
                item.source_type,
                item.source_url,
                item.title,
                item.body_text,
                item.published_at,
                item.collected_at,
                item.content_hash,
                self._jsonb(item.tags),
                self._jsonb(item.topic_ids),
                self._jsonb(item.raw_payload),
                self._jsonb(item.metadata),
            ),
        )
        return self._content_item_from_row(row)

    def get_content_by_platform_content_id(
        self,
        *,
        workspace_id: str,
        platform: str,
        platform_content_id: str,
    ) -> ContentItemRecord | None:
        row = self._fetchone(
            """
            SELECT id, workspace_id, brand_id, channel_id, author_id, platform, platform_content_id,
                   source_type, source_url, title, body_text, published_at, collected_at, content_hash,
                   tags, topic_ids, raw_payload, metadata
            FROM content_items
            WHERE workspace_id = %s AND platform = %s AND platform_content_id = %s
            """,
            (workspace_id, platform, platform_content_id),
        )
        return self._content_item_from_row(row) if row else None

    def get_content_by_source_url(
        self,
        *,
        workspace_id: str,
        platform: str,
        normalized_source_url: str,
    ) -> ContentItemRecord | None:
        row = self._fetchone(
            """
            SELECT id, workspace_id, brand_id, channel_id, author_id, platform, platform_content_id,
                   source_type, source_url, title, body_text, published_at, collected_at, content_hash,
                   tags, topic_ids, raw_payload, metadata
            FROM content_items
            WHERE workspace_id = %s
              AND platform = %s
              AND metadata->>'normalized_source_url' = %s
            LIMIT 1
            """,
            (workspace_id, platform, normalized_source_url),
        )
        return self._content_item_from_row(row) if row else None

    def get_content_by_content_hash(
        self,
        *,
        workspace_id: str,
        content_hash: str,
    ) -> ContentItemRecord | None:
        row = self._fetchone(
            """
            SELECT id, workspace_id, brand_id, channel_id, author_id, platform, platform_content_id,
                   source_type, source_url, title, body_text, published_at, collected_at, content_hash,
                   tags, topic_ids, raw_payload, metadata
            FROM content_items
            WHERE workspace_id = %s AND content_hash = %s
            LIMIT 1
            """,
            (workspace_id, content_hash),
        )
        return self._content_item_from_row(row) if row else None

    def list_content_items(self, brand_id: str) -> list[ContentItemRecord]:
        rows = self._fetchall(
            """
            SELECT id, workspace_id, brand_id, channel_id, author_id, platform, platform_content_id,
                   source_type, source_url, title, body_text, published_at, collected_at, content_hash,
                   tags, topic_ids, raw_payload, metadata
            FROM content_items
            WHERE brand_id = %s
            ORDER BY collected_at ASC
            """,
            (brand_id,),
        )
        return [self._content_item_from_row(row) for row in rows]

    def save_metrics_snapshot(self, snapshot: ContentMetricsSnapshotRecord) -> ContentMetricsSnapshotRecord:
        existing = self._fetchone(
            """
            SELECT id, workspace_id, content_item_id, snapshot_at, likes, comments, collects, shares, views,
                   follows_gained, reward_components, created_at
            FROM content_metrics_snapshots
            WHERE content_item_id = %s AND snapshot_at = %s
            LIMIT 1
            """,
            (snapshot.content_item_id, snapshot.snapshot_at),
        )
        snapshot_id = existing["id"] if existing else snapshot.id
        created_at = existing["created_at"] if existing else snapshot.created_at
        row = self._fetchone(
            """
            INSERT INTO content_metrics_snapshots (
                id, workspace_id, content_item_id, snapshot_at, likes, comments, collects, shares, views,
                follows_gained, reward_components, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                likes = EXCLUDED.likes,
                comments = EXCLUDED.comments,
                collects = EXCLUDED.collects,
                shares = EXCLUDED.shares,
                views = EXCLUDED.views,
                follows_gained = EXCLUDED.follows_gained,
                reward_components = EXCLUDED.reward_components
            RETURNING id, workspace_id, content_item_id, snapshot_at, likes, comments, collects, shares, views,
                      follows_gained, reward_components, created_at
            """,
            (
                snapshot_id,
                snapshot.workspace_id,
                snapshot.content_item_id,
                snapshot.snapshot_at,
                snapshot.likes,
                snapshot.comments,
                snapshot.collects,
                snapshot.shares,
                snapshot.views,
                snapshot.follows_gained,
                self._jsonb(snapshot.reward_components),
                created_at,
            ),
        )
        return self._metrics_from_row(row)

    def list_metrics_snapshots(self, content_item_id: str) -> list[ContentMetricsSnapshotRecord]:
        rows = self._fetchall(
            """
            SELECT id, workspace_id, content_item_id, snapshot_at, likes, comments, collects, shares, views,
                   follows_gained, reward_components, created_at
            FROM content_metrics_snapshots
            WHERE content_item_id = %s
            ORDER BY snapshot_at ASC
            """,
            (content_item_id,),
        )
        return [self._metrics_from_row(row) for row in rows]

    def save_comment(self, comment: CommentRecord) -> CommentRecord:
        row = self._fetchone(
            """
            INSERT INTO comments (
                id, workspace_id, content_item_id, platform_comment_id, author_name, body_text,
                commented_at, sentiment_label, metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (workspace_id, content_item_id, platform_comment_id) DO UPDATE SET
                author_name = EXCLUDED.author_name,
                body_text = EXCLUDED.body_text,
                commented_at = EXCLUDED.commented_at,
                sentiment_label = EXCLUDED.sentiment_label,
                metadata = EXCLUDED.metadata
            RETURNING id, workspace_id, content_item_id, platform_comment_id, author_name, body_text,
                      commented_at, sentiment_label, metadata
            """,
            (
                comment.id,
                comment.workspace_id,
                comment.content_item_id,
                comment.platform_comment_id,
                comment.author_name,
                comment.body_text,
                comment.commented_at,
                comment.sentiment_label,
                self._jsonb(comment.metadata),
            ),
        )
        return self._comment_from_row(row)

    def _fetchone(self, query: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        with self._connector(self._dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, params)
                row = cursor.fetchone()
            commit = getattr(connection, "commit", None)
            if callable(commit):
                commit()
        return row

    def _fetchall(self, query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        with self._connector(self._dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, params)
                rows = cursor.fetchall() or []
        return list(rows)

    def _jsonb(self, value: Any) -> Any:
        _connect, _dict_row, jsonb_cls = _load_psycopg_jsonb()
        return jsonb_cls(value)

    @staticmethod
    def _run_from_row(row: dict[str, Any]) -> IngestionRunRecord:
        return IngestionRunRecord(**row)

    @staticmethod
    def _author_from_row(row: dict[str, Any]) -> AuthorRecord:
        return AuthorRecord(**row)

    @staticmethod
    def _topic_from_row(row: dict[str, Any]) -> TopicRecord:
        return TopicRecord(**row)

    @staticmethod
    def _content_item_from_row(row: dict[str, Any]) -> ContentItemRecord:
        return ContentItemRecord(**row)

    @staticmethod
    def _metrics_from_row(row: dict[str, Any]) -> ContentMetricsSnapshotRecord:
        return ContentMetricsSnapshotRecord(**row)

    @staticmethod
    def _extension_capture_session_from_row(row: dict[str, Any]) -> ExtensionCaptureSessionRecord:
        return ExtensionCaptureSessionRecord(**row)

    @staticmethod
    def _data_import_preview_from_row(row: dict[str, Any]) -> DataImportPreviewRecord:
        return DataImportPreviewRecord(**row)

    @staticmethod
    def _comment_from_row(row: dict[str, Any]) -> CommentRecord:
        return CommentRecord(**row)
