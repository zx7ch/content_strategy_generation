"""E2E tests for T8 workflow-v2 complete and publish candidate recovery."""

from __future__ import annotations

import httpx
import pytest

from app.api.routes.router import app
from app.config import settings
from app.memory.workflow_store import WorkflowStore
from app.memory.thread_store import ThreadStore
from app.models.workflow import WorkflowArtifactType, WorkflowPhase
from app.services.workflow_run_manager import WorkflowRunManager


@pytest.fixture
async def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "complete_workflow_v2.db")
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


async def _seed_complete_ready_run(client: httpx.AsyncClient) -> tuple[str, str]:
    created = await client.post("/threads", json={"title": "Complete Workflow V2"})
    assert created.status_code == 201
    thread_id = created.json()["thread_id"]

    async with WorkflowRunManager(settings.SQLITE_DB_PATH) as manager:
        run = await manager.start_run(thread_id=thread_id, user_id="user-1")
        steps = await manager.initialize_steps(
            run.run_id,
            [{"step_name": "generation.aggregate_notes", "phase": WorkflowPhase.GENERATION}],
        )
        note_1 = await manager.attach_artifact(
            run_id=run.run_id,
            artifact_type=WorkflowArtifactType.GENERATED_NOTE,
            payload={"note_id": "note-1", "title": "第一篇", "content": "内容一", "tags": ["A"]},
            summary_text="第一篇",
            created_by_step_id=steps[0].step_id,
        )
        note_2 = await manager.attach_artifact(
            run_id=run.run_id,
            artifact_type=WorkflowArtifactType.GENERATED_NOTE,
            payload={"note_id": "note-2", "title": "第二篇", "content": "内容二", "tags": ["B"]},
            summary_text="第二篇",
            created_by_step_id=steps[0].step_id,
        )
        await manager.attach_artifact(
            run_id=run.run_id,
            artifact_type=WorkflowArtifactType.FINAL_RESULT,
            payload={
                "generated_notes": [
                    {"artifact_id": note_1.artifact_id, "payload_json": note_1.payload_json},
                    {"artifact_id": note_2.artifact_id, "payload_json": note_2.payload_json},
                ]
            },
            summary_text="final result",
            created_by_step_id=steps[0].step_id,
        )

    await app.state.thread_store.update_thread_active_run(thread_id, run.run_id)
    return thread_id, run.run_id


@pytest.mark.asyncio
async def test_complete_workflow_v2_creates_publish_candidates_and_timeline_message(client):
    thread_id, run_id = await _seed_complete_ready_run(client)

    response = await client.post(f"/threads/{thread_id}/complete")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert body["publish_candidate_count"] == 2

    candidates = await client.get("/publish-candidates")
    assert candidates.status_code == 200
    items = candidates.json()["items"]
    assert {item["note_id"] for item in items} == {"note-1", "note-2"}
    assert {item["session_id"] for item in items} == {run_id}
    assert {item["score_type"] for item in items} == {"predicted"}
    assert all(item["topic_type"] for item in items)
    assert all(item["core_hypothesis"] for item in items)

    async with WorkflowStore(settings.SQLITE_DB_PATH) as store:
        artifacts = await store.list_artifacts(run_id)
    assert [artifact.artifact_type for artifact in artifacts].count(WorkflowArtifactType.PUBLISH_CANDIDATE) == 2

    timeline = await client.get(f"/threads/{thread_id}/timeline")
    artifact_messages = [m for m in timeline.json()["messages"] if m["message_type"] == "artifact_result"]
    assert len(artifact_messages) == 1
    assert artifact_messages[0]["run_id"] == run_id


