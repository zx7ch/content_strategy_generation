"""Integration tests for T6.1 workflow pause/cancel job coordination."""

from __future__ import annotations

import uuid

import pytest

from app.config import settings
from app.memory.job_store import JobStore
from app.memory.session_state import SessionManager
from app.memory.workflow_store import WorkflowStore
from app.models.workflow import WorkflowPhase, WorkflowRunStatus, WorkflowStepStatus
from app.services.workflow_run_manager import WorkflowRunManager
from app.workers.job_worker import JobWorker


async def _create_session(db_path: str, session_id: str) -> None:
    async with SessionManager(db_path) as manager:
        await manager.create_session(session_id, "u1", "pause cancel test")


async def _seed_run_with_step(db_path: str):
    session_id = str(uuid.uuid4())
    await _create_session(db_path, session_id)
    async with WorkflowRunManager(db_path) as manager:
        run = await manager.start_run(thread_id=f"thread-{session_id}", user_id="u1")
        steps = await manager.initialize_steps(
            run.run_id,
            [{"step_name": "strategy.llm_synthesize", "phase": WorkflowPhase.STRATEGY}],
        )
    return session_id, run, steps[0]


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "workflow_pause_cancel_jobs.db"
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", str(db_path))
    return str(db_path)


class SuccessOrchestrator:
    async def run_job(self, job, **kwargs):
        return {"artifact_refs": [{"artifact_id": "artifact-1", "type": "strategy"}]}


class PauseBeforeSuccessOrchestrator:
    def __init__(self, db_path: str, run_id: str) -> None:
        self.db_path = db_path
        self.run_id = run_id

    async def run_job(self, job, **kwargs):
        async with WorkflowRunManager(self.db_path) as manager:
            await manager.pause_run(self.run_id)
        return {"artifact_refs": [{"artifact_id": "late", "type": "strategy"}]}


@pytest.mark.asyncio
async def test_pause_run_pauses_queued_and_retrying_workflow_jobs(isolated_db):
    session_id, run, step = await _seed_run_with_step(isolated_db)

    async with JobStore(isolated_db) as store:
        queued, _ = await store.enqueue(
            session_id=session_id,
            job_type="strategy",
            payload={"step_name": step.step_name},
            run_id=run.run_id,
            step_id=step.step_id,
        )
        retrying, _ = await store.enqueue(
            session_id=session_id,
            job_type="generate",
            payload={"step_name": step.step_name},
            run_id=run.run_id,
            step_id=step.step_id,
        )
        assert store._conn is not None
        await store._conn.execute("UPDATE jobs SET status='retrying' WHERE id=?", (retrying.id,))
        await store._conn.commit()

    async with WorkflowRunManager(isolated_db) as manager:
        paused = await manager.pause_run(run.run_id)

    async with JobStore(isolated_db) as store:
        queued_ref = await store.get_job(queued.id)
        retrying_ref = await store.get_job(retrying.id)
    async with WorkflowStore(isolated_db) as workflow_store:
        events = await workflow_store.list_events(run.run_id)

    assert paused.status == WorkflowRunStatus.PAUSING
    assert queued_ref is not None and queued_ref.status == "paused"
    assert retrying_ref is not None and retrying_ref.status == "paused"
    assert events[-1].event_type == "run_pause_requested"
    assert events[-1].payload_json["paused_job_count"] == 2


@pytest.mark.asyncio
async def test_running_job_safe_boundary_ack_pauses_run_step_and_job(isolated_db):
    session_id, run, step = await _seed_run_with_step(isolated_db)

    async with JobStore(isolated_db) as store:
        job, _ = await store.enqueue(
            session_id=session_id,
            job_type="strategy",
            payload={"step_name": step.step_name},
            run_id=run.run_id,
            step_id=step.step_id,
        )
        worker = JobWorker(
            job_store=store,
            orchestrator=PauseBeforeSuccessOrchestrator(isolated_db, run.run_id),
        )
        result = await worker.run_once()
        refreshed_job = await store.get_job(job.id)

    async with WorkflowStore(isolated_db) as workflow_store:
        refreshed_run = await workflow_store.get_run(run.run_id)
        refreshed_step = await workflow_store.get_step(step.step_id)
        events = await workflow_store.list_events(run.run_id)

    assert result is True
    assert refreshed_run is not None and refreshed_run.status == WorkflowRunStatus.PAUSED
    assert refreshed_step is not None
    assert refreshed_step.status == WorkflowStepStatus.RETRYING
    assert refreshed_step.output_artifact_refs_json is None
    assert refreshed_job is not None and refreshed_job.status == "paused"
    assert "run_paused" in [event.event_type for event in events]


