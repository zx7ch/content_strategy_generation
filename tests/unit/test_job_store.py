"""Unit tests for P1-6 JobStore queue semantics."""

from __future__ import annotations

import uuid

import pytest

from app.config import settings
from app.memory.job_store import JobStore
from app.memory.session_state import SessionManager
from app.memory.workflow_store import WorkflowStore
from app.models.workflow import WorkflowPhase, WorkflowStepStatus
from app.services.workflow_run_manager import WorkflowRunManager


async def _create_session(db_path: str, session_id: str) -> None:
    async with SessionManager(db_path) as manager:
        await manager.create_session(session_id, "u1", "test query")


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "job_store.db"
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", str(db_path))
    return str(db_path)


@pytest.mark.asyncio
async def test_enqueue_idempotency_key_deduplicates_jobs(isolated_db):
    session_id = str(uuid.uuid4())
    await _create_session(isolated_db, session_id)

    async with JobStore(isolated_db) as store:
        first, created_1 = await store.enqueue(
            session_id=session_id,
            job_type="strategy",
            payload={"k": 1},
            idempotency_key="idem-1",
        )
        second, created_2 = await store.enqueue(
            session_id=session_id,
            job_type="strategy",
            payload={"k": 2},
            idempotency_key="idem-1",
        )

        assert created_1 is True
        assert created_2 is False
        assert first.id == second.id
        assert await store.count_jobs(session_id, "strategy") == 1


@pytest.mark.asyncio
async def test_lease_enforces_single_running_job_per_session(isolated_db):
    session_id = str(uuid.uuid4())
    await _create_session(isolated_db, session_id)

    async with JobStore(isolated_db) as store:
        await store.enqueue(session_id=session_id, job_type="strategy")
        await store.enqueue(session_id=session_id, job_type="generate")

        leased_1 = await store.lease_one()
        assert leased_1 is not None
        assert leased_1.status == "running"

        leased_2 = await store.lease_one()
        assert leased_2 is None

        await store.mark_succeeded(leased_1.id)
        leased_3 = await store.lease_one()
        assert leased_3 is not None
        assert leased_3.session_id == session_id


@pytest.mark.asyncio
async def test_recover_expired_running_jobs_to_retrying(isolated_db):
    session_id = str(uuid.uuid4())
    await _create_session(isolated_db, session_id)

    async with JobStore(isolated_db) as store:
        job, _ = await store.enqueue(session_id=session_id, job_type="strategy")
        leased = await store.lease_one(lease_seconds=1)
        assert leased is not None

        await store._conn.execute(
            "UPDATE jobs SET lease_expires_at = DATETIME(CURRENT_TIMESTAMP, '-10 seconds') WHERE id = ?",
            (job.id,),
        )
        await store._conn.commit()

        recovered = await store.recover_expired_running_jobs()
        assert recovered == 1

        refreshed = await store.get_job(job.id)
        assert refreshed is not None
        assert refreshed.status == "retrying"
        assert refreshed.lease_expires_at is None
        assert refreshed.last_error_code == "LEASE_EXPIRED"


@pytest.mark.asyncio
async def test_recover_expired_running_jobs_to_failed_when_retry_budget_exhausted(isolated_db):
    session_id = str(uuid.uuid4())
    await _create_session(isolated_db, session_id)

    async with JobStore(isolated_db) as store:
        job, _ = await store.enqueue(session_id=session_id, job_type="strategy", max_attempts=1)
        leased = await store.lease_one(lease_seconds=1)
        assert leased is not None

        await store._conn.execute(
            "UPDATE jobs SET lease_expires_at = DATETIME(CURRENT_TIMESTAMP, '-10 seconds') WHERE id = ?",
            (job.id,),
        )
        await store._conn.commit()

        recovered = await store.recover_expired_running_jobs()
        assert recovered == 1

        refreshed = await store.get_job(job.id)
        assert refreshed is not None
        assert refreshed.status == "failed"
        assert refreshed.lease_expires_at is None
        assert refreshed.last_error_code == "LEASE_EXPIRED"


@pytest.mark.asyncio
async def test_job_store_bootstrap_sessions_schema_matches_current_lifecycle_fields(isolated_db):
    async with JobStore(isolated_db) as store:
        async with store._conn.execute("PRAGMA table_info(sessions)") as cursor:
            columns = {row[1] async for row in cursor}

    assert "pause_requested" in columns
    assert "pause_requested_at" in columns
    assert "reindex_state" in columns
    assert "reindex_attempts" in columns
    assert "freeze_until" not in columns


