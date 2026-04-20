from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from app.api.routes.router import app
from app.v2.decision import DecisionService, InMemoryDecisionStore
from app.v2.feedback.store import InMemoryFeedbackStore
from app.v2.foundation import InMemoryMasterDataStore, MasterDataService
from app.v2.ingestion import InMemoryIngestionStore, IngestionService
from app.v2.topic_pool import InMemoryTopicPoolStore, ScorerService, TopicPoolService


@pytest.fixture(autouse=True)
def reset_v2_decision_app_state():
    master_store = InMemoryMasterDataStore()
    master_service = MasterDataService(master_store)
    ingestion_store = InMemoryIngestionStore()
    ingestion_service = IngestionService(ingestion_store)
    topic_pool_store = InMemoryTopicPoolStore()
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
    decision_store = InMemoryDecisionStore()
    decision_service = DecisionService(
        master_data_service=master_service,
        topic_pool_store=topic_pool_store,
        decision_store=decision_store,
        scorer_service=scorer_service,
    )
    app.state.v2_master_data_store = master_store
    app.state.v2_master_data_service = master_service
    app.state.v2_ingestion_store = ingestion_store
    app.state.v2_ingestion_service = ingestion_service
    app.state.v2_topic_pool_store = topic_pool_store
    app.state.v2_topic_pool_service = topic_pool_service
    app.state.v2_decision_store = decision_store
    app.state.v2_decision_service = decision_service
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
    ):
        if hasattr(app.state, attr):
            delattr(app.state, attr)


def _create_workspace(client: TestClient, *, name: str = "Acme", slug: str = "acme") -> dict:
    response = client.post(
        "/workspaces",
        json={"name": name, "slug": slug, "timezone": "Asia/Shanghai"},
    )
    assert response.status_code == 201
    return response.json()


def _workspace_headers(workspace_id: str) -> dict[str, str]:
    return {
        "X-Workspace-Id": workspace_id,
        "X-User-Id": "user-1",
    }


def _create_brand(client: TestClient, workspace_id: str) -> dict:
    response = client.post(
        "/brands",
        headers=_workspace_headers(workspace_id),
        json={
            "name": "Acme Outdoor",
            "category": "outdoor",
            "stage": "growth",
            "target_audience": {"age_ranges": ["25-34"], "gender_skew": "female"},
        },
    )
    assert response.status_code == 201
    return response.json()


def _create_channel(client: TestClient, workspace_id: str, brand_id: str) -> dict:
    response = client.post(
        f"/brands/{brand_id}/channels",
        headers=_workspace_headers(workspace_id),
        json={"platform": "xiaohongshu", "account_name": "Acme 小红书"},
    )
    assert response.status_code == 201
    return response.json()


def _seed_policy_and_snapshot(client: TestClient, workspace_id: str, brand_id: str) -> None:
    policy_response = client.put(
        f"/brands/{brand_id}/policy-configs/active",
        headers=_workspace_headers(workspace_id),
        json={
            "policy_name": "baseline_rule_v1",
            "policy_version": "v1",
            "topic_type_targets": {
                "targets": [
                    {"topic_type": "scenario", "min_ratio": 0.34, "max_ratio": 0.7, "priority_boost": 0.12},
                    {"topic_type": "problem", "min_ratio": 0.0, "max_ratio": 0.5, "priority_boost": 0.03},
                ]
            },
        },
    )
    assert policy_response.status_code == 200
    snapshot_response = client.post(
        f"/brands/{brand_id}/state-snapshots",
        headers=_workspace_headers(workspace_id),
        json={
            "state_version": "state_v1",
            "stage": "growth",
            "state_features": {"audience_focus": "urban commuting"},
            "source_version": "v1",
        },
    )
    assert snapshot_response.status_code == 201


def _seed_topic_pool_evidence(client: TestClient, workspace_id: str, brand_id: str) -> None:
    channel = _create_channel(client, workspace_id, brand_id)
    source_sync = client.post(
        f"/brands/{brand_id}/source-syncs",
        headers=_workspace_headers(workspace_id),
        json={
            "source_type": "xhs_extension_capture",
            "source_adapter": "extension_source_sync_adapter_v1",
            "channel_id": channel["id"],
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
                    },
                    {
                        "note_id": "note-2",
                        "source_url": "https://www.xiaohongshu.com/explore/note-2",
                        "title": "尺码痛点怎么避坑",
                        "visible_text_excerpt": "买鞋最怕前掌挤脚和后跟磨脚",
                        "author_handle": "competitor-b",
                        "likes": 116,
                        "comments": 25,
                        "collects": 48,
                        "shares": 6,
                        "tags": ["避坑", "尺码"],
                    },
                    {
                        "note_id": "note-3",
                        "source_url": "https://www.xiaohongshu.com/explore/note-3",
                        "title": "周末轻徒步穿搭清单",
                        "visible_text_excerpt": "从鞋包到外套的一套轻量方案",
                        "author_handle": "competitor-c",
                        "likes": 104,
                        "comments": 14,
                        "collects": 41,
                        "shares": 5,
                        "tags": ["穿搭", "徒步"],
                    },
                ],
            },
        },
    )
    assert source_sync.status_code == 202
    refresh = client.post(
        f"/brands/{brand_id}/topic-pool/refresh",
        headers=_workspace_headers(workspace_id),
        json={"archive_threshold_days": 60},
    )
    assert refresh.status_code == 202


