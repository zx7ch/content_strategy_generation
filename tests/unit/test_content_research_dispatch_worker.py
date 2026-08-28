from __future__ import annotations

import asyncio
import sqlite3
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast

import pytest

from app.content_research.api_schemas import ContentResearchFormalResearchResponse
from app.content_research.async_dispatch import AsyncFormalResearchDispatchRepository
from app.content_research.async_pipeline_store import AsyncDirectionalPersistenceSession
from app.content_research.models import SubagentTaskRecord
from app.content_research.persistence_models import StageCheckpointRecord
from app.content_research.scope_contract import (
    CoverageSnapshot,
    DispatchLeaseContext,
    ExecutionContext,
    ExecutionLeaseFencedError,
    ScopeAuditEvent,
    ScopeConstraint,
    ScopeExecutionAttempt,
    ScopeExecutionAuthorization,
    ScopeExecutionContinuation,
    ScopeQueryGroupInput,
    build_scope_contract,
)
from app.content_research.service import (
    ContentResearchService,
    ReportPublicationMaterializationError,
)
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.content_research.worker import ContentResearchDispatchWorker


@pytest.mark.asyncio
async def test_dispatch_lease_renewal_keeps_sqlite_transaction_off_event_loop(
    tmp_path, monkeypatch
) -> None:
    repository = AsyncFormalResearchDispatchRepository(str(tmp_path / "dispatch-renew.db"))
    entered = threading.Event()
    release = threading.Event()

    def blocking_renew_sync(**_kwargs) -> bool:
        entered.set()
        assert release.wait(2)
        return True

    monkeypatch.setattr(repository, "_renew_sync", blocking_renew_sync)
    renewal = asyncio.create_task(
        repository.renew(
            workflow_run_id="run-renew",
            owner="worker-renew",
            token="token-renew",
        )
    )
    assert await asyncio.to_thread(entered.wait, 1)

    # A synchronous SQLite transaction in the heartbeat must never own the
    # event loop while it waits for another writer.
    await asyncio.wait_for(asyncio.sleep(0), timeout=0.1)
    release.set()
    assert await renewal is True


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


class FakeContinuationService(FakeFormalResearchService):
    def __init__(self) -> None:
        super().__init__()
        self.continuations: list[ScopeExecutionContinuation] = []

    async def execute_scope_continuation(self, continuation):
        self.continuations.append(continuation)


class FailingContinuationService(FakeContinuationService):
    async def execute_scope_continuation(self, continuation):
        await super().execute_scope_continuation(continuation)
        raise RuntimeError("authorized supplementary collection failed")


class ExecutionUnitContinuationService(FakeFormalResearchService):
    def __init__(self) -> None:
        super().__init__()
        self.executions: list[tuple[ScopeExecutionAttempt, ScopeExecutionContinuation]] = []

    async def execute_execution_unit(
        self, claim: ScopeExecutionAttempt, continuation: ScopeExecutionContinuation
    ) -> str:
        self.executions.append((claim, continuation))
        return "completed"


class PublicationFailureExecutionService(ExecutionUnitContinuationService):
    async def execute_execution_unit(
        self, claim: ScopeExecutionAttempt, continuation: ScopeExecutionContinuation
    ) -> str:
        self.executions.append((claim, continuation))
        raise ReportPublicationMaterializationError(
            "rpp_failed_materialization", ValueError("artifact write failed")
        )


