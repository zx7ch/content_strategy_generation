from __future__ import annotations

from datetime import timedelta

import pytest

from app.v2.decision.service import DecisionService, DecisionValidationError
from app.v2.decision.store import InMemoryDecisionStore
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
    workspace = master_service.create_workspace(name="Acme", slug="acme")
    brand = master_service.create_brand(
        workspace_id=workspace.id,
        name="Acme Outdoor",
        category="outdoor",
        stage="growth",
        target_audience={"age_ranges": ["25-34"], "gender_skew": "female"},
    )
    return master_service, topic_pool_store, decision_store, decision_service, workspace, brand


def _seed_policy_and_snapshot(master_service: MasterDataService, workspace_id: str, brand_id: str):
    policy = master_service.replace_active_brand_policy_config(
        workspace_id=workspace_id,
        brand_id=brand_id,
        policy_name="baseline_rule_v1",
        policy_version="v1",
        topic_type_targets={
            "targets": [
                {"topic_type": "scenario", "min_ratio": 0.34, "max_ratio": 1.0, "priority_boost": 0.12},
                {"topic_type": "problem", "min_ratio": 0.0, "max_ratio": 1.0, "priority_boost": 0.03},
            ]
        },
    )
    snapshot = master_service.create_brand_state_snapshot(
        workspace_id=workspace_id,
        brand_id=brand_id,
        state_version="state_v1",
        stage="growth",
        state_features={"audience_focus": "urban commuting"},
    )
    return policy, snapshot


def _save_candidate(
    topic_pool_store: InMemoryTopicPoolStore,
    *,
    workspace_id: str,
    brand_id: str,
    item_id: str,
    title: str,
    topic_type: str,
    score: float,
    historical_reward_score: float = 0.0,
    last_scored_at=None,
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
            historical_reward_score=historical_reward_score,
            policy_score=0.0,
            final_score=score,
            last_scored_at=last_scored_at,
            created_at=now,
            updated_at=now,
        )
    )


def test_decision_run_creates_three_unique_slots_and_persists_batch() -> None:
    master_service, topic_pool_store, decision_store, decision_service, workspace, brand = _build_services()
    policy, snapshot = _seed_policy_and_snapshot(master_service, workspace.id, brand.id)
    scored_at = utcnow()

    _save_candidate(topic_pool_store, workspace_id=workspace.id, brand_id=brand.id, item_id="a", title="通勤鞋", topic_type="scenario", score=0.81, last_scored_at=scored_at)
    _save_candidate(topic_pool_store, workspace_id=workspace.id, brand_id=brand.id, item_id="b", title="背包减压", topic_type="problem", score=0.77, last_scored_at=scored_at)
    _save_candidate(topic_pool_store, workspace_id=workspace.id, brand_id=brand.id, item_id="c", title="周末轻徒步", topic_type="scenario", score=0.75, last_scored_at=scored_at)
    _save_candidate(topic_pool_store, workspace_id=workspace.id, brand_id=brand.id, item_id="d", title="露营收纳", topic_type="core", score=0.69, last_scored_at=scored_at)

    result = decision_service.run_decision_batch(
        workspace_id=workspace.id,
        brand_id=brand.id,
        requested_slot_count=3,
    )

    assert result.requested_slot_count == 3
    assert result.chosen_count == 3
    assert len(result.items) == 3
    assert len({item.topic_pool_item_id for item in result.items}) == 3
    assert result.brand_policy_config_id == policy.id
    assert result.brand_state_snapshot_id == snapshot.id

    batch = decision_store.get_decision_batch(result.batch_id)
    assert batch is not None
    assert batch.chosen_count == 3
    assert len(decision_store.list_decision_batch_items(result.batch_id)) == 3


