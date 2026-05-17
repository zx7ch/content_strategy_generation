from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.routes.router import app, stream_session_events
from app.config import settings
from app.memory.job_store import JobStore
from app.memory.session_state import SessionManager
from app.models.session import SessionLifecycleState


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "router.db"
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", str(db_path))
    monkeypatch.setattr(settings, "SSE_HEARTBEAT_SECONDS", 1)
    return str(db_path)


def _create_session_via_api(client: TestClient, *, user_query: str = "护肤") -> str:
    response = client.post(
        "/sessions",
        json={"user_id": "u1", "user_query": user_query, "platform": "xiaohongshu", "mode": "editing"},
    )
    assert response.status_code == 201
    return response.json()["session_id"]


async def _set_session_state(db_path: str, session_id: str, **fields) -> None:
    async with SessionManager(db_path) as manager:
        await manager.update_session(session_id, **fields)


class _FakeRequest:
    async def is_disconnected(self) -> bool:
        return True


def test_post_sessions_returns_201_with_init_state(isolated_db):
    with TestClient(app) as client:
        response = client.post(
            "/sessions",
            json={"user_id": "u1", "user_query": "巴黎户外穿搭", "platform": "xiaohongshu", "mode": "editing"},
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["user_id"] == "u1"
    assert payload["user_query"] == "巴黎户外穿搭"
    assert payload["stage"] == "init"
    assert payload["lifecycle_state"] == "alive"
    assert payload["session_id"]


def test_v2_routes_allow_local_frontend_cors() -> None:
    origin = "http://127.0.0.1:3000"

    with TestClient(app) as client:
        default_workspace = client.get("/workspaces/default", headers={"Origin": origin})
        preflight = client.options(
            "/brands",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "X-Workspace-Id, X-User-Id",
            },
        )

    assert default_workspace.status_code == 200
    assert default_workspace.headers["access-control-allow-origin"] == origin
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == origin
    assert "X-Workspace-Id" in preflight.headers["access-control-allow-headers"]
    assert "X-User-Id" in preflight.headers["access-control-allow-headers"]


def test_health_returns_minimum_local_runtime_contract() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["service"] == "xhs-agent-runtime"
    assert payload["status"] == "healthy"
    assert payload["version"] == "0.1.0"
    assert payload["api_contract"] == "local-runtime-v1"
    assert payload["queue"] == "active"


def test_private_network_preflight_sets_allow_header() -> None:
    origin = "http://127.0.0.1:3000"

    with TestClient(app) as client:
        response = client.options(
            "/health",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Private-Network": "true",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-private-network"] == "true"


def test_strategy_and_generate_enforce_stage_contract(isolated_db):
    with TestClient(app) as client:
        session_id = _create_session_via_api(client)

        strategy_response = client.post(f"/sessions/{session_id}/strategy", json={"foo": "bar"})
        assert strategy_response.status_code == 202
        assert strategy_response.json()["stage"] == "strategy"

        repeated_strategy = client.post(f"/sessions/{session_id}/strategy", json={"foo": "bar"})
        assert repeated_strategy.status_code == 409
        assert repeated_strategy.json()["error_code"] == "INVALID_STAGE"

        generate_response = client.post(f"/sessions/{session_id}/generate", json={"topic": "护肤"})
        assert generate_response.status_code == 202
        assert generate_response.json()["stage"] == "generation"


def test_generate_without_strategy_returns_409(isolated_db):
    with TestClient(app) as client:
        session_id = _create_session_via_api(client)
        response = client.post(f"/sessions/{session_id}/generate", json={"topic": "护肤"})

    assert response.status_code == 409
    payload = response.json()
    assert payload["error_code"] == "INVALID_STAGE"
    assert payload["error_details"]["current_stage"] == "init"


def test_resume_is_idempotent(isolated_db):
    with TestClient(app) as client:
        session_id = _create_session_via_api(client)
        client.post(f"/sessions/{session_id}/strategy", json={"foo": "bar"})

    async def _pause_job():
        async with JobStore(isolated_db) as store:
            await store.pause_session_jobs(session_id)

    asyncio.run(_pause_job())

    with TestClient(app) as client:
        first = client.post(f"/sessions/{session_id}/resume")
        second = client.post(f"/sessions/{session_id}/resume")

    assert first.status_code == 200
    assert first.json()["resumed_jobs"] == 1
    assert second.status_code == 200
    assert second.json()["resumed_jobs"] == 0


