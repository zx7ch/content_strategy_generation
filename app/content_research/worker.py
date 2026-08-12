"""Durable worker for formal Content Research dispatch jobs."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import replace

from app.content_research.api_schemas import ContentResearchSourceCollectionRequest
from app.content_research.async_dispatch import AsyncFormalResearchDispatchRepository
from app.content_research.models import utcnow
from app.content_research.persistence_models import StageCheckpointRecord
from app.content_research.service import ContentResearchService
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore

logger = logging.getLogger(__name__)


class ContentResearchDispatchWorker:
    """Claims persisted jobs; a process restart recovers an expired lease."""

    def __init__(
        self,
        *,
        store: SQLiteContentResearchStore,
        service_factory: Callable[[], ContentResearchService],
        wake_event: asyncio.Event | None = None,
        recovery_scan_seconds: float = 5.0,
        lease_seconds: int = 120,
    ) -> None:
        self._store = store
        self._dispatch = AsyncFormalResearchDispatchRepository(store._db_path)
        self._service_factory = service_factory
        self._wake_event = wake_event or asyncio.Event()
        self._recovery_scan_seconds = recovery_scan_seconds
        self._lease_seconds = lease_seconds
        self._owner = f"content-research-worker:{uuid.uuid4().hex}"

    async def run_once(self) -> bool:
        job = await self._dispatch.claim_next(owner=self._owner, lease_seconds=self._lease_seconds)
        if job is None:
            return False
        lease_stop = asyncio.Event()
        lease_lost = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._heartbeat_lease(
                workflow_run_id=str(job["workflow_run_id"]),
                token=str(job["lease_token"]),
                stop_event=lease_stop,
                lease_lost=lease_lost,
            )
        )
        result = None
        execution_error: Exception | None = None
        try:
            self._recover_interrupted_tasks(str(job["workflow_run_id"]))
            result = await self._service_factory().start_formal_research(
                workflow_run_id=str(job["workflow_run_id"]),
                request=ContentResearchSourceCollectionRequest(
                    provider=str(job["provider"]),
                    source_kind=str(job["source_kind"]),
                    limit=int(job["limit_per_specialist"]),
                ),
            )
        except Exception as exc:  # preserve a durable diagnostic for retry/recovery
            execution_error = exc
            logger.exception(
                "content research dispatch failed",
                extra={"workflow_run_id": job["workflow_run_id"]},
            )
        finally:
            lease_stop.set()
            await heartbeat
        if lease_lost.is_set():
            logger.error(
                "content research dispatch lease lost; suppressing stale terminal write",
                extra={"workflow_run_id": job["workflow_run_id"]},
            )
            return True
        if execution_error is not None:
            await self._dispatch.complete(
                workflow_run_id=str(job["workflow_run_id"]),
                owner=self._owner,
                token=str(job["lease_token"]),
                error=str(execution_error),
            )
            return True
        assert result is not None
        try:
            error = (
                "; ".join(
                    str(item.get("error") or "formal task failed") for item in result.failed_tasks
                )
                if result.status == "failed"
                else None
            )
        finally:
            await self._dispatch.complete(
                workflow_run_id=str(job["workflow_run_id"]),
                owner=self._owner,
                token=str(job["lease_token"]),
                error=error,
            )
        return True

    async def _heartbeat_lease(
        self,
        *,
        workflow_run_id: str,
        token: str,
        stop_event: asyncio.Event,
        lease_lost: asyncio.Event,
    ) -> None:
        interval = max(1.0, self._lease_seconds / 3)
        while True:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
                return
            except asyncio.TimeoutError:
                pass
            if not await self._dispatch.renew(
                workflow_run_id=workflow_run_id,
                owner=self._owner,
                token=token,
                lease_seconds=self._lease_seconds,
            ):
                lease_lost.set()
                return

    def _recover_interrupted_tasks(self, workflow_run_id: str) -> None:
        """Recover parent task state without ever repeating an unknown call.

        A process can stop between marking a task running and entering the
        provider.  That parent marker is safe to queue again only when no
        operation checkpoint was claimed.  A claimed provider operation is
        deliberately surfaced as retryable/unknown instead of being replayed.
        """
        operation_checkpoints = self._store.list_typed_records(StageCheckpointRecord)
        for task in self._store.list_subagent_tasks_for_workflow(workflow_run_id):
            if task.status != "running":
                continue
            has_unknown_operation = any(
                checkpoint.workflow_run_id == workflow_run_id
                and checkpoint.subagent_task_id == task.id
                and checkpoint.stage_name == "operation"
                and checkpoint.status == "running"
                for checkpoint in operation_checkpoints
            )
            payload = dict(task.payload)
            if has_unknown_operation:
                payload["status"] = "failed"
                payload["output_payload"] = {
                    "error_code": "OPERATION_OUTCOME_UNKNOWN",
                    "error_message": "Previous provider operation was interrupted; its outcome is unknown and was not replayed.",
                    "retryable": True,
                }
                next_status = "failed"
            else:
                payload["status"] = "queued"
                next_status = "queued"
            self._store.save_subagent_task(
                replace(task, status=next_status, payload=payload, updated_at=utcnow())
            )

    async def run_loop(self, *, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                processed = await self.run_once()
            except Exception:
                # Queue polling must survive a transient SQLite failure; the
                # durable row remains queued and is safe to claim next tick.
                logger.exception("content research dispatch poll failed")
                processed = False
            if not processed:
                self._wake_event.clear()
                # Closing the clear/wait race matters: a confirmed job may be
                # committed between the empty claim and `clear()`.
                if await self.run_once():
                    continue
                try:
                    await asyncio.wait_for(
                        self._wake_event.wait(), timeout=self._recovery_scan_seconds
                    )
                except asyncio.TimeoutError:
                    pass