def test_v2_decision_run_and_review_endpoints_persist_live_batch_flow() -> None:
    with TestClient(app) as client:
        workspace = _create_workspace(client)
        brand = _create_brand(client, workspace["id"])
        _seed_policy_and_snapshot(client, workspace["id"], brand["id"])
        _seed_topic_pool_evidence(client, workspace["id"], brand["id"])

        run_response = client.post(
            f"/brands/{brand['id']}/decisions/run",
            headers=_workspace_headers(workspace["id"]),
            json={"requested_slot_count": 3, "objective": "topic_recommendation", "exploration_mode": "balanced"},
        )
        assert run_response.status_code == 201
        run_payload = run_response.json()
        assert run_payload["chosen_count"] >= 1
        assert len(run_payload["items"]) == run_payload["chosen_count"]

        review_response = client.patch(
            f"/decision-batches/{run_payload['batch_id']}/items/0",
            headers=_workspace_headers(workspace["id"]),
            json={
                "review_action": "edit_and_accept",
                "edited_title": "编辑后的选题标题",
                "review_notes": "更适合本品牌语气",
            },
        )
        get_response = client.get(
            f"/decision-batches/{run_payload['batch_id']}",
            headers=_workspace_headers(workspace["id"]),
        )
        latest_response = client.get(
            f"/brands/{brand['id']}/decision-batches/latest",
            headers=_workspace_headers(workspace["id"]),
        )

    assert review_response.status_code == 200
    reviewed = review_response.json()
    assert reviewed["review_status"] == "edit_and_accept"
    assert reviewed["title"] == "编辑后的选题标题"
    assert get_response.status_code == 200
    assert latest_response.status_code == 200
    get_payload = get_response.json()
    latest_payload = latest_response.json()
    assert get_payload["batch_id"] == run_payload["batch_id"]
    assert get_payload["items"][0]["title"] == "编辑后的选题标题"
    assert latest_payload["batch_id"] == run_payload["batch_id"]
    assert latest_payload["items"][0]["review_status"] == "edit_and_accept"


def test_v2_decision_run_requires_policy_and_snapshot() -> None:
    with TestClient(app) as client:
        workspace = _create_workspace(client)
        brand = _create_brand(client, workspace["id"])
        _seed_topic_pool_evidence(client, workspace["id"], brand["id"])

        response = client.post(
            f"/brands/{brand['id']}/decisions/run",
            headers=_workspace_headers(workspace["id"]),
            json={"requested_slot_count": 3},
        )

    assert response.status_code == 404
    payload = response.json()
    assert payload["error_code"] == "MASTER_DATA_NOT_FOUND"


def test_v2_decision_review_rejects_cross_workspace_scope() -> None:
    with TestClient(app) as client:
        workspace_a = _create_workspace(client, name="Acme", slug="acme")
        workspace_b = _create_workspace(client, name="Beta", slug="beta")
        brand = _create_brand(client, workspace_a["id"])
        _seed_policy_and_snapshot(client, workspace_a["id"], brand["id"])
        _seed_topic_pool_evidence(client, workspace_a["id"], brand["id"])
        run_response = client.post(
            f"/brands/{brand['id']}/decisions/run",
            headers=_workspace_headers(workspace_a["id"]),
            json={"requested_slot_count": 1},
        )
        assert run_response.status_code == 201
        batch_id = run_response.json()["batch_id"]

        response = client.patch(
            f"/decision-batches/{batch_id}/items/0",
            headers=_workspace_headers(workspace_b["id"]),
            json={"review_action": "reject"},
        )

    assert response.status_code == 403
    payload = response.json()
    assert payload["error_code"] == "WORKSPACE_SCOPE_MISMATCH"


def test_v2_decision_read_rejects_cross_workspace_scope() -> None:
    with TestClient(app) as client:
        workspace_a = _create_workspace(client, name="Acme", slug="acme")
        workspace_b = _create_workspace(client, name="Beta", slug="beta")
        brand = _create_brand(client, workspace_a["id"])
        _seed_policy_and_snapshot(client, workspace_a["id"], brand["id"])
        _seed_topic_pool_evidence(client, workspace_a["id"], brand["id"])
        run_response = client.post(
            f"/brands/{brand['id']}/decisions/run",
            headers=_workspace_headers(workspace_a["id"]),
            json={"requested_slot_count": 1},
        )
        assert run_response.status_code == 201
        batch_id = run_response.json()["batch_id"]

        by_id_response = client.get(
            f"/decision-batches/{batch_id}",
            headers=_workspace_headers(workspace_b["id"]),
        )
        latest_response = client.get(
            f"/brands/{brand['id']}/decision-batches/latest",
            headers=_workspace_headers(workspace_b["id"]),
        )

    assert by_id_response.status_code == 403
    assert by_id_response.json()["error_code"] == "WORKSPACE_SCOPE_MISMATCH"
    assert latest_response.status_code == 403
    assert latest_response.json()["error_code"] == "WORKSPACE_SCOPE_MISMATCH"
