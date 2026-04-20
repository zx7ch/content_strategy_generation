from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from app.api.routes.router import app
from app.v2.foundation import InMemoryMasterDataStore, MasterDataService


@pytest.fixture(autouse=True)
def reset_v2_app_state():
    store = InMemoryMasterDataStore()
    service = MasterDataService(store)
    app.state.v2_master_data_store = store
    app.state.v2_master_data_service = service
    yield
    for attr in ("v2_master_data_service", "v2_master_data_store"):
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


def test_v2_workspace_brand_and_channel_routes_share_master_data_scope() -> None:
    with TestClient(app) as client:
        workspace = _create_workspace(client)
        headers = _workspace_headers(workspace["id"])

        brand_response = client.post(
            "/brands",
            headers=headers,
            json={
                "name": "Acme Outdoor",
                "category": "outdoor",
                "stage": "cold_start",
                "target_audience": {"age_ranges": ["25-34"]},
            },
        )
        assert brand_response.status_code == 201
        brand = brand_response.json()
        assert brand["workspace_id"] == workspace["id"]
        assert brand["name"] == "Acme Outdoor"

        channel_response = client.post(
            f"/brands/{brand['id']}/channels",
            headers=headers,
            json={
                "platform": "xhs",
                "account_name": "Acme Outdoor",
                "profile_url": "https://www.xiaohongshu.com/user/profile/acme-outdoor",
                "metadata": {"owner_type": "brand"},
            },
        )
        assert channel_response.status_code == 201
        channel = channel_response.json()
        assert channel["workspace_id"] == workspace["id"]
        assert channel["brand_id"] == brand["id"]
        assert channel["account_name"] == "Acme Outdoor"
        assert channel["profile_url"] == "https://www.xiaohongshu.com/user/profile/acme-outdoor"

        listed_brands = client.get("/brands", headers=headers)
        listed_channels = client.get(f"/brands/{brand['id']}/channels", headers=headers)

        assert listed_brands.status_code == 200
        assert len(listed_brands.json()["items"]) == 1
        assert listed_brands.json()["items"][0]["id"] == brand["id"]

        assert listed_channels.status_code == 200
        assert len(listed_channels.json()["items"]) == 1
        assert listed_channels.json()["items"][0]["id"] == channel["id"]


def test_v2_brand_routes_require_workspace_auth_headers() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/brands",
            json={
                "name": "Acme Outdoor",
                "category": "outdoor",
                "stage": "cold_start",
            },
        )

    assert response.status_code == 401
    payload = response.json()
    assert payload["error_code"] == "WORKSPACE_AUTH_REQUIRED"


def test_v2_brand_routes_reject_cross_workspace_scope() -> None:
    with TestClient(app) as client:
        primary_workspace = _create_workspace(client, name="Acme", slug="acme")
        secondary_workspace = _create_workspace(client, name="Beta", slug="beta")

        brand_response = client.post(
            "/brands",
            headers=_workspace_headers(primary_workspace["id"]),
            json={
                "name": "Acme Outdoor",
                "category": "outdoor",
                "stage": "cold_start",
            },
        )
        brand_id = brand_response.json()["id"]

        forbidden = client.post(
            f"/brands/{brand_id}/channels",
            headers=_workspace_headers(secondary_workspace["id"]),
            json={"platform": "xhs"},
        )

    assert forbidden.status_code == 403
    payload = forbidden.json()
    assert payload["error_code"] == "WORKSPACE_SCOPE_MISMATCH"


