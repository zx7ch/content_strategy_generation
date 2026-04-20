"""Normalization and ingestion service for V2 P1-2 evidence entry points."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import uuid
import zipfile
from dataclasses import dataclass
from dataclasses import replace as dc_replace
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from xml.etree import ElementTree

from app.v2.foundation.models import utcnow
from app.v2.ingestion.models import (
    AuthorRecord,
    CommentRecord,
    ContentItemRecord,
    ContentMetricsSnapshotRecord,
    DataImportPreviewRecord,
    ExtensionCaptureSessionRecord,
    IngestionAcceptedResult,
    IngestionRunRecord,
)
from app.v2.ingestion.store import IngestionStore


class IngestionError(ValueError):
    """Raised when ingestion input or state violates the V2 ingestion contract."""


class IngestionValidationError(IngestionError):
    """Raised when a source-sync or import payload is invalid."""


@dataclass(frozen=True)
class _NormalizedCommentInput:
    platform_comment_id: str
    author_name: str | None
    body_text: str
    commented_at: datetime | None
    sentiment_label: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class _NormalizedContentInput:
    platform: str
    platform_content_id: str
    source_type: str
    source_url: str | None
    normalized_source_url: str | None
    title: str | None
    body_text: str | None
    published_at: datetime | None
    collected_at: datetime
    content_hash: str
    tags: list[str]
    raw_payload: dict[str, Any]
    metadata: dict[str, Any]
    likes: int
    comments: int
    collects: int
    shares: int
    views: int | None
    follows_gained: int | None
    reward_components: dict[str, Any]
    author_platform_id: str | None
    author_display_name: str | None
    author_profile_url: str | None
    author_metadata: dict[str, Any]
    comments_payload: list[_NormalizedCommentInput]


class IngestionService:
    def __init__(self, store: IngestionStore) -> None:
        self._store = store

    def create_source_sync(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        source_type: str,
        source_adapter: str | None,
        channel_id: str | None,
        capture_payload: dict[str, Any],
    ) -> IngestionAcceptedResult:
        if not source_type:
            raise IngestionValidationError("source_type is required")
        items = capture_payload.get("items")
        if not isinstance(items, list):
            raise IngestionValidationError("capture_payload.items must be a list")

        run = self._start_run(
            workspace_id=workspace_id,
            brand_id=brand_id,
            entry_type="source_sync",
            source_type=source_type,
            source_adapter=source_adapter,
            source_config={"capture_payload": {"page_type": capture_payload.get("page_type"), "captured_at": capture_payload.get("captured_at")}},
        )
        imported = 0
        deduped = 0
        try:
            normalized_items = [
                self._normalize_source_sync_item(
                    item,
                    source_type=source_type,
                    captured_at=capture_payload.get("captured_at"),
                )
                for item in items
            ]
            imported, deduped = self._ingest_items(
                workspace_id=workspace_id,
                brand_id=brand_id,
                channel_id=channel_id,
                run_id=run.id,
                normalized_items=normalized_items,
            )
            self._complete_run(
                run,
                stats={
                    "accepted_item_count": len(normalized_items),
                    "imported_item_count": imported,
                    "deduped_item_count": deduped,
                },
            )
        except Exception as exc:
            self._fail_run(run, exc)
            raise

        return IngestionAcceptedResult(
            ingestion_run_id=run.id,
            entry_type="source_sync",
            status="accepted",
            imported_item_count=imported,
            deduped_item_count=deduped,
        )

    def create_data_import(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        import_type: str,
        platform: str,
        rows: list[dict[str, Any]],
    ) -> IngestionAcceptedResult:
        if import_type != "historical_note_import_v1":
            raise IngestionValidationError("import_type must be historical_note_import_v1")
        if not platform:
            raise IngestionValidationError("platform is required")
        if not isinstance(rows, list) or not rows:
            raise IngestionValidationError("rows must be a non-empty list")

        run = self._start_run(
            workspace_id=workspace_id,
            brand_id=brand_id,
            entry_type="data_import",
            source_type="manual_import",
            source_adapter=import_type,
            source_config={"platform": platform, "import_type": import_type},
        )
        imported = 0
        deduped = 0
        try:
            normalized_items = [self._normalize_import_row(row, platform=platform) for row in rows]
            imported, deduped = self._ingest_items(
                workspace_id=workspace_id,
                brand_id=brand_id,
                channel_id=None,
                run_id=run.id,
                normalized_items=normalized_items,
            )
            self._complete_run(
                run,
                stats={
                    "accepted_row_count": len(normalized_items),
                    "imported_item_count": imported,
                    "deduped_item_count": deduped,
                },
            )
        except Exception as exc:
            self._fail_run(run, exc)
            raise

        return IngestionAcceptedResult(
            ingestion_run_id=run.id,
            entry_type="data_import",
            status="accepted",
            accepted_row_count=len(rows),
            imported_item_count=imported,
            deduped_item_count=deduped,
        )

    def list_ingestion_runs(self, *, brand_id: str, limit: int = 10) -> list[IngestionRunRecord]:
        return self._store.list_ingestion_runs(brand_id, limit=limit)

    def list_content_items(self, *, brand_id: str) -> list[ContentItemRecord]:
        return self._store.list_content_items(brand_id)

    def create_extension_capture_session(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        channel_id: str | None,
    ) -> ExtensionCaptureSessionRecord:
        now = utcnow()
        record = ExtensionCaptureSessionRecord(
            capture_session_id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            brand_id=brand_id,
            channel_id=channel_id,
            capture_token=str(uuid.uuid4()),
            status="pending_capture",
            expires_at=now + timedelta(minutes=15),
            created_at=now,
        )
        return self._store.save_extension_capture_session(record)

    def get_extension_capture_session(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        capture_session_id: str,
    ) -> ExtensionCaptureSessionRecord:
        record = self._store.get_extension_capture_session(
            workspace_id=workspace_id,
            brand_id=brand_id,
            capture_session_id=capture_session_id,
        )
        if record is None:
            raise IngestionValidationError("capture session not found")
        if record.status == "pending_capture" and record.expires_at < utcnow():
            record = dc_replace(record, status="expired")
            record = self._store.save_extension_capture_session(record)
        return record

    def submit_extension_capture(
        self,
        *,
        capture_session_id: str,
        capture_token: str,
        capture_payload: dict[str, Any],
    ) -> ExtensionCaptureSessionRecord:
        record = self._store.find_extension_capture_session(capture_session_id)
        if record is None:
            raise IngestionValidationError("capture session not found")
        if record.capture_token != capture_token:
            raise IngestionValidationError("capture token is invalid")
        if record.expires_at < utcnow():
            expired = dc_replace(record, status="expired")
            self._store.save_extension_capture_session(expired)
            raise IngestionValidationError("capture session expired")

        preview_payload = {
            "source_type": "xhs_extension_capture",
            "source_adapter": "extension_source_sync_adapter_v1",
            "channel_id": record.channel_id,
            "capture_payload": capture_payload,
        }
        syncing = dc_replace(
            record,
            status="syncing",
            captured_at=utcnow(),
            preview_payload=preview_payload,
            error_summary=None,
        )
        syncing = self._store.save_extension_capture_session(syncing)
        try:
            result = self.create_source_sync(
                workspace_id=record.workspace_id,
                brand_id=record.brand_id,
                source_type="xhs_extension_capture",
                source_adapter="extension_source_sync_adapter_v1",
                channel_id=record.channel_id,
                capture_payload=capture_payload,
            )
            completed = dc_replace(
                syncing,
                status="accepted",
                ingestion_receipt={
                    "ingestion_run_id": result.ingestion_run_id,
                    "entry_type": result.entry_type,
                    "status": result.status,
                    "accepted_row_count": result.accepted_row_count,
                    "imported_item_count": result.imported_item_count,
                    "deduped_item_count": result.deduped_item_count,
                },
            )
            return self._store.save_extension_capture_session(completed)
        except Exception as exc:
            failed = dc_replace(
                syncing,
                status="failed",
                error_summary={"type": exc.__class__.__name__, "message": str(exc)},
            )
            self._store.save_extension_capture_session(failed)
            raise

    def retry_extension_capture_sync(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        capture_session_id: str,
    ) -> ExtensionCaptureSessionRecord:
        record = self.get_extension_capture_session(
            workspace_id=workspace_id,
            brand_id=brand_id,
            capture_session_id=capture_session_id,
        )
        preview_payload = record.preview_payload or {}
        capture_payload = preview_payload.get("capture_payload")
        if not isinstance(capture_payload, dict):
            raise IngestionValidationError("capture session has no preview payload to retry")
        return self.submit_extension_capture(
            capture_session_id=capture_session_id,
            capture_token=record.capture_token,
            capture_payload=capture_payload,
        )

    def create_data_import_preview(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        file_name: str,
        import_type: str,
        platform: str,
        rows: list[dict[str, Any]],
    ) -> DataImportPreviewRecord:
        now = utcnow()
        preview_payload = {
            "import_type": import_type,
            "platform": platform,
            "rows": rows,
        }
        record = DataImportPreviewRecord(
            preview_id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            brand_id=brand_id,
            file_name=file_name or "historical-import.json",
            status="parsed",
            uploaded_at=now,
            parsed_row_count=len(rows),
            preview_payload=preview_payload,
        )
        record = self._store.save_data_import_preview(record)
        try:
            result = self.create_data_import(
                workspace_id=workspace_id,
                brand_id=brand_id,
                import_type=import_type,
                platform=platform,
                rows=rows,
            )
            completed = dc_replace(
                record,
                status="accepted",
                ingestion_receipt={
                    "ingestion_run_id": result.ingestion_run_id,
                    "entry_type": result.entry_type,
                    "status": result.status,
                    "accepted_row_count": result.accepted_row_count,
                    "imported_item_count": result.imported_item_count,
                    "deduped_item_count": result.deduped_item_count,
                },
            )
            return self._store.save_data_import_preview(completed)
        except Exception as exc:
            failed = dc_replace(
                record,
                status="failed",
                field_errors=[{"message": str(exc)}] if isinstance(exc, IngestionValidationError) else [],
                error_summary={"type": exc.__class__.__name__, "message": str(exc)},
            )
            self._store.save_data_import_preview(failed)
            raise

    def parse_uploaded_import_file(
        self,
        *,
        file_name: str,
        file_bytes: bytes,
    ) -> list[dict[str, Any]]:
        if not file_bytes:
            raise IngestionValidationError("uploaded file is empty")

        normalized_name = (file_name or "").strip().lower()
        if normalized_name.endswith(".json"):
            return self._parse_json_rows(file_bytes)
        if normalized_name.endswith(".csv"):
            return self._parse_delimited_rows(file_bytes, delimiter=",")
        if normalized_name.endswith(".tsv"):
            return self._parse_delimited_rows(file_bytes, delimiter="\t")
        if normalized_name.endswith(".xlsx"):
            return self._parse_xlsx_rows(file_bytes)
        raise IngestionValidationError("unsupported import file type; expected .json, .csv, .tsv, or .xlsx")

    def get_data_import_preview(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        preview_id: str,
    ) -> DataImportPreviewRecord:
        record = self._store.get_data_import_preview(
            workspace_id=workspace_id,
            brand_id=brand_id,
            preview_id=preview_id,
        )
        if record is None:
            raise IngestionValidationError("data import preview not found")
        return record

    def list_extension_capture_sessions(
        self,
        *,
        brand_id: str,
        limit: int = 10,
    ) -> list[ExtensionCaptureSessionRecord]:
        return self._store.list_extension_capture_sessions(brand_id=brand_id, limit=limit)

    def list_data_import_previews(
        self,
        *,
        brand_id: str,
        limit: int = 10,
    ) -> list[DataImportPreviewRecord]:
        return self._store.list_data_import_previews(brand_id=brand_id, limit=limit)

    def retry_data_import_sync(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        preview_id: str,
    ) -> DataImportPreviewRecord:
        record = self.get_data_import_preview(workspace_id=workspace_id, brand_id=brand_id, preview_id=preview_id)
        preview_payload = record.preview_payload or {}
        rows = preview_payload.get("rows")
        if not isinstance(rows, list):
            raise IngestionValidationError("data import preview has no payload to retry")
        return self.create_data_import_preview(
            workspace_id=workspace_id,
            brand_id=brand_id,
            file_name=record.file_name,
            import_type=str(preview_payload.get("import_type") or "historical_note_import_v1"),
            platform=str(preview_payload.get("platform") or "xiaohongshu"),
            rows=rows,
        )

    def _start_run(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        entry_type: str,
        source_type: str,
        source_adapter: str | None,
        source_config: dict[str, Any],
    ) -> IngestionRunRecord:
        now = utcnow()
        run = IngestionRunRecord(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            brand_id=brand_id,
            entry_type=entry_type,
            source_type=source_type,
            source_adapter=source_adapter,
            source_config=source_config,
            status="running",
            started_at=now,
            created_at=now,
        )
        self._store.save_ingestion_run(run)
        return run

    def _complete_run(self, run: IngestionRunRecord, *, stats: dict[str, Any]) -> None:
        self._store.save_ingestion_run(
            dc_replace(
                run,
                status="completed",
                stats=stats,
                finished_at=utcnow(),
            )
        )

    def _fail_run(self, run: IngestionRunRecord, exc: Exception) -> None:
        self._store.save_ingestion_run(
            dc_replace(
                run,
                status="failed",
                error_summary={"message": str(exc), "type": exc.__class__.__name__},
                finished_at=utcnow(),
            )
        )

    def _ingest_items(
        self,
        *,
        workspace_id: str,
        brand_id: str,
        channel_id: str | None,
        run_id: str,
        normalized_items: list[_NormalizedContentInput],
    ) -> tuple[int, int]:
        imported_count = 0
        deduped_count = 0

        for item in normalized_items:
            author_id = None
            if item.author_platform_id:
                existing_author = self._store.get_author_by_platform_identity(
                    workspace_id=workspace_id,
                    platform=item.platform,
                    platform_author_id=item.author_platform_id,
                )
                author = AuthorRecord(
                    id=existing_author.id if existing_author else str(uuid.uuid4()),
                    workspace_id=workspace_id,
                    platform=item.platform,
                    platform_author_id=item.author_platform_id,
                    display_name=item.author_display_name,
                    profile_url=item.author_profile_url,
                    metadata=item.author_metadata,
                    first_seen_at=existing_author.first_seen_at if existing_author else item.collected_at,
                    last_seen_at=item.collected_at,
                )
                author_id = self._store.save_author(author).id

            existing = self._store.get_content_by_platform_content_id(
                workspace_id=workspace_id,
                platform=item.platform,
                platform_content_id=item.platform_content_id,
            )
            if existing is None and item.normalized_source_url:
                existing = self._store.get_content_by_source_url(
                    workspace_id=workspace_id,
                    platform=item.platform,
                    normalized_source_url=item.normalized_source_url,
                )
            if existing is None and item.content_hash:
                existing = self._store.get_content_by_content_hash(
                    workspace_id=workspace_id,
                    content_hash=item.content_hash,
                )

            if existing is None:
                content = ContentItemRecord(
                    id=str(uuid.uuid4()),
                    workspace_id=workspace_id,
                    brand_id=brand_id,
                    channel_id=channel_id,
                    author_id=author_id,
                    platform=item.platform,
                    platform_content_id=item.platform_content_id,
                    source_type=item.source_type,
                    source_url=item.source_url,
                    title=item.title,
                    body_text=item.body_text,
                    published_at=item.published_at,
                    collected_at=item.collected_at,
                    content_hash=item.content_hash,
                    tags=item.tags,
                    raw_payload=item.raw_payload,
                    metadata={
                        **item.metadata,
                        "ingestion_run_id": run_id,
                        "normalized_source_url": item.normalized_source_url,
                    },
                )
                imported_count += 1
            else:
                deduped_count += 1
                content = dc_replace(
                    existing,
                    brand_id=brand_id,
                    channel_id=channel_id or existing.channel_id,
                    author_id=author_id or existing.author_id,
                    source_type=item.source_type,
                    source_url=item.source_url or existing.source_url,
                    title=item.title or existing.title,
                    body_text=item.body_text or existing.body_text,
                    published_at=item.published_at or existing.published_at,
                    collected_at=item.collected_at,
                    content_hash=item.content_hash or existing.content_hash,
                    tags=item.tags or existing.tags,
                    raw_payload=item.raw_payload or existing.raw_payload,
                    metadata={
                        **existing.metadata,
                        **item.metadata,
                        "ingestion_run_id": run_id,
                        "normalized_source_url": item.normalized_source_url or existing.metadata.get("normalized_source_url"),
                    },
                )

            content = self._store.save_content_item(content)

            snapshot_at = item.published_at or item.collected_at
            metrics = ContentMetricsSnapshotRecord(
                id=str(uuid.uuid4()),
                workspace_id=workspace_id,
                content_item_id=content.id,
                snapshot_at=snapshot_at,
                likes=item.likes,
                comments=item.comments,
                collects=item.collects,
                shares=item.shares,
                views=item.views,
                follows_gained=item.follows_gained,
                reward_components=item.reward_components,
            )
            self._store.save_metrics_snapshot(metrics)

            for raw_comment in item.comments_payload:
                comment = CommentRecord(
                    id=str(uuid.uuid4()),
                    workspace_id=workspace_id,
                    content_item_id=content.id,
                    platform_comment_id=raw_comment.platform_comment_id,
                    author_name=raw_comment.author_name,
                    body_text=raw_comment.body_text,
                    commented_at=raw_comment.commented_at,
                    sentiment_label=raw_comment.sentiment_label,
                    metadata=raw_comment.metadata,
                )
                self._store.save_comment(comment)

        return imported_count, deduped_count

    def _normalize_source_sync_item(
        self,
        item: dict[str, Any],
        *,
        source_type: str,
        captured_at: Any,
    ) -> _NormalizedContentInput:
        if not isinstance(item, dict):
            raise IngestionValidationError("capture_payload.items must contain objects")
        platform = "xiaohongshu"
        source_url = self._optional_str(item.get("source_url"))
        normalized_source_url = self._normalize_source_url(source_url)
        title = self._optional_str(item.get("title"))
        body_text = self._optional_str(item.get("body_text")) or self._optional_str(item.get("visible_text_excerpt")) or self._optional_str(item.get("excerpt"))
        published_at = self._parse_datetime(item.get("published_at"))
        collected_at = self._parse_datetime(captured_at) or utcnow()
        author_platform_id = self._optional_str(item.get("author_id")) or self._optional_str(item.get("author_handle")) or self._optional_str(item.get("author"))
        content_hash = self._build_content_hash(
            title=title,
            body_text=body_text,
            published_at=published_at,
            author_handle=author_platform_id,
        )
        platform_content_id = (
            self._optional_str(item.get("platform_content_id"))
            or self._optional_str(item.get("note_id"))
            or self._derive_platform_content_id(normalized_source_url, content_hash)
        )
        comments_payload = self._normalize_comments(item.get("comments_payload"))

        return _NormalizedContentInput(
            platform=platform,
            platform_content_id=platform_content_id,
            source_type=item.get("normalized_source_type") or "market_post",
            source_url=source_url,
            normalized_source_url=normalized_source_url,
            title=title,
            body_text=body_text,
            published_at=published_at,
            collected_at=collected_at,
            content_hash=content_hash,
            tags=self._normalize_tags(item.get("tags")),
            raw_payload=item,
            metadata={
                "source_type": source_type,
                "page_type": item.get("page_type"),
                "query_text": item.get("query_text"),
                "raw_href": item.get("raw_href"),
            },
            likes=self._coerce_int(item.get("likes")),
            comments=self._coerce_int(item.get("comments")),
            collects=self._coerce_int(item.get("collects") or item.get("collections")),
            shares=self._coerce_int(item.get("shares")),
            views=self._coerce_optional_int(item.get("views")),
            follows_gained=self._coerce_optional_int(item.get("follows_gained")),
            reward_components={},
            author_platform_id=author_platform_id,
            author_display_name=self._optional_str(item.get("author_name")) or self._optional_str(item.get("author")),
            author_profile_url=self._optional_str(item.get("author_profile_url")),
            author_metadata={"source_adapter": "xhs_extension_capture"},
            comments_payload=comments_payload,
        )

    def _normalize_import_row(self, row: dict[str, Any], *, platform: str) -> _NormalizedContentInput:
        if not isinstance(row, dict):
            raise IngestionValidationError("rows must contain objects")
        for field in ("published_at", "title", "body_text", "likes", "collects", "comments"):
            if row.get(field) in (None, ""):
                raise IngestionValidationError(f"historical_note_import_v1 missing required field: {field}")

        published_at = self._parse_datetime(row.get("published_at"))
        if published_at is None:
            raise IngestionValidationError("published_at must be a valid ISO datetime")

        source_url = self._optional_str(row.get("source_url"))
        normalized_source_url = self._normalize_source_url(source_url)
        author_platform_id = self._optional_str(row.get("author_handle"))
        content_hash = self._build_content_hash(
            title=self._optional_str(row.get("title")),
            body_text=self._optional_str(row.get("body_text")),
            published_at=published_at,
            author_handle=author_platform_id,
        )
        platform_content_id = (
            self._optional_str(row.get("platform_content_id"))
            or self._derive_platform_content_id(normalized_source_url, content_hash)
        )

        return _NormalizedContentInput(
            platform=platform,
            platform_content_id=platform_content_id,
            source_type="manual_import",
            source_url=source_url,
            normalized_source_url=normalized_source_url,
            title=self._optional_str(row.get("title")),
            body_text=self._optional_str(row.get("body_text")),
            published_at=published_at,
            collected_at=utcnow(),
            content_hash=content_hash,
            tags=self._normalize_tags(row.get("tags")),
            raw_payload=row,
            metadata={"import_type": "historical_note_import_v1"},
            likes=self._coerce_int(row.get("likes")),
            comments=self._coerce_int(row.get("comments")),
            collects=self._coerce_int(row.get("collects")),
            shares=self._coerce_int(row.get("shares")),
            views=self._coerce_optional_int(row.get("views")),
            follows_gained=self._coerce_optional_int(row.get("follows_gained")),
            reward_components={},
            author_platform_id=author_platform_id,
            author_display_name=self._optional_str(row.get("author_name")),
            author_profile_url=None,
            author_metadata={"import_type": "historical_note_import_v1"},
            comments_payload=[],
        )

    def _parse_json_rows(self, file_bytes: bytes) -> list[dict[str, Any]]:
        try:
            payload = json.loads(file_bytes.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IngestionValidationError("uploaded JSON file is not valid UTF-8 JSON") from exc

        if isinstance(payload, dict):
            rows = payload.get("rows")
        else:
            rows = payload
        if not isinstance(rows, list) or not rows:
            raise IngestionValidationError("uploaded JSON file must contain a non-empty rows array")
        return [self._normalize_import_preview_row(row) for row in rows]

    def _parse_delimited_rows(self, file_bytes: bytes, *, delimiter: str) -> list[dict[str, Any]]:
        try:
            text = file_bytes.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise IngestionValidationError("uploaded tabular file must be UTF-8 encoded") from exc

        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        if not reader.fieldnames:
            raise IngestionValidationError("uploaded tabular file is missing a header row")
        rows = [self._normalize_import_preview_row(row) for row in reader if any(value not in (None, "") for value in row.values())]
        if not rows:
            raise IngestionValidationError("uploaded tabular file must contain at least one data row")
        return rows

    def _parse_xlsx_rows(self, file_bytes: bytes) -> list[dict[str, Any]]:
        try:
            workbook = zipfile.ZipFile(io.BytesIO(file_bytes))
        except zipfile.BadZipFile as exc:
            raise IngestionValidationError("uploaded .xlsx file is not a valid workbook") from exc

        with workbook:
            shared_strings = self._read_xlsx_shared_strings(workbook)
            try:
                sheet_bytes = workbook.read("xl/worksheets/sheet1.xml")
            except KeyError as exc:
                raise IngestionValidationError("uploaded workbook is missing sheet1.xml") from exc

        root = ElementTree.fromstring(sheet_bytes)
        namespace = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        rows: list[list[str]] = []
        for row_node in root.findall(".//a:sheetData/a:row", namespace):
            row_values: list[str] = []
            for cell in row_node.findall("a:c", namespace):
                cell_ref = cell.attrib.get("r", "")
                column_index = self._xlsx_column_index(cell_ref)
                while len(row_values) <= column_index:
                    row_values.append("")
                row_values[column_index] = self._read_xlsx_cell_value(cell, namespace, shared_strings)
            if any(value != "" for value in row_values):
                rows.append(row_values)

        if len(rows) < 2:
            raise IngestionValidationError("uploaded workbook must include a header row and at least one data row")

        headers = [self._normalize_import_header(value) or f"column_{index}" for index, value in enumerate(rows[0])]
        normalized_rows: list[dict[str, Any]] = []
        for raw_row in rows[1:]:
            row_payload = {
                headers[index]: raw_row[index] if index < len(raw_row) else ""
                for index in range(len(headers))
            }
            if any(value not in (None, "") for value in row_payload.values()):
                normalized_rows.append(self._normalize_import_preview_row(row_payload))
        if not normalized_rows:
            raise IngestionValidationError("uploaded workbook must contain at least one non-empty data row")
        return normalized_rows

    def _normalize_import_preview_row(self, row: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(row, dict):
            raise IngestionValidationError("uploaded import rows must contain objects")
        normalized: dict[str, Any] = {}
        for key, value in row.items():
            normalized_key = self._normalize_import_header(key)
            if normalized_key:
                normalized[normalized_key] = value
        return normalized

    @staticmethod
    def _normalize_import_header(value: Any) -> str | None:
        if value is None:
            return None
        header = str(value).strip()
        if not header:
            return None
        canonical = header.lower().replace(" ", "_").replace("-", "_")
        aliases = {
            "published_at": "published_at",
            "publishedat": "published_at",
            "publish_time": "published_at",
            "发布时间": "published_at",
            "title": "title",
            "标题": "title",
            "body_text": "body_text",
            "body": "body_text",
            "正文": "body_text",
            "content": "body_text",
            "likes": "likes",
            "点赞": "likes",
            "collects": "collects",
            "collections": "collects",
            "收藏": "collects",
            "comments": "comments",
            "评论": "comments",
            "platform_content_id": "platform_content_id",
            "content_id": "platform_content_id",
            "note_id": "platform_content_id",
            "source_url": "source_url",
            "url": "source_url",
            "链接": "source_url",
            "内容链接": "source_url",
            "author_handle": "author_handle",
            "author": "author_handle",
            "账号": "author_handle",
            "author_name": "author_name",
            "作者": "author_name",
            "shares": "shares",
            "转发": "shares",
            "tags": "tags",
            "标签": "tags",
        }
        return aliases.get(canonical, canonical)

    @staticmethod
    def _read_xlsx_shared_strings(workbook: zipfile.ZipFile) -> list[str]:
        try:
            payload = workbook.read("xl/sharedStrings.xml")
        except KeyError:
            return []

        root = ElementTree.fromstring(payload)
        namespace = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        values: list[str] = []
        for string_node in root.findall("a:si", namespace):
            text_parts = [node.text or "" for node in string_node.findall(".//a:t", namespace)]
            values.append("".join(text_parts))
        return values

    @staticmethod
    def _read_xlsx_cell_value(
        cell: ElementTree.Element,
        namespace: dict[str, str],
        shared_strings: list[str],
    ) -> str:
        cell_type = cell.attrib.get("t")
        value_node = cell.find("a:v", namespace)
        inline_text = "".join(node.text or "" for node in cell.findall(".//a:t", namespace))
        if cell_type == "inlineStr":
            return inline_text
        if value_node is None:
            return inline_text
        value = value_node.text or ""
        if cell_type == "s":
            try:
                return shared_strings[int(value)]
            except (ValueError, IndexError):
                return value
        return value

    @staticmethod
    def _xlsx_column_index(cell_ref: str) -> int:
        letters = "".join(char for char in cell_ref if char.isalpha()).upper()
        if not letters:
            return 0
        index = 0
        for char in letters:
            index = index * 26 + (ord(char) - ord("A") + 1)
        return index - 1

    @staticmethod
    def _optional_str(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return str(value)

    @staticmethod
    def _coerce_int(value: Any) -> int:
        if value in (None, ""):
            return 0
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise IngestionValidationError(f"expected integer-compatible value, got: {value!r}") from exc

    @staticmethod
    def _coerce_optional_int(value: Any) -> int | None:
        if value in (None, ""):
            return None
        return IngestionService._coerce_int(value)

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise IngestionValidationError(f"invalid datetime value: {value}") from exc
        raise IngestionValidationError(f"invalid datetime value: {value!r}")

    @staticmethod
    def _normalize_tags(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        raise IngestionValidationError("tags must be a string or list")

    @staticmethod
    def _normalize_source_url(value: str | None) -> str | None:
        if not value:
            return None
        parts = urlsplit(value)
        path = parts.path.rstrip("/")
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))

    @staticmethod
    def _build_content_hash(
        *,
        title: str | None,
        body_text: str | None,
        published_at: datetime | None,
        author_handle: str | None,
    ) -> str:
        payload = "|".join(
            [
                (title or "").strip().lower(),
                (body_text or "").strip().lower(),
                published_at.isoformat() if published_at else "",
                (author_handle or "").strip().lower(),
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _derive_platform_content_id(normalized_source_url: str | None, content_hash: str) -> str:
        if normalized_source_url:
            path = urlsplit(normalized_source_url).path.strip("/")
            if path:
                return path.split("/")[-1]
        return f"derived-{content_hash[:16]}"

    def _normalize_comments(self, value: Any) -> list[_NormalizedCommentInput]:
        if value in (None, ""):
            return []
        if not isinstance(value, list):
            raise IngestionValidationError("comments_payload must be a list")
        normalized: list[_NormalizedCommentInput] = []
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                raise IngestionValidationError(f"comments_payload[{index}] must be an object")
            platform_comment_id = self._optional_str(item.get("platform_comment_id"))
            body_text = self._optional_str(item.get("body_text"))
            if not platform_comment_id or not body_text:
                raise IngestionValidationError(
                    f"comments_payload[{index}] requires platform_comment_id and body_text"
                )
            normalized.append(
                _NormalizedCommentInput(
                    platform_comment_id=platform_comment_id,
                    author_name=self._optional_str(item.get("author_name")),
                    body_text=body_text,
                    commented_at=self._parse_datetime(item.get("commented_at")),
                    sentiment_label=self._optional_str(item.get("sentiment_label")),
                    metadata=item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
                )
            )
        return normalized
