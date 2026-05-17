from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app.api.routes.router import _thread_event_stream, app
from app.config import settings
from app.memory.job_store import JobStore
from app.memory.thread_store import ThreadStore


class _FakeRequest:
    def __init__(self) -> None:
        self._disconnected = False

    async def is_disconnected(self) -> bool:
        return self._disconnected

    def disconnect(self) -> None:
        self._disconnected = True


@pytest.fixture
def isolated_dbs(tmp_path, monkeypatch):
    job_db = tmp_path / "thread-events-job.db"
    thread_db = tmp_path / "thread-events-thread.db"
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", str(job_db))
    monkeypatch.setattr(settings, "SSE_HEARTBEAT_SECONDS", 0.1)
    return str(job_db), str(thread_db)


async def _create_thread_with_session(
    thread_db: str,
    *,
    session_id: str | None,
) -> str:
    async with ThreadStore(thread_db) as ts:
        thread = await ts.create_thread("test-thread")
        if session_id is not None:
            await ts.update_thread_active_job(thread["id"], session_id, None)
    return thread["id"]


async def _append_event(
    job_db: str,
    session_id: str,
    *,
    event_name: str,
    stage: str,
    message: str,
) -> None:
    async with JobStore(job_db) as store:
        await store.append_session_event(
            session_id=session_id,
            event_name=event_name,
            stage=stage,
            payload={
                "message": message,
                "progress": 10,
                "error_code": None,
                "details": {},
            },
        )


@pytest.mark.asyncio
async def test_thread_events_replay_maps_event_names(isolated_dbs):
    """task_progress and task_completed session events must appear as workflow_* names."""
    job_db, thread_db = isolated_dbs
    session_id = "session-map-names"
    thread_id = await _create_thread_with_session(thread_db, session_id=session_id)

    await _append_event(job_db, session_id, event_name="task_progress", stage="strategy", message="m1")
    await _append_event(job_db, session_id, event_name="task_completed", stage="strategy", message="m2")

    request = _FakeRequest()
    request.disconnect()

    async with JobStore(job_db) as store:
        stream = _thread_event_stream(
            request,
            thread_id=thread_id,
            session_id=session_id,
            job_store=store,
            last_event_id=None,
        )
        chunks: list[str] = []
        async for chunk in stream:
            chunks.append(chunk)

    assert any("event: workflow_task_progress" in c for c in chunks)
    assert any("event: workflow_task_completed" in c for c in chunks)
    assert not any("event: task_progress" in c for c in chunks)
    assert not any("event: task_completed" in c for c in chunks)


@pytest.mark.asyncio
async def test_thread_events_404_for_nonexistent_thread(isolated_dbs):
    """GET /threads/<bad-id>/events must return 404 THREAD_NOT_FOUND."""
    job_db, thread_db = isolated_dbs

    async with JobStore(job_db) as js, ThreadStore(thread_db) as ts:
        app.state.job_store = js
        app.state.thread_store = ts
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/threads/nonexistent-bad-id/events")

    assert resp.status_code == 404
    body = resp.json()
    assert body["error_code"] == "THREAD_NOT_FOUND"


@pytest.mark.asyncio
async def test_thread_events_empty_stream_when_no_active_session(isolated_dbs):
    """Thread with no active_workflow_session_id must yield a comment-only SSE body."""
    job_db, thread_db = isolated_dbs
    thread_id = await _create_thread_with_session(thread_db, session_id=None)

    async with JobStore(job_db) as js, ThreadStore(thread_db) as ts:
        app.state.job_store = js
        app.state.thread_store = ts
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/threads/{thread_id}/events")

    assert resp.status_code == 200
    assert ": no active session" in resp.text


@pytest.mark.asyncio
async def test_thread_events_last_event_id_replay(isolated_dbs):
    """With Last-Event-ID set, only events after that ID must be replayed."""
    job_db, thread_db = isolated_dbs
    session_id = "session-last-event-id"
    thread_id = await _create_thread_with_session(thread_db, session_id=session_id)

    await _append_event(job_db, session_id, event_name="task_progress", stage="strategy", message="e1")
    await _append_event(job_db, session_id, event_name="task_progress", stage="strategy", message="e2")
    await _append_event(job_db, session_id, event_name="task_completed", stage="strategy", message="e3")

    async with JobStore(job_db) as store:
        all_events = await store.list_session_events(session_id)
    assert len(all_events) == 3
    first_event_id = all_events[0].event_id

    request = _FakeRequest()
    request.disconnect()

    async with JobStore(job_db) as store:
        stream = _thread_event_stream(
            request,
            thread_id=thread_id,
            session_id=session_id,
            job_store=store,
            last_event_id=first_event_id,
        )
        chunks: list[str] = []
        async for chunk in stream:
            chunks.append(chunk)

    assert len(chunks) == 2
    assert f"id: {all_events[1].event_id}" in chunks[0]
    assert f"id: {all_events[2].event_id}" in chunks[1]
    combined = "\n".join(chunks)
    assert "id: 1" not in combined