@pytest.mark.asyncio
async def test_pause_then_resume_session_jobs_status_flow(isolated_db):
    session_id = str(uuid.uuid4())
    await _create_session(isolated_db, session_id)

    async with JobStore(isolated_db) as store:
        queued_job, _ = await store.enqueue(session_id=session_id, job_type="strategy")
        retrying_job, _ = await store.enqueue(session_id=session_id, job_type="generate")

        await store._conn.execute(
            "UPDATE jobs SET status='retrying', updated_at=CURRENT_TIMESTAMP WHERE id = ?",
            (retrying_job.id,),
        )
        await store._conn.commit()

        paused = await store.pause_session_jobs(session_id)
        assert paused == 2

        queued_ref = await store.get_job(queued_job.id)
        retrying_ref = await store.get_job(retrying_job.id)
        assert queued_ref is not None and queued_ref.status == "paused"
        assert retrying_ref is not None and retrying_ref.status == "paused"

        resumed = await store.resume_paused_jobs(session_id)
        assert resumed == 2

        queued_ref = await store.get_job(queued_job.id)
        retrying_ref = await store.get_job(retrying_job.id)
        assert queued_ref is not None and queued_ref.status == "queued"
        assert retrying_ref is not None and retrying_ref.status == "queued"


@pytest.mark.asyncio
async def test_cancel_session_jobs_marks_all_unfinished_jobs_cancelled(isolated_db):
    session_id = str(uuid.uuid4())
    await _create_session(isolated_db, session_id)

    async with JobStore(isolated_db) as store:
        queued_job, _ = await store.enqueue(session_id=session_id, job_type="strategy")
        retrying_job, _ = await store.enqueue(session_id=session_id, job_type="generate")

        await store._conn.execute(
            "UPDATE jobs SET status='retrying', updated_at=CURRENT_TIMESTAMP WHERE id = ?",
            (retrying_job.id,),
        )
        await store._conn.commit()

        cancelled = await store.cancel_session_jobs(session_id)
        assert cancelled == 2

        queued_ref = await store.get_job(queued_job.id)
        retrying_ref = await store.get_job(retrying_job.id)
        assert queued_ref is not None and queued_ref.status == "cancelled"
        assert queued_ref.cancel_reason == "session_purged"
        assert retrying_ref is not None and retrying_ref.status == "cancelled"
        assert retrying_ref.cancel_reason == "session_purged"


# ---------------------------------------------------------------------------
# ALIGN-6: job-level pause / resume / cancel
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pause_job_queued_transitions_to_paused(isolated_db):
    session_id = str(uuid.uuid4())
    await _create_session(isolated_db, session_id)

    async with JobStore(isolated_db) as store:
        job, _ = await store.enqueue(session_id=session_id, job_type="strategy")
        assert job.status == "queued"

        updated = await store.pause_job(job.id)
        assert updated is not None
        assert updated.status == "paused"


@pytest.mark.asyncio
async def test_pause_job_running_does_not_change_status(isolated_db):
    session_id = str(uuid.uuid4())
    await _create_session(isolated_db, session_id)

    async with JobStore(isolated_db) as store:
        job, _ = await store.enqueue(session_id=session_id, job_type="strategy")
        # Simulate a running job by direct DB update
        await store._conn.execute(
            "UPDATE jobs SET status='running' WHERE id = ?", (job.id,)
        )
        await store._conn.commit()

        updated = await store.pause_job(job.id)
        assert updated is not None
        assert updated.status == "running"  # no change; caller decides 409


@pytest.mark.asyncio
async def test_resume_job_paused_transitions_to_queued(isolated_db):
    session_id = str(uuid.uuid4())
    await _create_session(isolated_db, session_id)

    async with JobStore(isolated_db) as store:
        job, _ = await store.enqueue(session_id=session_id, job_type="strategy")
        await store.pause_job(job.id)

        updated = await store.resume_job(job.id)
        assert updated is not None
        assert updated.status == "queued"


@pytest.mark.asyncio
async def test_cancel_job_queued_transitions_to_cancelled(isolated_db):
    session_id = str(uuid.uuid4())
    await _create_session(isolated_db, session_id)

    async with JobStore(isolated_db) as store:
        job, _ = await store.enqueue(session_id=session_id, job_type="strategy")

        updated = await store.cancel_job(job.id, reason="user_cancelled")
        assert updated is not None
        assert updated.status == "cancelled"
        assert updated.cancel_reason == "user_cancelled"


@pytest.mark.asyncio
async def test_cancel_job_running_transitions_to_cancelled(isolated_db):
    """cancel_job also cancels running jobs — worker checks before mark_succeeded."""
    session_id = str(uuid.uuid4())
    await _create_session(isolated_db, session_id)

    async with JobStore(isolated_db) as store:
        job, _ = await store.enqueue(session_id=session_id, job_type="strategy")
        await store._conn.execute(
            "UPDATE jobs SET status='running' WHERE id = ?", (job.id,)
        )
        await store._conn.commit()

        updated = await store.cancel_job(job.id)
        assert updated is not None
        assert updated.status == "cancelled"


