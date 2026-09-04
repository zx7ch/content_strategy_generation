from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.content_research.api_schemas import ContentResearchFormalResearchResponse
from app.content_research.async_dispatch import AsyncFormalResearchDispatchRepository
from app.content_research.lifecycle.coordinator import ContentResearchPersistenceCoordinator
from app.content_research.lifecycle.models import ContentResearchState, LifecycleCommand
from app.content_research.models import SubagentTaskRecord
from app.content_research.persistence_models import StageCheckpointRecord
from app.content_research.scope_contract import DispatchLeaseContext
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.content_research.worker import ContentResearchDispatchWorker
from app.core.runtime_schema_bootstrap import bootstrap_canonical_runtime_schema
from app.core.runtime_write_coordinator import RuntimeWriteCoordinator
from app.core.sqlite_connection_roles import open_readonly_database
from app.memory.thread_store import ThreadStore
from app.runtime_write_handlers import production_runtime_write_handlers


class _ControlledExecution:
    def __init__(self, *, block_first: bool) -> None:
        self.block_first = block_first
        self.calls: list[str] = []
        self.first_started = asyncio.Event()
        self.release_first = asyncio.Event()

    async def execute_claimed_dispatch(self, *, context, request):
        self.calls.append(context.workflow_run_id)
        if self.block_first and len(self.calls) == 1:
            self.first_started.set()
            await self.release_first.wait()
        return ContentResearchFormalResearchResponse(
            workflow_run_id=context.workflow_run_id,
            status="completed",
            task_count=1,
            completed_task_count=1,
            partial_completed_task_count=0,
            provider=request.provider,
            source_kind=request.source_kind,
            limit_per_specialist=request.limit,
        )


async def _wait_until(predicate, *, timeout: float = 2) -> None:
    async def poll() -> None:
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout=timeout)


def _dispatch_status(database: Path, run_id: str) -> str:
    with open_readonly_database(database) as connection:
        row = connection.execute(
            "SELECT status FROM content_research_dispatch_jobs WHERE workflow_run_id=?",
            (run_id,),
        ).fetchone()
    assert row is not None
    return str(row[0])


@pytest.mark.acceptance
def test_single_slot_scheduler_cancel_crash_and_restart(tmp_path: Path) -> None:
    async def exercise() -> None:
        database = tmp_path / "scheduler-recovery.sqlite"
        await bootstrap_canonical_runtime_schema(database, discovery_secret="acceptance-secret")
        writer = RuntimeWriteCoordinator(
            database,
            handlers=production_runtime_write_handlers(),
        )
        await writer.start()
        try:
            store = SQLiteContentResearchStore(str(database))
            dispatch = AsyncFormalResearchDispatchRepository(str(database))
            wake_event = asyncio.Event()
            controlled = _ControlledExecution(block_first=True)
            worker = ContentResearchDispatchWorker(
                store=store,
                execution_factory=lambda: controlled,
                wake_event=wake_event,
                recovery_scan_seconds=60,
                max_concurrent_runs=1,
            )
            for run_id in ("run-slot-a", "run-slot-b"):
                await dispatch.enqueue(
                    workflow_run_id=run_id,
                    provider="xiaohongshu",
                    source_kind="search_result",
                    limit=5,
                )

            stop_event = asyncio.Event()
            scheduler_task = asyncio.create_task(worker.run_loop(stop_event=stop_event))
            wake_event.set()
            await asyncio.wait_for(controlled.first_started.wait(), timeout=1)
            await asyncio.sleep(0.05)
            assert controlled.calls == ["run-slot-a"]
            assert _dispatch_status(database, "run-slot-b") == "queued"

            controlled.release_first.set()
            await _wait_until(lambda: len(controlled.calls) == 2)
            await _wait_until(
                lambda: _dispatch_status(database, "run-slot-b") == "completed"
            )
            stop_event.set()
            wake_event.set()
            await scheduler_task

            async with ThreadStore(str(database)) as threads:
                cancel_thread = await threads.create_thread(title="Cancel during provider call")
            coordinator = ContentResearchPersistenceCoordinator(str(database))
            await coordinator.apply(
                LifecycleCommand(
                    command_id="submit-run-cancel",
                    run_id="run-cancel",
                    expected_state=None,
                    expected_revision=0,
                    kind="submit_research_subject",
                    payload={
                        "thread_id": str(cancel_thread["id"]),
                        "user_id": "acceptance-user",
                        "seed_text": "cancel while provider is running",
                    },
                )
            )
            await dispatch.enqueue(
                workflow_run_id="run-cancel",
                provider="xiaohongshu",
                source_kind="search_result",
                limit=5,
            )
            cancel_control = _ControlledExecution(block_first=True)
            cancel_worker = ContentResearchDispatchWorker(
                store=store,
                execution_factory=lambda: cancel_control,
                lease_seconds=5,
                max_concurrent_runs=1,
            )
            in_provider_call = asyncio.create_task(cancel_worker.run_once())
            await asyncio.wait_for(cancel_control.first_started.wait(), timeout=1)
            cancelled = await coordinator.apply(
                LifecycleCommand(
                    command_id="cancel-during-provider-call",
                    run_id="run-cancel",
                    expected_state=ContentResearchState.PRESEARCH_RUNNING,
                    expected_revision=1,
                    kind="cancel",
                    payload={},
                )
            )
            cancel_control.release_first.set()
            assert await in_provider_call is True
            assert cancelled.state is ContentResearchState.CANCELLED_OR_FAILED
            assert await coordinator.load("run-cancel") == cancelled
            assert _dispatch_status(database, "run-cancel") == "failed"
            with open_readonly_database(database) as connection:
                assert connection.execute(
                    "SELECT last_error FROM content_research_dispatch_jobs "
                    "WHERE workflow_run_id='run-cancel'"
                ).fetchone() == ("user_cancelled",)

            crashed = _ControlledExecution(block_first=True)
            await dispatch.enqueue(
                workflow_run_id="run-crash",
                provider="xiaohongshu",
                source_kind="search_result",
                limit=5,
            )
            crashing_worker = ContentResearchDispatchWorker(
                store=store,
                execution_factory=lambda: crashed,
                lease_seconds=1,
                max_concurrent_runs=1,
            )
            interrupted = asyncio.create_task(crashing_worker.run_once())
            await asyncio.wait_for(crashed.first_started.wait(), timeout=1)
            interrupted.cancel()
            with pytest.raises(asyncio.CancelledError):
                await interrupted
            assert _dispatch_status(database, "run-crash") == "running"

            await asyncio.sleep(1.05)
            recovered = _ControlledExecution(block_first=False)
            restarted_worker = ContentResearchDispatchWorker(
                store=store,
                execution_factory=lambda: recovered,
                lease_seconds=5,
                max_concurrent_runs=1,
            )
            assert await restarted_worker.run_once() is True
            assert recovered.calls == ["run-crash"]
            assert _dispatch_status(database, "run-crash") == "completed"
        finally:
            await writer.close()

    asyncio.run(exercise())