def test_v2_active_policy_config_can_be_replaced_and_fetched() -> None:
    with TestClient(app) as client:
        workspace = _create_workspace(client)
        headers = _workspace_headers(workspace["id"])
        brand_response = client.post(
            "/brands",
            headers=headers,
            json={
                "name": "Acme Outdoor",
                "category": "outdoor",
                "stage": "cold_start",
            },
        )
        brand_id = brand_response.json()["id"]

        first = client.put(
            f"/brands/{brand_id}/policy-configs/active",
            headers=headers,
            json={
                "policy_name": "baseline_rule_v1",
                "policy_version": "v1",
                "topic_type_targets": {
                    "targets": [
                        {"topic_type": "scenario", "min_ratio": 0.3, "max_ratio": 0.6, "priority_boost": 0.1}
                    ]
                },
            },
        )
        second = client.put(
            f"/brands/{brand_id}/policy-configs/active",
            headers=headers,
            json={
                "policy_name": "baseline_rule_v1",
                "policy_version": "v2",
                "topic_type_targets": {
                    "targets": [
                        {"topic_type": "scenario", "min_ratio": 0.2, "max_ratio": 0.5, "priority_boost": 0.1}
                    ]
                },
            },
        )
        fetched = client.get(f"/brands/{brand_id}/policy-configs/active", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert fetched.status_code == 200
    assert first.json()["id"] != second.json()["id"]
    assert fetched.json()["id"] == second.json()["id"]
    assert fetched.json()["policy_version"] == "v2"
    assert fetched.json()["is_active"] is True


def test_v2_policy_validation_and_state_snapshot_routes() -> None:
    with TestClient(app) as client:
        workspace = _create_workspace(client)
        headers = _workspace_headers(workspace["id"])
        brand_response = client.post(
            "/brands",
            headers=headers,
            json={
                "name": "Acme Outdoor",
                "category": "outdoor",
                "stage": "cold_start",
            },
        )
        brand_id = brand_response.json()["id"]

        invalid_policy = client.put(
            f"/brands/{brand_id}/policy-configs/active",
            headers=headers,
            json={
                "policy_name": "baseline_rule_v1",
                "policy_version": "v1",
                "topic_type_targets": {
                    "targets": [
                        {"topic_type": "scenario", "min_ratio": 0.8, "max_ratio": 0.9},
                        {"topic_type": "problem", "min_ratio": 0.4, "max_ratio": 0.6},
                    ]
                },
            },
        )
        snapshot = client.post(
            f"/brands/{brand_id}/state-snapshots",
            headers=headers,
            json={
                "state_version": "state_v1",
                "stage": "cold_start",
                "state_features": {"recent_post_count_90d": 0},
            },
        )
        snapshot_list = client.get(f"/brands/{brand_id}/state-snapshots", headers=headers)

    assert invalid_policy.status_code == 422
    invalid_payload = invalid_policy.json()
    assert invalid_payload["error_code"] == "INVALID_MASTER_DATA_PAYLOAD"

    assert snapshot.status_code == 201
    snapshot_payload = snapshot.json()
    assert snapshot_payload["brand_id"] == brand_id
    assert snapshot_payload["workspace_id"] == workspace["id"]
    assert snapshot_payload["state_version"] == "state_v1"
    assert snapshot_list.status_code == 200
    assert snapshot_list.json()["items"][0]["id"] == snapshot_payload["id"]


def test_v2_brand_list_is_workspace_scoped() -> None:
    with TestClient(app) as client:
        workspace_a = _create_workspace(client, name="Acme", slug="acme")
        workspace_b = _create_workspace(client, name="Beta", slug="beta")

        response_a = client.post(
            "/brands",
            headers=_workspace_headers(workspace_a["id"]),
            json={"name": "Acme Outdoor", "category": "outdoor", "stage": "cold_start"},
        )
        response_b = client.post(
            "/brands",
            headers=_workspace_headers(workspace_b["id"]),
            json={"name": "Beta Beauty", "category": "beauty", "stage": "growth"},
        )

        list_a = client.get("/brands", headers=_workspace_headers(workspace_a["id"]))
        list_b = client.get("/brands", headers=_workspace_headers(workspace_b["id"]))

    assert response_a.status_code == 201
    assert response_b.status_code == 201
    assert list_a.status_code == 200
    assert list_b.status_code == 200
    assert [item["name"] for item in list_a.json()["items"]] == ["Acme Outdoor"]
    assert [item["name"] for item in list_b.json()["items"]] == ["Beta Beauty"]


def test_v2_policy_validation_rejects_typoed_topic_type_targets_shape() -> None:
    with TestClient(app) as client:
        workspace = _create_workspace(client)
        headers = _workspace_headers(workspace["id"])
        brand_response = client.post(
            "/brands",
            headers=headers,
            json={
                "name": "Acme Outdoor",
                "category": "outdoor",
                "stage": "cold_start",
            },
        )
        brand_id = brand_response.json()["id"]

        invalid_policy = client.put(
            f"/brands/{brand_id}/policy-configs/active",
            headers=headers,
            json={
                "policy_name": "baseline_rule_v1",
                "policy_version": "v1",
                "topic_type_targets": {"target": []},
            },
        )

    assert invalid_policy.status_code == 422
    payload = invalid_policy.json()
    assert payload["error_code"] == "INVALID_MASTER_DATA_PAYLOAD"
    assert "unsupported keys" in payload["error_message"]
