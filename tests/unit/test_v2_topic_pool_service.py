from __future__ import annotations

from datetime import timedelta

from app.v2.foundation.service import MasterDataService
from app.v2.feedback.store import InMemoryFeedbackStore
from app.v2.foundation.store import InMemoryMasterDataStore
from app.v2.ingestion.models import TopicRecord
from app.v2.ingestion.service import IngestionService
from app.v2.ingestion.store import InMemoryIngestionStore
from app.v2.topic_pool.scorer import ScorerService
from app.v2.topic_pool.models import TopicPoolItemRecord
from app.v2.topic_pool.service import TopicPoolService
from app.v2.topic_pool.store import InMemoryTopicPoolStore


def _build_services():
    master_store = InMemoryMasterDataStore()
    master_service = MasterDataService(master_store)
    ingestion_store = InMemoryIngestionStore()
    ingestion_service = IngestionService(ingestion_store)
    topic_pool_store = InMemoryTopicPoolStore()
    feedback_store = InMemoryFeedbackStore()
    topic_pool_service = TopicPoolService(
        master_data_service=master_service,
        ingestion_store=ingestion_store,
        topic_pool_store=topic_pool_store,
        scorer_service=ScorerService(
            master_data_service=master_service,
            topic_pool_store=topic_pool_store,
            feedback_store=feedback_store,
        ),
    )
    workspace = master_service.create_workspace(name="Acme", slug="acme")
    brand = master_service.create_brand(
        workspace_id=workspace.id,
        name="Acme Outdoor",
        category="outdoor",
        stage="growth",
        target_audience={"age_ranges": ["25-34"], "gender_skew": "female"},
    )
    return master_service, ingestion_service, ingestion_store, topic_pool_store, topic_pool_service, workspace, brand


def test_topic_pool_refresh_generates_candidates_with_normative_evidence_summary() -> None:
    _, ingestion_service, _, _, topic_pool_service, workspace, brand = _build_services()

    ingestion_service.create_source_sync(
        workspace_id=workspace.id,
        brand_id=brand.id,
        source_type="xhs_extension_capture",
        source_adapter="extension_source_sync_adapter_v1",
        channel_id=None,
        capture_payload={
            "page_type": "search_result",
            "captured_at": "2026-04-12T09:00:00+08:00",
            "items": [
                {
                    "note_id": "note-1",
                    "source_url": "https://www.xiaohongshu.com/explore/note-1",
                    "title": "通勤徒步鞋怎么选",
                    "visible_text_excerpt": "解决上下班和周末轻徒步切换问题",
                    "author_handle": "competitor-a",
                    "likes": 128,
                    "comments": 22,
                    "collects": 63,
                    "shares": 11,
                    "tags": ["通勤", "徒步"],
                },
                {
                    "note_id": "note-2",
                    "source_url": "https://www.xiaohongshu.com/explore/note-2",
                    "title": "通勤背包不压肩",
                    "visible_text_excerpt": "办公室和短途出差都能用",
                    "author_handle": "competitor-b",
                    "likes": 108,
                    "comments": 16,
                    "collects": 57,
                    "shares": 8,
                    "tags": ["通勤", "背包"],
                },
            ],
        },
    )

    refresh = topic_pool_service.refresh_topic_pool(
        workspace_id=workspace.id,
        brand_id=brand.id,
    )
    listing = topic_pool_service.list_topic_pool(
        workspace_id=workspace.id,
        brand_id=brand.id,
    )

    assert refresh.status == "completed"
    assert refresh.generated_item_count >= 1
    assert listing.total_candidate_count >= 1
    first_item = listing.items[0]
    assert first_item.topic_type == "scenario"
    assert first_item.evidence_summary["source_count"] >= 1
    assert first_item.evidence_summary["dominant_signal_type"] in {"engagement", "gap", "trend", "owned_performance"}
    assert len(first_item.evidence_summary["sources"]) == first_item.evidence_summary["source_count"]
    assert abs(first_item.evidence_summary["sources"][0]["weight"] * first_item.evidence_summary["source_count"] - 1) < 1e-6
    assert "competitor_scan_summary" in first_item.evidence_summary
    assert "insight_summary" in first_item.evidence_summary
    assert "agent_lineage" in first_item.evidence_summary
    assert first_item.evidence_summary["agent_lineage"]["competitor_scan_agent"]["status"] == "completed"
    assert first_item.evidence_summary["agent_lineage"]["pattern_insight_agent"]["status"] == "completed"
    assert first_item.title
    assert first_item.angle
    assert first_item.hypothesis
    assert first_item.final_score > 0
    assert first_item.score_breakdown["final_score"] == first_item.final_score
    assert first_item.score_breakdown["novelty_score"] > 0
    assert first_item.score_breakdown["fit_score"] > 0
    assert first_item.evidence_provenance
    assert first_item.evidence_provenance[0]["original_title"]
    assert first_item.evidence_provenance[0]["source_url"].startswith("https://")
    assert first_item.evidence_provenance[0]["signal_type"] in {"engagement", "gap", "trend", "owned_performance"}
    assert first_item.evidence_provenance[0]["contribution_weight"] > 0
    assert first_item.evidence_provenance[0]["likes"] >= 0