@pytest.mark.acceptance
def test_provider_unknown_outcome_policy_matrix(tmp_path: Path) -> None:
    async def exercise() -> None:
        database = tmp_path / "provider-outcome-policy.sqlite"
        await bootstrap_canonical_runtime_schema(database, discovery_secret="acceptance-secret")
        writer = RuntimeWriteCoordinator(
            database,
            handlers=production_runtime_write_handlers(),
        )
        await writer.start()
        try:
            store = SQLiteContentResearchStore(str(database))
            dispatch = AsyncFormalResearchDispatchRepository(str(database))
            for task_id in ("task-safe-before-provider", "task-provider-outcome-unknown"):
                store.save_subagent_task(
                    SubagentTaskRecord(
                        id=task_id,
                        workflow_run_id="run-provider-policy",
                        thread_id="thread-provider-policy",
                        schema_version="content_research_subagent_task_v1",
                        status="running",
                        plan_id="plan-provider-policy",
                        direction_id="product_marketing",
                        payload={
                            "schema_version": "content_research_subagent_task_v1",
                            "status": "running",
                        },
                    )
                )
            store.save_stage_checkpoint(
                StageCheckpointRecord(
                    id="checkpoint-provider-claimed",
                    schema_version="content_research_stage_checkpoint_v1",
                    payload={},
                    workflow_run_id="run-provider-policy",
                    subagent_task_id="task-provider-outcome-unknown",
                    stage_name="operation",
                    input_fingerprint="provider-operation-fingerprint",
                    status="running",
                )
            )
            await dispatch.enqueue(
                workflow_run_id="run-provider-policy",
                provider="xiaohongshu",
                source_kind="search_result",
                limit=5,
            )
            claimed = await dispatch.claim_next(owner="policy-worker", lease_seconds=30)
            assert claimed is not None
            context = DispatchLeaseContext(
                workflow_run_id="run-provider-policy",
                lease_owner="policy-worker",
                lease_token=str(claimed["lease_token"]),
            )

            recovered = await asyncio.to_thread(
                store.recover_interrupted_tasks_atomically,
                context,
                recovered_at=datetime.now(timezone.utc),
            )
            assert recovered is True
            tasks = {
                task.id: task
                for task in store.list_subagent_tasks_for_workflow("run-provider-policy")
            }
            assert tasks["task-safe-before-provider"].status == "queued"
            assert tasks["task-provider-outcome-unknown"].status == "outcome_unknown"
            assert (
                tasks["task-provider-outcome-unknown"].payload["output_payload"]["error_code"]
                == "OPERATION_OUTCOME_UNKNOWN"
            )

            stale_context = DispatchLeaseContext(
                workflow_run_id="run-provider-policy",
                lease_owner="stale-worker",
                lease_token="stale-token",
            )
            assert not await asyncio.to_thread(
                store.recover_interrupted_tasks_atomically,
                stale_context,
                recovered_at=datetime.now(timezone.utc),
            )
        finally:
            await writer.close()

    asyncio.run(exercise())
