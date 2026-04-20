"""Typed ingestion records for V2 P1-2 evidence ingestion."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.v2.foundation.models import utcnow


@dataclass(frozen=True)
class IngestionRunRecord:
    id: str
    workspace_id: str
    brand_id: str
    entry_type: str
    source_type: str
    source_adapter: str | None = None
    dedupe_key: str | None = None
    source_config: dict[str, Any] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)
    error_summary: dict[str, Any] = field(default_factory=dict)
    status: str = "accepted"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class AuthorRecord:
    id: str
    workspace_id: str
    platform: str
    platform_author_id: str
    display_name: str | None = None
    profile_url: str | None = None
    follower_count: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    first_seen_at: datetime = field(default_factory=utcnow)
    last_seen_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class TopicRecord:
    id: str
    workspace_id: str
    brand_id: str
    normalized_name: str
    display_name: str
    topic_type: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class ContentItemRecord:
    id: str
    workspace_id: str
    brand_id: str | None
    channel_id: str | None
    author_id: str | None
    platform: str
    platform_content_id: str
    source_type: str
    source_url: str | None = None
    title: str | None = None
    body_text: str | None = None
    published_at: datetime | None = None
    collected_at: datetime = field(default_factory=utcnow)
    content_hash: str | None = None
    tags: list[str] = field(default_factory=list)
    topic_ids: list[str] = field(default_factory=list)
    raw_payload: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ContentMetricsSnapshotRecord:
    id: str
    workspace_id: str
    content_item_id: str
    snapshot_at: datetime
    likes: int = 0
    comments: int = 0
    collects: int = 0
    shares: int = 0
    views: int | None = None
    follows_gained: int | None = None
    reward_components: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class CommentRecord:
    id: str
    workspace_id: str
    content_item_id: str
    platform_comment_id: str
    author_name: str | None = None
    body_text: str = ""
    commented_at: datetime | None = None
    sentiment_label: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IngestionAcceptedResult:
    ingestion_run_id: str
    entry_type: str
    status: str
    accepted_row_count: int | None = None
    imported_item_count: int | None = None
    deduped_item_count: int | None = None


@dataclass(frozen=True)
class ExtensionCaptureSessionRecord:
    capture_session_id: str
    workspace_id: str
    brand_id: str
    channel_id: str | None
    capture_token: str
    status: str
    expires_at: datetime
    created_at: datetime = field(default_factory=utcnow)
    captured_at: datetime | None = None
    preview_payload: dict[str, Any] | None = None
    ingestion_receipt: dict[str, Any] | None = None
    error_summary: dict[str, Any] | None = None


@dataclass(frozen=True)
class DataImportPreviewRecord:
    preview_id: str
    workspace_id: str
    brand_id: str
    file_name: str
    status: str
    uploaded_at: datetime
    parsed_row_count: int
    preview_payload: dict[str, Any] | None = None
    ingestion_receipt: dict[str, Any] | None = None
    field_errors: list[dict[str, Any]] = field(default_factory=list)
    error_summary: dict[str, Any] | None = None
