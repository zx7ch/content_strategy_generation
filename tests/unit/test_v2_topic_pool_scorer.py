from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.v2.feedback.models import PerformanceSnapshotRecord, PublishRecordRecord
from app.v2.feedback.store import InMemoryFeedbackStore
from app.v2.foundation.service import MasterDataService
from app.v2.foundation.store import InMemoryMasterDataStore
from app.v2.topic_pool.models import TopicPoolItemRecord
from app.v2.topic_pool.scorer import ScorerService
from app.v2.topic_pool.store import InMemoryTopicPoolStore
from app.v2.foundation.models import utcnow


def _build_services():
    master_store = InMemoryMasterDataStore()
    master_service = MasterDataService(master_store)
    topic_pool_store = InMemoryTopicPoolStore()
    feedback_store = InMemoryFeedbackStore()
    scorer_service = ScorerService(
        master_data_service=master_service,
        topic_pool_store=topic_pool_store,
        feedback_store=feedback_store,
    )
    workspace = master_service.create_workspace(name="Acme", slug="acme")
    brand = master_service.create_brand(
        workspace_id=workspace.id,
        name="Acme Outdoor",
        category="outdoor",
        stage="growth",
        target_audience={"age_ranges": ["25-34"], "gender_skew": "female"},
    )
    return master_service, topic_pool_store, feedback_store, scorer_service, workspace, brand


def _save_candidate(
    topic_pool_store: InMemoryTopicPoolStore,
    *,
    workspace_id: str,
    brand_id: str,
    item_id: str,
    topic_type: str,
    last_scored_at=None,
):
    now = utcnow()
    topic_pool_store.save_topic_pool_item(
        TopicPoolItemRecord(
            id=item_id,
            workspace_id=workspace_id,
            brand_id=brand_id,
            topic_id=f"topic-{item_id}",
            title=f"{item_id} 标题",
            angle=f"{item_id} angle",
            hypothesis=f"{item_id} hypothesis",
            evidence_summary={
                "source_count": 2,
                "dominant_signal_type": "engagement",
                "topic_type": topic_type,
                "sources": [
                    {"item_id": f"content-{item_id}-1", "signal_type": "engagement", "weight": 0.5},
                    {"item_id": f"content-{item_id}-2", "signal_type": "engagement", "weight": 0.5},
                ],
            },
            last_scored_at=last_scored_at,
            created_at=now,
            updated_at=now,
        )
    )


def test_scorer_service_refreshes_stale_items_from_policy_and_performance_snapshots() -> None:
    master_service, topic_pool_store, feedback_store, scorer_service, workspace, brand = _build_services()
    channel = master_service.create_brand_channel(
        workspace_id=workspace.id,
        brand_id=brand.id,
        platform="xiaohongshu",
        account_name="Acme Outdoor",
    )
    master_service.replace_active_brand_policy_config(
        workspace_id=workspace.id,
        brand_id=brand.id,
        policy_name="baseline_rule_v1",
        policy_version="v1",
        hard_filter_rules={"blocked_topic_types": ["competitor"]},
        brand_fit_rules={"preferred_topic_types": ["scenario"], "minimum_source_count": 1},
        topic_type_targets={"targets": [{"topic_type": "scenario", "priority_boost": 0.12, "min_ratio": 0.0, "max_ratio": 1.0}]},
    )
    candidate_scored_at = utcnow() - timedelta(days=1)
    _save_candidate(
        topic_pool_store,
        workspace_id=workspace.id,
        brand_id=brand.id,
        item_id="scenario-a",
        topic_type="scenario",
        last_scored_at=candidate_scored_at,
    )
    publish = feedback_store.save_publish_record(
        record=PublishRecordRecord(
            id="publish-1",
            workspace_id=workspace.id,
            brand_id=brand.id,
            channel_id=channel.id,
            topic_pool_item_id="scenario-a",
            decision_event_id=None,
            decision_batch_id=None,
            publish_status="published",
            published_at=datetime(2026, 4, 10, 9, 30, tzinfo=timezone.utc),
            metadata={"topic_type": "scenario"},
        )
    )
    feedback_store.save_performance_snapshot(
        PerformanceSnapshotRecord(
            id="snapshot-1",
            workspace_id=workspace.id,
            brand_id=brand.id,
            publish_record_id=publish.id,
            observation_window_hours=168,
            snapshot_at=datetime(2026, 4, 17, 9, 30, tzinfo=timezone.utc),
            reward_version="reward_v1",
            composite_reward=0.62,
        )
    )

    refreshed = scorer_service.ensure_fresh(workspace_id=workspace.id, brand_id=brand.id)

    assert len(refreshed) == 1
    item = refreshed[0]
    assert item.fit_score > 0
    assert item.policy_score > 0
    assert item.historical_reward_score == 0.62
    assert item.final_score > 0
    assert item.evidence_summary["brand_fit_check"] is True
    assert item.last_scored_at is not None


