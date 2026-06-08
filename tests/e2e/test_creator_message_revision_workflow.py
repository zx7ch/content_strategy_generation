"""E2E tests for T8.2 message-driven artifact revision flow."""

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
    db_path = str(tmp_path / "creator_message_revision_workflow.db")
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


async def _seed_two_note_result(client: httpx.AsyncClient) -> tuple[str, str, str]:
    created = await client.post("/threads", json={"title": "Revision Workflow"})
    assert created.status_code == 201
    thread_id = created.json()["thread_id"]

    started = await client.post(
        f"/threads/{thread_id}/messages",
        json={"text": "帮我生成两篇防晒衣笔记"},
    )
    assert started.status_code == 201
    run_id = started.json()["command_result"]["run_id"]

    async with WorkflowRunManager(settings.SQLITE_DB_PATH) as manager:
        first = await manager.attach_artifact(
            run_id=run_id,
            artifact_type=WorkflowArtifactType.GENERATED_NOTE,
            payload={"note_id": "note-1", "title": "第一篇", "content": "正式", "tags": []},
        )
        second = await manager.attach_artifact(
            run_id=run_id,
            artifact_type=WorkflowArtifactType.GENERATED_NOTE,
            payload={"note_id": "note-2", "title": "第二篇", "content": "正式", "tags": []},
        )

    await app.state.thread_store.append_artifact_result_message(
        thread_id=thread_id,
        run_id=run_id,
        artifact_refs=[
            {
                "artifact_id": first.artifact_id,
                "artifact_type": "generated_note",
                "artifact_version": first.artifact_version,
            },
            {
                "artifact_id": second.artifact_id,
                "artifact_type": "generated_note",
                "artifact_version": second.artifact_version,
            },
        ],
    )
    return thread_id, run_id, second.artifact_id


@pytest.mark.asyncio
async def test_message_revision_creates_patch_and_hydrates_timeline_result(client):
    thread_id, run_id, second_artifact_id = await _seed_two_note_result(client)

    response = await client.post(
        f"/threads/{thread_id}/messages",
        json={"text": "把第 2 篇改生活化"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["intent"] == "revise_artifact"
    assert body["command_result"]["accepted"] is True
    assert body["command_result"]["target_artifact_id"] == second_artifact_id

    timeline = await client.get(f"/threads/{thread_id}/timeline")
    assert timeline.status_code == 200
    artifact_messages = [m for m in timeline.json()["messages"] if m["message_type"] == "artifact_result"]
    assert len(artifact_messages) == 2
    revision_ref = artifact_messages[-1]["artifact_refs"][0]
    assert revision_ref["parent_artifact_id"] == second_artifact_id
    assert revision_ref["artifact"]["parent_artifact_id"] == second_artifact_id
    assert revision_ref["artifact"]["payload_mode"] == "patch"
    assert revision_ref["artifact"]["payload_json"]["note_id"] == "note-2"
    assert revision_ref["artifact"]["payload_json"]["revision_instruction"] == "把第 2 篇改生活化"

    result = await client.get(f"/threads/{thread_id}/result")
    assert result.status_code == 200
    notes = result.json()["notes"]
    assert any(note["note_id"] == "note-2" and note["title"] == "第二篇" for note in notes)

    snapshot = await client.get(f"/workflow-runs/{run_id}/snapshot", params={"thread_id": thread_id})
    assert snapshot.status_code == 200
    generated_notes = [a for a in snapshot.json()["artifacts"] if a["artifact_type"] == "generated_note"]
    assert any(note["parent_artifact_id"] == second_artifact_id for note in generated_notes)
