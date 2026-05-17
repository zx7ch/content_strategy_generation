"""E2E tests for ALIGN-4 Creator Workflow API — POST /threads/{id}/workflow."""

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
    agent_db = str(tmp_path / "agent.db")  # sessions + jobs share one file, same as production

    thread_store = ThreadStore(thread_db)
    await thread_store.connect()

    job_store = JobStore(agent_db)
    await job_store.connect()

    # Init session table schema — SessionManager uses the same db as JobStore in production
    from app.memory.session_state import SessionManager as SM
    async with SM(agent_db) as _:
        pass

    _orig_ts = getattr(app.state, "thread_store", None)
    _orig_js = getattr(app.state, "job_store", None)
    app.state.thread_store = thread_store
    app.state.job_store = job_store

    # Redirect SessionManager in the workflow endpoint to the tmp db
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


async def test_start_workflow_creates_session_and_job(client):
    create = await client.post("/threads", json={"title": "Workflow Test"})
    thread_id = create.json()["thread_id"]

    resp = await client.post(
        f"/threads/{thread_id}/workflow",
        json={"user_query": "帮我生成小红书笔记"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["thread_id"] == thread_id
    assert body["session_id"]
    assert body["job_id"]
    assert body["stage"] == "strategy"


async def test_start_workflow_updates_thread_active_job(client):
    create = await client.post("/threads", json={"title": "Active Job Test"})
    thread_id = create.json()["thread_id"]

    workflow = await client.post(
        f"/threads/{thread_id}/workflow",
        json={"user_query": "生成内容策略"},
    )
    job_id = workflow.json()["job_id"]
    session_id = workflow.json()["session_id"]

    detail = await client.get(f"/threads/{thread_id}")
    assert detail.status_code == 200
    thread = detail.json()["thread"]
    assert thread["active_workflow_session_id"] == session_id
    assert thread["active_job_id"] == job_id


async def test_start_workflow_nonexistent_thread_404(client):
    resp = await client.post(
        "/threads/does-not-exist/workflow",
        json={"user_query": "测试"},
    )
    assert resp.status_code == 404


async def test_start_workflow_each_call_creates_new_session(client):
    """TD-ALIGN4-1: each call always creates a new session (no reuse)."""
    create = await client.post("/threads", json={"title": "Multi Workflow"})
    thread_id = create.json()["thread_id"]

    r1 = await client.post(f"/threads/{thread_id}/workflow", json={"user_query": "第一次"})
    r2 = await client.post(f"/threads/{thread_id}/workflow", json={"user_query": "第二次"})

    assert r1.json()["session_id"] != r2.json()["session_id"]
