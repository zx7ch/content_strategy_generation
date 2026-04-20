"""Store protocol and in-memory implementation for V2 ingestion evidence."""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol

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


class IngestionStore(Protocol):
    def save_ingestion_run(self, run: IngestionRunRecord) -> IngestionRunRecord: ...

    def list_ingestion_runs(self, brand_id: str, limit: int = 10) -> list[IngestionRunRecord]: ...

    def save_extension_capture_session(
        self,
        session: ExtensionCaptureSessionRecord,
    ) -> ExtensionCaptureSessionRecord: ...

    def get_extension_capture_session(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        capture_session_id: str,
    ) -> ExtensionCaptureSessionRecord | None: ...

    def find_extension_capture_session(
        self,
        capture_session_id: str,
    ) -> ExtensionCaptureSessionRecord | None: ...

    def list_extension_capture_sessions(
        self,
        *,
        brand_id: str,
        limit: int = 10,
    ) -> list[ExtensionCaptureSessionRecord]: ...

    def save_data_import_preview(self, preview: DataImportPreviewRecord) -> DataImportPreviewRecord: ...

    def get_data_import_preview(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        preview_id: str,
    ) -> DataImportPreviewRecord | None: ...

    def list_data_import_previews(
        self,
        *,
        brand_id: str,
        limit: int = 10,
    ) -> list[DataImportPreviewRecord]: ...

    def save_author(self, author: AuthorRecord) -> AuthorRecord: ...

    def get_author_by_platform_identity(
        self,
        *,
        workspace_id: str,
        platform: str,
        platform_author_id: str,
    ) -> AuthorRecord | None: ...

    def save_topic(self, topic: TopicRecord) -> TopicRecord: ...

    def get_topic_by_normalized_name(
        self,
        *,
        brand_id: str,
        normalized_name: str,
    ) -> TopicRecord | None: ...

    def list_topics(self, brand_id: str) -> list[TopicRecord]: ...

    def save_content_item(self, item: ContentItemRecord) -> ContentItemRecord: ...

    def get_content_by_platform_content_id(
        self,
        *,
        workspace_id: str,
        platform: str,
        platform_content_id: str,
    ) -> ContentItemRecord | None: ...

    def get_content_by_source_url(
        self,
        *,
        workspace_id: str,
        platform: str,
        normalized_source_url: str,
    ) -> ContentItemRecord | None: ...

    def get_content_by_content_hash(
        self,
        *,
        workspace_id: str,
        content_hash: str,
    ) -> ContentItemRecord | None: ...

    def list_content_items(self, brand_id: str) -> list[ContentItemRecord]: ...

    def save_metrics_snapshot(self, snapshot: ContentMetricsSnapshotRecord) -> ContentMetricsSnapshotRecord: ...

    def list_metrics_snapshots(self, content_item_id: str) -> list[ContentMetricsSnapshotRecord]: ...

    def save_comment(self, comment: CommentRecord) -> CommentRecord: ...


