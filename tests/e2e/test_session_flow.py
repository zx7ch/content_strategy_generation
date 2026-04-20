from __future__ import annotations

import uuid

import httpx
import pytest

from app.config import settings
from app.main import app
from app.memory.session_state import SessionManager
from app.models.session import SessionLifecycleState


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "session-flow.db"
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", str(db_path))
    monkeypatch.setattr(settings, "JOB_POLL_INTERVAL_MS", 10)
    monkeypatch.setattr(settings, "SSE_HEARTBEAT_SECONDS", 1)
    return str(db_path)


async def _set_session_state(db_path: str, session_id: str, **fields) -> None:
    async with SessionManager(db_path) as manager:
        await manager.update_session(session_id, **fields)


@pytest.mark.asyncio
async def test_session_create_get_missing_and_purged_flow(isolated_db):
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        create = await client.post(
            "/sessions",
            json={"user_id": "u1", "user_query": "护肤", "platform": "xiaohongshu", "mode": "editing"},
        )
        assert create.status_code == 201
        session_id = create.json()["session_id"]

        get_ok = await client.get(f"/sessions/{session_id}")
        assert get_ok.status_code == 200
        assert get_ok.json()["stage"] == "init"

        missing = await client.get(f"/sessions/{uuid.uuid4()}")
        assert missing.status_code == 404

    await _set_session_state(
        isolated_db,
        session_id,
        lifecycle_state=SessionLifecycleState.PURGED,
        purged_at="2026-03-18T00:00:00",
    )

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        purged = await client.get(f"/sessions/{session_id}")
        assert purged.status_code == 410