def _save_execution_unit_continuation(
    store: SQLiteContentResearchStore,
) -> tuple[ScopeExecutionAuthorization, ScopeExecutionContinuation]:
    contract = build_scope_contract(
        workflow_run_id="run-execution-context",
        research_plan_id="plan-execution-context",
        version=1,
        constraints=(ScopeConstraint("core_object", "core", "shirt", "required"),),
        query_groups=(ScopeQueryGroupInput("shirt", "shirt"),),
    )
    store.save_scope_contract(contract)
    snapshot = CoverageSnapshot(
        id="coverage-execution-context",
        workflow_run_id=contract.workflow_run_id,
        scope_contract_id=contract.id,
        scope_contract_version=contract.version,
        state="awaiting_scope_decision",
        constraint_counts={},
        unmet_constraint_ids=("core_object",),
    )
    store.save_coverage_snapshot(snapshot)
    authorization = ScopeExecutionAuthorization(
        id="authorization-execution-context",
        workflow_run_id=contract.workflow_run_id,
        scope_contract_id=contract.id,
        scope_contract_version=contract.version,
        coverage_snapshot_id=snapshot.id,
        resolution="expand_required_constraint",
        execution_revision=2,
        state="authorized_collection",
    )
    continuation = ScopeExecutionContinuation(
        id="continuation-execution-context",
        authorization_id=authorization.id,
        workflow_run_id=contract.workflow_run_id,
        execution_revision=2,
        operation="supplementary_collection",
        supplementary_queries=("shirt sunscreen",),
        state="pending",
    )
    event = ScopeAuditEvent(
        id="coverage-resolution-execution-context",
        workflow_run_id=contract.workflow_run_id,
        scope_contract_id=contract.id,
        scope_contract_version=contract.version,
        event_name="coverage_resolved",
        payload={
            "schema_version": "content_research_scope_audit_event_v1",
            "coverage_snapshot_id": snapshot.id,
            "resolution": "expand_required_constraint",
            "constraint_id": "core_object",
        },
    )
    _, _, persisted_authorization, persisted_continuation, _ = (
        store.resolve_coverage_and_authorize_execution_atomically(
            snapshot=snapshot,
            authorization=authorization,
            continuation=continuation,
            event=event,
        )
    )
    return persisted_authorization, persisted_continuation


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
        workflow_run_id="run-retry",
        provider="xiaohongshu",
        source_kind="search_result",
        limit=12,
        retry_completed=True,
    )
    assert retried["status"] == "queued"
    assert retried["lease_owner"] is None
    assert retried["last_error"] is None


@pytest.mark.asyncio
async def test_worker_claims_persisted_scope_continuation_with_its_queries(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "continuation.db"))
    continuation = ScopeExecutionContinuation(
        id="sec-worker",
        authorization_id="sea-worker",
        workflow_run_id="run-worker",
        execution_revision=2,
        operation="supplementary_collection",
        supplementary_queries=("夏季 防晒 长袖衬衫", "夏季 透气 衬衫"),
        state="pending",
    )
    store.save_scope_execution_continuation(continuation)
    service = FakeContinuationService()
    worker = ContentResearchDispatchWorker(store=store, service_factory=lambda: service)

    assert await worker.run_once() is True
    assert service.calls == []
    assert len(service.continuations) == 1
    claimed = service.continuations[0]
    assert claimed.authorization_id == continuation.authorization_id
    assert claimed.supplementary_queries == continuation.supplementary_queries
    assert claimed.state == "running"
    persisted = store.list_scope_execution_continuations("run-worker")[0]
    assert persisted.state == "completed"


@pytest.mark.asyncio
async def test_worker_passes_the_claimed_execution_attempt_to_the_service(tmp_path: Path) -> None:
    """Dropping the attempt claim must fail before an authorized continuation can execute."""
    store = SQLiteContentResearchStore(str(tmp_path / "execution-context.db"))
    authorization, continuation = _save_execution_unit_continuation(store)
    service = ExecutionUnitContinuationService()
    worker = ContentResearchDispatchWorker(
        store=store,
        service_factory=lambda: cast(ContentResearchService, service),
    )

    assert await worker.run_once() is True

    assert len(service.executions) == 1
    claim, executed = service.executions[0]
    assert claim.execution_unit_id == authorization.execution_unit_id
    assert claim.attempt_no == 0
    assert claim.state == "running"
    assert claim.lease_owner
    assert claim.lease_token
    assert executed.authorization_id == continuation.authorization_id


@pytest.mark.asyncio
async def test_publication_only_failure_keeps_successful_execution_attempt_truthful(
    tmp_path: Path,
) -> None:
    store = SQLiteContentResearchStore(str(tmp_path / "publication-only-failure.db"))
    authorization, _continuation = _save_execution_unit_continuation(store)
    service = PublicationFailureExecutionService()
    worker = ContentResearchDispatchWorker(
        store=store,
        service_factory=lambda: cast(ContentResearchService, service),
    )

    assert await worker.run_once() is True

    attempt = store.get_scope_execution_attempt(
        str(authorization.execution_unit_id), 0
    )
    assert attempt is not None
    assert attempt.state == "completed"
    persisted_continuation = store.list_scope_execution_continuations(
        authorization.workflow_run_id
    )[0]
    assert persisted_continuation.state == "failed"
    with store._connect() as connection:
        last_error = connection.execute(
            "SELECT last_error FROM content_research_scope_execution_continuations "
            "WHERE authorization_id=?",
            (authorization.id,),
        ).fetchone()[0]
    assert "artifact write failed" in str(last_error)


