"""Integration tests for T6 workflow-bound job worker execution."""

from __future__ import annotations

import uuid

import pytest

from app.agents.orchestrator import JobOrchestrationError
from app.config import settings
from app.memory.job_store import JobStore
from app.memory.session_state import SessionManager
from app.memory.workflow_store import WorkflowStore
from app.models.workflow import WorkflowPhase, WorkflowRunStatus, WorkflowStepStatus
from app.services.workflow_run_manager import WorkflowRunManager
from app.workers.job_worker import JobWorker


async def _create_session(db_path: str, session_id: str) -> None:
    async with SessionManager(db_path) as manager:
        await manager.create_session(session_id, "u1", "workflow queue test")


async def _create_workflow_job(
    db_path: str,
    *,
    max_attempts: int = 3,
):
    session_id = str(uuid.uuid4())
    await _create_session(db_path, session_id)
    async with WorkflowRunManager(db_path) as manager:
        run = await manager.start_run(thread_id=f"thread-{session_id}", user_id="u1")
        steps = await manager.initialize_steps(
            run.run_id,
            [{"step_name": "strategy.llm_synthesize", "phase": WorkflowPhase.STRATEGY}],
        )
    async with JobStore(db_path) as store:
        job, _ = await store.enqueue(
            session_id=session_id,
            job_type="strategy",
            payload={"step_name": "strategy.llm_synthesize"},
            run_id=run.run_id,
            step_id=steps[0].step_id,
            max_attempts=max_attempts,
        )
    return session_id, run, steps[0], job


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "workflow_job_worker.db"
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", str(db_path))
    return str(db_path)


class SuccessOrchestrator:
    async def run_job(self, job, **kwargs):
        return {"artifact_refs": [{"artifact_id": "artifact-1", "type": "strategy"}]}


class RetryableOrchestrator:
    async def run_job(self, job, **kwargs):
        raise JobOrchestrationError("temporary", error_code="LLM_TIMEOUT", retryable=True)


class PermanentOrchestrator:
    async def run_job(self, job, **kwargs):
        raise JobOrchestrationError("fatal", error_code="FATAL", retryable=False)


@pytest.mark.asyncio
async def test_workflow_job_success_completes_step(isolated_db):
    _session_id, run, step, job = await _create_workflow_job(isolated_db)

    async with JobStore(isolated_db) as store:
        worker = JobWorker(job_store=store, orchestrator=SuccessOrchestrator())
        assert await worker.run_once() is True

        refreshed_job = await store.get_job(job.id)

    async with WorkflowStore(isolated_db) as workflow_store:
        refreshed_step = await workflow_store.get_step(step.step_id)
        events = await workflow_store.list_events(run.run_id)

    assert refreshed_job is not None
    assert refreshed_job.status == "succeeded"
    assert refreshed_step is not None
    assert refreshed_step.status == WorkflowStepStatus.SUCCEEDED
    assert refreshed_step.output_artifact_refs_json == [{"artifact_id": "artifact-1", "type": "strategy"}]
    assert "step_started" in [event.event_type for event in events]
    assert "step_completed" in [event.event_type for event in events]


@pytest.mark.asyncio
async def test_workflow_job_retryable_error_retries_job_and_step(isolated_db):
    _session_id, _run, step, job = await _create_workflow_job(isolated_db, max_attempts=3)

    async with JobStore(isolated_db) as store:
        worker = JobWorker(job_store=store, orchestrator=RetryableOrchestrator())
        result = await worker._execute_job(await store.lease_one())

        refreshed_job = await store.get_job(job.id)

    async with WorkflowStore(isolated_db) as workflow_store:
        refreshed_step = await workflow_store.get_step(step.step_id)

    assert result.retry_scheduled is True
    assert refreshed_job is not None
    assert refreshed_job.status == "retrying"
    assert refreshed_job.last_error_code == "LLM_TIMEOUT"
    assert refreshed_step is not None
    assert refreshed_step.status == WorkflowStepStatus.RETRYING
    assert refreshed_step.error_code == "LLM_TIMEOUT"


@pytest.mark.asyncio
async def test_workflow_job_permanent_error_fails_job_and_step(isolated_db):
    _session_id, _run, step, job = await _create_workflow_job(isolated_db)

    async with JobStore(isolated_db) as store:
        worker = JobWorker(job_store=store, orchestrator=PermanentOrchestrator())
        result = await worker._execute_job(await store.lease_one())

        refreshed_job = await store.get_job(job.id)

    async with WorkflowStore(isolated_db) as workflow_store:
        refreshed_step = await workflow_store.get_step(step.step_id)

    assert result.failed is True
    assert refreshed_job is not None
    assert refreshed_job.status == "failed"
    assert refreshed_job.last_error_code == "FATAL"
    assert refreshed_step is not None
    assert refreshed_step.status == WorkflowStepStatus.FAILED
    assert refreshed_step.error_code == "FATAL"


@pytest.mark.asyncio
async def test_cancel_run_prevents_late_success_commit(isolated_db):
    _session_id, run, step, job = await _create_workflow_job(isolated_db)

    class CancelBeforeSuccessOrchestrator:
        async def run_job(self, job, **kwargs):
            async with WorkflowRunManager(isolated_db) as manager:
                await manager.cancel_run(run.run_id)
            return {"artifact_refs": [{"artifact_id": "late", "type": "strategy"}]}

    async with JobStore(isolated_db) as store:
        worker = JobWorker(job_store=store, orchestrator=CancelBeforeSuccessOrchestrator())
        result = await worker._execute_job(await store.lease_one())

        refreshed_job = await store.get_job(job.id)

    async with WorkflowStore(isolated_db) as workflow_store:
        refreshed_run = await workflow_store.get_run(run.run_id)
        refreshed_step = await workflow_store.get_step(step.step_id)

    assert result.success is False
    assert refreshed_job is not None
    assert refreshed_job.status == "cancelled"
    assert refreshed_run is not None
    assert refreshed_run.status == WorkflowRunStatus.CANCELLED
    assert refreshed_step is not None
    assert refreshed_step.status == WorkflowStepStatus.CANCELLED
    assert refreshed_step.output_artifact_refs_json is None
