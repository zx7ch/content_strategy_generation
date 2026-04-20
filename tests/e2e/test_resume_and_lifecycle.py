from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
import pytest

from app.config import settings
from app.main import app
from app.memory.job_store import JobStore
from app.memory.session_state import SessionManager


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "resume-lifecycle.db"
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", str(db_path))
    monkeypatch.setattr(settings, "JOB_POLL_INTERVAL_MS", 10)
    monkeypatch.setattr(settings, "SSE_HEARTBEAT_SECONDS", 1)
    return str(db_path)


async def _mark_frozen_with_paused_job(db_path: str, session_id: str) -> None:
    async with JobStore(db_path) as store:
        await store.enqueue(session_id=session_id, job_type="strategy")
        await store.pause_session_jobs(session_id)

    async with SessionManager(db_path) as manager:
        await manager.update_session(
            session_id,
            pause_requested=True,
            pause_requested_at=datetime.utcnow().isoformat(),
            last_user_activity_at=(datetime.utcnow() - timedelta(days=2)).isoformat(),
        )


async def _backdate_for_purge(db_path: str, session_id: str) -> None:
    async with SessionManager(db_path) as manager:
        await manager.update_session(
            session_id,
            last_user_activity_at=(datetime.utcnow() - timedelta(days=11)).isoformat(),
        )


def test_resume_restores_frozen_session_and_paused_jobs(isolated_db):
    with TestClient(app) as client:
        create = client.post(
            "/sessions",
            json={"user_id": "u1", "user_query": "护肤", "platform": "xiaohongshu", "mode": "editing"},
        )
        session_id = create.json()["session_id"]

    asyncio.run(_mark_frozen_with_paused_job(isolated_db, session_id))

    with TestClient(app) as client:
        frozen = client.get(f"/sessions/{session_id}")
        assert frozen.status_code == 200
        assert frozen.json()["lifecycle_state"] == "frozen"

        first_resume = client.post(f"/sessions/{session_id}/resume")
        assert first_resume.status_code == 200
        assert first_resume.json()["lifecycle_state"] == "alive"
        assert first_resume.json()["resumed_jobs"] == 1

        second_resume = client.post(f"/sessions/{session_id}/resume")
        assert second_resume.status_code == 200
        assert second_resume.json()["resumed_jobs"] == 0

        resumed = client.get(f"/sessions/{session_id}")
        assert resumed.status_code == 200
        assert resumed.json()["lifecycle_state"] == "alive"
        assert resumed.json()["job_status"] in {"queued", "running"}


def test_purged_session_returns_410_for_get_and_resume(isolated_db):
    with TestClient(app) as client:
        create = client.post(
            "/sessions",
            json={"user_id": "u1", "user_query": "护肤", "platform": "xiaohongshu", "mode": "editing"},
        )
        session_id = create.json()["session_id"]

    asyncio.run(_backdate_for_purge(isolated_db, session_id))

    with TestClient(app) as client:
        get_response = client.get(f"/sessions/{session_id}")
        resume_response = client.post(f"/sessions/{session_id}/resume")

    assert get_response.status_code == 410
    assert get_response.json()["error_code"] == "SESSION_PURGED"
    assert resume_response.status_code == 410
    assert resume_response.json()["error_code"] == "SESSION_PURGED"
