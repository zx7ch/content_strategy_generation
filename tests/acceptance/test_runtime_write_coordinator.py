from __future__ import annotations

import asyncio
import sqlite3
import threading
from pathlib import Path

import pytest

from app.core.runtime_write_coordinator import (
    MutationIdentityConflictError,
    PersistenceOverloadedError,
    PersistenceUnavailableError,
    RuntimeWriteCoordinator,
    TypedMutation,
    WriterShuttingDownError,
)


def _mutation(identity: str, value: str = "one") -> TypedMutation:
    return TypedMutation.for_diagnostic_fact(
        mutation_id=identity,
        run_id="run-writer-matrix",
        value=value,
    )


def _fact_count(database: Path, mutation_id: str) -> int:
    with sqlite3.connect(database) as connection:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM diagnostic_facts WHERE mutation_id=?",
                (mutation_id,),
            ).fetchone()[0]
        )


@pytest.mark.acceptance
def test_writer_admission_receipt_crash_and_caller_cancel_matrix(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        database = tmp_path / "writer.sqlite"
        writer = RuntimeWriteCoordinator(database)
        await writer.start()
        committed = await writer.submit(_mutation("normal"))
        replayed = await writer.submit(_mutation("normal"))
        assert committed.replayed is False
        assert replayed.replayed is True
        assert replayed.result_fields == committed.result_fields
        assert replayed.committed_revision == committed.committed_revision == 1
        with pytest.raises(MutationIdentityConflictError) as conflict:
            await writer.submit(_mutation("normal", value="changed"))
        assert conflict.value.error_code == "MUTATION_IDENTITY_CONFLICT"
        assert _fact_count(database, "normal") == 1
        await writer.close()

        crash_once = True

        def crash_before_commit(stage: str, mutation: TypedMutation) -> None:
            nonlocal crash_once
            if stage == "before_commit" and mutation.mutation_id == "before" and crash_once:
                crash_once = False
                raise OSError("disk secret must not escape")

        failed = RuntimeWriteCoordinator(database, fault_injector=crash_before_commit)
        await failed.start()
        with pytest.raises(PersistenceUnavailableError) as unavailable:
            await failed.submit(_mutation("before"))
        assert str(unavailable.value) == "PERSISTENCE_UNAVAILABLE"
        assert _fact_count(database, "before") == 0
        await failed.close()

        restarted = RuntimeWriteCoordinator(database)
        await restarted.start()
        after_restart = await restarted.submit(_mutation("before"))
        assert after_restart.replayed is False
        assert _fact_count(database, "before") == 1
        await restarted.close()

        def crash_after_commit(stage: str, mutation: TypedMutation) -> None:
            if stage == "after_commit_before_ack" and mutation.mutation_id == "after":
                raise OSError("ack channel lost with secret")

        uncertain = RuntimeWriteCoordinator(database, fault_injector=crash_after_commit)
        await uncertain.start()
        with pytest.raises(PersistenceUnavailableError):
            await uncertain.submit(_mutation("after"))
        await uncertain.close()

        replay_writer = RuntimeWriteCoordinator(database)
        await replay_writer.start()
        resolved = await replay_writer.submit(_mutation("after"))
        assert resolved.replayed is True
        assert _fact_count(database, "after") == 1
        await replay_writer.close()

        entered = threading.Event()
        release = threading.Event()

        def block_after_enqueue(stage: str, mutation: TypedMutation) -> None:
            if stage == "before_apply" and mutation.mutation_id == "cancelled-await":
                entered.set()
                release.wait(timeout=5)

        cancellation_writer = RuntimeWriteCoordinator(
            database,
            fault_injector=block_after_enqueue,
        )
        await cancellation_writer.start()
        awaiting = asyncio.create_task(
            cancellation_writer.submit(_mutation("cancelled-await"))
        )
        assert await asyncio.to_thread(entered.wait, 2)
        awaiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await awaiting
        release.set()
        resolved_after_cancel = await cancellation_writer.submit(
            _mutation("cancelled-await")
        )
        assert resolved_after_cancel.replayed is True
        assert _fact_count(database, "cancelled-await") == 1
        await cancellation_writer.close()

    asyncio.run(exercise())


@pytest.mark.acceptance
def test_writer_overload_and_fatal_failure_are_safe(tmp_path: Path) -> None:
    async def exercise() -> None:
        database = tmp_path / "writer-failures.sqlite"
        entered = threading.Event()
        release = threading.Event()

        def block_inflight(stage: str, mutation: TypedMutation) -> None:
            if stage == "before_apply" and mutation.mutation_id == "inflight":
                entered.set()
                release.wait(timeout=5)

        writer = RuntimeWriteCoordinator(
            database,
            queue_capacity=1,
            fault_injector=block_inflight,
        )
        await writer.start()
        inflight = asyncio.create_task(writer.submit(_mutation("inflight")))
        assert await asyncio.to_thread(entered.wait, 2)
        queued = asyncio.create_task(writer.submit(_mutation("queued")))
        await asyncio.sleep(0)
        with pytest.raises(PersistenceOverloadedError) as overloaded:
            await writer.submit(_mutation("rejected"))
        assert str(overloaded.value) == "LOCAL_PERSISTENCE_OVERLOADED"
        assert _fact_count(database, "rejected") == 0

        closing = asyncio.create_task(writer.close())
        with pytest.raises(WriterShuttingDownError) as shutting_down:
            await queued
        assert str(shutting_down.value) == "WRITER_SHUTTING_DOWN"
        release.set()
        await inflight
        await closing

        def fatal_disk(stage: str, mutation: TypedMutation) -> None:
            if stage == "before_commit":
                raise sqlite3.OperationalError("disk full /private/secret")

        failed = RuntimeWriteCoordinator(database, fault_injector=fatal_disk)
        await failed.start()
        with pytest.raises(PersistenceUnavailableError) as fatal:
            await failed.submit(_mutation("fatal"))
        assert str(fatal.value) == "PERSISTENCE_UNAVAILABLE"
        assert failed.availability == "persistence_unavailable"
        with pytest.raises(PersistenceUnavailableError):
            await failed.submit(_mutation("after-fatal"))
        assert _fact_count(database, "fatal") == 0
        assert _fact_count(database, "after-fatal") == 0
        await failed.close()

    asyncio.run(exercise())


@pytest.mark.acceptance
def test_duplicate_canonical_writer_is_rejected_before_opening_sqlite(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        database = tmp_path / "duplicate-writer.sqlite"
        first = RuntimeWriteCoordinator(database)
        duplicate = RuntimeWriteCoordinator(database)
        await first.start()
        try:
            with pytest.raises(RuntimeError, match="already has an active Writer"):
                await duplicate.start()
            assert duplicate.availability == "new"
            committed = await first.submit(_mutation("sole-writer"))
            assert committed.replayed is False
        finally:
            await duplicate.close()
            await first.close()

    asyncio.run(exercise())
