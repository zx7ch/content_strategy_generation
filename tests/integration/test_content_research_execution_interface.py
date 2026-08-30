from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, cast

import pytest

import app.content_research.worker as worker_module
from app.content_research.api_schemas import (
    ContentResearchFormalResearchResponse,
    ContentResearchSourceCollectionRequest,
)
from app.content_research.async_dispatch import AsyncFormalResearchDispatchRepository
from app.content_research.execution import ContentResearchExecutionService
from app.content_research.scope_contract import (
    DispatchLeaseContext,
    ExecutionLeaseFencedError,
)
from app.content_research.stores.sqlite_store import SQLiteContentResearchStore
from app.content_research.worker import ContentResearchDispatchWorker


class _ExecutionApplication:
    def __init__(self, store: SQLiteContentResearchStore) -> None:
        self._store = store


class _CurrentClaimProbe:
    def __init__(self, store: SQLiteContentResearchStore) -> None:
        self._store = store
        self.contexts: list[DispatchLeaseContext] = []

    async def execute_claimed_dispatch(
        self,
        *,
        context: DispatchLeaseContext,
        request: ContentResearchSourceCollectionRequest,
    ) -> ContentResearchFormalResearchResponse:
        assert self._store.dispatch_context_is_live(context)
        self.contexts.append(context)
        return ContentResearchFormalResearchResponse(
            workflow_run_id=context.workflow_run_id,
            status="completed",
            task_count=0,
            completed_task_count=0,
            partial_completed_task_count=0,
            provider=request.provider,
            source_kind=request.source_kind,
            limit_per_specialist=request.limit,
        )

    async def record_dispatch_failure(
        self, _workflow_run_id: str, _error: BaseException | str
    ) -> None:
        raise AssertionError("the recovered current claim must not fail")


@pytest.mark.asyncio
async def test_execution_interface_rejects_stale_claims_and_recovers_current_claim(
    tmp_path,
) -> None:
    store = SQLiteContentResearchStore(str(tmp_path / "execution-interface.db"))
    repository = AsyncFormalResearchDispatchRepository(store._db_path)
    await repository.enqueue(
        workflow_run_id="run-execution-interface",
        provider="xiaohongshu",
        source_kind="search_result",
        limit=12,
    )
    stale = await repository.claim_next(owner="worker-stale", lease_seconds=1)
    assert stale is not None
    with store._connect() as connection:
        connection.execute(
            "UPDATE content_research_dispatch_jobs SET lease_expires_at=? WHERE workflow_run_id=?",
            (
                (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
                "run-execution-interface",
            ),
        )

    interface = ContentResearchExecutionService(cast(Any, _ExecutionApplication(store)))
    with pytest.raises(ExecutionLeaseFencedError, match="dispatch lease was fenced"):
        await interface.execute_claimed_dispatch(
            context=DispatchLeaseContext(
                workflow_run_id="run-execution-interface",
                lease_owner="worker-stale",
                lease_token=str(stale["lease_token"]),
            ),
            request=ContentResearchSourceCollectionRequest(
                provider="xiaohongshu",
                source_kind="search_result",
                limit=12,
            ),
        )

    current = _CurrentClaimProbe(store)
    worker = ContentResearchDispatchWorker(
        store=store,
        execution_factory=lambda: current,
    )
    assert await worker.run_once() is True
    assert len(current.contexts) == 1
    assert current.contexts[0].lease_token != str(stale["lease_token"])


def test_worker_module_has_no_old_application_dependency() -> None:
    assert "ContentResearchService" not in worker_module.__dict__