@pytest.mark.asyncio
async def test_failed_scope_continuation_is_reclaimed_only_by_exact_action_replay(tmp_path):
    store = SQLiteContentResearchStore(str(tmp_path / "continuation-failure.db"))
    continuation = ScopeExecutionContinuation(
        id="sec-failure",
        authorization_id="sea-failure",
        workflow_run_id="run-failure",
        execution_revision=2,
        operation="supplementary_collection",
        supplementary_queries=("夏季 防晒 长袖衬衫",),
        state="pending",
    )
    store.save_scope_execution_continuation(continuation)
    failing = FailingContinuationService()
    worker = ContentResearchDispatchWorker(store=store, service_factory=lambda: failing)

    assert await worker.run_once() is True
    failed = store.list_scope_execution_continuations("run-failure")[0]
    assert failed.state == "failed"
    with store._connect() as conn:
        last_error = conn.execute(
            "SELECT last_error FROM content_research_scope_execution_continuations WHERE authorization_id=?",
            (continuation.authorization_id,),
        ).fetchone()[0]
    assert "authorized supplementary collection failed" in (last_error or "")

    store.requeue_scope_execution_continuation(continuation.authorization_id)
    recovered = store.list_scope_execution_continuations("run-failure")[0]
    assert recovered.state == "pending"
    succeeding = FakeContinuationService()
    retry_worker = ContentResearchDispatchWorker(store=store, service_factory=lambda: succeeding)
    assert await retry_worker.run_once() is True
    assert store.list_scope_execution_continuations("run-failure")[0].state == "completed"


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
async def test_dispatch_recovery_serializes_takeover_and_uses_controlled_time(
    tmp_path: Path,
) -> None:
    """Lease validation and every interrupted-task rewrite share one transaction."""
    entered_update = threading.Event()
    release_update = threading.Event()
    fixed_now = datetime(2040, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    class BlockingStore(SQLiteContentResearchStore):
        def _raw_connect(self) -> sqlite3.Connection:
            conn = cast(sqlite3.Connection, super()._raw_connect())

            def block_recovery_update() -> int:
                entered_update.set()
                release_update.wait(timeout=5)
                return 1

            conn.create_function("block_recovery_update", 0, block_recovery_update)
            return conn

    store = BlockingStore(str(tmp_path / "dispatch-recovery-atomic.db"))
    for task_id in ("task-replayable", "task-unknown"):
        store.save_subagent_task(
            SubagentTaskRecord(
                id=task_id,
                workflow_run_id="run-recovery-atomic",
                thread_id="thread-recovery-atomic",
                schema_version="v1",
                status="running",
                plan_id="plan-recovery-atomic",
                direction_id="product_marketing",
                payload={"schema_version": "v1", "status": "running"},
            )
        )
    store.save_stage_checkpoint(
        StageCheckpointRecord(
            id="scp-recovery-unknown",
            schema_version="v1",
            payload={},
            workflow_run_id="run-recovery-atomic",
            subagent_task_id="task-unknown",
            stage_name="operation",
            input_fingerprint="operation-unknown",
            status="running",
        )
    )
    with store._connect() as conn:
        conn.execute(
            """INSERT INTO content_research_dispatch_jobs
               (workflow_run_id, provider, source_kind, limit_per_specialist, status,
                attempt_count, lease_expires_at, lease_owner, lease_token, created_at, updated_at)
               VALUES (?, 'xiaohongshu', 'search_result', 10, 'running', 1, ?, ?, ?, ?, ?)""",
            (
                "run-recovery-atomic",
                datetime(2040, 1, 2, 3, 9, 5, tzinfo=timezone.utc).isoformat(),
                "worker-a",
                "token-a",
                fixed_now.isoformat(),
                fixed_now.isoformat(),
            ),
        )
        conn.execute(
            """CREATE TRIGGER block_dispatch_recovery_update
               BEFORE UPDATE ON content_research_subagent_tasks
               WHEN NEW.id='task-replayable'
               BEGIN SELECT block_recovery_update(); END"""
        )

    context = DispatchLeaseContext("run-recovery-atomic", "worker-a", "token-a")
    recovery = asyncio.create_task(
        asyncio.to_thread(
            store.recover_interrupted_tasks_atomically,
            context,
            recovered_at=fixed_now,
        )
    )
    assert await asyncio.to_thread(entered_update.wait, 2)

    def takeover() -> None:
        with store._raw_connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """UPDATE content_research_dispatch_jobs
                   SET lease_owner='worker-b', lease_token='token-b'
                   WHERE workflow_run_id='run-recovery-atomic'"""
            )

    takeover_task = asyncio.create_task(asyncio.to_thread(takeover))
    await asyncio.sleep(0.05)
    assert not takeover_task.done(), "takeover must wait for the recovery transaction"
    release_update.set()
    assert await recovery is True
    await takeover_task

    tasks = {
        task.id: task for task in store.list_subagent_tasks_for_workflow("run-recovery-atomic")
    }
    assert tasks["task-replayable"].status == "queued"
    assert tasks["task-unknown"].status == "outcome_unknown"
    assert tasks["task-unknown"].payload["output_payload"]["error_code"] == (
        "OPERATION_OUTCOME_UNKNOWN"
    )
    before = tasks
    assert not store.recover_interrupted_tasks_atomically(context, recovered_at=fixed_now)
    assert {
        task.id: task for task in store.list_subagent_tasks_for_workflow("run-recovery-atomic")
    } == before


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
            workflow_run_id="run-event",
            provider="xiaohongshu",
            source_kind="search_result",
            limit=9,
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

    session = await AsyncDirectionalPersistenceSession.open(db_path, workflow_run_id="run-recovery")
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
async def test_async_pipeline_rejects_superseded_checkpoint_ownership_reassignment(
    tmp_path: Path,
) -> None:
    """A replay may replace checkpoint state, never its execution owner."""
    db_path = str(tmp_path / "dispatch-immutable-checkpoint.db")
    store = SQLiteContentResearchStore(db_path)
    original = StageCheckpointRecord(
        id="scp-immutable-recovery",
        schema_version="content_research_stage_checkpoint_v1",
        payload={"attempt": 1},
        workflow_run_id="run-recovery",
        subagent_task_id="task-recovery",
        stage_name="operation",
        input_fingerprint="fingerprint-recovery",
        status="superseded",
        scope_contract_id="scope-a",
        execution_unit_id="unit-a",
        attempt_no=1,
        execution_revision=2,
    )
    store.save_stage_checkpoint(original)
    session = await AsyncDirectionalPersistenceSession.open(
        db_path, workflow_run_id="run-recovery"
    )

    with pytest.raises(ValueError, match="immutable execution ownership"):
        session.save_stage_checkpoint(
            replace(
                original,
                status="completed",
                scope_contract_id="scope-b",
                execution_unit_id="unit-b",
                attempt_no=7,
                execution_revision=9,
            )
        )

    assert store.get_typed_record(StageCheckpointRecord, original.id) == original


