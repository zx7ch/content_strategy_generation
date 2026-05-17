"""E2E tests for ALIGN-8 complete / publish-candidate endpoints."""

from __future__ import annotations

import httpx
import pytest

from app.api.routes.router import app
from app.memory.job_store import JobStore
from app.memory.thread_store import ThreadStore


@pytest.fixture
async def client(tmp_path):
    db_path = str(tmp_path / "align8.db")
    thread_store = ThreadStore(db_path)
    await thread_store.connect()
    job_store = JobStore(db_path)
    await job_store.connect()

    _orig_thread = getattr(app.state, "thread_store", None)
    _orig_job = getattr(app.state, "job_store", None)
    app.state.thread_store = thread_store
    app.state.job_store = job_store

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c

    app.state.thread_store = _orig_thread
    app.state.job_store = _orig_job
    await thread_store.close()
    await job_store.close()


async def test_complete_thread_marks_accepted(client):
    """POST /threads/{id}/complete marks thread accepted, returns publish_candidate_count."""
    resp = await client.post("/threads", json={})
    assert resp.status_code == 201
    thread_id = resp.json()["thread_id"]

    resp = await client.post(f"/threads/{thread_id}/complete")
    assert resp.status_code == 200
    body = resp.json()
    assert body["thread_id"] == thread_id
    assert body["status"] == "accepted"
    assert isinstance(body["publish_candidate_count"], int)


async def test_complete_thread_idempotent(client):
    """Calling complete twice returns same accepted status without error."""
    resp = await client.post("/threads", json={})
    thread_id = resp.json()["thread_id"]

    first = await client.post(f"/threads/{thread_id}/complete")
    assert first.status_code == 200

    second = await client.post(f"/threads/{thread_id}/complete")
    assert second.status_code == 200
    assert second.json()["status"] == "accepted"
    assert second.json()["publish_candidate_count"] == first.json()["publish_candidate_count"]


async def test_complete_nonexistent_thread_404(client):
    """POST /threads/does-not-exist/complete returns 404."""
    resp = await client.post("/threads/does-not-exist/complete")
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "THREAD_NOT_FOUND"


async def test_list_publish_candidates_empty(client):
    """GET /publish-candidates on fresh store returns 200 with empty items."""
    resp = await client.get("/publish-candidates")
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert body["items"] == []