def test_get_session_returns_200_404_and_410(isolated_db):
    missing_id = str(uuid.uuid4())

    with TestClient(app) as client:
        session_id = _create_session_via_api(client)

        ok = client.get(f"/sessions/{session_id}")
        assert ok.status_code == 200
        assert ok.json()["stage"] == "init"

        missing = client.get(f"/sessions/{missing_id}")
        assert missing.status_code == 404
        assert missing.json()["error_code"] == "SESSION_NOT_FOUND"

    asyncio.run(
        _set_session_state(
            isolated_db,
            session_id,
            lifecycle_state=SessionLifecycleState.PURGED,
            purged_at="2026-03-17T00:00:00",
        )
    )

    with TestClient(app) as client:
        purged = client.get(f"/sessions/{session_id}")

    assert purged.status_code == 410
    assert purged.json()["error_code"] == "SESSION_PURGED"


def test_get_session_uses_runtime_budget_fields_from_similarity_report(isolated_db):
    with TestClient(app) as client:
        session_id = _create_session_via_api(client)

    asyncio.run(
        _set_session_state(
            isolated_db,
            session_id,
            similarity_report={
                "token_used": 321,
                "token_budget": 1200,
                "budget_remaining": 879,
                "budget_degraded": True,
            },
        )
    )

    with TestClient(app) as client:
        response = client.get(f"/sessions/{session_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["token_used"] == 321
    assert payload["token_budget"] == 1200
    assert payload["budget_remaining"] == 879
    assert payload["budget_degraded"] is True


def test_get_job_returns_200_and_404_with_uniform_error_schema(isolated_db):
    with TestClient(app) as client:
        session_id = _create_session_via_api(client)
        enqueue = client.post(f"/sessions/{session_id}/strategy", json={"foo": "bar"})
        job_id = enqueue.json()["job_id"]

        ok = client.get(f"/jobs/{job_id}")
        assert ok.status_code == 200
        assert ok.json()["job_id"] == job_id

        missing = client.get("/jobs/job_missing")

    assert missing.status_code == 404
    payload = missing.json()
    assert payload["error_code"] == "JOB_NOT_FOUND"
    assert payload["error_message"]
    assert "retryable" in payload
    assert "suggested_action" in payload


def test_strategy_on_frozen_session_returns_423(isolated_db):
    with TestClient(app) as client:
        session_id = _create_session_via_api(client)

    asyncio.run(
        _set_session_state(
            isolated_db,
            session_id,
            last_user_activity_at=(datetime.utcnow() - timedelta(days=2)).isoformat(),
        )
    )

    with TestClient(app) as client:
        response = client.post(f"/sessions/{session_id}/strategy", json={"foo": "bar"})

    assert response.status_code == 423
    assert response.json()["error_code"] == "SESSION_FROZEN"


def test_sse_endpoint_returns_event_stream_response(isolated_db):
    with TestClient(app) as client:
        session_id = _create_session_via_api(client)
        client.post(f"/sessions/{session_id}/strategy", json={"foo": "bar"})

    response = asyncio.run(stream_session_events(session_id, _FakeRequest(), last_event_id=None))
    replay_response = asyncio.run(stream_session_events(session_id, _FakeRequest(), last_event_id="1"))

    assert response.media_type == "text/event-stream"
    assert response.headers["Cache-Control"] == "no-cache"
    assert response.headers["Connection"] == "keep-alive"
    assert replay_response.media_type == "text/event-stream"


def test_sse_invalid_last_event_id_returns_uniform_error_schema(isolated_db):
    with TestClient(app) as client:
        session_id = _create_session_via_api(client)
        response = client.get(
            f"/sessions/{session_id}/events",
            headers={"Last-Event-ID": "not-an-int"},
        )

    assert response.status_code == 400
    payload = response.json()
    assert payload["error_code"] == "INVALID_LAST_EVENT_ID"
    assert payload["retryable"] is False
    assert payload["error_details"]["last_event_id"] == "not-an-int"


def test_web_search_capture_endpoint_not_available_in_phase1(isolated_db):
    with TestClient(app) as client:
        session_id = _create_session_via_api(client)
        response = client.post(f"/sessions/{session_id}/web-search/capture", json={})

    assert response.status_code == 404
