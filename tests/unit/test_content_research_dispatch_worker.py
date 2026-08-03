from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.content_research.api_schemas import ContentResearchFormalResearchResponse
from app.content_research.async_dispatch import AsyncFormalResearchDispatchRepository
from app.content_research.async_pipeline_store import AsyncDirectionalPersistenceSession
from app.content_research.persistence_models import StageCheckpointRecord
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.content_research.worker import ContentResearchDispatchWorker


class FakeFormalResearchService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []

    async def start_formal_research(self, *, workflow_run_id, request):
        self.calls.append((workflow_run_id, request.provider, request.limit))
        return ContentResearchFormalResearchResponse(
            workflow_run_id=workflow_run_id,
            status="completed",
            task_count=1,
            completed_task_count=1,
            partial_completed_task_count=0,
            provider=request.provider,
            source_kind=request.source_kind,
            limit_per_specialist=request.limit,
        )


@pytest.mark.asyncio
async def test_async_idle_dispatch_claim_does_not_acquire_a_sqlite_writer_lock(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "dispatch.db"))
    dispatch = AsyncFormalResearchDispatchRepository(store._db_path)

    assert await dispatch.claim_next(owner="worker") is None


@pytest.mark.asyncio
async def test_expired_dispatch_lease_is_recovered_by_a_new_worker(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "dispatch.db"))
    dispatch = AsyncFormalResearchDispatchRepository(store._db_path)
    await dispatch.enqueue(
        workflow_run_id="run-recover", provider="xiaohongshu", source_kind="search_result", limit=12
    )
    with store._connect() as conn:
        conn.execute(
            "UPDATE content_research_dispatch_jobs SET status = 'running', lease_expires_at = ? WHERE workflow_run_id = ?",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), "run-recover"),
        )
    service = FakeFormalResearchService()
    worker = ContentResearchDispatchWorker(store=store, service_factory=lambda: service)

    assert await worker.run_once() is True
    assert service.calls == [("run-recover", "xiaohongshu", 12)]
    with store._connect() as conn:
        status = conn.execute(
            "SELECT status FROM content_research_dispatch_jobs WHERE workflow_run_id = ?",
            ("run-recover",),
        ).fetchone()[0]
    assert status == "completed"


@pytest.mark.asyncio
async def test_completed_dispatch_is_requeued_only_by_explicit_retry(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "dispatch.db"))
    dispatch = AsyncFormalResearchDispatchRepository(store._db_path)
    await dispatch.enqueue(
        workflow_run_id="run-retry", provider="xiaohongshu", source_kind="search_result", limit=12
    )
    first = await dispatch.claim_next(owner="worker")
    assert first is not None
    assert await dispatch.complete(
        workflow_run_id="run-retry", owner="worker", token=str(first["lease_token"])
    )

    normal = await dispatch.enqueue(
        workflow_run_id="run-retry", provider="other", source_kind="search_result", limit=99
    )
    assert normal["status"] == "completed"
    retried = await dispatch.enqueue(
        workflow_run_id="run-retry", provider="xiaohongshu", source_kind="search_result", limit=12,
        retry_completed=True,
    )
    assert retried["status"] == "queued"
    assert retried["lease_owner"] is None
    assert retried["last_error"] is None


@pytest.mark.asyncio
async def test_stale_worker_token_cannot_terminalize_released_job(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "dispatch.db"))
    dispatch = AsyncFormalResearchDispatchRepository(store._db_path)
    await dispatch.enqueue(
        workflow_run_id="run-fenced", provider="xiaohongshu", source_kind="search_result", limit=12
    )
    first = await dispatch.claim_next(owner="worker-a", lease_seconds=1)
    assert first is not None
    with store._connect() as conn:
        conn.execute(
            "UPDATE content_research_dispatch_jobs SET lease_expires_at=? WHERE workflow_run_id=?",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), "run-fenced"),
        )
    second = await dispatch.claim_next(owner="worker-b", lease_seconds=60)
    assert second is not None

    assert not await dispatch.complete(
        workflow_run_id="run-fenced", owner="worker-a", token=str(first["lease_token"])
    )
    assert await dispatch.complete(
        workflow_run_id="run-fenced", owner="worker-b", token=str(second["lease_token"])
    )


