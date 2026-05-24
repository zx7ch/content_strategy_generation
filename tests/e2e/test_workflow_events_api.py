"""E2E tests for T3 workflow events API."""

from __future__ import annotations

import json

import httpx
import pytest

from app.api.routes.router import _workflow_event_stream, app, stream_workflow_events
from app.config import settings
from app.memory.workflow_store import WorkflowStore
from app.services.workflow_run_manager import WorkflowRunManager


class _DisconnectedRequest:
    async def is_disconnected(self) -> bool:
        return True


@pytest.fixture
async def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "workflow_events.db")
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", db_path)
    monkeypatch.setattr(settings, "SSE_HEARTBEAT_SECONDS", 0.2)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def _seed_events(db_path: str):
    async with WorkflowRunManager(db_path) as manager:
        run = await manager.start_run(thread_id="thread-events", user_id="user-1")
        await manager.pause_run(run.run_id)
        await manager.cancel_run(run.run_id)

    async with WorkflowStore(db_path) as store:
        events = await store.list_events(run.run_id)
    return run, events


async def _collect_stream_chunks(run_id: str, after_event_id: int | None = None) -> list[str]:
    chunks: list[str] = []
    async for chunk in _workflow_event_stream(
        _DisconnectedRequest(),
        run_id=run_id,
        after_event_id=after_event_id,
    ):
        chunks.append(chunk)
    return chunks


def _event_payloads(chunks: list[str]) -> list[dict]:
    payloads: list[dict] = []
    for chunk in chunks:
        data_line = next(line for line in chunk.splitlines() if line.startswith("data: "))
        payloads.append(json.loads(data_line.removeprefix("data: ")))
    return payloads


@pytest.mark.asyncio
async def test_workflow_events_endpoint_returns_sse_response(client):
    run, _events = await _seed_events(settings.SQLITE_DB_PATH)

    response = await stream_workflow_events(
        run.run_id,
        _DisconnectedRequest(),
        after_event_id=None,
        last_event_id=None,
    )

    assert response.media_type == "text/event-stream"
    assert response.headers["Cache-Control"] == "no-cache"


@pytest.mark.asyncio
async def test_workflow_events_replay_in_event_id_order(client):
    run, events = await _seed_events(settings.SQLITE_DB_PATH)

    chunks = await _collect_stream_chunks(run.run_id)
    payloads = _event_payloads(chunks)

    assert [payload["event_id"] for payload in payloads] == [event.event_id for event in events]
    assert [payload["event_type"] for payload in payloads] == [
        "run_started",
        "run_pause_requested",
        "run_cancel_requested",
    ]


@pytest.mark.asyncio
async def test_workflow_events_after_event_id_replays_only_newer_events(client):
    run, events = await _seed_events(settings.SQLITE_DB_PATH)

    chunks = await _collect_stream_chunks(run.run_id, after_event_id=events[0].event_id)
    payloads = _event_payloads(chunks)

    assert [payload["event_id"] for payload in payloads] == [
        events[1].event_id,
        events[2].event_id,
    ]


@pytest.mark.asyncio
async def test_workflow_events_last_event_id_header_replays_only_newer_events(client):
    run, events = await _seed_events(settings.SQLITE_DB_PATH)

    response = await stream_workflow_events(
        run.run_id,
        _DisconnectedRequest(),
        after_event_id=None,
        last_event_id=str(events[1].event_id),
    )
    chunks: list[str] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
    payloads = _event_payloads(chunks)

    assert [payload["event_id"] for payload in payloads] == [events[2].event_id]


@pytest.mark.asyncio
async def test_workflow_events_missing_run_returns_404(client):
    response = await client.get("/workflow-runs/run_missing/events")

    assert response.status_code == 404
    assert response.json()["error_code"] == "WORKFLOW_RUN_NOT_FOUND"