def test_decision_run_applies_priority_boost_and_ratio_reservation() -> None:
    master_service, topic_pool_store, _, decision_service, workspace, brand = _build_services()
    _seed_policy_and_snapshot(master_service, workspace.id, brand.id)
    scored_at = utcnow()

    _save_candidate(topic_pool_store, workspace_id=workspace.id, brand_id=brand.id, item_id="top-problem", title="尺码痛点", topic_type="problem", score=0.84, last_scored_at=scored_at)
    _save_candidate(topic_pool_store, workspace_id=workspace.id, brand_id=brand.id, item_id="boosted-scenario", title="通勤场景", topic_type="scenario", score=0.73, last_scored_at=scored_at)
    _save_candidate(topic_pool_store, workspace_id=workspace.id, brand_id=brand.id, item_id="core", title="产品卖点", topic_type="core", score=0.71, last_scored_at=scored_at)

    result = decision_service.run_decision_batch(
        workspace_id=workspace.id,
        brand_id=brand.id,
        requested_slot_count=2,
    )

    assert len(result.items) == 2
    assert result.items[0].topic_type == "scenario"
    assert "priority_boost" in result.items[0].reason_codes


def test_decision_run_refreshes_stale_scores_from_topic_pool_only() -> None:
    master_service, topic_pool_store, _, decision_service, workspace, brand = _build_services()
    _seed_policy_and_snapshot(master_service, workspace.id, brand.id)
    stale_scored_at = utcnow() - timedelta(days=1)
    _save_candidate(
        topic_pool_store,
        workspace_id=workspace.id,
        brand_id=brand.id,
        item_id="stale",
        title="旧候选",
        topic_type="scenario",
        score=0.5,
        historical_reward_score=0.4,
        last_scored_at=stale_scored_at,
    )

    decision_service.run_decision_batch(
        workspace_id=workspace.id,
        brand_id=brand.id,
        requested_slot_count=1,
    )

    refreshed = topic_pool_store.get_topic_pool_item_by_topic(brand_id=brand.id, topic_id="topic-stale")
    assert refreshed is not None
    assert refreshed.policy_score > 0
    assert refreshed.last_scored_at is not None
    assert refreshed.historical_reward_score == 0.0


def test_decision_review_updates_slot_status_and_edits() -> None:
    master_service, topic_pool_store, _, decision_service, workspace, brand = _build_services()
    _seed_policy_and_snapshot(master_service, workspace.id, brand.id)
    _save_candidate(
        topic_pool_store,
        workspace_id=workspace.id,
        brand_id=brand.id,
        item_id="slot",
        title="原始标题",
        topic_type="scenario",
        score=0.82,
        last_scored_at=utcnow(),
    )
    run_result = decision_service.run_decision_batch(
        workspace_id=workspace.id,
        brand_id=brand.id,
        requested_slot_count=1,
    )

    reviewed = decision_service.review_batch_item(
        workspace_id=workspace.id,
        batch_id=run_result.batch_id,
        slot_index=0,
        review_action="edit_and_accept",
        edited_title="编辑后标题",
        review_notes="更贴近品牌语气",
        reviewed_by_id="operator-1",
    )

    assert reviewed.review_status == "edit_and_accept"
    assert reviewed.title == "编辑后标题"
    assert reviewed.review_notes == "更贴近品牌语气"


def test_decision_review_requires_edit_payload_for_edit_and_accept() -> None:
    master_service, topic_pool_store, _, decision_service, workspace, brand = _build_services()
    _seed_policy_and_snapshot(master_service, workspace.id, brand.id)
    _save_candidate(
        topic_pool_store,
        workspace_id=workspace.id,
        brand_id=brand.id,
        item_id="slot",
        title="原始标题",
        topic_type="scenario",
        score=0.82,
        last_scored_at=utcnow(),
    )
    run_result = decision_service.run_decision_batch(
        workspace_id=workspace.id,
        brand_id=brand.id,
        requested_slot_count=1,
    )

    with pytest.raises(DecisionValidationError, match="requires at least one edited field"):
        decision_service.review_batch_item(
            workspace_id=workspace.id,
            batch_id=run_result.batch_id,
            slot_index=0,
            review_action="edit_and_accept",
        )