@pytest.mark.asyncio
async def test_event_wakeup_dispatches_without_waiting_for_recovery_scan(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "dispatch.db"))
    dispatch = AsyncFormalResearchDispatchRepository(store._db_path)
    service = FakeFormalResearchService()
    wake_event = asyncio.Event()
    worker = ContentResearchDispatchWorker(
        store=store,
        service_factory=lambda: service,
        wake_event=wake_event,
        recovery_scan_seconds=60,
    )
    stop_event = asyncio.Event()
    task = asyncio.create_task(worker.run_loop(stop_event=stop_event))
    try:
        # Let the worker reach its event wait. A 60-second recovery scan must
        # not determine the latency of an interactive confirmation.
        await asyncio.sleep(0.02)
        await dispatch.enqueue(
            workflow_run_id="run-event", provider="xiaohongshu", source_kind="search_result", limit=9
        )
        wake_event.set()

        async def wait_for_call() -> None:
            while not service.calls:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(wait_for_call(), timeout=0.5)
        assert service.calls == [("run-event", "xiaohongshu", 9)]
    finally:
        stop_event.set()
        wake_event.set()
        await task


@pytest.mark.asyncio
async def test_async_pipeline_session_flushes_checkpoint_without_sync_store_io(tmp_path):
    db_path = str(tmp_path / "dispatch.db")
    SQLiteContentResearchStore(db_path)
    session = await AsyncDirectionalPersistenceSession.open(db_path, workflow_run_id="run-async")
    checkpoint = StageCheckpointRecord(
        id="scp-async",
        schema_version="content_research_stage_checkpoint_v1",
        payload={"workflow_run_id": "run-async", "status": "started"},
        workflow_run_id="run-async",
        subagent_task_id="task-async",
        stage_name="operation",
        input_fingerprint="fingerprint-async",
        status="running",
    )
    session.save_stage_checkpoint(checkpoint)
    await session.flush()

    reloaded = await AsyncDirectionalPersistenceSession.open(db_path, workflow_run_id="run-async")
    assert reloaded.get_typed_record(StageCheckpointRecord, "scp-async") == checkpoint


@pytest.mark.asyncio
async def test_async_pipeline_session_replaces_superseded_checkpoint_on_same_run_recovery(tmp_path):
    db_path = str(tmp_path / "dispatch.db")
    store = SQLiteContentResearchStore(db_path)
    original = StageCheckpointRecord(
        id="scp-recovery",
        schema_version="content_research_stage_checkpoint_v1",
        payload={"workflow_run_id": "run-recovery", "failure_reason": "auth_required"},
        workflow_run_id="run-recovery",
        subagent_task_id="task-recovery",
        stage_name="operation",
        input_fingerprint="fingerprint-recovery",
        status="superseded",
    )
    store.save_stage_checkpoint(original)

    session = await AsyncDirectionalPersistenceSession.open(
        db_path, workflow_run_id="run-recovery"
    )
    replacement = StageCheckpointRecord(
        id=original.id,
        schema_version=original.schema_version,
        payload={"workflow_run_id": "run-recovery", "failure_reason": "auth_required"},
        workflow_run_id=original.workflow_run_id,
        subagent_task_id=original.subagent_task_id,
        stage_name=original.stage_name,
        input_fingerprint=original.input_fingerprint,
        status="completed",
    )
    session.save_stage_checkpoint(replacement)
    await session.flush()

    reloaded = await AsyncDirectionalPersistenceSession.open(
        db_path, workflow_run_id="run-recovery"
    )
    assert reloaded.get_typed_record(StageCheckpointRecord, original.id) == replacement


@pytest.mark.asyncio
async def test_stale_async_session_cannot_overwrite_recovered_checkpoint(tmp_path):
    db_path = str(tmp_path / "dispatch.db")
    store = SQLiteContentResearchStore(db_path)
    original = StageCheckpointRecord(
        id="scp-stale-recovery",
        schema_version="content_research_stage_checkpoint_v1",
        payload={"attempt": 0},
        workflow_run_id="run-recovery",
        subagent_task_id="task-recovery",
        stage_name="operation",
        input_fingerprint="fingerprint-recovery",
        status="superseded",
    )
    store.save_stage_checkpoint(original)
    first = await AsyncDirectionalPersistenceSession.open(
        db_path, workflow_run_id="run-recovery"
    )
    stale = await AsyncDirectionalPersistenceSession.open(
        db_path, workflow_run_id="run-recovery"
    )
    first.save_stage_checkpoint(
        replace(original, status="completed", payload={"attempt": 1})
    )
    stale.save_stage_checkpoint(
        replace(original, status="completed", payload={"attempt": 2})
    )

    await first.flush()
    with pytest.raises(RuntimeError, match="immutable persistence conflict"):
        await stale.flush()

    reloaded = await AsyncDirectionalPersistenceSession.open(
        db_path, workflow_run_id="run-recovery"
    )
    assert reloaded.get_typed_record(
        StageCheckpointRecord, original.id
    ).payload == {"attempt": 1}