@pytest.mark.asyncio
async def test_pause_job_not_found_returns_none(isolated_db):
    async with JobStore(isolated_db) as store:
        result = await store.pause_job("does-not-exist")
        assert result is None


@pytest.mark.asyncio
async def test_enqueue_persists_workflow_refs(isolated_db):
    session_id = str(uuid.uuid4())
    await _create_session(isolated_db, session_id)

    async with JobStore(isolated_db) as store:
        job, created = await store.enqueue(
            session_id=session_id,
            job_type="strategy",
            payload={"step_name": "strategy.llm_synthesize"},
            run_id="run-1",
            step_id="step-1",
            child_task_id="child-1",
        )

    assert created is True
    assert job.run_id == "run-1"
    assert job.step_id == "step-1"
    assert job.child_task_id == "child-1"
    assert job.payload["step_name"] == "strategy.llm_synthesize"


@pytest.mark.asyncio
async def test_recover_expired_workflow_job_syncs_step_retrying(isolated_db):
    session_id = str(uuid.uuid4())
    await _create_session(isolated_db, session_id)
    async with WorkflowRunManager(isolated_db) as manager:
        run = await manager.start_run(thread_id="thread-1", user_id="u1")
        steps = await manager.initialize_steps(
            run.run_id,
            [{"step_name": "strategy.llm_synthesize", "phase": WorkflowPhase.STRATEGY}],
        )

    async with JobStore(isolated_db) as store:
        job, _ = await store.enqueue(
            session_id=session_id,
            job_type="strategy",
            payload={"step_name": "strategy.llm_synthesize"},
            run_id=run.run_id,
            step_id=steps[0].step_id,
            max_attempts=3,
        )
        leased = await store.lease_one(lease_seconds=1)
        assert leased is not None
        async with WorkflowRunManager(isolated_db) as manager:
            await manager.start_step(run.run_id, "strategy.llm_synthesize", job_id=leased.id)
        await store._conn.execute(
            "UPDATE jobs SET lease_expires_at = DATETIME(CURRENT_TIMESTAMP, '-10 seconds') WHERE id = ?",
            (job.id,),
        )
        await store._conn.commit()

        recovered = await store.recover_expired_running_jobs()
        refreshed_job = await store.get_job(job.id)

    async with WorkflowStore(isolated_db) as workflow_store:
        refreshed_step = await workflow_store.get_step(steps[0].step_id)

    assert recovered == 1
    assert refreshed_job is not None
    assert refreshed_job.status == "retrying"
    assert refreshed_step is not None
    assert refreshed_step.status == WorkflowStepStatus.RETRYING
    assert refreshed_step.error_code == "LEASE_EXPIRED"


@pytest.mark.asyncio
async def test_recover_expired_workflow_job_syncs_step_failed_when_budget_exhausted(isolated_db):
    session_id = str(uuid.uuid4())
    await _create_session(isolated_db, session_id)
    async with WorkflowRunManager(isolated_db) as manager:
        run = await manager.start_run(thread_id="thread-1", user_id="u1")
        steps = await manager.initialize_steps(
            run.run_id,
            [{"step_name": "strategy.llm_synthesize", "phase": WorkflowPhase.STRATEGY}],
        )

    async with JobStore(isolated_db) as store:
        job, _ = await store.enqueue(
            session_id=session_id,
            job_type="strategy",
            payload={"step_name": "strategy.llm_synthesize"},
            run_id=run.run_id,
            step_id=steps[0].step_id,
            max_attempts=1,
        )
        leased = await store.lease_one(lease_seconds=1)
        assert leased is not None
        async with WorkflowRunManager(isolated_db) as manager:
            await manager.start_step(run.run_id, "strategy.llm_synthesize", job_id=leased.id)
        await store._conn.execute(
            "UPDATE jobs SET lease_expires_at = DATETIME(CURRENT_TIMESTAMP, '-10 seconds') WHERE id = ?",
            (job.id,),
        )
        await store._conn.commit()

        recovered = await store.recover_expired_running_jobs()
        refreshed_job = await store.get_job(job.id)

    async with WorkflowStore(isolated_db) as workflow_store:
        refreshed_step = await workflow_store.get_step(steps[0].step_id)

    assert recovered == 1
    assert refreshed_job is not None
    assert refreshed_job.status == "failed"
    assert refreshed_step is not None
    assert refreshed_step.status == WorkflowStepStatus.FAILED
    assert refreshed_step.error_code == "LEASE_EXPIRED"