def test_scorer_service_skips_fresh_items() -> None:
    master_service, topic_pool_store, _feedback_store, scorer_service, workspace, brand = _build_services()
    _save_candidate(
        topic_pool_store,
        workspace_id=workspace.id,
        brand_id=brand.id,
        item_id="fresh-a",
        topic_type="core",
        last_scored_at=utcnow(),
    )

    refreshed = scorer_service.ensure_fresh(workspace_id=workspace.id, brand_id=brand.id)

    assert refreshed[0].final_score == 0.0
    assert refreshed[0].fit_score == 0.0


def test_scorer_service_uses_global_mean_when_topic_type_history_missing() -> None:
    master_service, topic_pool_store, feedback_store, scorer_service, workspace, brand = _build_services()
    channel = master_service.create_brand_channel(
        workspace_id=workspace.id,
        brand_id=brand.id,
        platform="xiaohongshu",
        account_name="Acme Outdoor",
    )
    _save_candidate(
        topic_pool_store,
        workspace_id=workspace.id,
        brand_id=brand.id,
        item_id="problem-a",
        topic_type="problem",
        last_scored_at=utcnow() - timedelta(days=1),
    )
    publish = feedback_store.save_publish_record(
        record=PublishRecordRecord(
            id="publish-2",
            workspace_id=workspace.id,
            brand_id=brand.id,
            channel_id=channel.id,
            topic_pool_item_id=None,
            decision_event_id=None,
            decision_batch_id=None,
            publish_status="published",
            metadata={"topic_type": "scenario"},
        )
    )
    feedback_store.save_performance_snapshot(
        PerformanceSnapshotRecord(
            id="snapshot-2",
            workspace_id=workspace.id,
            brand_id=brand.id,
            publish_record_id=publish.id,
            observation_window_hours=168,
            snapshot_at=datetime(2026, 4, 17, 9, 30, tzinfo=timezone.utc),
            reward_version="reward_v1",
            composite_reward=0.41,
        )
    )

    refreshed = scorer_service.ensure_fresh(workspace_id=workspace.id, brand_id=brand.id)

    assert refreshed[0].historical_reward_score == 0.41


def test_scorer_service_refreshes_when_new_feedback_arrives_after_last_score() -> None:
    master_service, topic_pool_store, feedback_store, scorer_service, workspace, brand = _build_services()
    channel = master_service.create_brand_channel(
        workspace_id=workspace.id,
        brand_id=brand.id,
        platform="xiaohongshu",
        account_name="Acme Outdoor",
    )
    scored_at = utcnow()
    _save_candidate(
        topic_pool_store,
        workspace_id=workspace.id,
        brand_id=brand.id,
        item_id="scenario-b",
        topic_type="scenario",
        last_scored_at=scored_at,
    )
    publish = feedback_store.save_publish_record(
        record=PublishRecordRecord(
            id="publish-3",
            workspace_id=workspace.id,
            brand_id=brand.id,
            channel_id=channel.id,
            topic_pool_item_id="scenario-b",
            decision_event_id=None,
            decision_batch_id=None,
            publish_status="published",
            metadata={"topic_type": "scenario"},
        )
    )
    feedback_store.save_performance_snapshot(
        PerformanceSnapshotRecord(
            id="snapshot-3",
            workspace_id=workspace.id,
            brand_id=brand.id,
            publish_record_id=publish.id,
            observation_window_hours=168,
            snapshot_at=datetime(2026, 4, 17, 9, 30, tzinfo=timezone.utc),
            reward_version="reward_v1",
            composite_reward=0.57,
            created_at=scored_at + timedelta(seconds=1),
        )
    )

    refreshed = scorer_service.ensure_fresh(workspace_id=workspace.id, brand_id=brand.id)

    assert refreshed[0].historical_reward_score == 0.57
    assert refreshed[0].last_scored_at is not None
    assert refreshed[0].last_scored_at > scored_at
