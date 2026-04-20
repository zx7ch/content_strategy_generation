from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from app.api.routes.router import app
from app.v2.decision import DecisionService, InMemoryDecisionStore
from app.v2.feedback import FeedbackService, InMemoryFeedbackStore
from app.v2.foundation import InMemoryMasterDataStore, MasterDataService
from app.v2.ingestion import InMemoryIngestionStore, IngestionService
from app.v2.topic_pool import InMemoryTopicPoolStore, ScorerService, TopicPoolService


@pytest.fixture(autouse=True)
def reset_v2_feedback_app_state():
    master_store = InMemoryMasterDataStore()
    master_service = MasterDataService(master_store)
    ingestion_store = InMemoryIngestionStore()
    ingestion_service = IngestionService(ingestion_store)
    topic_pool_store = InMemoryTopicPoolStore()
    decision_store = InMemoryDecisionStore()
    feedback_store = InMemoryFeedbackStore()
    scorer_service = ScorerService(
        master_data_service=master_service,
        topic_pool_store=topic_pool_store,
        feedback_store=feedback_store,
    )
    topic_pool_service = TopicPoolService(
        master_data_service=master_service,
        ingestion_store=ingestion_store,
        topic_pool_store=topic_pool_store,
        scorer_service=scorer_service,
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
    app.state.v2_master_data_store = master_store
    app.state.v2_master_data_service = master_service
    app.state.v2_ingestion_store = ingestion_store
    app.state.v2_ingestion_service = ingestion_service
    app.state.v2_topic_pool_store = topic_pool_store
    app.state.v2_topic_pool_service = topic_pool_service
    app.state.v2_decision_store = decision_store
    app.state.v2_decision_service = decision_service
    app.state.v2_feedback_store = feedback_store
    app.state.v2_feedback_service = feedback_service
    yield
    for attr in (
        "v2_master_data_service",
        "v2_master_data_store",
        "v2_ingestion_service",
        "v2_ingestion_store",
        "v2_topic_pool_service",
        "v2_topic_pool_store",
        "v2_decision_service",
        "v2_decision_store",
        "v2_feedback_service",
        "v2_feedback_store",
    ):
        if hasattr(app.state, attr):
            delattr(app.state, attr)


def _create_workspace(client: TestClient) -> dict:
    response = client.post("/workspaces", json={"name": "Acme", "slug": "acme", "timezone": "Asia/Shanghai"})
    assert response.status_code == 201
    return response.json()


def _headers(workspace_id: str) -> dict[str, str]:
    return {"X-Workspace-Id": workspace_id, "X-User-Id": "user-1"}


def _create_brand_and_channel(client: TestClient, workspace_id: str) -> tuple[dict, dict]:
    brand = client.post(
        "/brands",
        headers=_headers(workspace_id),
        json={
            "name": "Acme Outdoor",
            "category": "outdoor",
            "stage": "growth",
            "target_audience": {"age_ranges": ["25-34"], "gender_skew": "female"},
        },
    )
    assert brand.status_code == 201
    channel = client.post(
        f"/brands/{brand.json()['id']}/channels",
        headers=_headers(workspace_id),
        json={"platform": "xiaohongshu", "account_name": "Acme 小红书"},
    )
    assert channel.status_code == 201
    return brand.json(), channel.json()


def _seed_decision_flow(client: TestClient, workspace_id: str, brand_id: str, channel_id: str) -> dict:
    policy = client.put(
        f"/brands/{brand_id}/policy-configs/active",
        headers=_headers(workspace_id),
        json={
            "policy_name": "baseline_rule_v1",
            "policy_version": "v1",
            "topic_type_targets": {"targets": [{"topic_type": "scenario", "min_ratio": 0.0, "max_ratio": 1.0, "priority_boost": 0.1}]},
        },
    )
    assert policy.status_code == 200
    snapshot = client.post(
        f"/brands/{brand_id}/state-snapshots",
        headers=_headers(workspace_id),
        json={"state_version": "state_v1", "stage": "growth", "state_features": {"audience_focus": "urban commuting"}, "source_version": "v1"},
    )
    assert snapshot.status_code == 201
    source_sync = client.post(
        f"/brands/{brand_id}/source-syncs",
        headers=_headers(workspace_id),
        json={
            "source_type": "xhs_extension_capture",
            "source_adapter": "extension_source_sync_adapter_v1",
            "channel_id": channel_id,
            "capture_payload": {
                "page_type": "search_result",
                "captured_at": "2026-04-11T10:00:00+08:00",
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
        },
    )
    assert source_sync.status_code == 202
    refresh = client.post(
        f"/brands/{brand_id}/topic-pool/refresh",
        headers=_headers(workspace_id),
        json={"archive_threshold_days": 60},
    )
    assert refresh.status_code == 202
    decision = client.post(
        f"/brands/{brand_id}/decisions/run",
        headers=_headers(workspace_id),
        json={"requested_slot_count": 1, "objective": "topic_recommendation", "exploration_mode": "balanced"},
    )
    assert decision.status_code == 201
    return decision.json()


def test_feedback_endpoints_cover_publish_performance_and_evaluation_loop() -> None:
    with TestClient(app) as client:
        workspace = _create_workspace(client)
        brand, channel = _create_brand_and_channel(client, workspace["id"])
        decision = _seed_decision_flow(client, workspace["id"], brand["id"], channel["id"])

        publish = client.post(
            "/publish-records",
            headers=_headers(workspace["id"]),
            json={
                "brand_id": brand["id"],
                "channel_id": channel["id"],
                "decision_event_id": decision["items"][0]["decision_event_id"],
                "decision_batch_id": decision["batch_id"],
                "topic_pool_item_id": decision["items"][0]["topic_pool_item_id"],
                "publish_status": "published",
                "published_at": "2026-04-10T09:30:00+08:00",
            },
        )
        assert publish.status_code == 201
        publish_payload = publish.json()

        publish_list = client.get(f"/brands/{brand['id']}/publish-records", headers=_headers(workspace["id"]))
        assert publish_list.status_code == 200
        assert publish_list.json()["items"][0]["decision_event_id"] == decision["items"][0]["decision_event_id"]

        performance = client.post(
            "/performance/import",
            headers=_headers(workspace["id"]),
            json={
                "publish_record_id": publish_payload["publish_record_id"],
                "observation_window_hours": 168,
                "snapshot_at": "2026-04-17T09:30:00+08:00",
                "reward_version": "reward_v1",
                "metrics": {
                    "impressions": 12000,
                    "clicks": 850,
                    "likes": 320,
                    "comments": 28,
                    "collects": 96,
                    "shares": 31,
                    "follows_gained": 12,
                    "conversion_proxy": {"value": 0.08, "type": "store_click_rate", "source": "manual_import"},
                },
            },
        )
        assert performance.status_code == 201
        assert performance.json()["composite_reward"] > 0

        performance_list = client.get(
            f"/brands/{brand['id']}/performance-snapshots",
            headers=_headers(workspace["id"]),
        )
        assert performance_list.status_code == 200
        assert performance_list.json()["items"][0]["publish_record_id"] == publish_payload["publish_record_id"]

        evaluation = client.post(
            "/evaluation-runs",
            headers=_headers(workspace["id"]),
            json={"brand_id": brand["id"], "evaluation_type": "replay"},
        )
        assert evaluation.status_code == 201
        evaluation_payload = evaluation.json()
        assert evaluation_payload["summary"]["sample_count"] == 1

        latest = client.get(
            f"/brands/{brand['id']}/evaluation-runs/latest",
            headers=_headers(workspace["id"]),
        )
        assert latest.status_code == 200
        assert latest.json()["evaluation_run_id"] == evaluation_payload["evaluation_run_id"]


def test_publish_record_rejects_mismatched_decision_lineage() -> None:
    with TestClient(app) as client:
        workspace = _create_workspace(client)
        brand, channel = _create_brand_and_channel(client, workspace["id"])
        decision = _seed_decision_flow(client, workspace["id"], brand["id"], channel["id"])

        response = client.post(
            "/publish-records",
            headers=_headers(workspace["id"]),
            json={
                "brand_id": brand["id"],
                "channel_id": channel["id"],
                "decision_event_id": decision["items"][0]["decision_event_id"],
                "decision_batch_id": "wrong-batch-id",
                "publish_status": "published",
            },
        )

    assert response.status_code == 422
    assert response.json()["error_code"] == "INVALID_FEEDBACK_PAYLOAD"