@pytest.mark.asyncio
async def test_async_pipeline_sql_conflict_preserves_checkpoint_ownership(
    tmp_path: Path,
) -> None:
    """A session opened before the checkpoint exists cannot bypass ownership checks."""
    db_path = str(tmp_path / "dispatch-concurrent-checkpoint.db")
    store = SQLiteContentResearchStore(db_path)
    stale_session = await AsyncDirectionalPersistenceSession.open(
        db_path, workflow_run_id="run-recovery"
    )
    original = StageCheckpointRecord(
        id="scp-concurrent-recovery",
        schema_version="content_research_stage_checkpoint_v1",
        payload={"attempt": 1},
        workflow_run_id="run-recovery",
        subagent_task_id="task-recovery",
        stage_name="operation",
        input_fingerprint="fingerprint-recovery",
        status="superseded",
        scope_contract_id="scope-a",
        execution_unit_id="unit-a",
        attempt_no=1,
        execution_revision=2,
    )
    store.save_stage_checkpoint(original)
    stale_session.save_stage_checkpoint(
        replace(
            original,
            status="completed",
            scope_contract_id="scope-b",
            execution_unit_id="unit-b",
            attempt_no=7,
            execution_revision=9,
        )
    )

    with pytest.raises(RuntimeError, match="immutable persistence conflict"):
        await stale_session.flush()

    assert store.get_typed_record(StageCheckpointRecord, original.id) == original


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
    first = await AsyncDirectionalPersistenceSession.open(db_path, workflow_run_id="run-recovery")
    stale = await AsyncDirectionalPersistenceSession.open(db_path, workflow_run_id="run-recovery")
    first.save_stage_checkpoint(replace(original, status="completed", payload={"attempt": 1}))
    stale.save_stage_checkpoint(replace(original, status="completed", payload={"attempt": 2}))

    await first.flush()
    with pytest.raises(RuntimeError, match="immutable persistence conflict"):
        await stale.flush()

    reloaded = await AsyncDirectionalPersistenceSession.open(
        db_path, workflow_run_id="run-recovery"
    )
    assert reloaded.get_typed_record(StageCheckpointRecord, original.id).payload == {"attempt": 1}


