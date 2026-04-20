from __future__ import annotations

import pytest

from app.v2.ingestion.service import IngestionService, IngestionValidationError
from app.v2.ingestion.store import InMemoryIngestionStore


def test_source_sync_ingests_author_content_and_metrics_with_dedupe() -> None:
    store = InMemoryIngestionStore()
    service = IngestionService(store)

    result = service.create_source_sync(
        workspace_id="ws-1",
        brand_id="brand-1",
        source_type="xhs_extension_capture",
        source_adapter="extension_source_sync_adapter_v1",
        channel_id="channel-1",
        capture_payload={
            "page_type": "search_result",
            "captured_at": "2026-04-12T09:00:00+08:00",
            "items": [
                {
                    "note_id": "abc123",
                    "query_text": "轻量徒步",
                    "source_url": "https://www.xiaohongshu.com/explore/abc123?xsec_token=token",
                    "title": "轻量徒步装备",
                    "visible_text_excerpt": "周末轻徒步不累脚",
                    "author_handle": "competitor-a",
                    "author_name": "竞品A",
                    "likes": 120,
                    "comments": 16,
                    "collects": 48,
                    "shares": 9,
                    "tags": ["徒步", "装备"],
                },
                {
                    "note_id": "abc123",
                    "query_text": "轻量徒步",
                    "source_url": "https://www.xiaohongshu.com/explore/abc123?xsec_token=token-2",
                    "title": "轻量徒步装备更新版",
                    "visible_text_excerpt": "周末轻徒步升级",
                    "author_handle": "competitor-a",
                    "author_name": "竞品A",
                    "likes": 128,
                    "comments": 18,
                    "collects": 50,
                    "shares": 10,
                },
            ],
        },
    )

    assert result.entry_type == "source_sync"
    assert result.status == "accepted"
    assert result.imported_item_count == 1
    assert result.deduped_item_count == 1

    content_items = service.list_content_items(brand_id="brand-1")
    assert len(content_items) == 1
    assert content_items[0].author_id is not None
    assert content_items[0].channel_id == "channel-1"
    assert content_items[0].metadata["normalized_source_url"] == "https://www.xiaohongshu.com/explore/abc123"
    snapshots = store.list_metrics_snapshots(content_items[0].id)
    assert len(snapshots) == 1
    assert snapshots[0].likes == 128


def test_historical_import_dedupes_by_normalized_source_url_when_platform_id_missing() -> None:
    store = InMemoryIngestionStore()
    service = IngestionService(store)

    result = service.create_data_import(
        workspace_id="ws-1",
        brand_id="brand-1",
        import_type="historical_note_import_v1",
        platform="xiaohongshu",
        rows=[
            {
                "published_at": "2025-09-10T12:00:00+08:00",
                "title": "换季敏感肌稳定住了",
                "body_text": "正文内容一",
                "likes": 320,
                "collects": 96,
                "comments": 28,
                "source_url": "https://www.xiaohongshu.com/explore/abc?xsec_token=1",
            },
            {
                "published_at": "2025-09-10T12:00:00+08:00",
                "title": "换季敏感肌稳定住了",
                "body_text": "正文内容二",
                "likes": 350,
                "collects": 101,
                "comments": 31,
                "source_url": "https://www.xiaohongshu.com/explore/abc?xsec_token=2",
            },
        ],
    )

    assert result.accepted_row_count == 2
    assert result.imported_item_count == 1
    assert result.deduped_item_count == 1

    content_items = service.list_content_items(brand_id="brand-1")
    assert len(content_items) == 1
    assert content_items[0].metadata["normalized_source_url"] == "https://www.xiaohongshu.com/explore/abc"
    snapshots = store.list_metrics_snapshots(content_items[0].id)
    assert len(snapshots) == 1
    assert snapshots[0].likes == 350


def test_historical_import_requires_normative_fields() -> None:
    service = IngestionService(InMemoryIngestionStore())

    with pytest.raises(IngestionValidationError, match="missing required field: body_text"):
        service.create_data_import(
            workspace_id="ws-1",
            brand_id="brand-1",
            import_type="historical_note_import_v1",
            platform="xiaohongshu",
            rows=[
                {
                    "published_at": "2025-09-10T12:00:00+08:00",
                    "title": "换季敏感肌稳定住了",
                    "likes": 320,
                    "collects": 96,
                    "comments": 28,
                    }
                ],
            )


