from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from app.api.routes.router import app
from app.v2.feedback.store import InMemoryFeedbackStore
from app.v2.foundation import InMemoryMasterDataStore, MasterDataService
from app.v2.ingestion import InMemoryIngestionStore, IngestionService
from app.v2.topic_pool import InMemoryTopicPoolStore, ScorerService, TopicPoolService


@pytest.fixture(autouse=True)
def reset_v2_topic_pool_app_state():
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
    app.state.v2_master_data_store = master_store
    app.state.v2_master_data_service = master_service
    app.state.v2_ingestion_store = ingestion_store
    app.state.v2_ingestion_service = ingestion_service
    app.state.v2_topic_pool_store = topic_pool_store
    app.state.v2_topic_pool_service = topic_pool_service
    yield
    for attr in (
        "v2_master_data_service",
        "v2_master_data_store",
        "v2_ingestion_service",
        "v2_ingestion_store",
        "v2_topic_pool_service",
        "v2_topic_pool_store",
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
        json={
            "platform": "xiaohongshu",
            "account_name": "Acme Outdoor",
            "profile_url": "https://www.xiaohongshu.com/user/profile/acme-outdoor",
        },
    )
    assert response.status_code == 201
    return response.json()


def _seed_topic_pool_evidence(client: TestClient, workspace_id: str, brand_id: str) -> None:
    channel = _create_channel(client, workspace_id, brand_id)
    response = client.post(
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
                    }
                ],
            },
        },
    )
    assert response.status_code == 202


def test_v2_topic_pool_refresh_and_list_endpoints_return_live_candidate_inventory() -> None:
    with TestClient(app) as client:
        workspace = _create_workspace(client)
        brand = _create_brand(client, workspace["id"])
        _seed_topic_pool_evidence(client, workspace["id"], brand["id"])

        refresh_response = client.post(
            f"/brands/{brand['id']}/topic-pool/refresh",
            headers=_workspace_headers(workspace["id"]),
            json={"archive_threshold_days": 60},
        )
        list_response = client.get(
            f"/brands/{brand['id']}/topic-pool",
            headers=_workspace_headers(workspace["id"]),
        )

    assert refresh_response.status_code == 202
    refresh_payload = refresh_response.json()
    assert refresh_payload["status"] == "completed"
    assert refresh_payload["generated_item_count"] >= 1

    assert list_response.status_code == 200
    payload = list_response.json()
    assert payload["brand"]["id"] == brand["id"]
    assert payload["brand"]["stage"] == "growth"
    assert payload["stats"]["total_candidate_count"] >= 1
    assert payload["items"][0]["topic_type"] == "scenario"
    assert payload["items"][0]["evidence_summary"]["source_count"] >= 1
    assert payload["items"][0]["source_agent"] == "topic_hypothesis_agent"
    assert payload["items"][0]["score_breakdown"]["final_score"] == payload["items"][0]["final_score"]
    assert payload["items"][0]["score_breakdown"]["novelty_score"] > 0
    assert payload["items"][0]["score_breakdown"]["fit_score"] > 0
    assert payload["items"][0]["evidence_provenance"][0]["original_title"]
    assert payload["items"][0]["evidence_provenance"][0]["source_url"].startswith("https://")
    assert payload["items"][0]["evidence_provenance"][0]["signal_type"]
    assert payload["items"][0]["evidence_provenance"][0]["contribution_weight"] > 0


def test_v2_topic_pool_routes_reject_cross_workspace_scope() -> None:
    with TestClient(app) as client:
        workspace_a = _create_workspace(client, name="Acme", slug="acme")
        workspace_b = _create_workspace(client, name="Beta", slug="beta")
        brand = _create_brand(client, workspace_a["id"])
        _seed_topic_pool_evidence(client, workspace_a["id"], brand["id"])

        response = client.post(
            f"/brands/{brand['id']}/topic-pool/refresh",
            headers=_workspace_headers(workspace_b["id"]),
            json={"archive_threshold_days": 60},
        )

    assert response.status_code == 403
    payload = response.json()
    assert payload["error_code"] == "WORKSPACE_SCOPE_MISMATCH"
