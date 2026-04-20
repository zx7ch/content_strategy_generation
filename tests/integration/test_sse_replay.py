from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from app.api.routes.router import _event_stream
from app.config import settings
from app.memory.job_store import JobStore
from app.memory.session_state import SessionManager


class _FakeRequest:
    def __init__(self) -> None:
        self._disconnected = False

    async def is_disconnected(self) -> bool:
        return self._disconnected

    def disconnect(self) -> None:
        self._disconnected = True


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "sse-replay.db"
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", str(db_path))
    monkeypatch.setattr(settings, "SSE_HEARTBEAT_SECONDS", 0.1)
    return str(db_path)


async def _create_session(db_path: str, session_id: str) -> None:
    async with SessionManager(db_path) as manager:
        await manager.create_session(session_id, "u1", "护肤")


async def _append_stage_event(db_path: str, session_id: str) -> None:
    async with JobStore(db_path) as store:
        await store.append_session_event(
            session_id=session_id,
            event_name="stage_changed",
            stage="strategy",
            payload={
                "message": "strategy queued",
                "progress": 0,
                "error_code": None,
                "details": {"stage": "strategy"},
            },
        )


async def _force_lifecycle(db_path: str, session_id: str, *, stale_delta: timedelta) -> None:
    async with SessionManager(db_path) as manager:
        stale_ts = (datetime.utcnow() - stale_delta).isoformat()
        await manager._conn.execute(
            "UPDATE sessions SET last_user_activity_at = ?, last_activity_at = ? WHERE session_id = ?",
            (stale_ts, stale_ts, session_id),
        )
        await manager._conn.commit()
        await manager.refresh_lifecycle_state(session_id)


@pytest.mark.asyncio
async def test_sse_replay_then_live_lifecycle_event(isolated_db):
    session_id = "session-sse-lifecycle"
    await _create_session(isolated_db, session_id)
    await _append_stage_event(isolated_db, session_id)

    request = _FakeRequest()
    stream = _event_stream(request, session_id=session_id, last_event_id=None)

    async def _freeze_session() -> None:
        await asyncio.sleep(0.15)
        await _force_lifecycle(isolated_db, session_id, stale_delta=timedelta(hours=25))

    trigger = asyncio.create_task(_freeze_session())
    try:
        replay = await asyncio.wait_for(anext(stream), timeout=0.5)
        live = ""
        for _ in range(4):
            candidate = await asyncio.wait_for(anext(stream), timeout=0.5)
            if "event: session_frozen" in candidate:
                live = candidate
                break
    finally:
        request.disconnect()
        await stream.aclose()
        await trigger

    assert "event: stage_changed" in replay
    assert "id: 1" in replay
    assert "event: session_frozen" in live
    assert "id: 2" in live


@pytest.mark.asyncio
async def test_sse_reconnect_with_last_event_id_replays_remaining_lifecycle_state(isolated_db):
    session_id = "session-sse-reconnect"
    await _create_session(isolated_db, session_id)

    await _force_lifecycle(isolated_db, session_id, stale_delta=timedelta(hours=25))
    await _force_lifecycle(isolated_db, session_id, stale_delta=timedelta(days=11))

    async with JobStore(isolated_db) as store:
        events = await store.list_session_events(session_id)

    assert [event.event_name for event in events] == ["session_frozen", "session_purged"]
    assert [event.event_id for event in events] == [1, 2]

    request = _FakeRequest()
    stream = _event_stream(request, session_id=session_id, last_event_id=1)
    try:
        replay = await asyncio.wait_for(anext(stream), timeout=0.5)
    finally:
        request.disconnect()
        await stream.aclose()

    assert "event: session_purged" in replay
    assert "id: 2" in replay
