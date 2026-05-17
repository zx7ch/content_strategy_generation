"""Integration tests for P1-6 queue worker + API enqueue flow."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import uuid

import aiosqlite
from fastapi.testclient import TestClient
import pytest

from app.agents.orchestrator import JobOrchestrationError
from app.api.routes.router import app
from app.config import settings
from app.memory.job_store import JobStore
from app.memory.session_state import SessionManager
from app.workers.job_worker import JobWorker


async def _create_session(db_path: str, session_id: str) -> None:
    async with SessionManager(db_path) as manager:
        await manager.create_session(session_id, "u1", "queue test")


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "job_worker.db"
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", str(db_path))
    return str(db_path)


def test_strategy_and_generate_enqueue_returns_202_with_job_id(isolated_db):
    session_id = str(uuid.uuid4())
    asyncio.run(_create_session(isolated_db, session_id))

    with TestClient(app) as client:
        strategy_resp = client.post(f"/sessions/{session_id}/strategy", json={"foo": "bar"})
        assert strategy_resp.status_code == 202
        strategy_payload = strategy_resp.json()
        assert strategy_payload["session_id"] == session_id
        assert strategy_payload["job_status"] == "queued"
        assert strategy_payload["job_id"].startswith("job_")

        generate_resp = client.post(
            f"/sessions/{session_id}/generate",
            json={"topic": "护肤", "output_language": "en-US"},
        )
        assert generate_resp.status_code == 202
        generate_payload = generate_resp.json()
        assert generate_payload["session_id"] == session_id
        assert generate_payload["job_status"] == "queued"
        assert generate_payload["job_id"].startswith("job_")


@pytest.mark.asyncio
async def test_worker_retry_backoff_then_failed_after_max_attempts(isolated_db):
    session_id = str(uuid.uuid4())
    await _create_session(isolated_db, session_id)

    class RetryAlwaysOrchestrator:
        async def run_job(self, job, **kwargs):
            raise JobOrchestrationError(
                "temporary failure",
                error_code="LLM_TIMEOUT",
                retryable=True,
            )

    async with JobStore(isolated_db) as store:
        job, _ = await store.enqueue(
            session_id=session_id,
            job_type="strategy",
            max_attempts=2,
        )
        worker = JobWorker(job_store=store, orchestrator=RetryAlwaysOrchestrator(), poll_interval_ms=10)

        processed = await worker.run_once()
        assert processed is True

        after_first = await store.get_job(job.id)
        assert after_first is not None
        assert after_first.status == "retrying"
        assert after_first.last_error_code == "LLM_TIMEOUT"
        assert after_first.not_before is not None

        await store._conn.execute(
            "UPDATE jobs SET not_before = DATETIME(CURRENT_TIMESTAMP, '-1 second') WHERE id = ?",
            (job.id,),
        )
        await store._conn.commit()

        processed = await worker.run_once()
        assert processed is True

        after_second = await store.get_job(job.id)
        assert after_second is not None
        assert after_second.status == "failed"
        assert after_second.attempts == 2
        assert after_second.last_error_code == "JOB_MAX_RETRIES_EXCEEDED"


@pytest.mark.asyncio
async def test_worker_replay_keeps_business_write_idempotent_with_upsert(isolated_db):
    session_id = str(uuid.uuid4())
    await _create_session(isolated_db, session_id)

    async with aiosqlite.connect(isolated_db) as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_side_effects (
                session_id TEXT NOT NULL,
                job_type TEXT NOT NULL,
                processed_at TEXT NOT NULL,
                PRIMARY KEY (session_id, job_type)
            )
            """
        )
        await conn.commit()

    class IdempotentWriteOrchestrator:
        async def run_job(self, job, **kwargs):
            async with aiosqlite.connect(isolated_db) as conn:
                await conn.execute(
                    """
                    INSERT INTO business_side_effects(session_id, job_type, processed_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(session_id, job_type) DO UPDATE
                    SET processed_at = excluded.processed_at
                    """,
                    (job.session_id, job.job_type, datetime.utcnow().isoformat()),
                )
                await conn.commit()
            return {"ok": True}

    async with JobStore(isolated_db) as store:
        job, _ = await store.enqueue(session_id=session_id, job_type="strategy")
        worker = JobWorker(job_store=store, orchestrator=IdempotentWriteOrchestrator(), poll_interval_ms=10)

        assert await worker.run_once() is True

        # Simulate replay: set succeeded job back to retrying and let worker execute again.
        await store._conn.execute(
            "UPDATE jobs SET status='retrying', not_before=CURRENT_TIMESTAMP WHERE id=?",
            (job.id,),
        )
        await store._conn.commit()

        assert await worker.run_once() is True

    async with aiosqlite.connect(isolated_db) as conn:
        # With auto-enqueue, both strategy and generate each run exactly once
        # across the two run_once() calls (idempotent upsert prevents duplicates).
        async with conn.execute("SELECT COUNT(*) FROM business_side_effects") as cursor:
            row = await cursor.fetchone()
            assert row[0] == 2
        async with conn.execute(
            "SELECT COUNT(*) FROM business_side_effects WHERE job_type = 'strategy'"
        ) as cursor:
            row = await cursor.fetchone()
            assert row[0] == 1


def test_resume_endpoint_is_idempotent(isolated_db):
    session_id = str(uuid.uuid4())
    asyncio.run(_create_session(isolated_db, session_id))

    async def _prepare_paused_job() -> None:
        async with JobStore(isolated_db) as store:
            await store.enqueue(session_id=session_id, job_type="strategy")
            await store.pause_session_jobs(session_id)

    asyncio.run(_prepare_paused_job())

    with TestClient(app) as client:
        first = client.post(f"/sessions/{session_id}/resume")
        assert first.status_code == 200
        first_payload = first.json()
        assert first_payload["session_id"] == session_id
        assert first_payload["lifecycle_state"] == "alive"
        assert first_payload["resumed_jobs"] == 1

        second = client.post(f"/sessions/{session_id}/resume")
        assert second.status_code == 200
        second_payload = second.json()
        assert second_payload["resumed_jobs"] == 0


@pytest.mark.asyncio
async def test_purged_session_cancels_paused_jobs_and_worker_skips_execution(isolated_db):
    session_id = str(uuid.uuid4())
    await _create_session(isolated_db, session_id)

    class NoopOrchestrator:
        async def run_job(self, job, **kwargs):
            return {"job_id": job.id}

    async with JobStore(isolated_db) as store:
        first_job, _ = await store.enqueue(session_id=session_id, job_type="strategy")
        second_job, _ = await store.enqueue(session_id=session_id, job_type="generate")
        await store.pause_session_jobs(session_id)

    async with SessionManager(isolated_db) as manager:
        stale_ts = (datetime.utcnow() - timedelta(days=11)).isoformat()
        await manager._conn.execute(
            "UPDATE sessions SET last_user_activity_at = ?, last_activity_at = ? WHERE session_id = ?",
            (stale_ts, stale_ts, session_id),
        )
        await manager._conn.commit()
        lifecycle = await manager.refresh_lifecycle_state(session_id)

    async with JobStore(isolated_db) as store:
        first_ref = await store.get_job(first_job.id)
        second_ref = await store.get_job(second_job.id)
        worker = JobWorker(job_store=store, orchestrator=NoopOrchestrator(), poll_interval_ms=10)
        processed = await worker.run_once()

    assert lifecycle is not None
    assert lifecycle.value == "purged"
    assert first_ref is not None and first_ref.status == "cancelled"
    assert second_ref is not None and second_ref.status == "cancelled"
    assert processed is False
