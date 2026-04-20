from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from app.v2.decision.service import DecisionService
from app.v2.decision.store import InMemoryDecisionStore
from app.v2.feedback.service import FeedbackService, FeedbackValidationError
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
    decision_store = InMemoryDecisionStore()
    feedback_store = InMemoryFeedbackStore()
    scorer_service = ScorerService(
        master_data_service=master_service,
        topic_pool_store=topic_pool_store,
        feedback_store=feedback_store,
    )
    decision_service = DecisionService(
        master_data_service=master_service,
        topic_pool_store=topic_pool_store,
        decision_store=decision_store,
        scorer_service=scorer_service,
    )
    feedback_service = FeedbackService(
        master_data_service=master_service,
        topic_pool_store=topic_pool_store,
        decision_store=decision_store,
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
    channel = master_service.create_brand_channel(
        workspace_id=workspace.id,
        brand_id=brand.id,
        platform="xiaohongshu",
        account_name="Acme 小红书",
    )
    return (
        master_service,
        topic_pool_store,
        decision_store,
        feedback_store,
        decision_service,
        feedback_service,
        workspace,
        brand,
        channel,
    )


def _seed_policy_and_snapshot(master_service: MasterDataService, workspace_id: str, brand_id: str):
    master_service.replace_active_brand_policy_config(
        workspace_id=workspace_id,
        brand_id=brand_id,
        policy_name="baseline_rule_v1",
        policy_version="v1",
        topic_type_targets={"targets": [{"topic_type": "scenario", "min_ratio": 0.0, "max_ratio": 1.0, "priority_boost": 0.1}]},
    )
    master_service.create_brand_state_snapshot(
        workspace_id=workspace_id,
        brand_id=brand_id,
        state_version="state_v1",
        stage="growth",
        state_features={"audience_focus": "urban commuting"},
    )


def _save_candidate(
    topic_pool_store: InMemoryTopicPoolStore,
    *,
    workspace_id: str,
    brand_id: str,
    item_id: str,
    title: str,
    topic_type: str,
    score: float,
):
    now = utcnow()
    topic_pool_store.save_topic_pool_item(
        TopicPoolItemRecord(
            id=item_id,
            workspace_id=workspace_id,
            brand_id=brand_id,
            topic_id=f"topic-{item_id}",
            title=title,
            angle=f"{title} angle",
            hypothesis=f"{title} hypothesis",
            evidence_summary={
                "source_count": 1,
                "dominant_signal_type": "engagement",
                "topic_type": topic_type,
                "sources": [{"item_id": f"content-{item_id}", "signal_type": "engagement", "weight": 1.0}],
            },
            novelty_score=0.4,
            fit_score=0.3,
            trend_score=0.3,
            historical_reward_score=0.0,
            policy_score=0.0,
            final_score=score,
            last_scored_at=now,
            created_at=now,
            updated_at=now,
        )
    )


def test_publish_record_and_performance_import_persist_reward_lineage() -> None:
    (
        master_service,
        topic_pool_store,
        _decision_store,
        feedback_store,
        decision_service,
        feedback_service,
        workspace,
        brand,
        channel,
    ) = _build_services()
    _seed_policy_and_snapshot(master_service, workspace.id, brand.id)
    _save_candidate(topic_pool_store, workspace_id=workspace.id, brand_id=brand.id, item_id="a", title="通勤鞋", topic_type="scenario", score=0.82)

    batch = decision_service.run_decision_batch(workspace_id=workspace.id, brand_id=brand.id, requested_slot_count=1)
    publish = feedback_service.create_publish_record(
        workspace_id=workspace.id,
        brand_id=brand.id,
        channel_id=channel.id,
        decision_event_id=batch.items[0].decision_event_id,
        publish_status="published",
        published_at=datetime(2026, 4, 10, 9, 30, tzinfo=timezone.utc),
    )

    snapshot = feedback_service.import_performance_snapshot(
        workspace_id=workspace.id,
        publish_record_id=publish.id,
        observation_window_hours=168,
        snapshot_at=datetime(2026, 4, 17, 9, 30, tzinfo=timezone.utc),
        reward_version="reward_v1",
        metrics={
            "impressions": 12000,
            "clicks": 850,
            "likes": 320,
            "comments": 28,
            "collects": 96,
            "shares": 31,
            "follows_gained": 12,
            "conversion_proxy": {"value": 0.08, "type": "store_click_rate", "source": "manual_import"},
        },
    )

    assert snapshot.composite_reward > 0
    assert len(feedback_store.list_feedback_events(brand.id)) == 1
    stored_publish = feedback_store.get_publish_record(publish.id)
    assert stored_publish is not None
    assert stored_publish.metadata["topic_type"] == "scenario"


def test_evaluation_run_succeeds_with_complete_replay_lineage() -> None:
    (
        master_service,
        topic_pool_store,
        _decision_store,
        _feedback_store,
        decision_service,
        feedback_service,
        workspace,
        brand,
        channel,
    ) = _build_services()
    _seed_policy_and_snapshot(master_service, workspace.id, brand.id)
    _save_candidate(topic_pool_store, workspace_id=workspace.id, brand_id=brand.id, item_id="a", title="通勤鞋", topic_type="scenario", score=0.82)
    _save_candidate(topic_pool_store, workspace_id=workspace.id, brand_id=brand.id, item_id="b", title="尺码痛点", topic_type="problem", score=0.74)

    batch = decision_service.run_decision_batch(workspace_id=workspace.id, brand_id=brand.id, requested_slot_count=1)
    publish = feedback_service.create_publish_record(
        workspace_id=workspace.id,
        brand_id=brand.id,
        channel_id=channel.id,
        decision_event_id=batch.items[0].decision_event_id,
        publish_status="published",
    )
    feedback_service.import_performance_snapshot(
        workspace_id=workspace.id,
        publish_record_id=publish.id,
        observation_window_hours=168,
        snapshot_at=datetime(2026, 4, 17, 9, 30, tzinfo=timezone.utc),
        reward_version="reward_v1",
        metrics={
            "impressions": 12000,
            "clicks": 850,
            "likes": 320,
            "comments": 28,
            "collects": 96,
            "shares": 31,
            "follows_gained": 12,
            "conversion_proxy": {"value": 0.08, "type": "store_click_rate", "source": "manual_import"},
        },
    )

    run = feedback_service.create_evaluation_run(
        workspace_id=workspace.id,
        brand_id=brand.id,
        evaluation_type="replay",
    )

    assert run.sample_count == 1
    assert run.summary["estimated_policy_value"] >= 0
    assert run.summary["exploration_entropy"] > 0
    assert "candidate_quality" in run.summary
    assert run.slices


def test_evaluation_run_fails_closed_when_propensity_is_missing() -> None:
    (
        master_service,
        topic_pool_store,
        decision_store,
        _feedback_store,
        decision_service,
        feedback_service,
        workspace,
        brand,
        channel,
    ) = _build_services()
    _seed_policy_and_snapshot(master_service, workspace.id, brand.id)
    _save_candidate(topic_pool_store, workspace_id=workspace.id, brand_id=brand.id, item_id="a", title="通勤鞋", topic_type="scenario", score=0.82)

    batch = decision_service.run_decision_batch(workspace_id=workspace.id, brand_id=brand.id, requested_slot_count=1)
    event = decision_store.get_decision_event(batch.items[0].decision_event_id)
    assert event is not None
    decision_store.save_decision_event(replace(event, propensities=[]))

    publish = feedback_service.create_publish_record(
        workspace_id=workspace.id,
        brand_id=brand.id,
        channel_id=channel.id,
        decision_event_id=batch.items[0].decision_event_id,
        publish_status="published",
    )
    feedback_service.import_performance_snapshot(
        workspace_id=workspace.id,
        publish_record_id=publish.id,
        observation_window_hours=168,
        snapshot_at=datetime(2026, 4, 17, 9, 30, tzinfo=timezone.utc),
        reward_version="reward_v1",
        metrics={
            "impressions": 12000,
            "clicks": 850,
            "likes": 320,
            "comments": 28,
            "collects": 96,
            "shares": 31,
            "follows_gained": 12,
            "conversion_proxy": {"value": 0.08, "type": "store_click_rate", "source": "manual_import"},
        },
    )

    with pytest.raises(FeedbackValidationError, match="incomplete for replay-critical lineage"):
        feedback_service.create_evaluation_run(
            workspace_id=workspace.id,
            brand_id=brand.id,
            evaluation_type="replay",
        )
