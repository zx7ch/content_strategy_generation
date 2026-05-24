"""E2E tests for T4 creator message workflow-v2 entry."""

from __future__ import annotations

import httpx
import pytest

from app.api.routes.router import app
from app.config import settings
from app.memory.thread_store import ThreadStore
from app.memory.workflow_store import WorkflowStore


@pytest.fixture
async def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "creator_message_workflow_v2.db")
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", db_path)
    thread_store = ThreadStore(db_path)
    await thread_store.connect()

    original_thread_store = getattr(app.state, "thread_store", None)
    app.state.thread_store = thread_store

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c

    app.state.thread_store = original_thread_store
    await thread_store.close()


async def _create_thread(client: httpx.AsyncClient) -> str:
    response = await client.post("/threads", json={"title": "Workflow V2"})
    assert response.status_code == 201
    return response.json()["thread_id"]


@pytest.mark.asyncio
async def test_generation_message_creates_active_run(client):
    thread_id = await _create_thread(client)

    response = await client.post(
        f"/threads/{thread_id}/messages",
        json={"text": "帮我生成一组小红书防晒衣笔记"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["intent"] == "start_workflow"
    assert body["command_result"]["action"] == "start_workflow"
    assert body["active_run_snapshot"]["run"]["status"] == "running"
    assert body["active_run_snapshot"]["run"]["thread_id"] == thread_id
    assert body["active_run_snapshot"]["run"]["current_step"] == "intake.capture_request"
    assert body["active_run_snapshot"]["steps"][0]["step_name"] == "intake.capture_request"


@pytest.mark.asyncio
async def test_running_supplement_message_writes_constraint(client):
    thread_id = await _create_thread(client)
    start = await client.post(
        f"/threads/{thread_id}/messages",
        json={"text": "帮我生成内容策略"},
    )
    run_id = start.json()["command_result"]["run_id"]

    response = await client.post(
        f"/threads/{thread_id}/messages",
        json={"text": "目标用户改为25-35岁女性"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["intent"] == "add_constraint"
    assert body["command_result"]["accepted"] is True
    assert body["active_run_snapshot"]["run"]["constraint_version"] == 1
    assert body["active_run_snapshot"]["constraints"][0]["raw_text"] == "目标用户改为25-35岁女性"
    assert body["active_run_snapshot"]["constraints"][0]["constraint_type"] == "target_audience"

    async with WorkflowStore(settings.SQLITE_DB_PATH) as store:
        constraints = await store.list_constraints(run_id)
        events = await store.list_events(run_id)
    assert len(constraints) == 1
    assert constraints[0].constraint_type.value == "target_audience"
    assert events[-1].event_type == "constraint_added"


@pytest.mark.asyncio
async def test_pause_resume_cancel_commands_use_run_commands(client):
    thread_id = await _create_thread(client)
    await client.post(f"/threads/{thread_id}/messages", json={"text": "帮我生成内容策略"})

    pause = await client.post(f"/threads/{thread_id}/messages", json={"text": "暂停一下"})
    assert pause.json()["command_result"]["action"] == "pause_run"
    assert pause.json()["active_run_snapshot"]["run"]["status"] == "pausing"

    run_id = pause.json()["active_run_snapshot"]["run"]["run_id"]
    async with WorkflowStore(settings.SQLITE_DB_PATH) as store:
        assert store._conn is not None
        await store._conn.execute("UPDATE workflow_runs SET status='paused' WHERE run_id=?", (run_id,))
        await store._conn.commit()

    resume = await client.post(f"/threads/{thread_id}/messages", json={"text": "继续"})
    assert resume.json()["command_result"]["action"] == "resume_run"
    assert resume.json()["active_run_snapshot"]["run"]["status"] == "running"

    cancel = await client.post(f"/threads/{thread_id}/messages", json={"text": "取消任务"})
    assert cancel.json()["command_result"]["action"] == "cancel_run"
    assert cancel.json()["active_run_snapshot"]["run"]["status"] == "cancelling"


@pytest.mark.asyncio
async def test_ask_status_returns_snapshot_summary(client):
    thread_id = await _create_thread(client)
    await client.post(f"/threads/{thread_id}/messages", json={"text": "帮我生成内容策略"})

    response = await client.post(
        f"/threads/{thread_id}/messages",
        json={"text": "进度怎么样了？"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["intent"] == "ask_status"
    assert body["command_result"]["action"] == "ask_status"
    assert "当前任务状态" in body["assistant_reply"]
    assert body["active_run_snapshot"]["run"]["status"] == "running"


@pytest.mark.asyncio
async def test_low_confidence_constraint_does_not_change_workflow_state(client):
    thread_id = await _create_thread(client)
    start = await client.post(f"/threads/{thread_id}/messages", json={"text": "帮我生成内容策略"})
    run_id = start.json()["command_result"]["run_id"]

    response = await client.post(
        f"/threads/{thread_id}/messages",
        json={"text": "也许随便改一下吧"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["intent"] == "add_constraint"
    assert body["command_result"]["accepted"] is False
    assert body["command_result"]["reason"] == "low_confidence"

    async with WorkflowStore(settings.SQLITE_DB_PATH) as store:
        constraints = await store.list_constraints(run_id)
    assert constraints == []
