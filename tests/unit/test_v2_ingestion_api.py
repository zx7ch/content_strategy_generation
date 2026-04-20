from __future__ import annotations

import base64

from fastapi.testclient import TestClient
import pytest

from app.api.routes.router import app
from app.v2.foundation import InMemoryMasterDataStore, MasterDataService
from app.v2.ingestion import InMemoryIngestionStore, IngestionService


@pytest.fixture(autouse=True)
def reset_v2_ingestion_app_state():
    master_store = InMemoryMasterDataStore()
    master_service = MasterDataService(master_store)
    ingestion_store = InMemoryIngestionStore()
    ingestion_service = IngestionService(ingestion_store)
    app.state.v2_master_data_store = master_store
    app.state.v2_master_data_service = master_service
    app.state.v2_ingestion_store = ingestion_store
    app.state.v2_ingestion_service = ingestion_service
    yield
    for attr in (
        "v2_master_data_service",
        "v2_master_data_store",
        "v2_ingestion_service",
        "v2_ingestion_store",
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
        json={"name": "Acme Outdoor", "category": "outdoor", "stage": "cold_start"},
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


def test_v2_source_sync_endpoint_accepts_capture_and_persists_evidence() -> None:
    with TestClient(app) as client:
        workspace = _create_workspace(client)
        brand = _create_brand(client, workspace["id"])
        channel = _create_channel(client, workspace["id"], brand["id"])
        response = client.post(
            f"/brands/{brand['id']}/source-syncs",
            headers=_workspace_headers(workspace["id"]),
            json={
                "source_type": "xhs_extension_capture",
                "source_adapter": "extension_source_sync_adapter_v1",
                "channel_id": channel["id"],
                "capture_payload": {
                    "page_type": "search_result",
                    "captured_at": "2026-04-11T10:00:00+08:00",
                    "items": [
                        {
                            "note_id": "abc123",
                            "source_url": "https://www.xiaohongshu.com/explore/abc123",
                            "title": "轻量徒步装备",
                            "visible_text_excerpt": "正文摘要",
                            "author_handle": "competitor-a",
                            "likes": 10,
                            "comments": 2,
                            "collects": 5,
                        }
                    ],
                },
            },
        )

    assert response.status_code == 202
    payload = response.json()
    assert payload["entry_type"] == "source_sync"
    assert payload["status"] == "accepted"
    assert payload["imported_item_count"] == 1

    store = app.state.v2_ingestion_store
    assert len(store.list_content_items(brand["id"])) == 1


def test_v2_data_import_endpoint_validates_required_fields() -> None:
    with TestClient(app) as client:
        workspace = _create_workspace(client)
        brand = _create_brand(client, workspace["id"])
        response = client.post(
            f"/brands/{brand['id']}/data-imports",
            headers=_workspace_headers(workspace["id"]),
            json={
                "import_type": "historical_note_import_v1",
                "platform": "xiaohongshu",
                "rows": [
                    {
                        "published_at": "2025-09-10T12:00:00+08:00",
                        "title": "缺正文",
                        "body_text": "",
                        "likes": 1,
                        "collects": 2,
                        "comments": 3,
                    }
                ],
            },
        )

    assert response.status_code == 422
    payload = response.json()
    assert payload["error_code"] == "INVALID_INGESTION_PAYLOAD"


def test_v2_ingestion_routes_reject_cross_workspace_scope() -> None:
    with TestClient(app) as client:
        workspace_a = _create_workspace(client, name="Acme", slug="acme")
        workspace_b = _create_workspace(client, name="Beta", slug="beta")
        brand = _create_brand(client, workspace_a["id"])

        response = client.post(
            f"/brands/{brand['id']}/data-imports",
            headers=_workspace_headers(workspace_b["id"]),
            json={
                "import_type": "historical_note_import_v1",
                "platform": "xiaohongshu",
                "rows": [
                    {
                        "published_at": "2025-09-10T12:00:00+08:00",
                        "title": "换季敏感肌稳定住了",
                        "body_text": "正文内容",
                        "likes": 320,
                        "collects": 96,
                        "comments": 28,
                    }
                ],
            },
        )

    assert response.status_code == 403
    payload = response.json()
    assert payload["error_code"] == "WORKSPACE_SCOPE_MISMATCH"


def test_v2_extension_capture_session_flow_returns_preview_and_receipt() -> None:
    with TestClient(app) as client:
        workspace = _create_workspace(client)
        brand = _create_brand(client, workspace["id"])
        channel = _create_channel(client, workspace["id"], brand["id"])

        create_response = client.post(
            f"/brands/{brand['id']}/extension-capture-sessions",
            headers=_workspace_headers(workspace["id"]),
            json={"channel_id": channel["id"]},
        )
        assert create_response.status_code == 201
        created = create_response.json()

        submit_response = client.post(
            "/extension-captures",
            headers=_workspace_headers(workspace["id"]),
            json={
                "capture_session_id": created["capture_session_id"],
                "capture_token": created["capture_token"],
                "capture_payload": {
                    "page_type": "search_result",
                    "captured_at": "2026-04-11T10:00:00+08:00",
                    "items": [
                        {
                            "note_id": "abc123",
                            "source_url": "https://www.xiaohongshu.com/explore/abc123",
                            "title": "轻量徒步装备",
                            "visible_text_excerpt": "正文摘要",
                            "author_handle": "competitor-a",
                            "likes": 10,
                            "comments": 2,
                            "collects": 5,
                        }
                    ],
                },
            },
        )
        assert submit_response.status_code == 202

        get_response = client.get(
            f"/brands/{brand['id']}/extension-capture-sessions/{created['capture_session_id']}",
            headers=_workspace_headers(workspace["id"]),
        )

    assert get_response.status_code == 200
    payload = get_response.json()
    assert payload["status"] == "accepted"
    assert payload["preview_payload"]["source_type"] == "xhs_extension_capture"
    assert payload["ingestion_receipt"]["entry_type"] == "source_sync"


def test_v2_data_import_preview_flow_returns_preview_and_receipt() -> None:
    with TestClient(app) as client:
        workspace = _create_workspace(client)
        brand = _create_brand(client, workspace["id"])

        response = client.post(
            f"/brands/{brand['id']}/data-import-previews",
            headers=_workspace_headers(workspace["id"]),
            json={
                "file_name": "historical-import.json",
                "import_type": "historical_note_import_v1",
                "platform": "xiaohongshu",
                "rows": [
                    {
                        "published_at": "2025-09-10T12:00:00+08:00",
                        "title": "换季敏感肌稳定住了",
                        "body_text": "正文内容",
                        "likes": 320,
                        "collects": 96,
                        "comments": 28,
                    }
                ],
            },
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "accepted"
    assert payload["preview_payload"]["import_type"] == "historical_note_import_v1"
    assert payload["ingestion_receipt"]["entry_type"] == "data_import"


def test_v2_data_import_preview_accepts_uploaded_file_payload() -> None:
    with TestClient(app) as client:
        workspace = _create_workspace(client)
        brand = _create_brand(client, workspace["id"])

        response = client.post(
            f"/brands/{brand['id']}/data-import-previews",
            headers=_workspace_headers(workspace["id"]),
            json={
                "file_name": "historical-import.csv",
                "import_type": "historical_note_import_v1",
                "platform": "xiaohongshu",
                "file_content_base64": base64.b64encode(
                    (
                        "published_at,title,body_text,likes,collects,comments\n"
                        "2025-09-10T12:00:00+08:00,换季敏感肌稳定住了,正文内容,320,96,28\n"
                    ).encode("utf-8")
                ).decode("ascii"),
            },
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["parsed_row_count"] == 1
    assert payload["preview_payload"]["rows"][0]["title"] == "换季敏感肌稳定住了"


def test_v2_retry_endpoints_reuse_current_preview_payloads() -> None:
    with TestClient(app) as client:
        workspace = _create_workspace(client)
        brand = _create_brand(client, workspace["id"])
        channel = _create_channel(client, workspace["id"], brand["id"])

        session_response = client.post(
            f"/brands/{brand['id']}/extension-capture-sessions",
            headers=_workspace_headers(workspace["id"]),
            json={"channel_id": channel["id"]},
        )
        session = session_response.json()
        client.post(
            "/extension-captures",
            headers=_workspace_headers(workspace["id"]),
            json={
                "capture_session_id": session["capture_session_id"],
                "capture_token": session["capture_token"],
                "capture_payload": {
                    "page_type": "search_result",
                    "captured_at": "2026-04-11T10:00:00+08:00",
                    "items": [
                        {
                            "note_id": "abc123",
                            "source_url": "https://www.xiaohongshu.com/explore/abc123",
                            "title": "轻量徒步装备",
                            "visible_text_excerpt": "正文摘要",
                            "author_handle": "competitor-a",
                            "likes": 10,
                            "comments": 2,
                            "collects": 5,
                        }
                    ],
                },
            },
        )

        preview_response = client.post(
            f"/brands/{brand['id']}/data-import-previews",
            headers=_workspace_headers(workspace["id"]),
            json={
                "file_name": "historical-import.json",
                "import_type": "historical_note_import_v1",
                "platform": "xiaohongshu",
                "rows": [
                    {
                        "published_at": "2025-09-10T12:00:00+08:00",
                        "title": "换季敏感肌稳定住了",
                        "body_text": "正文内容",
                        "likes": 320,
                        "collects": 96,
                        "comments": 28,
                    }
                ],
            },
        )
        preview = preview_response.json()

        retry_session_response = client.post(
            f"/brands/{brand['id']}/extension-capture-sessions/{session['capture_session_id']}/retry-sync",
            headers=_workspace_headers(workspace["id"]),
        )
        retry_preview_response = client.post(
            f"/brands/{brand['id']}/data-import-previews/{preview['preview_id']}/retry-sync",
            headers=_workspace_headers(workspace["id"]),
        )

    assert retry_session_response.status_code == 202
    assert retry_session_response.json()["status"] == "accepted"
    assert retry_preview_response.status_code == 202
    assert retry_preview_response.json()["status"] == "accepted"