@pytest.mark.asyncio
async def test_resume_run_resumes_paused_workflow_jobs(isolated_db):
    session_id, run, step = await _seed_run_with_step(isolated_db)
    async with JobStore(isolated_db) as store:
        job, _ = await store.enqueue(
            session_id=session_id,
            job_type="strategy",
            payload={"step_name": step.step_name},
            run_id=run.run_id,
            step_id=step.step_id,
        )
        await store.pause_workflow_run_jobs(run.run_id)

    async with WorkflowRunManager(isolated_db) as manager:
        await manager.pause_run(run.run_id)
        await manager.ack_pause_at_boundary(run.run_id, step.step_name)
        resumed = await manager.resume_run(run.run_id)

    async with JobStore(isolated_db) as store:
        refreshed_job = await store.get_job(job.id)

    assert resumed.status == WorkflowRunStatus.RUNNING
    assert refreshed_job is not None and refreshed_job.status == "queued"


@pytest.mark.asyncio
async def test_cancel_run_cancels_queued_running_jobs_and_child_tasks(isolated_db):
    session_id, run, step = await _seed_run_with_step(isolated_db)
    async with WorkflowRunManager(isolated_db) as manager:
        child = (
            await manager.create_child_tasks(
                run_id=run.run_id,
                step_id=step.step_id,
                tasks=[{"task_type": "note_generation", "slot_index": 0}],
            )
        )[0]

    async with JobStore(isolated_db) as store:
        queued, _ = await store.enqueue(
            session_id=session_id,
            job_type="generate",
            payload={"step_name": step.step_name},
            run_id=run.run_id,
            step_id=step.step_id,
            child_task_id=child.child_task_id,
        )
        running, _ = await store.enqueue(
            session_id=session_id,
            job_type="strategy",
            payload={"step_name": step.step_name},
            run_id=run.run_id,
            step_id=step.step_id,
        )
        assert store._conn is not None
        await store._conn.execute("UPDATE jobs SET status='running' WHERE id=?", (running.id,))
        await store._conn.commit()

    async with WorkflowRunManager(isolated_db) as manager:
        cancelling = await manager.cancel_run(run.run_id)

    async with JobStore(isolated_db) as store:
        queued_ref = await store.get_job(queued.id)
        running_ref = await store.get_job(running.id)
    async with WorkflowStore(isolated_db) as workflow_store:
        child_ref = await workflow_store.get_child_task(child.child_task_id)
        events = await workflow_store.list_events(run.run_id)

    assert cancelling.status == WorkflowRunStatus.CANCELLING
    assert queued_ref is not None and queued_ref.status == "cancelled"
    assert running_ref is not None and running_ref.status == "cancelled"
    assert child_ref is not None and child_ref.status == WorkflowStepStatus.CANCELLED
    assert events[-1].event_type == "run_cancel_requested"
    assert events[-1].payload_json["cancelled_job_count"] == 2


@pytest.mark.asyncio
async def test_late_worker_success_after_cancel_does_not_commit_artifact(isolated_db):
    session_id, run, step = await _seed_run_with_step(isolated_db)

    class CancelBeforeSuccessOrchestrator:
        async def run_job(self, job, **kwargs):
            async with WorkflowRunManager(isolated_db) as manager:
                await manager.cancel_run(run.run_id)
            return {"artifact_refs": [{"artifact_id": "late", "type": "strategy"}]}

    async with JobStore(isolated_db) as store:
        job, _ = await store.enqueue(
            session_id=session_id,
            job_type="strategy",
            payload={"step_name": step.step_name},
            run_id=run.run_id,
            step_id=step.step_id,
        )
        worker = JobWorker(job_store=store, orchestrator=CancelBeforeSuccessOrchestrator())
        await worker.run_once()
        refreshed_job = await store.get_job(job.id)

    async with WorkflowStore(isolated_db) as workflow_store:
        refreshed_run = await workflow_store.get_run(run.run_id)
        refreshed_step = await workflow_store.get_step(step.step_id)

    assert refreshed_job is not None and refreshed_job.status == "cancelled"
    assert refreshed_run is not None and refreshed_run.status == WorkflowRunStatus.CANCELLED
    assert refreshed_step is not None
    assert refreshed_step.status == WorkflowStepStatus.CANCELLED
    assert refreshed_step.output_artifact_refs_json is None