def test_decision_service_can_read_latest_batch_and_reflect_reviewed_fields() -> None:
    master_service, topic_pool_store, _, decision_service, workspace, brand = _build_services()
    _seed_policy_and_snapshot(master_service, workspace.id, brand.id)
    _save_candidate(
        topic_pool_store,
        workspace_id=workspace.id,
        brand_id=brand.id,
        item_id="slot",
        title="原始标题",
        topic_type="scenario",
        score=0.82,
        last_scored_at=utcnow(),
    )
    run_result = decision_service.run_decision_batch(
        workspace_id=workspace.id,
        brand_id=brand.id,
        requested_slot_count=1,
    )
    decision_service.review_batch_item(
        workspace_id=workspace.id,
        batch_id=run_result.batch_id,
        slot_index=0,
        review_action="edit_and_accept",
        edited_title="读取后的标题",
    )

    latest = decision_service.get_latest_decision_batch(
        workspace_id=workspace.id,
        brand_id=brand.id,
    )
    by_id = decision_service.get_decision_batch(
        workspace_id=workspace.id,
        batch_id=run_result.batch_id,
    )

    assert latest.batch_id == run_result.batch_id
    assert latest.items[0].title == "读取后的标题"
    assert latest.items[0].review_status == "edit_and_accept"
    assert by_id.items[0].decision_mode in {"Exploitation", "Exploration"}


def test_decision_run_uses_ceil_for_min_ratio_guarantees() -> None:
    master_service, topic_pool_store, _, decision_service, workspace, brand = _build_services()
    master_service.replace_active_brand_policy_config(
        workspace_id=workspace.id,
        brand_id=brand.id,
        policy_name="baseline_rule_v1",
        policy_version="v1",
        topic_type_targets={
            "targets": [
                {"topic_type": "scenario", "min_ratio": 0.34, "max_ratio": 1.0, "priority_boost": 0.1},
                {"topic_type": "problem", "min_ratio": 0.0, "max_ratio": 1.0, "priority_boost": 0.0},
            ]
        },
    )
    master_service.create_brand_state_snapshot(
        workspace_id=workspace.id,
        brand_id=brand.id,
        state_version="state_v1",
        stage="growth",
        state_features={"audience_focus": "urban commuting"},
    )
    scored_at = utcnow()
    _save_candidate(topic_pool_store, workspace_id=workspace.id, brand_id=brand.id, item_id="s1", title="通勤鞋", topic_type="scenario", score=0.9, last_scored_at=scored_at)
    _save_candidate(topic_pool_store, workspace_id=workspace.id, brand_id=brand.id, item_id="s2", title="周末轻徒步", topic_type="scenario", score=0.88, last_scored_at=scored_at)
    _save_candidate(topic_pool_store, workspace_id=workspace.id, brand_id=brand.id, item_id="p1", title="尺码痛点", topic_type="problem", score=0.7, last_scored_at=scored_at)

    result = decision_service.run_decision_batch(
        workspace_id=workspace.id,
        brand_id=brand.id,
        requested_slot_count=3,
    )

    scenario_count = sum(1 for item in result.items if item.topic_type == "scenario")
    assert scenario_count == 2


