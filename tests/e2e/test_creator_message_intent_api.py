"""E2E tests for ALIGN-5 Creator Message Intent API — POST /threads/{id}/messages."""

from __future__ import annotations

import uuid

import httpx
import pytest

from app.api.routes.router import app
from app.memory.job_store import JobStore
from app.memory.session_state import SessionManager
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


async def _create_thread_with_job(client: httpx.AsyncClient) -> tuple[str, str, str]:
    """Helper: create a thread with a legacy session/job link."""
    create = await client.post("/threads", json={"title": "Intent Test"})
    thread_id = create.json()["thread_id"]
    session_id = f"sess-{uuid.uuid4().hex}"
    async with SessionManager(app.state.job_store.db_path) as session_manager:
        await session_manager.create_session(
            session_id=session_id,
            user_id="user-1",
            user_query="帮我生成内容策略",
        )
    job, _created = await app.state.job_store.enqueue(
        session_id=session_id,
        job_type="strategy",
    )
    await app.state.thread_store.update_thread_active_job(thread_id, session_id, job.id)
    return thread_id, session_id, job.id


async def test_message_free_chat_no_active_job(client):
    """free_chat intent when no workflow has been started."""
    create = await client.post("/threads", json={"title": "No Job Thread"})
    thread_id = create.json()["thread_id"]

    resp = await client.post(
        f"/threads/{thread_id}/messages",
        json={"text": "你好，能帮我做什么？"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["intent"] == "free_chat"
    assert body["job_action_result"] is None


async def test_message_add_constraint_with_running_job(client):
    """add_constraint intent when a job is queued/running."""
    thread_id, _, _ = await _create_thread_with_job(client)

    resp = await client.post(
        f"/threads/{thread_id}/messages",
        json={"text": "目标用户改为25-35岁女性"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["intent"] == "add_constraint"


async def test_message_pause_job_intent(client):
    """pause_job intent triggers pause action."""
    thread_id, session_id, _ = await _create_thread_with_job(client)

    resp = await client.post(
        f"/threads/{thread_id}/messages",
        json={"text": "暂停一下"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["intent"] == "pause_job"
    assert body["job_action_result"] is not None
    assert body["job_action_result"]["action"] == "pause"


async def test_message_ask_status_intent(client):
    """ask_status intent returns job status info."""
    thread_id, _, job_id = await _create_thread_with_job(client)

    resp = await client.post(
        f"/threads/{thread_id}/messages",
        json={"text": "任务进度怎么样了？"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["intent"] == "ask_status"
    result = body["job_action_result"]
    assert result is not None
    assert result["job_id"] == job_id
