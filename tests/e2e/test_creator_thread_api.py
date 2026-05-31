"""E2E tests for ALIGN-3 Creator thread / message API endpoints."""

from __future__ import annotations

import httpx
import pytest

from app.api.routes.router import app
from app.memory.job_store import JobStore
from app.memory.thread_store import ThreadStore


@pytest.fixture
async def client(tmp_path):
    """Create an isolated HTTP client with temp-db ThreadStore + JobStore on app.state."""
    db_path = str(tmp_path / "e2e_threads.db")
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


async def test_post_threads_creates_thread(client):
    resp = await client.post("/threads", json={})
    assert resp.status_code == 201
    body = resp.json()
    assert body["thread_id"]
    assert body["title"]
    assert body["status"] == "active"


async def test_post_threads_with_title(client):
    resp = await client.post("/threads", json={"title": "我的对话"})
    assert resp.status_code == 201
    assert resp.json()["title"] == "我的对话"


async def test_get_threads_lists_created(client):
    await client.post("/threads", json={"title": "Thread A"})
    await client.post("/threads", json={"title": "Thread B"})

    resp = await client.get("/threads")
    assert resp.status_code == 200
    titles = [item["title"] for item in resp.json()["items"]]
    assert "Thread A" in titles
    assert "Thread B" in titles


async def test_get_thread_detail_empty_messages(client):
    create = await client.post("/threads", json={"title": "Detail Test"})
    thread_id = create.json()["thread_id"]

    resp = await client.get(f"/threads/{thread_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["thread"]["thread_id"] == thread_id
    assert body["messages"] == []


async def test_patch_thread_renames_thread(client):
    create = await client.post("/threads", json={"title": "旧名称"})
    thread_id = create.json()["thread_id"]

    resp = await client.patch(f"/threads/{thread_id}", json={"title": "新名称"})

    assert resp.status_code == 200
    assert resp.json()["title"] == "新名称"
    detail = await client.get(f"/threads/{thread_id}")
    assert detail.json()["thread"]["title"] == "新名称"


async def test_delete_thread_removes_thread_and_messages(client):
    create = await client.post("/threads", json={"title": "Delete Test"})
    thread_id = create.json()["thread_id"]
    await client.post(f"/threads/{thread_id}/messages", json={"text": "hello"})

    resp = await client.delete(f"/threads/{thread_id}")

    assert resp.status_code == 200
    assert resp.json() == {"thread_id": thread_id, "deleted": True}
    missing = await client.get(f"/threads/{thread_id}")
    assert missing.status_code == 404


async def test_post_message_to_thread(client):
    create = await client.post("/threads", json={})
    thread_id = create.json()["thread_id"]

    resp = await client.post(f"/threads/{thread_id}/messages", json={"text": "帮我生成笔记"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["message"]["text"] == "帮我生成笔记"
    assert body["message"]["role"] == "user"
    assert body["intent"] == "start_workflow"
    assert body["command_result"]["action"] == "start_workflow"
    assert body["active_run_snapshot"]["run"]["thread_id"] == thread_id


async def test_get_nonexistent_thread_404(client):
    resp = await client.get("/threads/does-not-exist")
    assert resp.status_code == 404


async def test_post_message_nonexistent_thread_404(client):
    resp = await client.post("/threads/does-not-exist/messages", json={"text": "hello"})
    assert resp.status_code == 404
