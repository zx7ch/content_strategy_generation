"""E2E tests for T3 workflow snapshot API."""

from __future__ import annotations

import aiosqlite
import httpx
import pytest

from app.api.routes.router import app
from app.config import settings
from app.memory.job_store import JobStore
from app.memory.workflow_store import WorkflowStore
from app.models.workflow import WorkflowArtifactType, WorkflowConstraintType, WorkflowPhase
from app.services.workflow_run_manager import WorkflowRunManager


@pytest.fixture
async def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "workflow_snapshot.db")
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", db_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def _seed_snapshot(db_path: str):
    async with WorkflowRunManager(db_path) as manager:
        run = await manager.start_run(thread_id="thread-1", user_id="user-1")
        steps = await manager.initialize_steps(
            run.run_id,
            [
                {"step_name": "intake.capture_request", "phase": WorkflowPhase.INTAKE},
                {"step_name": "strategy.llm_synthesize", "phase": WorkflowPhase.STRATEGY},
            ],
        )
        await manager.start_step(run.run_id, "intake.capture_request", job_id="job-active")

    async with WorkflowStore(db_path) as store:
        await store.create_child_task(
            run_id=run.run_id,
            step_id=steps[0].step_id,
            task_type="note_generation",
            slot_index=0,
        )
        await store.create_artifact(
            run_id=run.run_id,
            thread_id=run.thread_id,
            artifact_type=WorkflowArtifactType.STRATEGY,
            payload={"positioning": "轻运动"},
            storage_table="strategy_data",
            storage_key="strategy-1",
        )
        await store.create_constraint(
            run_id=run.run_id,
            thread_id=run.thread_id,
            message_id="msg-constraint",
            raw_text="风格更生活化",
            constraint_type=WorkflowConstraintType.STYLE,
            scope="run",
            normalized={"tone": "lifestyle"},
        )

    async with JobStore(db_path) as job_store:
        await job_store.enqueue(
            session_id="session-1",
            job_type="strategy",
            payload={"run_id": run.run_id},
            idempotency_key="job-active-key",
        )
        assert job_store._conn is not None
        await job_store._conn.execute("UPDATE jobs SET id='job-active' WHERE id LIKE 'job_%'")
        await job_store._conn.commit()

    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            """
            UPDATE workflow_runs
            SET active_job_id='job-active', active_job_type='strategy'
            WHERE run_id=?
            """,
            (run.run_id,),
        )
        await conn.commit()

    return run


@pytest.mark.asyncio
async def test_workflow_snapshot_restores_current_state(client):
    db_path = settings.SQLITE_DB_PATH
    run = await _seed_snapshot(db_path)

    response = await client.get(f"/workflow-runs/{run.run_id}/snapshot")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"run", "steps", "child_tasks", "artifacts", "constraints", "active_job"}
    assert body["run"]["run_id"] == run.run_id
    assert body["run"]["status"] == "running"
    assert body["run"]["current_step"] == "intake.capture_request"
    assert [step["step_name"] for step in body["steps"]] == [
        "intake.capture_request",
        "strategy.llm_synthesize",
    ]
    assert body["steps"][0]["status"] == "running"
    assert body["child_tasks"][0]["task_type"] == "note_generation"
    assert body["artifacts"][0]["artifact_type"] == "strategy"
    assert body["constraints"][0]["constraint_type"] == "style"
    assert body["active_job"]["id"] == "job-active"


@pytest.mark.asyncio
async def test_workflow_snapshot_thread_mismatch_returns_clear_error(client):
    run = await _seed_snapshot(settings.SQLITE_DB_PATH)

    response = await client.get(
        f"/workflow-runs/{run.run_id}/snapshot",
        params={"thread_id": "other-thread"},
    )

    assert response.status_code == 409
    body = response.json()
    assert body["error_code"] == "THREAD_RUN_MISMATCH"
    assert body["error_details"]["run_thread_id"] == "thread-1"


@pytest.mark.asyncio
async def test_workflow_snapshot_missing_run_returns_404(client):
    response = await client.get("/workflow-runs/run_missing/snapshot")

    assert response.status_code == 404
    assert response.json()["error_code"] == "WORKFLOW_RUN_NOT_FOUND"
