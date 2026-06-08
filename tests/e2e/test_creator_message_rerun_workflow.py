"""E2E tests for T8.3 message-driven workflow rerun."""

from __future__ import annotations

import httpx
import pytest

from app.api.routes.router import app
from app.config import settings
from app.memory.thread_store import ThreadStore
from app.models.workflow import WorkflowArtifactType
from app.services.workflow_run_manager import WorkflowRunManager


@pytest.fixture
async def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "creator_message_rerun_workflow.db")
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


async def _seed_old_workflow_result(client: httpx.AsyncClient) -> tuple[str, str, str]:
    created = await client.post("/threads", json={"title": "Rerun Workflow"})
    assert created.status_code == 201
    thread_id = created.json()["thread_id"]

    started = await client.post(
        f"/threads/{thread_id}/messages",
        json={"text": "帮我生成防晒衣笔记"},
    )
    assert started.status_code == 201
    old_run_id = started.json()["command_result"]["run_id"]

    async with WorkflowRunManager(settings.SQLITE_DB_PATH) as manager:
        old_note = await manager.attach_artifact(
            run_id=old_run_id,
            artifact_type=WorkflowArtifactType.GENERATED_NOTE,
            payload={"note_id": "old-note", "title": "防晒衣", "content": "旧主题", "tags": ["防晒衣"]},
        )
        await manager.attach_artifact(
            run_id=old_run_id,
            artifact_type=WorkflowArtifactType.FINAL_RESULT,
            payload={
                "generated_notes": [
                    {
                        "artifact_id": old_note.artifact_id,
                        "artifact_type": "generated_note",
                        "payload_json": old_note.payload_json,
                    }
                ]
            },
        )

    await app.state.thread_store.append_artifact_result_message(
        thread_id=thread_id,
        run_id=old_run_id,
        artifact_refs=[
            {
                "artifact_id": old_note.artifact_id,
                "artifact_type": "generated_note",
                "artifact_version": old_note.artifact_version,
            }
        ],
    )
    return thread_id, old_run_id, old_note.artifact_id


@pytest.mark.asyncio
async def test_rerun_message_creates_new_active_run_without_inheriting_old_result(client):
    thread_id, old_run_id, old_artifact_id = await _seed_old_workflow_result(client)

    response = await client.post(
        f"/threads/{thread_id}/messages",
        json={"text": "不要防晒衣了，改成徒步鞋"},
    )

    assert response.status_code == 201
    body = response.json()
    new_run_id = body["command_result"]["run_id"]
    assert body["intent"] == "rerun_workflow"
    assert body["command_result"]["action"] == "rerun_workflow"
    assert body["command_result"]["accepted"] is True
    assert body["command_result"]["parent_run_id"] == old_run_id
    assert new_run_id != old_run_id
    assert "新" in body["assistant_reply"]
    assert body["active_run_snapshot"]["run"]["run_id"] == new_run_id
    assert body["active_run_snapshot"]["artifacts"] == []
    assert body["active_run_snapshot"]["steps"][0]["checkpoint_json"] == {
        "run_type": "rerun",
        "parent_run_id": old_run_id,
        "rerun_request": "不要防晒衣了，改成徒步鞋",
    }

    thread = await client.get(f"/threads/{thread_id}")
    assert thread.status_code == 200
    assert thread.json()["thread"]["active_run_id"] == new_run_id

    old_snapshot = await client.get(f"/workflow-runs/{old_run_id}/snapshot", params={"thread_id": thread_id})
    assert old_snapshot.status_code == 200
    assert old_artifact_id in {artifact["artifact_id"] for artifact in old_snapshot.json()["artifacts"]}

    timeline = await client.get(f"/threads/{thread_id}/timeline")
    assert timeline.status_code == 200
    artifact_messages = [m for m in timeline.json()["messages"] if m["message_type"] == "artifact_result"]
    assert len(artifact_messages) == 1
    assert artifact_messages[0]["run_id"] == old_run_id
    assert artifact_messages[0]["artifact_refs"][0]["artifact_id"] == old_artifact_id

    result = await client.get(f"/threads/{thread_id}/result")
    assert result.status_code == 200
    assert result.json()["session_id"] == new_run_id
    assert result.json()["notes"] == []
