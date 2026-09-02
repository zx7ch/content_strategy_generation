from __future__ import annotations

import asyncio
from pathlib import Path

from app.core.runtime_write_coordinator import RuntimeWriteCoordinator
from app.core.sqlite_connection_roles import (
    SQLiteConnectionOpened,
    observe_sqlite_connections,
)
from app.memory.job_mutations import bootstrap_job_store_schema, job_mutation_handlers
from app.memory.job_store import JobStore


def test_job_store_enqueue_uses_runtime_writer(tmp_path: Path) -> None:
    async def exercise(opened: list[SQLiteConnectionOpened]) -> None:
        database = tmp_path / "jobs.sqlite"
        writer = RuntimeWriteCoordinator(
            database,
            handlers=job_mutation_handlers(),
        )
        await writer.start()
        async with JobStore(str(database), writer=writer) as jobs:
            created, was_created = await jobs.enqueue(
                session_id="session-owned",
                job_type="strategy",
                payload={"subject": "single writer"},
                idempotency_key="owned-enqueue",
            )
            loaded = await jobs.get_job(created.id)
        await writer.close()

        assert loaded == created
        assert was_created is True
        assert created.status == "queued"
        assert created.idempotency_key == "owned-enqueue"
        assert [event.role for event in opened].count("writer") == 1
        assert all(event.role != "bootstrap" for event in opened)

    opened: list[SQLiteConnectionOpened] = []
    bootstrap_job_store_schema(tmp_path / "jobs.sqlite")
    with observe_sqlite_connections(opened.append):
        asyncio.run(exercise(opened))


def test_job_store_lifecycle_and_events_use_runtime_writer(tmp_path: Path) -> None:
    async def exercise() -> None:
        writer = RuntimeWriteCoordinator(database, handlers=job_mutation_handlers())
        await writer.start()
        async with JobStore(str(database), writer=writer) as jobs:
            queued, _ = await jobs.enqueue(
                session_id="session-lifecycle",
                job_type="generate",
                run_id="run-lifecycle",
            )
            leased = await jobs.lease_one()
            assert leased is not None
            assert leased.id == queued.id
            assert leased.status == "running"
            assert await jobs.mark_failed(
                leased.id,
                error_code="TEST_FAILURE",
                error_message="expected",
            )

            pausable, _ = await jobs.enqueue(
                session_id="session-lifecycle",
                job_type="strategy",
                run_id="run-lifecycle",
            )
            assert (await jobs.pause_job(pausable.id)).status == "paused"
            assert (await jobs.resume_job(pausable.id)).status == "queued"
            assert (await jobs.cancel_job(pausable.id)).status == "cancelled"

            event = await jobs.append_session_event(
                session_id="session-lifecycle",
                event_name="job_tested",
                job_id=pausable.id,
                payload={"owned": True},
            )
            assert event.event_name == "job_tested"
            assert event.payload == {"owned": True}
        await writer.close()

    database = tmp_path / "jobs.sqlite"
    bootstrap_job_store_schema(database)
    asyncio.run(exercise())