def test_decision_run_respects_max_ratio_caps_during_global_fill() -> None:
    master_service, topic_pool_store, _, decision_service, workspace, brand = _build_services()
    master_service.replace_active_brand_policy_config(
        workspace_id=workspace.id,
        brand_id=brand.id,
        policy_name="baseline_rule_v1",
        policy_version="v1",
        topic_type_targets={
            "targets": [
                {"topic_type": "scenario", "min_ratio": 0.0, "max_ratio": 0.34, "priority_boost": 0.0},
                {"topic_type": "problem", "min_ratio": 0.0, "max_ratio": 1.0, "priority_boost": 0.0},
            ]
        },
    )
    master_service.create_brand_state_snapshot(
        workspace_id=workspace.id,
        brand_id=brand.id,
        state_version="state_v1",
        stage="growth",
        state_features={"audience_focus": "urban commuting"},
    )
    scored_at = utcnow()
    _save_candidate(topic_pool_store, workspace_id=workspace.id, brand_id=brand.id, item_id="s1", title="通勤鞋", topic_type="scenario", score=0.95, last_scored_at=scored_at)
    _save_candidate(topic_pool_store, workspace_id=workspace.id, brand_id=brand.id, item_id="s2", title="周末轻徒步", topic_type="scenario", score=0.93, last_scored_at=scored_at)
    _save_candidate(topic_pool_store, workspace_id=workspace.id, brand_id=brand.id, item_id="p1", title="尺码痛点", topic_type="problem", score=0.81, last_scored_at=scored_at)
    _save_candidate(topic_pool_store, workspace_id=workspace.id, brand_id=brand.id, item_id="c1", title="核心卖点", topic_type="core", score=0.79, last_scored_at=scored_at)

    result = decision_service.run_decision_batch(
        workspace_id=workspace.id,
        brand_id=brand.id,
        requested_slot_count=3,
    )

    scenario_count = sum(1 for item in result.items if item.topic_type == "scenario")
    assert scenario_count == 1


def test_decision_run_trims_infeasible_min_slots_by_priority_boost() -> None:
    master_service, topic_pool_store, decision_store, decision_service, workspace, brand = _build_services()
    master_service.replace_active_brand_policy_config(
        workspace_id=workspace.id,
        brand_id=brand.id,
        policy_name="baseline_rule_v1",
        policy_version="v1",
        topic_type_targets={
            "targets": [
                {"topic_type": "scenario", "min_ratio": 0.25, "max_ratio": 1.0, "priority_boost": 0.12},
                {"topic_type": "audience", "min_ratio": 0.25, "max_ratio": 1.0, "priority_boost": 0.01},
                {"topic_type": "problem", "min_ratio": 0.25, "max_ratio": 1.0, "priority_boost": 0.04},
                {"topic_type": "trend", "min_ratio": 0.25, "max_ratio": 1.0, "priority_boost": 0.05},
            ]
        },
    )
    master_service.create_brand_state_snapshot(
        workspace_id=workspace.id,
        brand_id=brand.id,
        state_version="state_v1",
        stage="growth",
        state_features={"audience_focus": "urban commuting"},
    )
    scored_at = utcnow()
    _save_candidate(topic_pool_store, workspace_id=workspace.id, brand_id=brand.id, item_id="s1", title="通勤鞋", topic_type="scenario", score=0.88, last_scored_at=scored_at)
    _save_candidate(topic_pool_store, workspace_id=workspace.id, brand_id=brand.id, item_id="a1", title="女生通勤", topic_type="audience", score=0.84, last_scored_at=scored_at)
    _save_candidate(topic_pool_store, workspace_id=workspace.id, brand_id=brand.id, item_id="p1", title="尺码痛点", topic_type="problem", score=0.83, last_scored_at=scored_at)
    _save_candidate(topic_pool_store, workspace_id=workspace.id, brand_id=brand.id, item_id="t1", title="周末热度", topic_type="trend", score=0.82, last_scored_at=scored_at)

    result = decision_service.run_decision_batch(
        workspace_id=workspace.id,
        brand_id=brand.id,
        requested_slot_count=3,
    )

    selected_types = {item.topic_type for item in result.items}
    assert "audience" not in selected_types
    assert selected_types == {"scenario", "problem", "trend"}
    batch = decision_store.get_decision_batch(result.batch_id)
    assert batch is not None
    assert batch.context_snapshot["constraint_infeasible"] is True
    assert batch.context_snapshot["quota_plan"]["audience"]["min_slots"] == 0
