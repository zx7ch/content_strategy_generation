"""Acceptance coverage for T10 creator workflow-v2 cleanup and full loop."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.api.routes.router import _workflow_event_stream, app
from app.config import settings
from app.memory.thread_store import ThreadStore
from app.memory.workflow_store import WorkflowStore
from app.models.workflow import WorkflowArtifactType, WorkflowPhase
from app.services.workflow_run_manager import WorkflowRunManager


class _DisconnectedRequest:
    async def is_disconnected(self) -> bool:
        return True


@pytest.fixture
async def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "creator_workflow_v2_full_loop.db")
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
    response = await client.post("/threads", json={"title": "T10 Full Loop"})
    assert response.status_code == 201
    return response.json()["thread_id"]


async def _seed_result_artifacts(thread_id: str, run_id: str, *, prefix: str) -> list[dict]:
    async with WorkflowRunManager(settings.SQLITE_DB_PATH) as manager:
        steps = await manager.initialize_steps(
            run_id,
            [{"step_name": f"{prefix}.aggregate_notes", "phase": WorkflowPhase.GENERATION}],
        )
        first = await manager.attach_artifact(
            run_id=run_id,
            artifact_type=WorkflowArtifactType.GENERATED_NOTE,
            payload={
                "note_id": f"{prefix}-note-1",
                "title": f"{prefix} 第一篇",
                "content": "完整链路验收内容",
                "tags": ["T10"],
            },
            summary_text=f"{prefix} note 1",
            created_by_step_id=steps[-1].step_id,
        )
        second = await manager.attach_artifact(
            run_id=run_id,
            artifact_type=WorkflowArtifactType.GENERATED_NOTE,
            payload={
                "note_id": f"{prefix}-note-2",
                "title": f"{prefix} 第二篇",
                "content": "可修改的验收内容",
                "tags": ["workflow-v2"],
            },
            summary_text=f"{prefix} note 2",
            created_by_step_id=steps[-1].step_id,
        )
        await manager.attach_artifact(
            run_id=run_id,
            artifact_type=WorkflowArtifactType.FINAL_RESULT,
            payload={
                "generated_notes": [
                    {"artifact_id": first.artifact_id, "payload_json": first.payload_json},
                    {"artifact_id": second.artifact_id, "payload_json": second.payload_json},
                ]
            },
            summary_text=f"{prefix} final result",
            created_by_step_id=steps[-1].step_id,
        )

    refs = [
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
    ]
    await app.state.thread_store.append_artifact_result_message(
        thread_id=thread_id,
        run_id=run_id,
        artifact_refs=refs,
    )
    return refs


async def _workflow_event_payloads(run_id: str, after_event_id: int | None = None) -> list[dict]:
    chunks: list[str] = []
    async for chunk in _workflow_event_stream(
        _DisconnectedRequest(),
        run_id=run_id,
        after_event_id=after_event_id,
    ):
        chunks.append(chunk)
    payloads: list[dict] = []
    for chunk in chunks:
        data_line = next(line for line in chunk.splitlines() if line.startswith("data: "))
        payloads.append(json.loads(data_line.removeprefix("data: ")))
    return payloads


@pytest.mark.acceptance
@pytest.mark.asyncio
async def test_removed_workflow_endpoint_is_not_reachable(client):
    thread_id = await _create_thread(client)

    response = await client.post(
        f"/threads/{thread_id}/workflow",
        json={"user_query": "帮我生成一组小红书防晒衣笔记"},
    )

    assert response.status_code == 404

    thread = await client.get(f"/threads/{thread_id}")
    assert thread.status_code == 200
    detail = thread.json()["thread"]
    assert detail["active_run_id"] is None
    assert detail["active_workflow_session_id"] is None
    assert detail["active_job_id"] is None


@pytest.mark.acceptance
@pytest.mark.asyncio
async def test_message_driven_workflow_v2_full_loop_and_recovery(client):
    thread_id = await _create_thread(client)

    started = await client.post(
        f"/threads/{thread_id}/messages",
        json={"text": "帮我生成两篇防晒衣笔记"},
    )
    assert started.status_code == 201
    run_id = started.json()["command_result"]["run_id"]

    constrained = await client.post(
        f"/threads/{thread_id}/messages",
        json={"text": "目标用户改为25-35岁女性"},
    )
    assert constrained.status_code == 201
    assert constrained.json()["intent"] == "add_constraint"
    assert constrained.json()["active_run_snapshot"]["run"]["constraint_version"] == 1

    pause = await client.post(f"/threads/{thread_id}/messages", json={"text": "暂停一下"})
    assert pause.json()["command_result"]["action"] == "pause_run"
    assert pause.json()["active_run_snapshot"]["run"]["status"] == "pausing"
    async with WorkflowStore(settings.SQLITE_DB_PATH) as store:
        assert store._conn is not None
        await store._conn.execute("UPDATE workflow_runs SET status='paused' WHERE run_id=?", (run_id,))
        await store._conn.commit()
    resume = await client.post(f"/threads/{thread_id}/messages", json={"text": "继续"})
    assert resume.json()["command_result"]["action"] == "resume_run"
    assert resume.json()["active_run_snapshot"]["run"]["status"] == "running"
    status = await client.post(f"/threads/{thread_id}/messages", json={"text": "进度怎么样了？"})
    assert status.json()["command_result"]["action"] == "ask_status"
    assert status.json()["active_run_snapshot"]["run"]["run_id"] == run_id

    refs = await _seed_result_artifacts(thread_id, run_id, prefix="sunshirt")
    replay_payloads = await _workflow_event_payloads(run_id)
    assert {"run_started", "constraint_added", "run_pause_requested", "run_resumed", "artifact_attached"} <= {
        payload["event_type"] for payload in replay_payloads
    }
    latest_event_id = max(payload["event_id"] for payload in replay_payloads)
    assert await _workflow_event_payloads(run_id, after_event_id=latest_event_id) == []

    snapshot = await client.get(f"/workflow-runs/{run_id}/snapshot", params={"thread_id": thread_id})
    assert snapshot.status_code == 200
    assert {artifact["artifact_id"] for artifact in snapshot.json()["artifacts"]} >= {
        ref["artifact_id"] for ref in refs
    }

    revision = await client.post(
        f"/threads/{thread_id}/messages",
        json={"text": "把第 2 篇改生活化"},
    )
    assert revision.status_code == 201
    assert revision.json()["intent"] == "revise_artifact"
    assert revision.json()["command_result"]["accepted"] is True
    assert revision.json()["command_result"]["target_artifact_id"] == refs[1]["artifact_id"]

    regenerate = await client.post(
        f"/threads/{thread_id}/messages",
        json={"text": "重新生成一版"},
    )
    assert regenerate.status_code == 201
    assert regenerate.json()["command_result"] == {
        "action": "regenerate_artifact",
        "accepted": True,
        "run_id": run_id,
        "dispatch": "workflow_command",
    }

    rerun = await client.post(
        f"/threads/{thread_id}/messages",
        json={"text": "不要防晒衣了，改成徒步鞋"},
    )
    assert rerun.status_code == 201
    new_run_id = rerun.json()["command_result"]["run_id"]
    assert rerun.json()["intent"] == "rerun_workflow"
    assert rerun.json()["command_result"]["parent_run_id"] == run_id
    assert new_run_id != run_id

    await _seed_result_artifacts(thread_id, new_run_id, prefix="hiking")
    completed = await client.post(f"/threads/{thread_id}/complete")
    assert completed.status_code == 200
    assert completed.json()["publish_candidate_count"] == 2

    timeline = await client.get(f"/threads/{thread_id}/timeline")
    assert timeline.status_code == 200
    artifact_messages = [m for m in timeline.json()["messages"] if m["message_type"] == "artifact_result"]
    assert len(artifact_messages) >= 3
    assert {message["run_id"] for message in artifact_messages} >= {run_id, new_run_id}
    revision_refs = [
        ref
        for message in artifact_messages
        for ref in message["artifact_refs"]
        if ref.get("parent_artifact_id") == refs[1]["artifact_id"]
    ]
    assert revision_refs
    assert revision_refs[0]["artifact"]["payload_mode"] == "patch"

    candidates = await client.get("/publish-candidates")
    assert candidates.status_code == 200
    assert {item["session_id"] for item in candidates.json()["items"]} == {new_run_id}

    cancel_thread_id = await _create_thread(client)
    cancel_start = await client.post(
        f"/threads/{cancel_thread_id}/messages",
        json={"text": "帮我生成控制分支测试笔记"},
    )
    assert cancel_start.status_code == 201
    cancel_response = await client.post(
        f"/threads/{cancel_thread_id}/messages",
        json={"text": "取消任务"},
    )
    assert cancel_response.status_code == 201
    assert cancel_response.json()["command_result"]["action"] == "cancel_run"
    assert cancel_response.json()["active_run_snapshot"]["run"]["status"] == "cancelling"


@pytest.mark.acceptance
def test_frontend_and_docs_expose_only_workflow_v2_recovery_contracts():
    creator_page = Path("frontend/src/app/creator/page.tsx").read_text()
    api_client = Path("frontend/src/lib/api.ts").read_text()
    readme = Path("README.md").read_text()

    assert "inferTaskIntent" not in creator_page
    assert "active_run_snapshot" in creator_page
    assert "getWorkflowRunSnapshot" in creator_page
    assert "subscribeWorkflowRunEvents" in creator_page
    assert "/workflow-runs/${runId}/snapshot" in api_client
    assert "/workflow-runs/${runId}/events" in api_client
    assert "/threads/{thread_id}/messages" in readme
    assert "/threads/{thread_id}/workflow" in readme
    assert "workflow-v2" in readme
