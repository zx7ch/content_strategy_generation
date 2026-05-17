"""E2E tests for ALIGN-6 Job Control API — POST /jobs/{id}/pause|resume|cancel."""

from __future__ import annotations

import httpx
import pytest

from app.api.routes.router import app
from app.memory.job_store import JobStore
from app.memory.thread_store import ThreadStore


@pytest.fixture
async def client(tmp_path):
    """Isolated client with tmp ThreadStore + JobStore + SessionManager schema."""
    thread_db = str(tmp_path / "threads.db")
    agent_db = str(tmp_path / "agent.db")

    thread_store = ThreadStore(thread_db)
    await thread_store.connect()

    job_store = JobStore(agent_db)
    await job_store.connect()

    from app.memory.session_state import SessionManager as SM
    async with SM(agent_db) as _:
        pass

    _orig_ts = getattr(app.state, "thread_store", None)
    _orig_js = getattr(app.state, "job_store", None)
    app.state.thread_store = thread_store
    app.state.job_store = job_store

    import app.api.routes.router as router_module
    _orig_db = router_module.settings.SQLITE_DB_PATH
    router_module.settings.SQLITE_DB_PATH = agent_db

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c

    router_module.settings.SQLITE_DB_PATH = _orig_db
    app.state.thread_store = _orig_ts
    app.state.job_store = _orig_js
    await thread_store.close()
    await job_store.close()


async def _create_workflow(client: httpx.AsyncClient) -> tuple[str, str]:
    """Helper: create thread + start workflow, return (thread_id, job_id)."""
    create = await client.post("/threads", json={"title": "Control Test"})
    thread_id = create.json()["thread_id"]
    wf = await client.post(
        f"/threads/{thread_id}/workflow",
        json={"user_query": "生成内容策略"},
    )
    job_id = wf.json()["job_id"]
    return thread_id, job_id


async def test_pause_queued_job_returns_200(client):
    _, job_id = await _create_workflow(client)

    resp = await client.post(f"/jobs/{job_id}/pause")
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == job_id
    assert body["status"] == "paused"


async def test_resume_paused_job_returns_200(client):
    _, job_id = await _create_workflow(client)

    await client.post(f"/jobs/{job_id}/pause")
    resp = await client.post(f"/jobs/{job_id}/resume")
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == job_id
    assert body["status"] == "queued"


async def test_cancel_queued_job_returns_200(client):
    _, job_id = await _create_workflow(client)

    resp = await client.post(f"/jobs/{job_id}/cancel")
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == job_id
    assert body["status"] == "cancelled"


async def test_cancel_nonexistent_job_returns_404(client):
    resp = await client.post("/jobs/does-not-exist/cancel")
    assert resp.status_code == 404


async def test_cancel_already_cancelled_job_returns_409(client):
    _, job_id = await _create_workflow(client)

    await client.post(f"/jobs/{job_id}/cancel")
    resp = await client.post(f"/jobs/{job_id}/cancel")
    assert resp.status_code == 409


async def test_resume_non_paused_job_returns_409(client):
    _, job_id = await _create_workflow(client)

    # Job is queued, not paused — resume should 409
    resp = await client.post(f"/jobs/{job_id}/resume")
    assert resp.status_code == 409
