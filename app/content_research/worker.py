"""Durable worker for formal Content Research dispatch jobs."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import datetime

from app.content_research.api_schemas import ContentResearchSourceCollectionRequest
from app.content_research.async_dispatch import (
    AsyncFormalResearchDispatchRepository,
    AsyncScopeExecutionContinuationRepository,
    AsyncScopeExecutionUnitRepository,
)
from app.content_research.models import utcnow
from app.content_research.scope_contract import DispatchLeaseContext
from app.content_research.service import (
    ContentResearchService,
    ReportPublicationMaterializationError,
)
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
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._store = store
        self._dispatch = AsyncFormalResearchDispatchRepository(store._db_path)
        self._continuations = AsyncScopeExecutionContinuationRepository(store._db_path)
        self._execution_units = AsyncScopeExecutionUnitRepository(store._db_path)
        self._service_factory = service_factory
        self._wake_event = wake_event or asyncio.Event()
        self._recovery_scan_seconds = recovery_scan_seconds
        self._lease_seconds = lease_seconds
        self._clock = clock
        self._owner = f"content-research-worker:{uuid.uuid4().hex}"

    async def run_once(self) -> bool:
        continuation = await self._continuations.claim_next(
            owner=self._owner, lease_seconds=self._lease_seconds
        )
        if continuation is not None:
            unit_claim = None
            if continuation.execution_unit_id:
                unit_claim = await self._execution_units.claim_execution_unit(
                    execution_unit_id=continuation.execution_unit_id,
                    owner=self._owner,
                    lease_seconds=self._lease_seconds,
                )
                if unit_claim is None:
                    await self._continuations.complete(
                        authorization_id=continuation.authorization_id,
                        owner=self._owner,
                        token=str(continuation.lease_token or ""),
                        error="execution unit was not claimable",
                    )
                    return True
            return await self._run_scope_continuation(continuation, unit_claim=unit_claim)
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
            dispatch_context = DispatchLeaseContext(
                workflow_run_id=str(job["workflow_run_id"]),
                lease_owner=self._owner,
                lease_token=str(job["lease_token"]),
            )
            if not self._recover_interrupted_tasks(dispatch_context):
                lease_lost.set()
                return True
            service = self._service_factory()
            execute_claimed = getattr(service, "execute_claimed_dispatch", None)
            request = ContentResearchSourceCollectionRequest(
                provider=str(job["provider"]),
                source_kind=str(job["source_kind"]),
                limit=int(job["limit_per_specialist"]),
            )
            if callable(execute_claimed):
                result = await execute_claimed(
                    context=dispatch_context,
                    request=request,
                )
            else:
                result = await service.start_formal_research(
                    workflow_run_id=dispatch_context.workflow_run_id,
                    request=request,
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

    async def _run_scope_continuation(self, continuation, *, unit_claim=None) -> bool:
        token = str(continuation.lease_token or "")
        if not token:
            raise RuntimeError("claimed scope continuation is missing its lease token")
        lease_stop = asyncio.Event()
        lease_lost = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._heartbeat_continuation_lease(
                authorization_id=continuation.authorization_id,
                token=token,
                stop_event=lease_stop,
                lease_lost=lease_lost,
            )
        )
        unit_heartbeat = None
        unit_lease_lost = asyncio.Event()
        if unit_claim is not None:
            unit_token = str(unit_claim.lease_token or "")
            unit_heartbeat = asyncio.create_task(
                self._heartbeat_execution_unit_lease(
                    execution_unit_id=unit_claim.execution_unit_id,
                    attempt_no=unit_claim.attempt_no,
                    token=unit_token,
                    stop_event=lease_stop,
                    lease_lost=unit_lease_lost,
                )
            )
        error: Exception | None = None
        terminal_state = "completed"
        try:
            service = self._service_factory()
            if unit_claim is not None:
                terminal_state = await service.execute_execution_unit(unit_claim, continuation)
            else:
                await service.execute_scope_continuation(continuation)
        except Exception as exc:
            error = exc
            terminal_state = (
                "completed"
                if isinstance(exc, ReportPublicationMaterializationError)
                else "failed"
            )
            if unit_claim is not None:
                attempt = self._store.get_scope_execution_attempt(
                    unit_claim.execution_unit_id, unit_claim.attempt_no
                )
                if attempt is not None and attempt.provider_state == "outcome_unknown":
                    terminal_state = "outcome_unknown"
            logger.exception(
                "content research scope continuation failed",
                extra={
                    "workflow_run_id": continuation.workflow_run_id,
                    "authorization_id": continuation.authorization_id,
                },
            )
        finally:
            lease_stop.set()
            await heartbeat
            if unit_heartbeat is not None:
                await unit_heartbeat
        if lease_lost.is_set() or unit_lease_lost.is_set():
            logger.error(
                "content research scope continuation lease lost",
                extra={"authorization_id": continuation.authorization_id},
            )
            return True
        if unit_claim is not None:
            completed = await self._execution_units.complete_execution_unit(
                execution_unit_id=unit_claim.execution_unit_id,
                attempt_no=unit_claim.attempt_no,
                owner=self._owner,
                lease_token=str(unit_claim.lease_token or ""),
                state=terminal_state,
            )
            if not completed:
                return True
        await self._continuations.complete(
            authorization_id=continuation.authorization_id,
            owner=self._owner,
            token=token,
            error=str(error) if error is not None else None,
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

    async def _heartbeat_continuation_lease(
        self,
        *,
        authorization_id: str,
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
            if not await self._continuations.renew(
                authorization_id=authorization_id,
                owner=self._owner,
                token=token,
                lease_seconds=self._lease_seconds,
            ):
                lease_lost.set()
                return

    async def _heartbeat_execution_unit_lease(
        self,
        *,
        execution_unit_id: str,
        attempt_no: int,
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
            if not await self._execution_units.renew_execution_unit_lease(
                execution_unit_id=execution_unit_id,
                attempt_no=attempt_no,
                owner=self._owner,
                lease_token=token,
                lease_seconds=self._lease_seconds,
            ):
                lease_lost.set()
                return

    def _recover_interrupted_tasks(
        self,
        context: DispatchLeaseContext,
    ) -> bool:
        """Recover parent task state without ever repeating an unknown call.

        A process can stop between marking a task running and entering the
        provider.  That parent marker is safe to queue again only when no
        operation checkpoint was claimed.  A claimed provider operation is
        deliberately surfaced as retryable/unknown instead of being replayed.
        """
        return self._store.recover_interrupted_tasks_atomically(
            context, recovered_at=self._clock()
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