def test_extension_capture_session_auto_syncs_and_returns_receipt() -> None:
    service = IngestionService(InMemoryIngestionStore())

    session = service.create_extension_capture_session(
        workspace_id="ws-1",
        brand_id="brand-1",
        channel_id="channel-1",
    )
    completed = service.submit_extension_capture(
        capture_session_id=session.capture_session_id,
        capture_token=session.capture_token,
        capture_payload={
            "page_type": "search_result",
            "captured_at": "2026-04-12T09:00:00+08:00",
            "items": [
                {
                    "note_id": "note-1",
                    "source_url": "https://www.xiaohongshu.com/explore/note-1",
                    "title": "通勤徒步鞋怎么选",
                    "visible_text_excerpt": "正文摘要",
                    "author_handle": "competitor-a",
                    "likes": 10,
                    "comments": 2,
                    "collects": 5,
                }
            ],
        },
    )

    assert completed.status == "accepted"
    assert completed.preview_payload is not None
    assert completed.ingestion_receipt is not None
    assert completed.ingestion_receipt["entry_type"] == "source_sync"


def test_data_import_preview_auto_syncs_and_returns_receipt() -> None:
    service = IngestionService(InMemoryIngestionStore())

    preview = service.create_data_import_preview(
        workspace_id="ws-1",
        brand_id="brand-1",
        file_name="historical-import.json",
        import_type="historical_note_import_v1",
        platform="xiaohongshu",
        rows=[
            {
                "published_at": "2025-09-10T12:00:00+08:00",
                "title": "换季敏感肌稳定住了",
                "body_text": "正文内容",
                "likes": 320,
                "collects": 96,
                "comments": 28,
            }
        ],
    )

    assert preview.status == "accepted"
    assert preview.preview_payload is not None
    assert preview.ingestion_receipt is not None
    assert preview.ingestion_receipt["entry_type"] == "data_import"


def test_parse_uploaded_csv_import_file_normalizes_headers() -> None:
    service = IngestionService(InMemoryIngestionStore())

    rows = service.parse_uploaded_import_file(
        file_name="historical-import.csv",
        file_bytes=(
            "发布时间,标题,正文,点赞,收藏,评论,链接\n"
            "2025-09-10T12:00:00+08:00,换季敏感肌稳定住了,正文内容,320,96,28,https://www.xiaohongshu.com/explore/abc\n"
        ).encode("utf-8"),
    )

    assert rows == [
        {
            "published_at": "2025-09-10T12:00:00+08:00",
            "title": "换季敏感肌稳定住了",
            "body_text": "正文内容",
            "likes": "320",
            "collects": "96",
            "comments": "28",
            "source_url": "https://www.xiaohongshu.com/explore/abc",
        }
    ]


def test_retry_extension_capture_sync_reuses_existing_preview_payload() -> None:
    service = IngestionService(InMemoryIngestionStore())

    session = service.create_extension_capture_session(
        workspace_id="ws-1",
        brand_id="brand-1",
        channel_id="channel-1",
    )
    service.submit_extension_capture(
        capture_session_id=session.capture_session_id,
        capture_token=session.capture_token,
        capture_payload={
            "page_type": "search_result",
            "captured_at": "2026-04-12T09:00:00+08:00",
            "items": [
                {
                    "note_id": "note-1",
                    "source_url": "https://www.xiaohongshu.com/explore/note-1",
                    "title": "通勤徒步鞋怎么选",
                    "visible_text_excerpt": "正文摘要",
                    "author_handle": "competitor-a",
                    "likes": 10,
                    "comments": 2,
                    "collects": 5,
                }
            ],
        },
    )

    retried = service.retry_extension_capture_sync(
        workspace_id="ws-1",
        brand_id="brand-1",
        capture_session_id=session.capture_session_id,
    )

    assert retried.status == "accepted"
    assert retried.ingestion_receipt is not None
    assert retried.ingestion_receipt["entry_type"] == "source_sync"


def test_retry_data_import_sync_reuses_existing_preview_payload() -> None:
    service = IngestionService(InMemoryIngestionStore())

    preview = service.create_data_import_preview(
        workspace_id="ws-1",
        brand_id="brand-1",
        file_name="historical-import.json",
        import_type="historical_note_import_v1",
        platform="xiaohongshu",
        rows=[
            {
                "published_at": "2025-09-10T12:00:00+08:00",
                "title": "换季敏感肌稳定住了",
                "body_text": "正文内容",
                "likes": 320,
                "collects": 96,
                "comments": 28,
            }
        ],
    )

    retried = service.retry_data_import_sync(
        workspace_id="ws-1",
        brand_id="brand-1",
        preview_id=preview.preview_id,
    )

    assert retried.status == "accepted"
    assert retried.preview_payload is not None
    assert retried.ingestion_receipt is not None
    assert retried.ingestion_receipt["entry_type"] == "data_import"