@pytest.mark.asyncio
async def test_live_context_write_serializes_takeover_and_rejects_later_stale_checkpoint(
    tmp_path: Path,
) -> None:
    """A takeover cannot interleave between the live predicate and its domain insert."""
    db_path = str(tmp_path / "atomic-execution-write.db")
    entered_insert = threading.Event()
    release_insert = threading.Event()

    class BlockingSQLiteContentResearchStore(SQLiteContentResearchStore):
        def _raw_connect(self) -> sqlite3.Connection:
            conn = cast(sqlite3.Connection, super()._raw_connect())

            def block_execution_checkpoint_insert() -> int:
                entered_insert.set()
                release_insert.wait(timeout=5)
                return 1

            conn.create_function(
                "block_execution_checkpoint_insert",
                0,
                block_execution_checkpoint_insert,
            )
            return conn

    store = BlockingSQLiteContentResearchStore(db_path)
    authorization, _continuation = _save_execution_unit_continuation(store)
    claim_a = store.claim_execution_unit(
        execution_unit_id=str(authorization.execution_unit_id),
        owner="worker-a",
        lease_seconds=1,
    )
    assert claim_a is not None and claim_a.lease_token
    context_a = ExecutionContext(
        execution_unit_id=claim_a.execution_unit_id,
        attempt_no=claim_a.attempt_no,
        lease_token=claim_a.lease_token,
        scope_contract_id=authorization.scope_contract_id,
    )
    with store._connect() as conn:
        conn.execute(
            """CREATE TRIGGER block_execution_checkpoint_insert
               BEFORE INSERT ON content_research_stage_checkpoints
               WHEN NEW.id='scp-atomic-before-takeover'
               BEGIN
                 SELECT block_execution_checkpoint_insert();
               END"""
        )

    scoped = store.for_execution_context(context_a)
    before_takeover = StageCheckpointRecord(
        id="scp-atomic-before-takeover",
        schema_version="content_research_stage_checkpoint_v1",
        payload={"attempt": 0},
        workflow_run_id=authorization.workflow_run_id,
        subagent_task_id="task-atomic",
        stage_name="aggregate",
        input_fingerprint="before-takeover",
        status="completed",
    )

    first_write = asyncio.create_task(asyncio.to_thread(scoped.save_stage_checkpoint, before_takeover))
    assert await asyncio.to_thread(entered_insert.wait, 2)
    assert claim_a.lease_expires_at is not None
    seconds_until_expiry = (
        claim_a.lease_expires_at - datetime.now(timezone.utc)
    ).total_seconds()
    await asyncio.sleep(max(0.0, seconds_until_expiry) + 0.25)
    takeover = asyncio.create_task(
        asyncio.to_thread(
            store.claim_execution_unit,
            execution_unit_id=claim_a.execution_unit_id,
            owner="worker-b",
            lease_seconds=120,
        )
    )
    await asyncio.sleep(0.05)
    assert not takeover.done(), "takeover must wait for the guarded write transaction"
    release_insert.set()
    await first_write
    claim_b = await takeover
    assert claim_b is not None

    after_takeover = replace(
        before_takeover,
        id="scp-stale-after-takeover",
        input_fingerprint="after-takeover",
    )
    with pytest.raises(ExecutionLeaseFencedError):
        await asyncio.to_thread(scoped.save_stage_checkpoint, after_takeover)
    assert store.get_typed_record(StageCheckpointRecord, before_takeover.id) == before_takeover
    assert store.get_typed_record(StageCheckpointRecord, after_takeover.id) is None
    assert any(
        fact.attempt_no == claim_a.attempt_no
        and fact.kind == "lease_fenced"
        and fact.payload.get("operation") == "content_research_store_transaction"
        for fact in store.execution_trace(claim_a.execution_unit_id)
    )