@pytest.mark.asyncio
async def test_publish_candidates_are_workspace_brand_and_run_scoped(client):
    headers = {"X-Workspace-Id": "workspace-a", "X-User-Id": "user-1"}
    created = await client.post(
        "/threads",
        headers=headers,
        json={"title": "Scoped Workflow", "brand_id": "brand-a"},
    )
    assert created.status_code == 201
    thread_id = created.json()["thread_id"]

    async with WorkflowRunManager(settings.SQLITE_DB_PATH) as manager:
        run = await manager.start_run(thread_id=thread_id, user_id="user-1")
        steps = await manager.initialize_steps(
            run.run_id,
            [{"step_name": "generation.aggregate_notes", "phase": WorkflowPhase.GENERATION}],
        )
        note = await manager.attach_artifact(
            run_id=run.run_id,
            artifact_type=WorkflowArtifactType.GENERATED_NOTE,
            payload={
                "note_id": "scoped-note",
                "title": "范围内笔记",
                "content": "内容",
                "topic_type": "问题",
                "core_hypothesis": "痛点明确",
                "score": 0.77,
            },
            created_by_step_id=steps[0].step_id,
        )
        await manager.attach_artifact(
            run_id=run.run_id,
            artifact_type=WorkflowArtifactType.FINAL_RESULT,
            payload={"generated_notes": [{"artifact_id": note.artifact_id, "payload_json": note.payload_json}]},
            created_by_step_id=steps[0].step_id,
        )

    await app.state.thread_store.update_thread_active_run(thread_id, run.run_id)
    completed = await client.post(f"/threads/{thread_id}/complete", headers=headers)
    assert completed.status_code == 200

    in_scope = await client.get(
        f"/publish-candidates?brand_id=brand-a&thread_id={thread_id}&run_id={run.run_id}",
        headers=headers,
    )
    assert in_scope.status_code == 200
    assert [item["note_id"] for item in in_scope.json()["items"]] == ["scoped-note"]
    assert in_scope.json()["items"][0]["workspace_id"] == "workspace-a"
    assert in_scope.json()["items"][0]["brand_id"] == "brand-a"

    wrong_brand = await client.get("/publish-candidates?brand_id=brand-b", headers=headers)
    assert wrong_brand.status_code == 200
    assert wrong_brand.json()["items"] == []

    wrong_workspace = await client.get(
        "/publish-candidates?brand_id=brand-a",
        headers={"X-Workspace-Id": "workspace-b", "X-User-Id": "user-1"},
    )
    assert wrong_workspace.status_code == 200
    assert wrong_workspace.json()["items"] == []


@pytest.mark.asyncio
async def test_complete_workflow_v2_is_idempotent(client):
    thread_id, _run_id = await _seed_complete_ready_run(client)

    first = await client.post(f"/threads/{thread_id}/complete")
    second = await client.post(f"/threads/{thread_id}/complete")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["publish_candidate_count"] == first.json()["publish_candidate_count"] == 2

    candidates = await client.get("/publish-candidates")
    assert len(candidates.json()["items"]) == 2

    timeline = await client.get(f"/threads/{thread_id}/timeline")
    assert len([m for m in timeline.json()["messages"] if m["message_type"] == "artifact_result"]) == 1


@pytest.mark.asyncio
async def test_complete_without_final_publishes_only_accepted_generated_notes(client):
    created = await client.post("/threads", json={"title": "Accepted Notes Only"})
    assert created.status_code == 201
    thread_id = created.json()["thread_id"]

    async with WorkflowRunManager(settings.SQLITE_DB_PATH) as manager:
        run = await manager.start_run(thread_id=thread_id, user_id="user-1")
        draft = await manager.attach_artifact(
            run_id=run.run_id,
            artifact_type=WorkflowArtifactType.GENERATED_NOTE,
            payload={"note_id": "draft", "title": "草稿", "content": "不发布"},
        )
        accepted = await manager.attach_artifact(
            run_id=run.run_id,
            artifact_type=WorkflowArtifactType.GENERATED_NOTE,
            payload={"note_id": "accepted", "title": "已接受", "content": "发布"},
        )
    async with WorkflowStore(settings.SQLITE_DB_PATH) as store:
        await store.update_artifact_status(draft.artifact_id, "superseded")
        await store.update_artifact_status(accepted.artifact_id, "accepted")
    await app.state.thread_store.update_thread_active_run(thread_id, run.run_id)

    response = await client.post(f"/threads/{thread_id}/complete")

    assert response.status_code == 200
    assert response.json()["publish_candidate_count"] == 1
    candidates = await client.get("/publish-candidates")
    assert [item["note_id"] for item in candidates.json()["items"]] == ["accepted"]