def test_topic_pool_refresh_reuses_existing_topic_and_archives_stale_candidates() -> None:
    _, ingestion_service, ingestion_store, topic_pool_store, topic_pool_service, workspace, brand = _build_services()

    ingestion_service.create_source_sync(
        workspace_id=workspace.id,
        brand_id=brand.id,
        source_type="xhs_extension_capture",
        source_adapter="extension_source_sync_adapter_v1",
        channel_id=None,
        capture_payload={
            "page_type": "search_result",
            "captured_at": "2026-04-12T09:00:00+08:00",
            "items": [
                {
                    "note_id": "note-1",
                    "source_url": "https://www.xiaohongshu.com/explore/note-1",
                    "title": "通勤徒步鞋怎么选",
                    "visible_text_excerpt": "解决上下班和周末轻徒步切换问题",
                    "author_handle": "competitor-a",
                    "likes": 128,
                    "comments": 22,
                    "collects": 63,
                    "shares": 11,
                    "tags": ["通勤", "徒步"],
                }
            ],
        },
    )

    first_refresh = topic_pool_service.refresh_topic_pool(workspace_id=workspace.id, brand_id=brand.id)
    topics_after_first = ingestion_store.list_topics(brand.id)
    second_refresh = topic_pool_service.refresh_topic_pool(workspace_id=workspace.id, brand_id=brand.id)
    topics_after_second = ingestion_store.list_topics(brand.id)

    old_topic = ingestion_store.save_topic(
        TopicRecord(
            id="topic-stale",
            workspace_id=workspace.id,
            brand_id=brand.id,
            normalized_name="old-gap",
            display_name="旧痛点",
            topic_type="problem",
        )
    )
    stale_time = second_refresh.refreshed_at - timedelta(days=61)
    topic_pool_store.save_topic_pool_item(
        TopicPoolItemRecord(
            id="item-stale",
            workspace_id=workspace.id,
            brand_id=brand.id,
            topic_id=old_topic.id,
            title="旧痛点的用户问题拆解",
            angle="旧 angle",
            hypothesis="旧 hypothesis",
            evidence_summary={
                "sources": [{"item_id": "legacy", "signal_type": "gap", "weight": 1.0}],
                "source_count": 1,
                "dominant_signal_type": "gap",
                "snapshot_at": stale_time.isoformat(),
            },
            final_score=0.51,
            created_at=stale_time,
            updated_at=stale_time,
        )
    )

    final_refresh = topic_pool_service.refresh_topic_pool(workspace_id=workspace.id, brand_id=brand.id)
    listing = topic_pool_service.list_topic_pool(workspace_id=workspace.id, brand_id=brand.id)

    assert first_refresh.generated_item_count == 1
    assert second_refresh.generated_item_count == 1
    assert len(topics_after_first) == 1
    assert len(topics_after_second) == 1
    assert final_refresh.archived_item_count == 1
    assert all(item.display_name != "旧痛点" for item in listing.items)


def test_topic_pool_refresh_skips_invalid_candidates_before_persistence() -> None:
    _, ingestion_service, ingestion_store, _, topic_pool_service, workspace, brand = _build_services()

    ingestion_service.create_source_sync(
        workspace_id=workspace.id,
        brand_id=brand.id,
        source_type="xhs_extension_capture",
        source_adapter="extension_source_sync_adapter_v1",
        channel_id=None,
        capture_payload={
            "page_type": "search_result",
            "captured_at": "2026-04-12T09:00:00+08:00",
            "items": [
                {
                    "note_id": "note-empty",
                    "source_url": "https://www.xiaohongshu.com/explore/note-empty",
                    "title": "",
                    "visible_text_excerpt": "",
                    "author_handle": "competitor-a",
                    "likes": 10,
                    "comments": 1,
                    "collects": 2,
                    "shares": 0,
                    "tags": [],
                }
            ],
        },
    )

    refresh = topic_pool_service.refresh_topic_pool(workspace_id=workspace.id, brand_id=brand.id)
    listing = topic_pool_service.list_topic_pool(workspace_id=workspace.id, brand_id=brand.id)

    assert refresh.generated_item_count == 0
    assert listing.total_candidate_count == 0
    assert ingestion_store.list_topics(brand.id) == []
