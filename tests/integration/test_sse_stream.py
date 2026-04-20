from __future__ import annotations

import asyncio

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
    db_path = tmp_path / "sse-stream.db"
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", str(db_path))
    monkeypatch.setattr(settings, "SSE_HEARTBEAT_SECONDS", 0.1)
    return str(db_path)


async def _create_session(db_path: str, session_id: str) -> None:
    async with SessionManager(db_path) as manager:
        await manager.create_session(session_id, "u1", "护肤")


async def _append_event(
    db_path: str,
    session_id: str,
    *,
    event_name: str,
    stage: str,
    message: str,
) -> None:
    async with JobStore(db_path) as store:
        await store.append_session_event(
            session_id=session_id,
            event_name=event_name,
            stage=stage,
            payload={
                "message": message,
                "progress": 0,
                "error_code": None,
                "details": {"stage": stage},
            },
        )


@pytest.mark.asyncio
async def test_event_stream_keeps_connection_open_and_emits_multiple_heartbeats(isolated_db):
    session_id = "session-heartbeats"
    await _create_session(isolated_db, session_id)
    request = _FakeRequest()
    stream = _event_stream(request, session_id=session_id, last_event_id=None)

    try:
        first = await asyncio.wait_for(anext(stream), timeout=0.5)
        second = await asyncio.wait_for(anext(stream), timeout=0.5)
    finally:
        request.disconnect()
        await stream.aclose()

    assert "event: heartbeat" in first
    assert "event: heartbeat" in second
    assert "id:" not in first
    assert "id:" not in second


@pytest.mark.asyncio
async def test_event_stream_replays_then_delivers_live_events_without_reconnect(isolated_db):
    session_id = "session-live-events"
    await _create_session(isolated_db, session_id)
    await _append_event(
        isolated_db,
        session_id,
        event_name="stage_changed",
        stage="strategy",
        message="initial replay event",
    )

    request = _FakeRequest()
    stream = _event_stream(request, session_id=session_id, last_event_id=None)

    async def _append_live() -> None:
        await asyncio.sleep(0.15)
        await _append_event(
            isolated_db,
            session_id,
            event_name="task_progress",
            stage="strategy",
            message="live event",
        )

    live_task = asyncio.create_task(_append_live())
    try:
        replay = await asyncio.wait_for(anext(stream), timeout=0.5)
        live = ""
        for _ in range(3):
            candidate = await asyncio.wait_for(anext(stream), timeout=0.5)
            if "event: task_progress" in candidate:
                live = candidate
                break
    finally:
        request.disconnect()
        await stream.aclose()
        await live_task

    assert "event: stage_changed" in replay
    assert "id: 1" in replay
    assert live
    assert "event: task_progress" in live
    assert "id: 2" in live


@pytest.mark.asyncio
async def test_heartbeat_does_not_advance_reconnect_cursor_past_persisted_events(isolated_db):
    session_id = "session-reconnect"
    await _create_session(isolated_db, session_id)
    await _append_event(
        isolated_db,
        session_id,
        event_name="stage_changed",
        stage="strategy",
        message="persisted one",
    )

    request = _FakeRequest()
    stream = _event_stream(request, session_id=session_id, last_event_id=None)
    try:
        first = await asyncio.wait_for(anext(stream), timeout=0.5)
        heartbeat = await asyncio.wait_for(anext(stream), timeout=0.5)
    finally:
        request.disconnect()
        await stream.aclose()

    await _append_event(
        isolated_db,
        session_id,
        event_name="task_completed",
        stage="strategy",
        message="persisted two",
    )

    reconnect_request = _FakeRequest()
    reconnect_stream = _event_stream(reconnect_request, session_id=session_id, last_event_id=1)
    try:
        replay_after_reconnect = await asyncio.wait_for(anext(reconnect_stream), timeout=0.5)
    finally:
        reconnect_request.disconnect()
        await reconnect_stream.aclose()

    assert "id: 1" in first
    assert "event: heartbeat" in heartbeat
    assert "id:" not in heartbeat
    assert "event: task_completed" in replay_after_reconnect
    assert "id: 2" in replay_after_reconnect
