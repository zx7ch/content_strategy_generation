"""Integration tests for T10.1 workflow step recovery and commit guard."""

from __future__ import annotations

import uuid

import pytest

from app.config import settings
from app.agents.orchestrator import Orchestrator
from app.memory.job_store import JobStore
from app.memory.session_state import SessionManager
from app.memory.thread_store import ThreadStore
from app.memory.workflow_store import WorkflowStore
from app.models.workflow import WorkflowArtifactType, WorkflowPhase, WorkflowRunStatus, WorkflowStepStatus
from app.services.step_executors import ArtifactStepExecutor, StepExecutorRegistry
from app.services.workflow_run_manager import WorkflowRunManager
from app.workers.job_worker import JobWorker


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "workflow_step_recovery_e2e.db"
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", str(db_path))
    return str(db_path)


async def _create_session(db_path: str, session_id: str) -> None:
    async with SessionManager(db_path) as manager:
        await manager.create_session(session_id, "user-1", "real boundary test")


async def _seed_workflow_job(db_path: str, *, step_name: str = "strategy.llm_synthesize", max_attempts: int = 3):
    session_id = f"sess-{uuid.uuid4().hex}"
    await _create_session(db_path, session_id)
    thread_store = ThreadStore(db_path)
    await thread_store.connect()
    thread = await thread_store.create_thread(title="T10.1 recovery")
    await thread_store.close()
    async with WorkflowRunManager(db_path) as manager:
        run = await manager.start_run(
            thread_id=thread["id"],
            user_id="user-1",
            initial_request="real boundary test",
        )
        step = (
            await manager.initialize_steps(
                run.run_id,
                [{"step_name": step_name, "phase": WorkflowPhase.STRATEGY}],
            )
        )[0]
    async with JobStore(db_path) as store:
        job, _created = await store.enqueue(
            session_id=session_id,
            job_type="strategy",
            payload={"step_name": step.step_name},
            run_id=run.run_id,
            step_id=step.step_id,
            max_attempts=max_attempts,
        )
    return session_id, run, step, job


async def _run_worker_once(db_path: str, registry: StepExecutorRegistry) -> None:
    async with JobStore(db_path) as store:
        worker = JobWorker(
            job_store=store,
            orchestrator=Orchestrator(db_path=db_path, step_executor_registry=registry),
        )
        await worker.run_once()


@pytest.mark.asyncio
async def test_late_external_success_after_cancel_does_not_attach_artifact(isolated_db):
    _session_id, run, step, job = await _seed_workflow_job(isolated_db)

    async def cancel_then_return_payload(_context):
        async with WorkflowRunManager(isolated_db) as manager:
            await manager.cancel_run(run.run_id)
        return {"positioning": "late success"}

    registry = StepExecutorRegistry(
        {
            step.step_name: ArtifactStepExecutor(
                db_path=isolated_db,
                runner=cancel_then_return_payload,
                artifact_type=WorkflowArtifactType.STRATEGY,
            )
        }
    )

    await _run_worker_once(isolated_db, registry)

    async with WorkflowStore(isolated_db) as store:
        refreshed_run = await store.get_run(run.run_id)
        refreshed_step = await store.get_step(step.step_id)
        artifacts = await store.list_artifacts(run.run_id)
        events = await store.list_events(run.run_id)
    async with JobStore(isolated_db) as store:
        refreshed_job = await store.get_job(job.id)

    assert refreshed_run is not None and refreshed_run.status == WorkflowRunStatus.CANCELLED
    assert refreshed_step is not None and refreshed_step.status == WorkflowStepStatus.CANCELLED
    assert refreshed_step.output_artifact_refs_json is None
    assert artifacts == []
    assert refreshed_job is not None and refreshed_job.status == "cancelled"
    assert "run_cancelled" in [event.event_type for event in events]


@pytest.mark.asyncio
async def test_late_external_success_after_pause_does_not_attach_artifact(isolated_db):
    _session_id, run, step, job = await _seed_workflow_job(isolated_db)

    async def pause_then_return_payload(_context):
        async with WorkflowRunManager(isolated_db) as manager:
            await manager.pause_run(run.run_id)
        return {"positioning": "late paused success"}

    registry = StepExecutorRegistry(
        {
            step.step_name: ArtifactStepExecutor(
                db_path=isolated_db,
                runner=pause_then_return_payload,
                artifact_type=WorkflowArtifactType.STRATEGY,
            )
        }
    )

    await _run_worker_once(isolated_db, registry)

    async with WorkflowStore(isolated_db) as store:
        refreshed_run = await store.get_run(run.run_id)
        refreshed_step = await store.get_step(step.step_id)
        artifacts = await store.list_artifacts(run.run_id)
        events = await store.list_events(run.run_id)
    async with JobStore(isolated_db) as store:
        refreshed_job = await store.get_job(job.id)

    assert refreshed_run is not None and refreshed_run.status == WorkflowRunStatus.PAUSED
    assert refreshed_step is not None and refreshed_step.status == WorkflowStepStatus.RETRYING
    assert refreshed_step.output_artifact_refs_json is None
    assert artifacts == []
    assert refreshed_job is not None and refreshed_job.status == "paused"
    assert "run_paused" in [event.event_type for event in events]


@pytest.mark.asyncio
async def test_lease_expired_running_workflow_job_retries_step(isolated_db):
    _session_id, run, step, job = await _seed_workflow_job(isolated_db, max_attempts=3)

    async with WorkflowRunManager(isolated_db) as manager:
        await manager.start_step(run.run_id, step.step_name, job_id=job.id)
    async with JobStore(isolated_db) as store:
        assert store._conn is not None
        await store._conn.execute(
            """
            UPDATE jobs
            SET status='running', attempts=1, lease_expires_at='2000-01-01 00:00:00'
            WHERE id=?
            """,
            (job.id,),
        )
        await store._conn.commit()
        recovered = await store.recover_expired_running_jobs()
        refreshed_job = await store.get_job(job.id)

    async with WorkflowStore(isolated_db) as store:
        refreshed_step = await store.get_step(step.step_id)

    assert recovered == 1
    assert refreshed_job is not None and refreshed_job.status == "retrying"
    assert refreshed_step is not None and refreshed_step.status == WorkflowStepStatus.RETRYING
    assert refreshed_step.error_code == "LEASE_EXPIRED"


@pytest.mark.asyncio
async def test_lease_expired_exhausted_workflow_job_fails_step(isolated_db):
    _session_id, run, step, job = await _seed_workflow_job(isolated_db, max_attempts=1)

    async with WorkflowRunManager(isolated_db) as manager:
        await manager.start_step(run.run_id, step.step_name, job_id=job.id)
    async with JobStore(isolated_db) as store:
        assert store._conn is not None
        await store._conn.execute(
            """
            UPDATE jobs
            SET status='running', attempts=1, max_attempts=1, lease_expires_at='2000-01-01 00:00:00'
            WHERE id=?
            """,
            (job.id,),
        )
        await store._conn.commit()
        recovered = await store.recover_expired_running_jobs()
        refreshed_job = await store.get_job(job.id)

    async with WorkflowStore(isolated_db) as store:
        refreshed_step = await store.get_step(step.step_id)

    assert recovered == 1
    assert refreshed_job is not None and refreshed_job.status == "failed"
    assert refreshed_step is not None and refreshed_step.status == WorkflowStepStatus.FAILED
    assert refreshed_step.error_code == "LEASE_EXPIRED"
