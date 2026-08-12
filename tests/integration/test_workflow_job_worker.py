"""Integration tests for T6 workflow-bound job worker execution."""

from __future__ import annotations

import uuid

import pytest

from app.agents.orchestrator import JobOrchestrationError, Orchestrator
from app.config import settings
from app.memory.job_store import JobStore
from app.memory.session_state import SessionManager
from app.memory.thread_store import ThreadStore
from app.memory.workflow_store import WorkflowStore
from app.models.workflow import WorkflowPhase, WorkflowRunStatus, WorkflowStepStatus
from app.services.workflow_run_manager import WorkflowRunManager
from app.services.step_executors import StepExecutionResult, StepExecutorRegistry, StrategyStepExecutor
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
    async with ThreadStore(db_path) as thread_store:
        thread = await thread_store.create_thread(title="workflow queue test")
        message = await thread_store.append_message(
            thread_id=thread["id"],
            role="user",
            text="workflow queue test",
            intent="start_workflow",
        )
    async with WorkflowRunManager(db_path) as manager:
        run = await manager.start_run(
            thread_id=thread["id"],
            user_id="u1",
            user_message_id=message["id"],
            initial_request=message["text"],
        )
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


class NoArtifactExecutor:
    async def execute(self, run_id, step_name):
        return StepExecutionResult(step_name=step_name)


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
async def test_workflow_job_auto_enqueues_next_step_and_completes_run_without_legacy_session(isolated_db):
    async with ThreadStore(isolated_db) as thread_store:
        thread = await thread_store.create_thread(title="auto advance")
        message = await thread_store.append_message(
            thread_id=thread["id"],
            role="user",
            text="auto advance",
        )
    async with WorkflowRunManager(isolated_db) as manager:
        run = await manager.start_run(
            thread_id=thread["id"],
            user_id="u1",
            user_message_id=message["id"],
            initial_request=message["text"],
        )
        steps = await manager.initialize_steps(
            run.run_id,
            [
                {"step_name": "intake.capture_request", "phase": WorkflowPhase.INTAKE},
                {"step_name": "context.build_context", "phase": WorkflowPhase.CONTEXT},
            ],
        )
    async with JobStore(isolated_db) as store:
        first_job, _ = await store.enqueue(
            session_id=run.run_id,
            job_type="strategy",
            payload={"step_name": steps[0].step_name},
            run_id=run.run_id,
            step_id=steps[0].step_id,
            idempotency_key=f"workflow-step:{run.run_id}:{steps[0].step_id}",
        )
        worker = JobWorker(
            job_store=store,
            orchestrator=Orchestrator(
                db_path=isolated_db,
                step_executor_registry=StepExecutorRegistry(
                    {
                        steps[0].step_name: NoArtifactExecutor(),
                        steps[1].step_name: NoArtifactExecutor(),
                    }
                ),
            ),
        )
        assert first_job.session_id == run.run_id
        assert await worker.run_once() is True
        second_job = await store.get_latest_job_for_session(run.run_id)
        assert second_job is not None
        assert second_job.payload["step_name"] == steps[1].step_name
        assert await worker.run_once() is True

    async with WorkflowStore(isolated_db) as workflow_store:
        refreshed_run = await workflow_store.get_run(run.run_id)
        refreshed_steps = await workflow_store.list_steps(run.run_id)
        events = await workflow_store.list_events(run.run_id)

    assert refreshed_run is not None
    assert refreshed_run.status == WorkflowRunStatus.SUCCEEDED
    assert [step.status for step in refreshed_steps] == [
        WorkflowStepStatus.SUCCEEDED,
        WorkflowStepStatus.SUCCEEDED,
    ]
    assert "run_succeeded" in [event.event_type for event in events]


@pytest.mark.asyncio
async def test_workflow_job_executes_registered_step_executor_and_does_not_enqueue_legacy_generate(isolated_db):
    session_id, run, step, job = await _create_workflow_job(isolated_db)

    async def fake_strategy(context):
        assert context.run["run_id"] == run.run_id
        return {"positioning": "registry path"}

    registry = StepExecutorRegistry(
        {
            "strategy.llm_synthesize": StrategyStepExecutor(
                db_path=isolated_db,
                strategy_runner=fake_strategy,
            )
        }
    )

    async with JobStore(isolated_db) as store:
        worker = JobWorker(
            job_store=store,
            orchestrator=Orchestrator(db_path=isolated_db, step_executor_registry=registry),
        )
        result = await worker._execute_job(await store.lease_one())
        refreshed_job = await store.get_job(job.id)
        generate_count = await store.count_jobs(session_id, "generate")

    async with WorkflowStore(isolated_db) as workflow_store:
        refreshed_step = await workflow_store.get_step(step.step_id)
        artifacts = await workflow_store.list_artifacts(run.run_id)

    assert result.success is True
    assert refreshed_job is not None
    assert refreshed_job.status == "succeeded"
    assert generate_count == 0
    assert refreshed_step is not None
    assert refreshed_step.status == WorkflowStepStatus.SUCCEEDED
    assert refreshed_step.output_artifact_refs_json[0]["artifact_type"] == "strategy"
    assert artifacts[-1].payload_json["positioning"] == "registry path"


@pytest.mark.asyncio
async def test_unsupported_workflow_step_fails_job_and_step(isolated_db):
    _session_id, _run, step, job = await _create_workflow_job(isolated_db)

    async with JobStore(isolated_db) as store:
        worker = JobWorker(
            job_store=store,
            orchestrator=Orchestrator(db_path=isolated_db, step_executor_registry=StepExecutorRegistry()),
        )
        result = await worker._execute_job(await store.lease_one())
        refreshed_job = await store.get_job(job.id)

    async with WorkflowStore(isolated_db) as workflow_store:
        refreshed_step = await workflow_store.get_step(step.step_id)

    assert result.failed is True
    assert refreshed_job is not None
    assert refreshed_job.status == "failed"
    assert refreshed_job.last_error_code == "WORKFLOW_STEP_UNSUPPORTED"
    assert refreshed_step is not None
    assert refreshed_step.status == WorkflowStepStatus.FAILED
    assert refreshed_step.error_code == "WORKFLOW_STEP_UNSUPPORTED"


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
