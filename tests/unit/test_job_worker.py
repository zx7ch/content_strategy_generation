from __future__ import annotations

import uuid

import pytest

from app.config import settings
from app.memory.job_store import JobStore
from app.memory.session_state import SessionManager
from app.models.session import SessionStage
from app.workers.job_worker import JobWorker, RetryableJobError


async def _create_session(db_path: str, session_id: str) -> None:
    async with SessionManager(db_path) as manager:
        await manager.create_session(session_id, "u1", "worker test")


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "job_worker_unit.db"
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", str(db_path))
    return str(db_path)


@pytest.mark.asyncio
async def test_execute_job_marks_failed_after_retry_budget_exhausted(isolated_db):
    session_id = str(uuid.uuid4())
    await _create_session(isolated_db, session_id)

    class AlwaysRetryOrchestrator:
        async def run_job(self, job, **kwargs):
            raise RetryableJobError("temporary failure", error_code="LLM_TIMEOUT")

    async with JobStore(isolated_db) as store:
        job, _ = await store.enqueue(
            session_id=session_id,
            job_type="strategy",
            max_attempts=1,
        )
        worker = JobWorker(job_store=store, orchestrator=AlwaysRetryOrchestrator(), poll_interval_ms=10)

        result = await worker.run_once()

        assert result is True
        refreshed = await store.get_job(job.id)
        assert refreshed is not None
        assert refreshed.status == "failed"
        assert refreshed.attempts == 1
        assert refreshed.last_error_code == "JOB_MAX_RETRIES_EXCEEDED"

    async with SessionManager(isolated_db) as manager:
        session = await manager.get_session(session_id)
        assert session is not None
        assert session.stage == SessionStage.FAILED
        assert session.error is not None
        assert session.error.code == "JOB_MAX_RETRIES_EXCEEDED"
