"""E2E tests for T8 artifact reference timeline recovery."""

from __future__ import annotations

import httpx
import pytest

from app.api.routes.router import app
from app.config import settings
from app.memory.thread_store import ThreadStore
from app.models.workflow import WorkflowArtifactType, WorkflowPhase
from app.services.workflow_run_manager import WorkflowRunManager


@pytest.fixture
async def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "timeline_artifacts.db")
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


async def _seed_workflow_result(client: httpx.AsyncClient) -> tuple[str, str, dict[str, str]]:
    created = await client.post("/threads", json={"title": "Artifact Timeline"})
    assert created.status_code == 201
    thread_id = created.json()["thread_id"]

    async with WorkflowRunManager(settings.SQLITE_DB_PATH) as manager:
        run = await manager.start_run(thread_id=thread_id, user_id="user-1")
        steps = await manager.initialize_steps(
            run.run_id,
            [{"step_name": "generation.aggregate_notes", "phase": WorkflowPhase.GENERATION}],
        )
        strategy = await manager.attach_artifact(
            run_id=run.run_id,
            artifact_type=WorkflowArtifactType.STRATEGY,
            payload={"positioning": "通勤防晒衣", "audience": "25-35岁女性"},
            summary_text="strategy",
            created_by_step_id=steps[0].step_id,
        )
        note = await manager.attach_artifact(
            run_id=run.run_id,
            artifact_type=WorkflowArtifactType.GENERATED_NOTE,
            payload={"note_id": "note-1", "title": "通勤防晒衣", "content": "轻薄好穿", "tags": ["防晒衣"]},
            summary_text="通勤防晒衣",
            created_by_step_id=steps[0].step_id,
        )
        final = await manager.attach_artifact(
            run_id=run.run_id,
            artifact_type=WorkflowArtifactType.FINAL_RESULT,
            payload={
                "generated_notes": [
                    {
                        "artifact_id": note.artifact_id,
                        "artifact_type": "generated_note",
                        "payload_json": note.payload_json,
                    }
                ]
            },
            summary_text="final result",
            created_by_step_id=steps[0].step_id,
        )

    store = app.state.thread_store
    await store.update_thread_active_run(thread_id, run.run_id)
    return thread_id, run.run_id, {
        "strategy": strategy.artifact_id,
        "note": note.artifact_id,
        "final": final.artifact_id,
    }


@pytest.mark.asyncio
async def test_timeline_restores_artifact_result_reference(client):
    thread_id, run_id, artifacts = await _seed_workflow_result(client)

    result = await client.get(f"/threads/{thread_id}/result")
    assert result.status_code == 200
    assert result.json()["notes"][0]["note_id"] == "note-1"

    timeline = await client.get(f"/threads/{thread_id}/timeline")

    assert timeline.status_code == 200
    body = timeline.json()
    artifact_messages = [m for m in body["messages"] if m["message_type"] == "artifact_result"]
    assert len(artifact_messages) == 1
    message = artifact_messages[0]
    assert message["run_id"] == run_id
    assert message["text"] == "创作结果已生成。"
    assert artifacts["final"] in {ref["artifact_id"] for ref in message["artifact_refs"]}
    hydrated = message["artifact_refs"][0]["artifact"]
    assert hydrated["artifact_type"] == "final_result"
    assert hydrated["payload_json"]["generated_notes"][0]["payload_json"]["title"] == "通勤防晒衣"


@pytest.mark.asyncio
async def test_thread_result_recovers_strategy_and_notes_from_workflow_artifacts(client):
    thread_id, _run_id, _artifacts = await _seed_workflow_result(client)

    response = await client.get(f"/threads/{thread_id}/result")

    assert response.status_code == 200
    body = response.json()
    assert body["strategy"]["positioning"] == "通勤防晒衣"
    assert body["notes"] == [
        {
            "note_id": "note-1",
            "title": "通勤防晒衣",
            "content": "轻薄好穿",
            "tags": ["防晒衣"],
        }
    ]


@pytest.mark.asyncio
async def test_revision_artifact_does_not_overwrite_original_note(client):
    thread_id, _run_id, artifacts = await _seed_workflow_result(client)
    async with WorkflowRunManager(settings.SQLITE_DB_PATH) as manager:
        await manager.attach_artifact(
            run_id=_run_id,
            artifact_type=WorkflowArtifactType.GENERATED_NOTE,
            artifact_version=2,
            parent_artifact_id=artifacts["note"],
            payload={
                "patch_type": "merge",
                "base_artifact_id": artifacts["note"],
                "base_artifact_version": 2,
                "changed_fields": {"title": "改写防晒衣", "content": "更生活化", "tags": ["穿搭"]},
            },
            summary_text="rewrite",
        )

    snapshot = await client.get(f"/workflow-runs/{_run_id}/snapshot", params={"thread_id": thread_id})

    assert snapshot.status_code == 200
    notes = [a for a in snapshot.json()["artifacts"] if a["artifact_type"] == "generated_note"]
    assert len(notes) == 2
    assert {note["artifact_id"] for note in notes} >= {artifacts["note"]}
    assert any(note["parent_artifact_id"] == artifacts["note"] for note in notes)
    rewrite = next(note for note in notes if note["parent_artifact_id"] == artifacts["note"])
    assert rewrite["payload_mode"] == "patch"
    assert rewrite["payload_json"]["note_id"] == "note-1"
    assert rewrite["payload_json"]["title"] == "改写防晒衣"


@pytest.mark.asyncio
async def test_timeline_hydrates_patch_artifact_result_for_version_chain(client):
    thread_id, run_id, artifacts = await _seed_workflow_result(client)
    async with WorkflowRunManager(settings.SQLITE_DB_PATH) as manager:
        rewrite = await manager.attach_artifact(
            run_id=run_id,
            artifact_type=WorkflowArtifactType.GENERATED_NOTE,
            artifact_version=2,
            parent_artifact_id=artifacts["note"],
            payload={
                "patch_type": "merge",
                "base_artifact_id": artifacts["note"],
                "base_artifact_version": 2,
                "changed_fields": {"title": "改写防晒衣", "content": "更生活化", "tags": ["穿搭"]},
            },
            summary_text="rewrite",
        )

    await app.state.thread_store.append_artifact_result_message(
        thread_id=thread_id,
        run_id=run_id,
        artifact_refs=[
            {
                "artifact_id": rewrite.artifact_id,
                "artifact_type": "generated_note",
                "artifact_version": rewrite.artifact_version,
                "parent_artifact_id": artifacts["note"],
            }
        ],
        text="已生成修改版本。",
        idempotent=False,
    )

    timeline = await client.get(f"/threads/{thread_id}/timeline")

    assert timeline.status_code == 200
    artifact_messages = [m for m in timeline.json()["messages"] if m["message_type"] == "artifact_result"]
    rewrite_message = artifact_messages[-1]
    rewrite_ref = rewrite_message["artifact_refs"][0]
    assert rewrite_ref["parent_artifact_id"] == artifacts["note"]
    assert rewrite_ref["artifact"]["payload_mode"] == "patch"
    assert rewrite_ref["artifact"]["parent_artifact_id"] == artifacts["note"]
    assert rewrite_ref["artifact"]["materialized_payload_json"]["note_id"] == "note-1"
    assert rewrite_ref["artifact"]["materialized_payload_json"]["title"] == "改写防晒衣"
    assert "changed_fields" not in rewrite_ref["artifact"]["materialized_payload_json"]