class InMemoryIngestionStore:
    def __init__(self) -> None:
        self._runs: dict[str, IngestionRunRecord] = {}
        self._capture_sessions: dict[str, ExtensionCaptureSessionRecord] = {}
        self._import_previews: dict[str, DataImportPreviewRecord] = {}
        self._authors: dict[str, AuthorRecord] = {}
        self._author_identity_index: dict[tuple[str, str, str], str] = {}
        self._topics: dict[str, TopicRecord] = {}
        self._content_items: dict[str, ContentItemRecord] = {}
        self._content_platform_index: dict[tuple[str, str, str], str] = {}
        self._content_url_index: dict[tuple[str, str, str], str] = {}
        self._content_hash_index: dict[tuple[str, str], str] = {}
        self._metrics: dict[str, ContentMetricsSnapshotRecord] = {}
        self._metrics_by_item_and_time: dict[tuple[str, str], str] = {}
        self._comments: dict[str, CommentRecord] = {}
        self._comments_by_identity: dict[tuple[str, str, str], str] = {}

    def save_ingestion_run(self, run: IngestionRunRecord) -> IngestionRunRecord:
        self._runs[run.id] = run
        return run

    def list_ingestion_runs(self, brand_id: str, limit: int = 10) -> list[IngestionRunRecord]:
        runs = [run for run in self._runs.values() if run.brand_id == brand_id]
        runs.sort(key=lambda item: item.created_at, reverse=True)
        return runs[:limit]

    def save_extension_capture_session(
        self,
        session: ExtensionCaptureSessionRecord,
    ) -> ExtensionCaptureSessionRecord:
        self._capture_sessions[session.capture_session_id] = session
        return session

    def get_extension_capture_session(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        capture_session_id: str,
    ) -> ExtensionCaptureSessionRecord | None:
        session = self._capture_sessions.get(capture_session_id)
        if session is None or session.workspace_id != workspace_id or session.brand_id != brand_id:
            return None
        return session

    def find_extension_capture_session(
        self,
        capture_session_id: str,
    ) -> ExtensionCaptureSessionRecord | None:
        return self._capture_sessions.get(capture_session_id)

    def list_extension_capture_sessions(
        self,
        *,
        brand_id: str,
        limit: int = 10,
    ) -> list[ExtensionCaptureSessionRecord]:
        sessions = [session for session in self._capture_sessions.values() if session.brand_id == brand_id]
        sessions.sort(key=lambda item: item.created_at, reverse=True)
        return sessions[:limit]

    def save_data_import_preview(self, preview: DataImportPreviewRecord) -> DataImportPreviewRecord:
        self._import_previews[preview.preview_id] = preview
        return preview

    def get_data_import_preview(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        preview_id: str,
    ) -> DataImportPreviewRecord | None:
        preview = self._import_previews.get(preview_id)
        if preview is None or preview.workspace_id != workspace_id or preview.brand_id != brand_id:
            return None
        return preview

    def list_data_import_previews(
        self,
        *,
        brand_id: str,
        limit: int = 10,
    ) -> list[DataImportPreviewRecord]:
        previews = [preview for preview in self._import_previews.values() if preview.brand_id == brand_id]
        previews.sort(key=lambda item: item.uploaded_at, reverse=True)
        return previews[:limit]

    def save_author(self, author: AuthorRecord) -> AuthorRecord:
        identity = (author.workspace_id, author.platform, author.platform_author_id)
        existing_id = self._author_identity_index.get(identity)
        if existing_id is not None and existing_id != author.id:
            existing = self._authors[existing_id]
            merged = replace(
                author,
                id=existing.id,
                first_seen_at=existing.first_seen_at,
            )
            self._authors[existing.id] = merged
            return merged
        self._authors[author.id] = author
        self._author_identity_index[identity] = author.id
        return author

    def get_author_by_platform_identity(
        self,
        *,
        workspace_id: str,
        platform: str,
        platform_author_id: str,
    ) -> AuthorRecord | None:
        author_id = self._author_identity_index.get((workspace_id, platform, platform_author_id))
        if author_id is None:
            return None
        return self._authors.get(author_id)

    def save_topic(self, topic: TopicRecord) -> TopicRecord:
        for existing in self._topics.values():
            if existing.brand_id == topic.brand_id and existing.normalized_name == topic.normalized_name:
                topic = replace(
                    topic,
                    id=existing.id,
                    created_at=existing.created_at,
                )
                break
        self._topics[topic.id] = topic
        return topic

    def get_topic_by_normalized_name(
        self,
        *,
        brand_id: str,
        normalized_name: str,
    ) -> TopicRecord | None:
        for topic in self._topics.values():
            if topic.brand_id == brand_id and topic.normalized_name == normalized_name:
                return topic
        return None

    def list_topics(self, brand_id: str) -> list[TopicRecord]:
        topics = [topic for topic in self._topics.values() if topic.brand_id == brand_id]
        topics.sort(key=lambda item: item.created_at)
        return topics

    def save_content_item(self, item: ContentItemRecord) -> ContentItemRecord:
        self._content_items[item.id] = item
        self._content_platform_index[(item.workspace_id, item.platform, item.platform_content_id)] = item.id
        normalized_source_url = item.metadata.get("normalized_source_url")
        if isinstance(normalized_source_url, str) and normalized_source_url:
            self._content_url_index[(item.workspace_id, item.platform, normalized_source_url)] = item.id
        if item.content_hash:
            self._content_hash_index[(item.workspace_id, item.content_hash)] = item.id
        return item

    def get_content_by_platform_content_id(
        self,
        *,
        workspace_id: str,
        platform: str,
        platform_content_id: str,
    ) -> ContentItemRecord | None:
        content_id = self._content_platform_index.get((workspace_id, platform, platform_content_id))
        if content_id is None:
            return None
        return self._content_items.get(content_id)

    def get_content_by_source_url(
        self,
        *,
        workspace_id: str,
        platform: str,
        normalized_source_url: str,
    ) -> ContentItemRecord | None:
        content_id = self._content_url_index.get((workspace_id, platform, normalized_source_url))
        if content_id is None:
            return None
        return self._content_items.get(content_id)

    def get_content_by_content_hash(
        self,
        *,
        workspace_id: str,
        content_hash: str,
    ) -> ContentItemRecord | None:
        content_id = self._content_hash_index.get((workspace_id, content_hash))
        if content_id is None:
            return None
        return self._content_items.get(content_id)

    def list_content_items(self, brand_id: str) -> list[ContentItemRecord]:
        items = [item for item in self._content_items.values() if item.brand_id == brand_id]
        items.sort(key=lambda item: item.collected_at)
        return items

    def save_metrics_snapshot(self, snapshot: ContentMetricsSnapshotRecord) -> ContentMetricsSnapshotRecord:
        key = (snapshot.content_item_id, snapshot.snapshot_at.isoformat())
        existing_id = self._metrics_by_item_and_time.get(key)
        if existing_id is not None and existing_id != snapshot.id:
            snapshot = replace(snapshot, id=existing_id, created_at=self._metrics[existing_id].created_at)
        self._metrics[snapshot.id] = snapshot
        self._metrics_by_item_and_time[key] = snapshot.id
        return snapshot

    def list_metrics_snapshots(self, content_item_id: str) -> list[ContentMetricsSnapshotRecord]:
        rows = [item for item in self._metrics.values() if item.content_item_id == content_item_id]
        rows.sort(key=lambda item: item.snapshot_at)
        return rows

    def save_comment(self, comment: CommentRecord) -> CommentRecord:
        identity = (comment.workspace_id, comment.content_item_id, comment.platform_comment_id)
        existing_id = self._comments_by_identity.get(identity)
        if existing_id is not None and existing_id != comment.id:
            comment = replace(comment, id=existing_id)
        self._comments[comment.id] = comment
        self._comments_by_identity[identity] = comment.id
        return comment
